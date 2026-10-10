"""DEV-50 — the delivered-token statement: what a version delivered, counted as tokens, as the basis of invoicing.

Director 10.10.2026 decided it one point at a time (each answer is quoted in the ticket): the basis is the code we
DELIVERED, not the agent's tokens; only added and changed lines count; code and tests share one rate, documentation
has its own; documentation is what the customer receives — never spec copies, any Zadanie or the agent's notes; a fix
of our own error is never billed; tokens are counted with o200k_base, shipped inside the cockpit.

The tests build a small real repository — founding, version 0.1.0, fast fix 0.1.1 — so every rule is seen on the
files it decides about, and count with the shipped tokenizer itself.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import update

from backend.db.models.foundation import User
from backend.db.models.pipeline import PipelineMessage, PipelineState
from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.services import delivered_tokens as dt
from backend.services import delivery_statement as st
from backend.services import orchestrator, system_setting

# ── 1. the tokenizer is the standard, and only the shipped one ────────────────


def test_the_shipped_vocabulary_counts_exactly_as_the_o200k_base_standard():
    """Counts measured with the public o200k_base (tiktoken 0.14.0) on 10.10.2026 — a different vocabulary would
    count these differently."""
    assert dt.count_tokens("Faktúra sa uloží do priečinka zákazníka.") == 15
    assert dt.count_tokens("def usage_cost(db, usage):") == 7
    assert dt.tokenizer_label() == "o200k_base (tiktoken 0.14.0)"


def test_a_vocabulary_that_is_not_the_standard_is_refused(tmp_path, monkeypatch):
    broken = tmp_path / "o200k_base.tiktoken"
    broken.write_bytes(dt.VOCABULARY.read_bytes()[:-50] + b"\n")
    monkeypatch.setattr(dt, "VOCABULARY", broken)
    dt.encoding.cache_clear()
    try:
        with pytest.raises(dt.VocabularyMismatch):
            dt.encoding()
    finally:
        dt.encoding.cache_clear()


def test_counting_never_reaches_for_the_network(monkeypatch):
    """The library would download the vocabulary; the cockpit must never let it."""
    import tiktoken.load
    import tiktoken.registry

    def _no_network(*_a, **_k):
        raise AssertionError("tokenizér siahol na internet")

    monkeypatch.setattr(tiktoken.load, "read_file", _no_network)
    monkeypatch.setattr(tiktoken.load, "read_file_cached", _no_network)
    monkeypatch.setattr(tiktoken.registry, "ENCODINGS", {})  # nothing the library remembered from earlier
    dt.encoding.cache_clear()
    try:
        assert dt.count_tokens("Súpis dodaných tokenov") > 0
    finally:
        dt.encoding.cache_clear()


# ── 2. which file counts as what — one rule ────────────────────────────────────


@pytest.mark.parametrize(
    ("path", "head", "expected"),
    [
        ("backend/app/invoices.py", "", (dt.KIND_CODE, None)),
        ("frontend/src/pages/Invoices.tsx", "", (dt.KIND_CODE, None)),
        (".github/workflows/ci.yml", "", (dt.KIND_CODE, None)),
        ("backend/tests/test_invoices.py", "", (dt.KIND_TESTS, None)),
        ("frontend/src/__tests__/Invoices.test.tsx", "", (dt.KIND_TESTS, None)),
        ("conftest.py", "", (dt.KIND_TESTS, None)),
        ("docs/specs/versions/v1.6.0/specification.md", "", (dt.KIND_DOCS, None)),
        ("docs/specs/versions/v1.6.0/design.md", "", (dt.KIND_DOCS, None)),
        ("docs/specs/versions/v1.6.0/RELEASE_NOTES.md", "", (dt.KIND_DOCS, None)),
        ("docs/specs/backend/BEHAVIOR.md", "", (dt.KIND_DOCS, None)),
        ("README.md", "", (dt.KIND_DOCS, None)),
        ("docs/specs/backend/ARCHITECTURE.md", "", (dt.KIND_DOCS, None)),
        ("docs/specs/versions/v1.3.0/customer-requirements.md", "", (None, dt.EXCLUDED_ZADANIE)),
        ("MEMORY.md", "", (None, dt.EXCLUDED_AGENT_NOTES)),
        ("CLAUDE.md", "", (None, dt.EXCLUDED_AGENT_NOTES)),
        (".claude/agents/ai-agent/CLAUDE.md", "", (None, dt.EXCLUDED_AGENT_NOTES)),
        ("frontend/package-lock.json", "", (None, dt.EXCLUDED_LOCKFILE)),
        ("backend/poetry.lock", "", (None, dt.EXCLUDED_LOCKFILE)),
        ("frontend/src/services/api/pipeline.generated.ts", "", (None, dt.EXCLUDED_GENERATED)),
        ("frontend/src/lib/error-codes.ts", "// AUTO-GENERATED from ERROR_CODES.md", (None, dt.EXCLUDED_GENERATED)),
        ("frontend/public/logo.png", "", (None, dt.EXCLUDED_BINARY)),
        ("frontend/public/icon.svg", "", (None, dt.EXCLUDED_BINARY)),
    ],
)
def test_every_file_is_code_tests_documentation_or_left_out_with_its_reason(path, head, expected):
    assert dt.classify(path, head, "1.6.0") == expected


@pytest.mark.parametrize(
    ("path", "version", "expected"),
    [
        # NEX Inbox mirrors its living documents into the version's folder — the same edits twice.
        ("docs/specs/versions/v1.6.0/spec/backend/ARCHITECTURE.md", "1.6.0", (None, dt.EXCLUDED_SPEC_COPY)),
        ("docs/specs/versions/v1.0.0/spec/backend/ARCHITECTURE.md", "v1.0.0", (None, dt.EXCLUDED_SPEC_COPY)),
        # NEX Manager keeps its living specification in the first version's folder and edits it every version.
        ("docs/specs/versions/v0.1.0/spec/api/openapi.yaml", "1.2.2", (dt.KIND_DOCS, None)),
        ("docs/specs/versions/v0.1.0/spec/backend/ARCHITECTURE.md", "1.2.2", (dt.KIND_DOCS, None)),
    ],
)
def test_only_the_counted_versions_own_spec_mirror_is_left_out(path, version, expected):
    assert dt.classify(path, "", version) == expected


# ── 3. a small real project: founding, 0.1.0, fast fix 0.1.1 ──────────────────


def _git(root, *args, date=None):
    env = dict(
        os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t"
    )
    if date:
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = date
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True, env=env).stdout


def _write(root, path, text):
    f = root / path
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text, encoding="utf-8")


APP_V1 = "def total(items):\n    return sum(i.price for i in items)\n"
APP_V2 = "def total(items):\n    return round(sum(i.price for i in items), 2)\n"


@pytest.fixture
def project(db_session, tmp_path, monkeypatch):
    db = db_session
    monkeypatch.setattr(orchestrator.claude_agent, "PROJECTS_ROOT", tmp_path)
    suffix = uuid.uuid4().hex[:8]
    root = tmp_path / f"supis-{suffix}"
    root.mkdir()
    _git(root, "init", "-q")
    # Founding — the cockpit's template; not the customer's to pay for.
    _write(root, "README.md", "# Šablóna projektu\n")
    _write(root, "backend/app/template.py", "TEMPLATE = True\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "chore: bootstrap", date="2026-10-01T08:00:00+00:00")
    # …and the cockpit's own founding steps after it (CI wiring) — still before the build began.
    _write(root, ".github/workflows/ci.yml", "name: CI\non: [push]\njobs: {}\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "ci: wire CI", date="2026-10-01T09:00:00+00:00")
    # Version 0.1.0 — built after 2026-10-02 09:00.
    _write(root, "backend/app/invoices.py", APP_V1 + "def vat(x):\n    return x * 0.23\n")
    _write(root, "backend/tests/test_invoices.py", "def test_total():\n    assert True\n")
    _write(root, "docs/specs/versions/v0.1.0/specification.md", "# Špecifikácia\nFaktúra sa uloží do priečinka.\n")
    _write(root, "docs/specs/versions/v0.1.0/customer-requirements.md", "Chcem faktúry.\n")
    _write(root, "docs/specs/versions/v0.1.0/spec/ARCH.md", "# Architektúra (kópia)\n")
    _write(root, "frontend/package-lock.json", '{"lockfileVersion": 3}\n')
    _write(root, "frontend/src/api.generated.ts", "export type X = 1;\n")
    _write(root, "MEMORY.md", "poznámky agenta\n")
    (root / "frontend" / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00binary")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "feat: 0.1.0", date="2026-10-03T10:00:00+00:00")
    delivered_010 = _git(root, "rev-parse", "HEAD").strip()
    # Fast fix 0.1.1 — one line changed, one deleted, one file renamed without change.
    _write(root, "backend/app/invoices.py", APP_V2)
    _git(root, "mv", "backend/app/template.py", "backend/app/base.py")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "fix: 0.1.1", date="2026-10-05T10:00:00+00:00")
    delivered_011 = _git(root, "rev-parse", "HEAD").strip()

    owner = User(username=f"u_{suffix}", email=f"{suffix}@t.sk", password_hash="x", role="ri")
    db.add(owner)
    db.flush()
    proj = Project(
        name="Faktúry", slug=root.name, type="standard", auth_mode="password", description="", created_by=owner.id
    )
    db.add(proj)
    db.flush()

    def version(number, flow, delivered, started):
        v = Version(project_id=proj.id, version_number=number, name=number, status="active")
        db.add(v)
        db.flush()
        db.add(
            PipelineState(
                version_id=v.id,
                flow_type=flow,
                current_stage="done",
                current_actor="auditor",
                status="done",
                next_action="",
            )
        )
        msg = orchestrator._record_message(
            db,
            version_id=v.id,
            stage="verifikacia",
            author="auditor",
            recipient="manazer",
            kind="verdict",
            content="PASS",
            payload={"verdict": "PASS", "phase": "verifikacia", "verified_sha": delivered},
        )
        db.flush()
        db.execute(update(PipelineMessage).where(PipelineMessage.id == msg.id).values(created_at=started))
        db.flush()
        return v

    v010 = version("0.1.0", "new_version", delivered_010, datetime(2026, 10, 2, 9, 0, tzinfo=timezone.utc))
    v011 = version("0.1.1", "fast_fix", delivered_011, datetime(2026, 10, 4, 9, 0, tzinfo=timezone.utc))
    return db, root, owner, v010, v011, delivered_010, delivered_011


def _rates(db, owner, code, docs):
    system_setting.upsert(db, st.RATE_CODE_KEY, str(code), updated_by=owner.id)
    system_setting.upsert(db, st.RATE_DOCS_KEY, str(docs), updated_by=owner.id)
    system_setting.invalidate_cache()


def test_the_first_version_counts_what_it_delivered_since_its_build_began(project):
    db, root, _owner, v010, _v011, delivered_010, _ = project
    founding = _git(root, "rev-parse", f"{delivered_010}~1").strip()  # the last founding commit, not the first

    p = st.preview(db, v010, root)

    assert (p.blocked, p.base_sha, p.delivered_sha, p.delivered_source) == (
        None,
        founding,
        delivered_010,
        "verifikacia",
    )
    rows = {f.path: (f.kind, f.excluded) for f in p.count.files}
    assert rows == {
        "backend/app/invoices.py": (dt.KIND_CODE, None),
        "backend/tests/test_invoices.py": (dt.KIND_TESTS, None),
        "docs/specs/versions/v0.1.0/specification.md": (dt.KIND_DOCS, None),
        "docs/specs/versions/v0.1.0/customer-requirements.md": (None, dt.EXCLUDED_ZADANIE),
        "docs/specs/versions/v0.1.0/spec/ARCH.md": (None, dt.EXCLUDED_SPEC_COPY),
        "frontend/package-lock.json": (None, dt.EXCLUDED_LOCKFILE),
        "frontend/src/api.generated.ts": (None, dt.EXCLUDED_GENERATED),
        "MEMORY.md": (None, dt.EXCLUDED_AGENT_NOTES),
        "frontend/logo.png": (None, dt.EXCLUDED_BINARY),
    }, "šablóna zo založenia (README, template.py, CI) sa do prvej verzie nesmie rátať"
    code = APP_V1 + "def vat(x):\n    return x * 0.23"
    assert p.count.tokens(dt.KIND_CODE) == dt.count_tokens(code)
    assert sum(f.tokens for f in p.count.files if f.excluded) == 0


def test_the_next_version_counts_only_added_and_changed_lines_from_the_previous_delivery(project):
    db, root, _owner, _v010, v011, delivered_010, delivered_011 = project

    p = st.preview(db, v011, root)

    assert (p.base_sha, p.delivered_sha) == (delivered_010, delivered_011)
    rows = {f.path: (f.kind, f.lines, f.tokens) for f in p.count.files}
    changed = "    return round(sum(i.price for i in items), 2)"
    assert rows == {"backend/app/invoices.py": (dt.KIND_CODE, 1, dt.count_tokens(changed))}, (
        "zmazané riadky a premenovaný súbor sa rátali"
    )


def test_a_fast_fix_nobody_classified_cannot_be_issued(project):
    db, root, owner, _v010, v011, _, _ = project
    _rates(db, owner, 4.0, 1.5)

    p = st.preview(db, v011, root)

    assert p.work_kind is None and p.amounts is None
    assert p.cannot_issue == [st.KIND_UNDECIDED]
    with pytest.raises(st.CannotIssue, match="kokpit to nehádá"):
        st.issue(db, v011, root, owner.id)


def test_a_fix_of_our_own_error_is_issued_at_zero(project):
    db, root, owner, _v010, v011, _, _ = project
    _rates(db, owner, 4.0, 1.5)
    v011.work_kind = "fix"
    db.flush()

    row = st.issue(db, v011, root, owner.id)

    assert (row.work_kind, row.amount_code_eur, row.amount_docs_eur, row.amount_eur) == (
        "fix",
        Decimal("0.00"),
        Decimal("0.00"),
        Decimal("0.00"),
    )
    assert row.tokens_code > 0, "aj oprava zadarmo ukáže, čo sa dodalo"


def test_a_change_is_billed_by_tokens_and_the_issued_statement_keeps_its_rates(project):
    db, root, owner, v010, _v011, _, _ = project
    _rates(db, owner, 4.0, 1.5)

    row = st.issue(db, v010, root, owner.id)
    code = (Decimal(row.tokens_code + row.tokens_tests) / 1000 * Decimal("4.0")).quantize(Decimal("0.01"))
    docs = (Decimal(row.tokens_docs) / 1000 * Decimal("1.5")).quantize(Decimal("0.01"))
    assert row.work_kind == "change", "nová verzia je nová práca"
    assert (row.amount_code_eur, row.amount_docs_eur, row.amount_eur) == (code, docs, code + docs)
    assert (row.rate_code, row.rate_docs) == (Decimal("4.0"), Decimal("1.5"))

    _rates(db, owner, 10.0, 5.0)
    db.refresh(row)
    assert (row.rate_code, row.amount_code_eur, row.amount_docs_eur, row.amount_eur) == (
        Decimal("4.0"),
        code,
        docs,
        code + docs,
    ), "neskoršia zmena sadzby prepočítala už vydaný súpis"
    assert st.preview(db, v010, root).rate_code == Decimal("10.0")


def test_without_rates_the_statement_is_shown_but_not_issued(project):
    db, root, owner, v010, _v011, _, _ = project
    _rates(db, owner, 0, 0)

    p = st.preview(db, v010, root)

    assert p.count is not None and p.cannot_issue == [st.RATES_MISSING]
    with pytest.raises(st.CannotIssue):
        st.issue(db, v010, root, owner.id)


def test_the_delivered_state_is_the_sign_off_then_the_pass_then_the_tag(project):
    db, root, _owner, v010, _v011, delivered_010, delivered_011 = project
    _git(root, "tag", "-a", "v0.1.0", "-m", "iná značka", _git(root, "rev-parse", f"{delivered_010}~1").strip())
    assert st.delivered_commit(db, v010, root) == (delivered_010, "verifikacia"), "značka prebila Verifikáciu"

    orchestrator._record_message(
        db,
        version_id=v010.id,
        stage="priprava",
        author="manazer",
        recipient="ai_agent",
        kind="notification",
        content="Hotovo",
        payload={"hotovo": True, "hotovo_sha": delivered_011},
    )
    db.flush()
    assert st.delivered_commit(db, v010, root) == (delivered_011, "hotovo")


def test_a_version_without_a_pass_is_found_by_its_tag(project):
    db, root, _owner, _v010, v011, _, delivered_011 = project
    db.execute(update(PipelineMessage).where(PipelineMessage.version_id == v011.id).values(payload={"verdict": "x"}))
    _git(root, "tag", "-a", "v0.1.1", "-m", "verzia", delivered_011)

    assert st.delivered_commit(db, v011, root) == (delivered_011, "znacka")


def test_an_unfinished_version_has_no_statement(project):
    db, root, _owner, v010, _v011, _, _ = project
    db.execute(update(PipelineState).where(PipelineState.version_id == v010.id).values(current_stage="programovanie"))

    assert st.preview(db, v010, root).blocked == st.NOT_DONE


def test_the_csv_lists_every_file_and_why_some_do_not_count(project):
    db, root, owner, v010, _v011, _, _ = project
    _rates(db, owner, 4.0, 1.5)
    row = st.issue(db, v010, root, owner.id)

    text = st.csv_text(row, v010, db.get(Project, v010.project_id))

    assert "backend/app/invoices.py;kód;" in text
    assert f"docs/specs/versions/v0.1.0/customer-requirements.md;;1;0;{dt.EXCLUDED_ZADANIE}" in text
    assert "Tokenizér;o200k_base (tiktoken 0.14.0)" in text


def _issued(db, version, **values):
    """A statement as issued — the numbers of the first one the Director issued (NEX Inbox 1.7.0, 10.10.2026)."""
    row = st.DeliveryStatement(
        version_id=version.id,
        base_sha="ec28fb9ab8d31cc836cb09ce7a03f08d81cee4af",
        delivered_sha="48de08076e594d4b224281b1befd0a5ba0fa9ca3",
        delivered_source=st.SOURCE_VERIFIED,
        tokenizer="o200k_base (tiktoken 0.14.0)",
        work_kind="change",
        tokens_code=94321,
        tokens_tests=79569,
        tokens_docs=76087,
        rate_code=Decimal("2.0000"),
        rate_docs=Decimal("1.0000"),
        amount_code_eur=Decimal("347.78"),
        amount_docs_eur=Decimal("76.09"),
        amount_eur=Decimal("423.87"),
        files=[
            {"path": "backend/app/inbox.py", "kind": "kod", "excluded": None, "lines": 9000, "tokens": 94321},
            {"path": "backend/tests/test_inbox.py", "kind": "skusky", "excluded": None, "lines": 6106, "tokens": 79569},
            {"path": "docs/specification.md", "kind": "dokumentacia", "excluded": None, "lines": 2737, "tokens": 76087},
            {"path": "package-lock.json", "kind": None, "excluded": dt.EXCLUDED_LOCKFILE, "lines": 0, "tokens": 0},
        ],
        created_at=datetime(2026, 10, 10, 13, 36, 8, 268343, tzinfo=timezone.utc),
    )
    for key, value in values.items():
        setattr(row, key, value)
    db.add(row)
    db.flush()
    return row


def test_the_csv_reads_in_a_slovak_spreadsheet_line_by_line_as_on_the_screen(project):
    """Director 10.10.2026: „Áno, doplň sumu ku každému riadku a oprav CSV“ — decimal commas (a point makes a Slovak
    spreadsheet read 423.87 as text or a date), the delivered state and the issue time in words, an amount on every
    line and the total as their sum."""
    db, _root, _owner, v010, _v011, _, _ = project
    row = _issued(db, v010)

    lines = st.csv_text(row, v010, db.get(Project, v010.project_id)).splitlines()

    assert lines[:8] == [
        "Súpis dodaných tokenov",
        "Projekt;Faktúry",
        "Verzia;0.1.0",
        "Vydaný;10.10.2026 15:36:08",
        "Druh práce;zmena alebo nová práca",
        "Od stavu kódu;ec28fb9ab8d31cc836cb09ce7a03f08d81cee4af",
        "Po stav kódu;48de08076e594d4b224281b1befd0a5ba0fa9ca3 (stav, na ktorom prešla Verifikácia)",
        "Tokenizér;o200k_base (tiktoken 0.14.0)",
    ]
    assert lines[9:15] == [
        "Druh;Riadkov;Tokenov;Sadzba (€ / 1 000 tokenov);Suma (€)",
        "Kód a skúšky;15106;173890;2,00;347,78",
        "z toho kód;9000;94321;;",
        "z toho skúšky;6106;79569;;",
        "Dokumentácia;2737;76087;1,00;76,09",
        "Spolu;17843;249977;;423,87",
    ]
    assert lines[16] == "Súbor;Druh;Riadkov;Tokenov;Neráta sa — prečo"
    assert f"package-lock.json;;0;0;{dt.EXCLUDED_LOCKFILE}" in lines


def test_a_rate_keeps_its_places_beyond_two_and_an_unsplit_statement_still_shows_its_total(project):
    db, _root, _owner, v010, _v011, _, _ = project
    row = _issued(
        db,
        v010,
        rate_docs=Decimal("0.0125"),
        amount_code_eur=None,
        amount_docs_eur=None,
        amount_eur=Decimal("348.73"),
    )

    lines = st.csv_text(row, v010, db.get(Project, v010.project_id)).splitlines()

    assert lines[10:15] == [
        "Kód a skúšky;15106;173890;2,00;",
        "z toho kód;9000;94321;;",
        "z toho skúšky;6106;79569;;",
        "Dokumentácia;2737;76087;0,0125;",
        "Spolu;17843;249977;;348,73",
    ]


# ── 5. every line carries its amount; the total is their sum ──────────────────


def test_each_line_is_rounded_to_cents_and_the_total_is_their_sum():
    director = st.amounts("change", 94321, 79569, 76087, Decimal("2"), Decimal("1"))
    assert (director.code, director.docs, director.total) == (
        Decimal("347.78"),
        Decimal("76.09"),
        Decimal("423.87"),
    ), "prvý vydaný súpis (NEX Inbox 1.7.0)"

    # Half a cent on each line: rounding the total alone would bill 0,01 € while the lines show 0,01 € + 0,01 €.
    halves = st.amounts("change", 5, 0, 5, Decimal("1"), Decimal("1"))
    assert (halves.code, halves.docs, halves.total) == (Decimal("0.01"), Decimal("0.01"), Decimal("0.02"))

    fix = st.amounts("fix", 94321, 79569, 76087, Decimal("2"), Decimal("1"))
    assert (fix.code, fix.docs, fix.total) == (Decimal("0.00"), Decimal("0.00"), Decimal("0.00"))


def test_a_statement_whose_lines_do_not_add_up_is_refused_by_the_database(project):
    # pg8000 reports a violated check as ProgrammingError, psycopg as IntegrityError — both are DBAPIError.
    from sqlalchemy.exc import DBAPIError

    db, _root, _owner, v010, _v011, _, _ = project
    with pytest.raises(DBAPIError, match="ck_delivery_statements_lines_add_up"):
        with db.begin_nested():
            _issued(db, v010, amount_docs_eur=Decimal("76.08"))


# ── 4. the HTTP surface ───────────────────────────────────────────────────────


def _as(db, user):
    """Signed in as ``user`` — loaded anew for every request, as in production (a route that commits detaches the
    objects the previous request held)."""
    from backend.core.security import get_current_user, require_shu_or_above
    from backend.main import app

    user_id = user.id
    for dep in (get_current_user, require_shu_or_above):
        app.dependency_overrides[dep] = lambda: db.get(User, user_id)


def test_the_screen_gets_the_preview_issues_downloads_and_decides_the_kind(client, project):
    db, _root, owner, v010, v011, _, _ = project
    _rates(db, owner, 4.0, 1.5)
    _as(db, owner)

    change = client.get(f"/api/v1/versions/{v010.id}/delivery-statement").json()["preview"]
    assert change["delivered_source_label"] == "stav, na ktorom prešla Verifikácia"
    assert Decimal(change["amount_code_eur"]) > 0 and Decimal(change["amount_docs_eur"]) > 0
    assert Decimal(change["amount_code_eur"]) + Decimal(change["amount_docs_eur"]) == Decimal(change["amount_eur"])

    view = client.get(f"/api/v1/versions/{v011.id}/delivery-statement").json()
    assert view["preview"]["work_kind"] is None and view["issued"] == []
    assert client.post(f"/api/v1/versions/{v011.id}/delivery-statement").status_code == 409

    assert client.put(f"/api/v1/versions/{v011.id}/work-kind", json={"work_kind": "fix"}).json() == {"work_kind": "fix"}
    issued = client.post(f"/api/v1/versions/{v011.id}/delivery-statement")
    assert issued.status_code == 201, issued.text
    assert issued.json()["amount_eur"] in ("0.00", "0.0", 0, "0")
    assert (issued.json()["amount_code_eur"], issued.json()["amount_docs_eur"]) == ("0.00", "0.00")

    csv = client.get(f"/api/v1/delivery-statements/{issued.json()['id']}/csv")
    assert csv.status_code == 200 and "attachment" in csv.headers["content-disposition"]
    assert "backend/app/invoices.py" in csv.content.decode("utf-8-sig")


def test_a_fast_fix_and_a_new_version_carry_their_kind_from_the_start(client, project):
    from backend.services import fast_fix as fast_fix_service

    db, _root, owner, _v010, _v011, _, _ = project
    proj_id = _v010.project_id
    _as(db, owner)

    created = client.post(
        f"/api/v1/projects/{proj_id}/versions", json={"version_number": "0.2.0", "work_kind": "change"}
    )
    assert created.status_code in (200, 201), created.text
    assert db.get(Version, uuid.UUID(created.json()["id"])).work_kind == "change"

    version = fast_fix_service.create_patch_version(db, project_id=proj_id, user_id=owner.id)
    assert version.work_kind is None, "rýchla oprava bez voľby ostáva neurčená — kokpit nehádá"


# ── 6. statements issued before the lines were kept ──────────────────────────


def test_an_issued_statement_gets_its_lines_only_where_they_add_up_to_its_total(monkeypatch):
    """Migration 111 fills the line amounts of statements issued before it from their own frozen tokens and rates —
    and never touches a total: a statement whose lines would not add up to it keeps its total alone."""
    from alembic import command
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import Session

    from tests.test_migration_versions import (
        _alembic_config,
        _create_clean_database,
        _drop_database_if_exists,
        _get_test_database_url,
    )

    base_url = _get_test_database_url()
    parts = base_url.rsplit("/", 1)
    admin_url = parts[0] + "/postgres"
    db_name = parts[1].split("?")[0] + "_mig111"
    db_url = parts[0] + "/" + db_name
    _create_clean_database(admin_url, db_name)
    engine = create_engine(db_url)
    try:
        config = _alembic_config(db_url, monkeypatch)
        command.upgrade(config, "110")
        with Session(engine) as db:
            owner = User(username="u111", email="u111@t.sk", password_hash="x", role="ri")
            db.add(owner)
            db.flush()
            proj = Project(
                name="P", slug="p111", type="standard", auth_mode="password", description="", created_by=owner.id
            )
            db.add(proj)
            db.flush()
            version = Version(project_id=proj.id, version_number="1.7.0", name="1.7.0", status="active")
            db.add(version)
            db.commit()
            version_id = version.id
        issued = {
            # the Director's NEX Inbox 1.7.0 — lines 347,78 + 76,09 = 423,87
            "director": ("change", 94321, 79569, 76087, "2", "1", "423.87"),
            # half a cent on each line, total rounded alone: lines 0,01 + 0,01 ≠ 0,01
            "halves": ("change", 5, 0, 5, "1", "1", "0.01"),
            "fix": ("fix", 94321, 79569, 76087, "2", "1", "0.00"),
        }
        with engine.begin() as conn:
            for sha, (kind, code, tests_, docs, rate_code, rate_docs, total) in issued.items():
                conn.execute(
                    text(
                        "INSERT INTO delivery_statements (id, version_id, base_sha, delivered_sha, delivered_source, "
                        "tokenizer, work_kind, tokens_code, tokens_tests, tokens_docs, rate_code, rate_docs, "
                        "amount_eur, files) VALUES (gen_random_uuid(), :v, 'a', :sha, 'verifikacia', 'o200k_base', "
                        ":kind, :code, :tests, :docs, :rc, :rd, :total, '[]'::jsonb)"
                    ),
                    {
                        "v": version_id,
                        "sha": sha,
                        "kind": kind,
                        "code": code,
                        "tests": tests_,
                        "docs": docs,
                        "rc": Decimal(rate_code),
                        "rd": Decimal(rate_docs),
                        "total": Decimal(total),
                    },
                )

        command.upgrade(config, "111")

        with engine.connect() as conn:
            rows = {
                r.delivered_sha: (r.amount_code_eur, r.amount_docs_eur, r.amount_eur)
                for r in conn.execute(
                    text("SELECT delivered_sha, amount_code_eur, amount_docs_eur, amount_eur FROM delivery_statements")
                )
            }
        assert rows == {
            "director": (Decimal("347.78"), Decimal("76.09"), Decimal("423.87")),
            "halves": (None, None, Decimal("0.01")),
            "fix": (Decimal("0.00"), Decimal("0.00"), Decimal("0.00")),
        }

        command.downgrade(config, "110")
        with engine.connect() as conn:
            columns = {
                r[0]
                for r in conn.execute(
                    text("SELECT column_name FROM information_schema.columns WHERE table_name = 'delivery_statements'")
                )
            }
        assert {"amount_code_eur", "amount_docs_eur"} & columns == set()
    finally:
        engine.dispose()
        _drop_database_if_exists(admin_url, db_name)
