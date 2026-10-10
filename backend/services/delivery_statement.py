"""DEV-50 — the delivered-token statement of a version: preview, issue, CSV.

What a version delivered is the difference between two DELIVERED states of the customer's repository: the
version's own (its sign-off commit, else the commit its Verifikácia passed on, else its version tag — the statement
says which) and the previous delivered version's. A version that is the first one is counted from the project's
state when its build began — the template the cockpit founds a project from is not the customer's to pay for.
Counting is :mod:`delivered_tokens`; this module adds the decisions around it (Director 10.10.2026, all in DEV-50):

* a fix of an error in delivered code is never billed (0 €); a fast fix whose kind nobody decided cannot be issued —
  the cockpit does not guess it; a new version is new work;
* code and tests share one rate, documentation has its own (Nastavenia); without rates a statement can be previewed
  but not issued;
* each line (code and tests, documentation) carries its own amount, rounded to cents; the total is their sum;
* next to the figures, what the agent's work cost per 1 000 tokens of each kind (Náklady) — so rates rest on data.
"""

from __future__ import annotations

import csv
import io
import subprocess
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.config.settings import settings
from backend.db.models.delivery_statement import DeliveryStatement
from backend.db.models.pipeline import PipelineMessage, PipelineState
from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.services import delivered_tokens, metrics, pipeline_metrics, system_setting

WORK_FIX = "fix"
WORK_CHANGE = "change"
WORK_KINDS = (WORK_FIX, WORK_CHANGE)

SOURCE_SIGNOFF = "hotovo"
SOURCE_VERIFIED = "verifikacia"
SOURCE_TAG = "znacka"
#: Where the delivered state came from, as the statement says it — on the screen and in the CSV.
SOURCE_LABELS = {
    SOURCE_SIGNOFF: "schválenie verzie",
    SOURCE_VERIFIED: "stav, na ktorom prešla Verifikácia",
    SOURCE_TAG: "značka verzie v gite",
}

#: Which phases make which kind (for the calibration): documentation is written in Príprava and Návrh, code and
#: tests in Vizuál, Programovanie and the Verifikácia fix rounds.
DOC_PHASES = ("priprava", "navrh")
CODE_PHASES = ("vizual", "programovanie", "verifikacia")

RATE_CODE_KEY = "billing_rate_code"
RATE_DOCS_KEY = "billing_rate_docs"

NOT_DONE = "Verzia ešte nie je hotová — súpis sa robí z dodanej verzie."
NO_DELIVERED = (
    "Nedá sa určiť, ktorý stav kódu verzia dodala — nemá zápis z Verifikácie, schválenie ani značku verzie v gite."
)
NO_BASE = "Nedá sa určiť, od ktorého stavu kódu sa verzia ráta (predchádzajúca verzia ani začiatok stavby)."
KIND_UNDECIDED = (
    "Pri rýchlej oprave treba určiť, či opravovala chybu v dodanom kóde (neúčtuje sa), alebo robila zmenu "
    "(účtuje sa) — kokpit to nehádá."
)
RATES_MISSING = "V Nastaveniach chýbajú sadzby fakturácie (kód a skúšky, dokumentácia) — súpis sa nedá vydať."


def work_kind(db: Session, version: Version) -> Optional[str]:
    """The version's work kind as it counts: decided, else ``change`` for a new version, else undecided."""
    if version.work_kind in WORK_KINDS:
        return version.work_kind
    flow = db.execute(select(PipelineState.flow_type).where(PipelineState.version_id == version.id)).scalar()
    return WORK_CHANGE if flow == "new_version" else None


def _is_done(db: Session, version: Version) -> bool:
    stage = db.execute(select(PipelineState.current_stage).where(PipelineState.version_id == version.id)).scalar()
    return stage == "done" or version.status == "released"


def _git(repo: Path, *args: str) -> Optional[str]:
    try:
        done = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout.strip() if done.returncode == 0 and done.stdout.strip() else None


def delivered_commit(db: Session, version: Version, repo: Path) -> tuple[Optional[str], Optional[str]]:
    """``(sha, source)`` of what the version delivered — the sign-off, else the PASS commit, else the version tag."""
    from backend.services.orchestrator import _manazer_signoff

    signoff = _manazer_signoff(db, version.id) or {}
    if signoff.get("hotovo_sha"):
        return str(signoff["hotovo_sha"]), SOURCE_SIGNOFF
    verdict = db.execute(
        select(PipelineMessage.payload)
        .where(
            PipelineMessage.version_id == version.id,
            PipelineMessage.stage == "verifikacia",
            PipelineMessage.kind == "verdict",
            PipelineMessage.payload["verdict"].astext == "PASS",
        )
        .order_by(PipelineMessage.seq.desc())
        .limit(1)
    ).scalar()
    sha = (verdict or {}).get("verified_sha")
    if sha and sha != "legacy":
        return str(sha), SOURCE_VERIFIED
    tagged = _git(repo, "rev-parse", "--verify", "--quiet", f"refs/tags/v{version.version_number}^{{commit}}")
    if tagged:
        return tagged, SOURCE_TAG
    return None, None


def base_commit(db: Session, version: Version, repo: Path, delivered: str) -> Optional[str]:
    """The delivered state of the previous finished version; for the first one, the project when its build began."""
    from backend.services.deploy import _semver_sort_key

    mine = _semver_sort_key(version.version_number)
    earlier = sorted(
        (
            v
            for v in db.execute(select(Version).where(Version.project_id == version.project_id)).scalars()
            if v.id != version.id and _semver_sort_key(v.version_number) < mine and _is_done(db, v)
        ),
        key=lambda v: _semver_sort_key(v.version_number),
        reverse=True,
    )
    for previous in earlier:
        sha, _source = delivered_commit(db, previous, repo)
        if sha:
            return sha
    started = db.execute(
        select(PipelineMessage.created_at)
        .where(PipelineMessage.version_id == version.id)
        .order_by(PipelineMessage.seq.asc())
        .limit(1)
    ).scalar()
    if started is None:
        return None
    return _git(repo, "rev-list", "-1", f"--before={started.isoformat()}", delivered)


def _rates(db: Session) -> tuple[Decimal, Decimal]:
    return (
        Decimal(str(system_setting.get_float(db, RATE_CODE_KEY))),
        Decimal(str(system_setting.get_float(db, RATE_DOCS_KEY))),
    )


CENT = Decimal("0.01")


@dataclass(frozen=True)
class Amounts:
    """€ for the delivery, per line — code and tests, documentation — and in total.

    Each line is rounded to cents and the total is their sum, so an invoice built from the lines never differs from
    the statement by a cent (Director 10.10.2026: „Ešte by som chcel doplniť pre každý riadok na konci sumu“)."""

    code: Decimal
    docs: Decimal

    @property
    def total(self) -> Decimal:
        return self.code + self.docs


def amounts(
    kind: str, tokens_code: int, tokens_tests: int, tokens_docs: int, rate_code: Decimal, rate_docs: Decimal
) -> Amounts:
    """0 for a fix of our own error; otherwise each line's tokens / 1 000 × its rate, rounded to cents."""
    if kind == WORK_FIX:
        return Amounts(code=Decimal("0.00"), docs=Decimal("0.00"))
    return Amounts(
        code=(Decimal(tokens_code + tokens_tests) / 1000 * rate_code).quantize(CENT, rounding=ROUND_HALF_UP),
        docs=(Decimal(tokens_docs) / 1000 * rate_docs).quantize(CENT, rounding=ROUND_HALF_UP),
    )


@dataclass
class Calibration:
    """What the agent's work on the version cost — per kind and per 1 000 delivered tokens (from Náklady)."""

    eur_code: Optional[float] = None
    eur_docs: Optional[float] = None
    complete: bool = True
    per_1k_code: Optional[float] = None
    per_1k_docs: Optional[float] = None


def calibration(db: Session, version_id: uuid.UUID, count: delivered_tokens.Count) -> Calibration:
    pricing = metrics.plan_pricing(db)
    by_phase = pipeline_metrics.aggregate_usage_by_phase(db, version_id)
    out = Calibration()
    for phases, kinds, attr in (
        (CODE_PHASES, (delivered_tokens.KIND_CODE, delivered_tokens.KIND_TESTS), "code"),
        (DOC_PHASES, (delivered_tokens.KIND_DOCS,), "docs"),
    ):
        spend = metrics.node_spend(pricing, metrics.merged([by_phase.get(p) for p in phases]))
        if spend is None:
            continue
        out.complete = out.complete and spend["eur_complete"]
        setattr(out, f"eur_{attr}", spend["eur"])
        tokens = sum(count.tokens(k) for k in kinds)
        if spend["eur"] is not None and tokens:
            setattr(out, f"per_1k_{attr}", round(spend["eur"] / tokens * 1000, 2))
    return out


@dataclass
class Preview:
    """The statement as it would be issued now — or why it cannot be counted / issued."""

    version_number: str
    blocked: Optional[str] = None  # cannot be counted at all
    work_kind: Optional[str] = None
    base_sha: Optional[str] = None
    delivered_sha: Optional[str] = None
    delivered_source: Optional[str] = None
    tokenizer: str = field(default_factory=delivered_tokens.tokenizer_label)
    count: Optional[delivered_tokens.Count] = None
    rate_code: Decimal = Decimal("0")
    rate_docs: Decimal = Decimal("0")
    amounts: Optional[Amounts] = None
    cannot_issue: list[str] = field(default_factory=list)
    calibration: Optional[Calibration] = None


def preview(db: Session, version: Version, repo: Path) -> Preview:
    out = Preview(version_number=version.version_number, work_kind=work_kind(db, version))
    out.rate_code, out.rate_docs = _rates(db)
    if not _is_done(db, version):
        out.blocked = NOT_DONE
        return out
    out.delivered_sha, out.delivered_source = delivered_commit(db, version, repo)
    if not out.delivered_sha:
        out.blocked = NO_DELIVERED
        return out
    out.base_sha = base_commit(db, version, repo, out.delivered_sha)
    if not out.base_sha:
        out.blocked = NO_BASE
        return out
    try:
        out.count = delivered_tokens.count(repo, out.base_sha, out.delivered_sha, version.version_number)
    except delivered_tokens.GitUnreadable as exc:
        out.blocked = f"Kód projektu sa nedá prečítať: {exc}"
        return out
    out.calibration = calibration(db, version.id, out.count)
    if out.work_kind is None:
        out.cannot_issue.append(KIND_UNDECIDED)
    if out.rate_code <= 0 or out.rate_docs <= 0:
        out.cannot_issue.append(RATES_MISSING)
    if out.work_kind is not None:
        out.amounts = amounts(
            out.work_kind,
            out.count.tokens(delivered_tokens.KIND_CODE),
            out.count.tokens(delivered_tokens.KIND_TESTS),
            out.count.tokens(delivered_tokens.KIND_DOCS),
            out.rate_code,
            out.rate_docs,
        )
    return out


class CannotIssue(ValueError):
    """The statement cannot be issued — the message says why, for the Manažér."""


def files_payload(count: delivered_tokens.Count) -> list[dict]:
    return [
        {"path": f.path, "kind": f.kind, "excluded": f.excluded, "lines": f.lines, "tokens": f.tokens}
        for f in count.files
    ]


def issue(db: Session, version: Version, repo: Path, user_id: Optional[uuid.UUID]) -> DeliveryStatement:
    """Freeze the statement as it is now — rates, rule and tokenizer stay with it (a later change cannot alter it)."""
    p = preview(db, version, repo)
    if p.blocked:
        raise CannotIssue(p.blocked)
    if p.cannot_issue:
        raise CannotIssue(" ".join(p.cannot_issue))
    assert p.count is not None and p.work_kind is not None and p.amounts is not None
    row = DeliveryStatement(
        version_id=version.id,
        base_sha=p.base_sha,
        delivered_sha=p.delivered_sha,
        delivered_source=p.delivered_source,
        tokenizer=p.tokenizer,
        work_kind=p.work_kind,
        tokens_code=p.count.tokens(delivered_tokens.KIND_CODE),
        tokens_tests=p.count.tokens(delivered_tokens.KIND_TESTS),
        tokens_docs=p.count.tokens(delivered_tokens.KIND_DOCS),
        rate_code=p.rate_code,
        rate_docs=p.rate_docs,
        amount_code_eur=p.amounts.code,
        amount_docs_eur=p.amounts.docs,
        amount_eur=p.amounts.total,
        files=files_payload(p.count),
        created_by=user_id,
    )
    db.add(row)
    db.flush()
    return row


KIND_LABELS = {
    delivered_tokens.KIND_CODE: "kód",
    delivered_tokens.KIND_TESTS: "skúšky",
    delivered_tokens.KIND_DOCS: "dokumentácia",
}
WORK_LABELS = {WORK_FIX: "oprava chyby v dodanom kóde (neúčtuje sa)", WORK_CHANGE: "zmena alebo nová práca"}


def _number(value: Decimal, places: int = 2) -> str:
    """A decimal as a Slovak spreadsheet reads it — a comma, never a point (``423,87``); a rate keeps the places it
    has beyond two (``0,0125``)."""
    shown = max(places, -value.normalize().as_tuple().exponent)
    return f"{value:.{shown}f}".replace(".", ",")


def _local_time(at: datetime) -> str:
    """The issue time as the Manažér saw it on the screen — local, without microseconds."""
    return at.astimezone(ZoneInfo(settings.display_timezone)).strftime("%d.%m.%Y %H:%M:%S")


def csv_text(row: DeliveryStatement, version: Version, project: Project) -> str:
    """The issued statement as CSV for a Slovak spreadsheet (semicolons, decimal commas) — the basis for the invoice:
    who and what, then the lines as on the screen (lines, tokens, rate, amount — the total is their sum), then every
    file and why some do not count."""
    lines = {kind: sum(f["lines"] for f in row.files if f["kind"] == kind) for kind in delivered_tokens.KINDS}
    code_lines = lines[delivered_tokens.KIND_CODE] + lines[delivered_tokens.KIND_TESTS]
    out = io.StringIO()
    w = csv.writer(out, delimiter=";", lineterminator="\n")
    w.writerow(["Súpis dodaných tokenov"])
    w.writerow(["Projekt", project.name])
    w.writerow(["Verzia", version.version_number])
    w.writerow(["Vydaný", _local_time(row.created_at)])
    w.writerow(["Druh práce", WORK_LABELS.get(row.work_kind, row.work_kind)])
    w.writerow(["Od stavu kódu", row.base_sha])
    source = SOURCE_LABELS.get(row.delivered_source, row.delivered_source)
    w.writerow(["Po stav kódu", f"{row.delivered_sha} ({source})"])
    w.writerow(["Tokenizér", row.tokenizer])
    w.writerow([])
    w.writerow(["Druh", "Riadkov", "Tokenov", "Sadzba (€ / 1 000 tokenov)", "Suma (€)"])

    def line_amount(value: Optional[Decimal]) -> str:
        # A statement issued before the lines were kept, whose lines would not add up to its total, has none.
        return _number(value) if value is not None else ""

    w.writerow(
        [
            "Kód a skúšky",
            code_lines,
            row.tokens_code + row.tokens_tests,
            _number(row.rate_code),
            line_amount(row.amount_code_eur),
        ]
    )
    w.writerow(["z toho kód", lines[delivered_tokens.KIND_CODE], row.tokens_code, "", ""])
    w.writerow(["z toho skúšky", lines[delivered_tokens.KIND_TESTS], row.tokens_tests, "", ""])
    w.writerow(
        [
            "Dokumentácia",
            lines[delivered_tokens.KIND_DOCS],
            row.tokens_docs,
            _number(row.rate_docs),
            line_amount(row.amount_docs_eur),
        ]
    )
    w.writerow(
        [
            "Spolu",
            code_lines + lines[delivered_tokens.KIND_DOCS],
            row.tokens_code + row.tokens_tests + row.tokens_docs,
            "",
            _number(row.amount_eur),
        ]
    )
    w.writerow([])
    w.writerow(["Súbor", "Druh", "Riadkov", "Tokenov", "Neráta sa — prečo"])
    for f in row.files:
        w.writerow([f["path"], KIND_LABELS.get(f["kind"] or "", ""), f["lines"], f["tokens"], f["excluded"] or ""])
    return out.getvalue()
