"""DEV-50 — the delivered-token statement of a version: preview, issue, CSV.

What a version delivered is the difference between two DELIVERED states of the customer's repository: the
version's own (its sign-off commit, else the commit its Verifikácia passed on, else its version tag — the statement
says which) and the previous delivered version's. A version that is the first one is counted from the project's
state when its build began — the template the cockpit founds a project from is not the customer's to pay for.
Counting is :mod:`delivered_tokens`; this module adds the decisions around it (Director 10.10.2026, all in DEV-50):

* a fix of an error in delivered code is a warranty fix — its category's discount is 100 % unless the manager
  decides otherwise (DEV-75); a fast fix whose kind nobody decided cannot be issued — the cockpit does not guess it;
  a new version is new work;
* code and tests share one rate, documentation has its own (Nastavenia); without rates a statement can be previewed
  but not issued;
* each line carries its own amount, rounded to cents; the total is their sum. DEV-75: the lines are the billing
  category of the task that wrote the delivered code × (code and tests, documentation), each with its full value,
  discount and amount (:mod:`billing_category`);
* a version has one valid statement: issuing again replaces the valid one, only when the Manažér confirms that one
  (DEV-54) — the old one stays, marked as replaced, so it cannot be invoiced by mistake;
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
from typing import Optional, get_args

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from backend.core.local_time import local_time
from backend.db.models.delivered_authorship import DeliveredAuthorship
from backend.db.models.delivery_statement import DeliveryStatement
from backend.db.models.pipeline import PipelineMessage, PipelineState
from backend.db.models.projects import Project
from backend.db.models.version_rate_change import VersionRateChange
from backend.db.models.versions import Version
from backend.schemas.delivery_statement import WorkKind
from backend.services import billing_category, delivered_tokens, metrics, pipeline_metrics, system_setting
from backend.services import version as version_service

WORK_FIX = "fix"
WORK_CHANGE = "change"
WORK_GIFT = "gift"
WORK_KINDS = get_args(WorkKind)
#: DEV-75, DEV-118 — the billing category of a task nobody decided, by the work kind of its version.
CATEGORY_OF_KIND = {
    WORK_FIX: billing_category.BUG,
    WORK_CHANGE: billing_category.REQUEST,
    WORK_GIFT: billing_category.GIFT,
}

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
    "Pri rýchlej oprave treba určiť, či opravovala chybu v dodanom kóde (záručná oprava), robila zmenu (účtuje sa), "
    "alebo je dar — pozornosť ICC; kokpit to nehádá."
)
RATES_MISSING = "V Nastaveniach chýbajú sadzby fakturácie (kód a skúšky, dokumentácia) — súpis sa nedá vydať."
REPLACE_UNCONFIRMED = "Verzia už má platný súpis z {when} za {amount} € — nový ho nahradí, len keď to potvrdíš."
VALID_CHANGED = "Platný súpis tejto verzie sa medzitým zmenil — obnov stránku a pozri, ktorý platí."


def work_kind(db: Session, version: Version) -> Optional[str]:
    """The version's work kind as it counts: decided, else ``change`` for a new version, else undecided."""
    if version.work_kind in WORK_KINDS:
        return version.work_kind
    flow = db.execute(select(PipelineState.flow_type).where(PipelineState.version_id == version.id)).scalar()
    return WORK_CHANGE if flow == "new_version" else None


def version_category(kind: Optional[str]) -> Optional[str]:
    """DEV-75 — the billing category of a task nobody decided: a fix of our error → bug, a change → request,
    DEV-118: a gift → gift."""
    return CATEGORY_OF_KIND.get(kind) if kind else None


def _git(repo: Path, *args: str) -> Optional[str]:
    try:
        done = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout.strip() if done.returncode == 0 and done.stdout.strip() else None


def _recorded_delivery(db: Session, version: Version) -> tuple[Optional[str], Optional[str]]:
    """``(sha, source)`` the database knows the version delivered — the sign-off, else the PASS commit; no git."""
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
    return None, None


def _tagged(repo: Path, version_number: str) -> Optional[str]:
    return _git(repo, "rev-parse", "--verify", "--quiet", f"refs/tags/v{version_number}^{{commit}}")


def delivered_commit(db: Session, version: Version, repo: Path) -> tuple[Optional[str], Optional[str]]:
    """``(sha, source)`` of what the version delivered — the sign-off, else the PASS commit, else the version tag."""
    sha, source = _recorded_delivery(db, version)
    if sha:
        return sha, source
    tagged = _tagged(repo, version.version_number)
    return (tagged, SOURCE_TAG) if tagged else (None, None)


#: One step of finding where a version began: ``("sha", commit)`` known from the database, ``("tag", version
#: number)`` to look up in git, ``("before", ISO time)`` — the project's state when the version's build began.
BaseStep = tuple[str, str]


def base_plan(db: Session, version: Version) -> list[BaseStep]:
    """The database half of :func:`base_commit` — what to try, in order, without touching git.

    DEV-65: Verifikácia reads the version's range from an ``async`` round; the plan is read there and resolved by
    :func:`resolve_base` off the main loop, so no git command waits on it (ICCINT-74)."""
    from backend.services.deploy import _semver_sort_key

    mine = _semver_sort_key(version.version_number)
    earlier = sorted(
        (
            v
            for v in db.execute(select(Version).where(Version.project_id == version.project_id)).scalars()
            if v.id != version.id
            and _semver_sort_key(v.version_number) < mine
            and version_service.build_finished(db, v)
        ),
        key=lambda v: _semver_sort_key(v.version_number),
        reverse=True,
    )
    steps: list[BaseStep] = []
    for previous in earlier:
        sha, _source = _recorded_delivery(db, previous)
        steps.append(("sha", sha) if sha else ("tag", previous.version_number))
    started = db.execute(
        select(PipelineMessage.created_at)
        .where(PipelineMessage.version_id == version.id)
        .order_by(PipelineMessage.seq.asc())
        .limit(1)
    ).scalar()
    if started is not None:
        steps.append(("before", started.isoformat()))
    return steps


def resolve_base(repo: Path, steps: list[BaseStep], delivered: str) -> Optional[str]:
    """The git half of :func:`base_commit`: the first step that names a commit."""
    for kind, value in steps:
        if kind == "sha":
            return value
        if kind == "tag" and (tagged := _tagged(repo, value)):
            return tagged
        if kind == "before":
            return _git(repo, "rev-list", "-1", f"--before={value}", delivered)
    return None


def base_commit(db: Session, version: Version, repo: Path, delivered: str) -> Optional[str]:
    """The delivered state of the previous finished version; for the first one, the project when its build began."""
    return resolve_base(repo, base_plan(db, version), delivered)


def settings_rates(db: Session) -> tuple[Decimal, Decimal]:
    """The rates in Nastavenia — code and tests, documentation — per 1 000 tokens."""
    return (
        Decimal(str(system_setting.get_float(db, RATE_CODE_KEY))),
        Decimal(str(system_setting.get_float(db, RATE_DOCS_KEY))),
    )


def _rates(db: Session, version: Optional[Version] = None) -> tuple[Decimal, Decimal]:
    """The rates a version's statement counts with: its own where it has one (DEV-74), else Nastavenia."""
    code, docs = settings_rates(db)
    if version is not None and version.rate_code is not None:
        code = Decimal(version.rate_code)
    if version is not None and version.rate_docs is not None:
        docs = Decimal(version.rate_docs)
    return code, docs


#: DEV-74 — a version's own rate keeps the places the database keeps.
RATE_PLACES = Decimal("0.0001")
RATE_NOT_POSITIVE = "Sadzba musí byť väčšia ako 0 € — prázdna znamená sadzbu z Nastavení."


def set_rates(
    db: Session, version: Version, rate_code: Optional[Decimal], rate_docs: Optional[Decimal], changed_by
) -> Optional[VersionRateChange]:
    """Set (or, with ``None``, return to Nastavenia) a version's own rates and keep the change — who, when, from what
    to what. An issued statement keeps the rates it was issued with; only a new one replacing it counts with these.
    Returns the recorded change, ``None`` when nothing changed."""
    new = []
    for value in (rate_code, rate_docs):
        if value is None:
            new.append(None)
            continue
        value = Decimal(value)
        if value <= 0:
            raise ValueError(RATE_NOT_POSITIVE)
        new.append(value.quantize(RATE_PLACES, rounding=ROUND_HALF_UP))
    previous = [version.rate_code, version.rate_docs]
    if [None if v is None else Decimal(v) for v in previous] == new:
        return None
    change = VersionRateChange(
        version_id=version.id,
        previous_rate_code=previous[0],
        previous_rate_docs=previous[1],
        rate_code=new[0],
        rate_docs=new[1],
        changed_by=changed_by,
    )
    version.rate_code, version.rate_docs = new
    db.add(change)
    db.flush()
    return change


def rate_changes(db: Session, version_id: uuid.UUID) -> list[VersionRateChange]:
    """Every change of a version's own rates, the newest first."""
    return list(
        db.execute(
            select(VersionRateChange)
            .where(VersionRateChange.version_id == version_id)
            .order_by(VersionRateChange.created_at.desc())
        ).scalars()
    )


@dataclass(frozen=True)
class Amounts:
    """€ for the delivery, per kind — code and tests, documentation — and in total, summed from the statement's lines.

    Each line is rounded to cents and the total is their sum, so an invoice built from the lines never differs from
    the statement by a cent (Director 10.10.2026: „Ešte by som chcel doplniť pre každý riadok na konci sumu“)."""

    code: Decimal
    docs: Decimal
    #: Before the discounts (DEV-75) — what the delivery was worth.
    full: Decimal

    @property
    def total(self) -> Decimal:
        return self.code + self.docs


def amounts(lines: list[billing_category.Line]) -> Amounts:
    zero = Decimal("0.00")
    return Amounts(
        code=sum((ln.value.amount for ln in lines if ln.kind == billing_category.KIND_CODE), zero),
        docs=sum((ln.value.amount for ln in lines if ln.kind == billing_category.KIND_DOCS), zero),
        full=sum((ln.value.full for ln in lines), zero),
    )


#: DEV-75 — the rule :func:`delivered_tokens.line_commits` is read by; a kept authorship made by another is redone.
AUTHORSHIP_RULE = 1


def _authorship(db: Session, version: Version, repo: Path, base: str, head: str) -> delivered_tokens.Authorship:
    """Which plan task wrote each delivered line. Who wrote it is kept once per (base, delivered) pair — blame takes
    seconds and the billing tab counts every version; which task a commit belongs to is read from git each time.

    The kept answer is written in a savepoint and stays only when the caller commits — the statement's routes do."""
    row = db.execute(
        select(DeliveredAuthorship).where(
            DeliveredAuthorship.project_id == version.project_id,
            DeliveredAuthorship.base_sha == base,
            DeliveredAuthorship.head_sha == head,
        )
    ).scalar_one_or_none()
    if row is not None and (row.payload or {}).get("rule") == AUTHORSHIP_RULE:
        commits = row.payload["files"]
    else:
        commits = delivered_tokens.line_commits(repo, base, head)
        payload = {"rule": AUTHORSHIP_RULE, "files": commits}
        with db.begin_nested():
            if row is not None:
                row.payload = payload
            else:
                db.execute(
                    insert(DeliveredAuthorship)
                    .values(
                        id=uuid.uuid4(), project_id=version.project_id, base_sha=base, head_sha=head, payload=payload
                    )
                    .on_conflict_do_nothing(constraint="uq_delivered_authorship_range")
                )
    return delivered_tokens.task_authorship(commits, delivered_tokens.task_of_commits(repo, base, head))


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
    #: DEV-74 — the rates in Nastavenia, and whether the version counts with its own instead.
    settings_rate_code: Decimal = Decimal("0")
    settings_rate_docs: Decimal = Decimal("0")
    rate_code_own: bool = False
    rate_docs_own: bool = False
    amounts: Optional[Amounts] = None
    cannot_issue: list[str] = field(default_factory=list)
    calibration: Optional[Calibration] = None
    #: DEV-75 — the category of a task nobody decided (``None`` while the work kind is undecided), the discounts the
    #: statement counts with (and Nastavenia's, and which are the version's own), the lines and every task's share.
    version_category: Optional[str] = None
    discounts: dict[str, Decimal] = field(default_factory=dict)
    settings_discounts: dict[str, Decimal] = field(default_factory=dict)
    discounts_own: dict[str, bool] = field(default_factory=dict)
    lines: list[billing_category.Line] = field(default_factory=list)
    tasks: list[billing_category.TaskShare] = field(default_factory=list)


def preview(db: Session, version: Version, repo: Path) -> Preview:
    out = Preview(version_number=version.version_number, work_kind=work_kind(db, version))
    out.rate_code, out.rate_docs = _rates(db, version)
    out.settings_rate_code, out.settings_rate_docs = settings_rates(db)
    out.rate_code_own, out.rate_docs_own = version.rate_code is not None, version.rate_docs is not None
    out.version_category = version_category(out.work_kind)
    out.discounts = billing_category.discounts(db, version)
    out.settings_discounts = billing_category.settings_discounts(db)
    out.discounts_own = {c: v is not None for c, v in billing_category.own_discounts(version).items()}
    plan = billing_category.plan_tasks(db, version.id)
    out.tasks = [billing_category.TaskShare(task=t, category=t.category or out.version_category) for t in plan]
    if not version_service.build_finished(db, version):
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
        authors = _authorship(db, version, repo, out.base_sha, out.delivered_sha)
        out.count = delivered_tokens.count(
            repo, out.base_sha, out.delivered_sha, version.version_number, authors=authors
        )
    except delivered_tokens.GitUnreadable as exc:
        out.blocked = f"Kód projektu sa nedá prečítať: {exc}"
        return out
    out.calibration = calibration(db, version.id, out.count)
    if out.work_kind is None:
        out.cannot_issue.append(KIND_UNDECIDED)
    if out.rate_code <= 0 or out.rate_docs <= 0:
        out.cannot_issue.append(RATES_MISSING)
    if out.version_category is not None:
        rates = {billing_category.KIND_CODE: out.rate_code, billing_category.KIND_DOCS: out.rate_docs}
        out.lines, out.tasks = billing_category.split(out.count, plan, out.version_category, rates, out.discounts)
        out.amounts = amounts(out.lines)
    return out


class CannotIssue(ValueError):
    """The statement cannot be issued — the message says why, for the Manažér."""


def files_payload(count: delivered_tokens.Count) -> list[dict]:
    return [
        {"path": f.path, "kind": f.kind, "excluded": f.excluded, "lines": f.lines, "tokens": f.tokens}
        for f in count.files
    ]


def valid_statement(db: Session, version_id: uuid.UUID) -> Optional[DeliveryStatement]:
    """The version's valid statement — the one not replaced (the database keeps at most one)."""
    return db.execute(
        select(DeliveryStatement).where(
            DeliveryStatement.version_id == version_id, DeliveryStatement.replaced_at.is_(None)
        )
    ).scalar_one_or_none()


#: DEV-73 — where a version stands for invoicing, as the billing tab shows it.
BILLING_ISSUED = "issued"  # a valid statement exists
BILLING_READY = "ready"  # can be issued now
BILLING_NEEDS_KIND = "needs_kind"  # a fast fix whose work kind nobody decided
BILLING_CANNOT = "cannot"  # countable, but something else stops the issue (e.g. no rates)
BILLING_BLOCKED = "blocked"  # cannot be counted at all
BILLING_NOT_DONE = "not_done"  # still being built


@dataclass
class BillingRow:
    version: Version
    flow_type: Optional[str]
    preview: Preview
    valid: Optional[DeliveryStatement]
    state: str
    #: The sentence why it cannot be issued (``needs_kind`` / ``cannot`` / ``blocked`` / ``not_done``).
    reason: Optional[str] = None

    @property
    def differs(self) -> bool:
        """A valid statement whose amount the preview would count differently today (other rates or delivery)."""
        if self.valid is None or self.preview.amounts is None:
            return False
        return Decimal(self.preview.amounts.total) != Decimal(self.valid.amount_eur)


def _billing_state(preview: Preview, valid: Optional[DeliveryStatement]) -> tuple[str, Optional[str]]:
    if valid is not None:
        return BILLING_ISSUED, None
    if preview.blocked == NOT_DONE:
        return BILLING_NOT_DONE, preview.blocked
    if preview.blocked:
        return BILLING_BLOCKED, preview.blocked
    if KIND_UNDECIDED in preview.cannot_issue:
        return BILLING_NEEDS_KIND, KIND_UNDECIDED
    if preview.cannot_issue:
        return BILLING_CANNOT, " ".join(preview.cannot_issue)
    return BILLING_READY, None


def billing_rows(db: Session, project: Project, repo: Path) -> list[BillingRow]:
    """DEV-73 — every version of a project for invoicing, the newest first: its statement as it would be issued now
    and the valid one, if any. 12.10.2026 the statement of only the selected version was visible and the versions
    between 1.5.0 and 1.7.1 looked lost although nothing was."""
    from backend.services.deploy import _semver_sort_key

    versions = sorted(
        db.execute(select(Version).where(Version.project_id == project.id)).scalars(),
        key=lambda v: _semver_sort_key(v.version_number),
        reverse=True,
    )
    flows = dict(
        db.execute(
            select(PipelineState.version_id, PipelineState.flow_type).where(
                PipelineState.version_id.in_([v.id for v in versions])
            )
        ).all()
    )
    rows: list[BillingRow] = []
    for version in versions:
        p = preview(db, version, repo)
        valid = valid_statement(db, version.id)
        state, reason = _billing_state(p, valid)
        rows.append(
            BillingRow(
                version=version, flow_type=flows.get(version.id), preview=p, valid=valid, state=state, reason=reason
            )
        )
    return rows


def issue(
    db: Session,
    version: Version,
    repo: Path,
    user_id: Optional[uuid.UUID],
    replace: Optional[uuid.UUID] = None,
) -> DeliveryStatement:
    """Freeze the statement as it is now — rates, rule and tokenizer stay with it (a later change cannot alter it).

    When the version already has a valid statement, the new one replaces it — only if ``replace`` names exactly that
    one (DEV-54): what the Manažér confirmed is what gets replaced, never a statement someone issued meanwhile."""
    p = preview(db, version, repo)
    if p.blocked:
        raise CannotIssue(p.blocked)
    if p.cannot_issue:
        raise CannotIssue(" ".join(p.cannot_issue))
    assert p.count is not None and p.work_kind is not None and p.amounts is not None
    current = valid_statement(db, version.id)
    if current is None and replace is not None:
        raise CannotIssue(VALID_CHANGED)
    if current is not None and current.id != replace:
        raise CannotIssue(
            REPLACE_UNCONFIRMED.format(when=_local_time(current.created_at), amount=_number(current.amount_eur))
        )
    if current is not None:
        # Marked first, in the same transaction, at the moment the new one is issued (both read now()).
        current.replaced_at = func.now()
        db.flush()
    row = DeliveryStatement(
        replaces_id=current.id if current is not None else None,
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
        lines=[line.payload() for line in p.lines],
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
WORK_LABELS = {
    WORK_FIX: "oprava chyby v dodanom kóde (záručná oprava)",
    WORK_CHANGE: "zmena alebo nová práca",
    WORK_GIFT: "dar — pozornosť ICC",
}


def _number(value: Decimal, places: int = 2) -> str:
    """A decimal as a Slovak spreadsheet reads it — a comma, never a point (``423,87``); a rate keeps the places it
    has beyond two (``0,0125``)."""
    shown = max(places, -value.normalize().as_tuple().exponent)
    return f"{value:.{shown}f}".replace(".", ",")


def _local_time(at: datetime) -> str:
    """The issue time as the Manažér saw it on the screen — local, without microseconds."""
    return local_time(at).strftime("%d.%m.%Y %H:%M:%S")


def csv_text(row: DeliveryStatement, version: Version, project: Project) -> str:
    """The issued statement as CSV for a Slovak spreadsheet (semicolons, decimal commas) — the basis for the invoice:
    who and what, then the lines as on the screen (DEV-75: category × kind with full value, discount and amount, and
    which task is on which line; a statement issued before keeps its two lines — the total is their sum), then every
    file and why some do not count."""
    lines = {kind: sum(f["lines"] for f in row.files if f["kind"] == kind) for kind in delivered_tokens.KINDS}
    code_lines = lines[delivered_tokens.KIND_CODE] + lines[delivered_tokens.KIND_TESTS]
    out = io.StringIO()
    w = csv.writer(out, delimiter=";", lineterminator="\n")
    w.writerow(["Súpis dodaných tokenov"])
    if row.replaced_at is not None:
        w.writerow([f"NAHRADENÝ súpisom z {_local_time(row.replaced_at)} — nepoužiť na faktúru"])
    w.writerow(["Projekt", project.name])
    w.writerow(["Verzia", version.version_number])
    w.writerow(["Vydaný", _local_time(row.created_at)])
    w.writerow(["Druh práce", WORK_LABELS.get(row.work_kind, row.work_kind)])
    w.writerow(["Od stavu kódu", row.base_sha])
    source = SOURCE_LABELS.get(row.delivered_source, row.delivered_source)
    w.writerow(["Po stav kódu", f"{row.delivered_sha} ({source})"])
    w.writerow(["Tokenizér", row.tokenizer])
    w.writerow([])
    if row.lines is not None:
        _csv_category_lines(w, row)
        _csv_files(w, row)
        return out.getvalue()
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
    _csv_files(w, row)
    return out.getvalue()


def _csv_category_lines(w, row: DeliveryStatement) -> None:
    """DEV-75 — the frozen lines: category × kind, full value, discount and amount; then the tasks on each line."""
    w.writerow(
        [
            "Riadok súpisu",
            "Riadkov",
            "Tokenov",
            "Sadzba (€ / 1 000 tokenov)",
            "Plná hodnota (€)",
            "Zľava (%)",
            "Zľava (€)",
            "Dôvod zľavy",
            "Suma (€)",
        ]
    )
    for ln in row.lines:
        w.writerow(
            [
                ln["label"],
                ln["lines"],
                ln["tokens"],
                _number(Decimal(ln["rate"])),
                _number(Decimal(ln["full_eur"])),
                _number(Decimal(ln["discount_pct"])),
                _number(Decimal(ln["discount_eur"])),
                ln["discount_label"] or "",
                _number(Decimal(ln["amount_eur"])),
            ]
        )
    w.writerow(
        [
            "Spolu",
            sum(ln["lines"] for ln in row.lines),
            sum(ln["tokens"] for ln in row.lines),
            "",
            _number(sum((Decimal(ln["full_eur"]) for ln in row.lines), Decimal("0"))),
            "",
            _number(sum((Decimal(ln["discount_eur"]) for ln in row.lines), Decimal("0"))),
            "",
            _number(row.amount_eur),
        ]
    )
    w.writerow([])
    w.writerow(["Úloha", "Názov", "Riadok súpisu", "Riadkov", "Tokenov"])
    for ln in row.lines:
        for task in ln["tasks"]:
            w.writerow([task["label"], task["title"], ln["label"], task["lines"], task["tokens"]])
    w.writerow([])


def _csv_files(w, row: DeliveryStatement) -> None:
    w.writerow([])
    w.writerow(["Súbor", "Druh", "Riadkov", "Tokenov", "Neráta sa — prečo"])
    for f in row.files:
        w.writerow([f["path"], KIND_LABELS.get(f["kind"] or "", ""), f["lines"], f["tokens"], f["excluded"] or ""])
