"""DEV-75 — every task has a billing category and every category a discount; the statement bills each delivered line
by the task whose commit wrote it.

12.10.2026 the Director: besides bug fixes and customer requests a gift — an attention of ICC, on the invoice with a
discount up to 100 %; the category is set for every task. Approved: bug / request / gift, empty = the version's own
(a fast fix of our error → bug, otherwise request); discounts in Nastavenia (bug 100 as a warranty fix, request 0,
gift 100), a version may have its own with every change kept; lines category × (code and tests, documentation), work
without a task as its own line in the version's category; an issued statement keeps its lines.

The tests build a small real repository whose commits name their plan tasks as the AI Agent writes them.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select, update

from backend.db.models.delivered_authorship import DeliveredAuthorship
from backend.db.models.foundation import User
from backend.db.models.pipeline import PipelineMessage, PipelineState
from backend.db.models.projects import Project
from backend.db.models.tasks import Epic, Feat, Task
from backend.db.models.versions import Version
from backend.services import billing_category as bc
from backend.services import delivered_tokens as dt
from backend.services import delivery_statement as st
from backend.services import orchestrator, system_setting
from backend.services.pipeline_status import TaskPlanTask
from tests.test_delivered_tokens_statement import _as, _git, _rates, _write

INVOICES = "def total(items):\n    return sum(i.price for i in items)\n"
EXPORT = "def export(rows):\n    return ';'.join(rows)\n"
SPEC = "# Špecifikácia\nFaktúra sa uloží do priečinka.\n"


@pytest.fixture
def billed(db_session, tmp_path, monkeypatch):
    """Founding; 0.1.0 = task 1.1.1 (invoices), task 1.1.2 (export), the design document without a task and a fix
    after verification without a task; fast fix 0.1.1 of our error = task 2.1.1."""
    db = db_session
    monkeypatch.setattr(orchestrator.claude_agent, "PROJECTS_ROOT", tmp_path)
    suffix = uuid.uuid4().hex[:8]
    root = tmp_path / f"kategorie-{suffix}"
    root.mkdir()
    _git(root, "init", "-q")
    _write(root, "README.md", "# Šablóna\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "chore: bootstrap", date="2026-10-01T08:00:00+00:00")

    def commit(subject, files, date):
        for path, text in files.items():
            _write(root, path, text)
        _git(root, "add", "-A")
        _git(root, "commit", "-q", "-m", subject, date=date)
        return _git(root, "rev-parse", "HEAD").strip()

    commit("docs(navrh): specification", {"docs/specs/versions/v0.1.0/specification.md": SPEC}, "2026-10-03T08:00:00Z")
    commit(
        "feat(invoices): totals (TASK #1.1.1)",
        {"backend/app/invoices.py": INVOICES, "backend/tests/test_invoices.py": "def test_total():\n    pass\n"},
        "2026-10-03T09:00:00Z",
    )
    commit("feat(export): rows (TASK #1.1.2)", {"backend/app/export.py": EXPORT}, "2026-10-03T10:00:00Z")
    delivered_010 = commit(
        "fix(0.1.0): Verifikácia round 1",
        {"backend/app/invoices.py": INVOICES + "\n\ndef vat(x):\n    return x * 0.23\n"},
        "2026-10-03T11:00:00Z",
    )
    delivered_011 = commit(
        "fix(invoices): rounding (TASK #2.1.1)",
        {"backend/app/invoices.py": INVOICES.replace("sum(", "round(sum(").replace("items)\n", "items), 2)\n", 1)},
        "2026-10-05T10:00:00Z",
    )

    owner = User(username=f"u_{suffix}", email=f"{suffix}@t.sk", password_hash="x", role="ri")
    db.add(owner)
    db.flush()
    proj = Project(
        name="Kategórie", slug=root.name, type="standard", auth_mode="password", description="", created_by=owner.id
    )
    db.add(proj)
    db.flush()

    def version(number, flow, delivered, started, tasks, work_kind=None):
        v = Version(project_id=proj.id, version_number=number, name=number, status="active", work_kind=work_kind)
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
        epic_number = (db.execute(select(Epic.number).where(Epic.project_id == proj.id)).scalars().all() or [0])[-1]
        epic = Epic(project_id=proj.id, version_id=v.id, number=epic_number + 1, title="Faktúry")
        db.add(epic)
        db.flush()
        feat = Feat(epic_id=epic.id, number=1, title="Faktúry")
        db.add(feat)
        db.flush()
        rows = [Task(feat_id=feat.id, number=i, title=t, task_type="backend") for i, t in enumerate(tasks, 1)]
        db.add_all(rows)
        db.flush()
        return v, rows

    v010, tasks_010 = version(
        "0.1.0", "new_version", delivered_010, datetime(2026, 10, 2, 9, 0, tzinfo=timezone.utc), ["Súčty", "Export"]
    )
    v011, tasks_011 = version(
        "0.1.1", "fast_fix", delivered_011, datetime(2026, 10, 4, 9, 0, tzinfo=timezone.utc), ["Zaokrúhlenie"], "fix"
    )
    _rates(db, owner, 2.0, 1.0)
    return db, root, owner, v010, tasks_010, v011, tasks_011


def _lines(p):
    return [(ln.category, ln.kind, ln.tasked, sorted(ln.tasks)) for ln in p.lines]


# ── 1. who wrote each delivered line ─────────────────────────────────────────


def test_each_delivered_line_belongs_to_the_task_whose_commit_wrote_it(billed):
    _db, root, _owner, _v010, _t, _v011, _t2 = billed
    base, head = _git(root, "rev-list", "--max-parents=0", "HEAD").strip(), _git(root, "rev-parse", "HEAD~1").strip()

    authors = dt.task_authorship(dt.line_commits(root, base, head), dt.task_of_commits(root, base, head))

    assert authors["backend/app/export.py"] == [["1.1.2", 2]]
    assert authors["backend/tests/test_invoices.py"] == [["1.1.1", 2]]
    assert authors["backend/app/invoices.py"] == [["1.1.1", 2], ["", 4]], "the fix after verification has no task"
    assert authors["docs/specs/versions/v0.1.0/specification.md"] == [["", 2]]


def test_a_line_rewritten_by_a_later_task_belongs_to_the_later_one(billed):
    _db, root, _owner, _v010, _t, _v011, _t2 = billed
    base, head = _git(root, "rev-parse", "HEAD~1").strip(), _git(root, "rev-parse", "HEAD").strip()

    authors = dt.task_authorship(dt.line_commits(root, base, head), dt.task_of_commits(root, base, head))

    assert authors == {"backend/app/invoices.py": [["2.1.1", 1]]}


# ── 2. the statement's lines ─────────────────────────────────────────────────


def test_the_statement_splits_by_category_and_work_without_a_task_has_its_own_line(billed):
    db, root, _owner, v010, tasks, _v011, _t2 = billed
    tasks[1].billing_category = bc.GIFT
    db.flush()

    p = st.preview(db, v010, root)

    assert p.version_category == bc.REQUEST
    assert _lines(p) == [
        (bc.REQUEST, bc.KIND_CODE, True, ["1.1.1"]),
        (bc.REQUEST, bc.KIND_CODE, False, []),
        (bc.REQUEST, bc.KIND_DOCS, False, []),
        (bc.GIFT, bc.KIND_CODE, True, ["1.1.2"]),
    ]
    gift = p.lines[-1]
    assert gift.tokens == dt.count_tokens(EXPORT.rstrip("\n"))
    assert (gift.value.full, gift.value.amount) == (gift.value.discount, Decimal("0.00")), "a gift is free"
    assert gift.payload()["discount_label"] == "pozornosť ICC"
    assert sum(ln.tokens for ln in p.lines) == sum(p.count.tokens(k) for k in dt.KINDS), (
        "every counted token is on exactly one line"
    )
    assert p.amounts.total == sum((ln.value.amount for ln in p.lines), Decimal("0"))
    assert p.amounts.full - p.amounts.total == gift.value.full
    assert [(s.task.label, s.category, s.tokens > 0) for s in p.tasks] == [
        ("1.1.1", bc.REQUEST, True),
        ("1.1.2", bc.GIFT, True),
    ]


def test_a_fast_fix_of_our_error_is_a_warranty_fix_at_full_value_and_zero(billed):
    db, root, _owner, _v010, _t, v011, _tasks = billed

    p = st.preview(db, v011, root)

    assert p.version_category == bc.BUG
    assert _lines(p) == [(bc.BUG, bc.KIND_CODE, True, ["2.1.1"])]
    line = p.lines[0].payload()
    assert line["discount_label"] == "záručná oprava" and Decimal(line["discount_pct"]) == 100
    assert Decimal(line["full_eur"]) > 0 and Decimal(line["amount_eur"]) == 0
    assert p.amounts.total == 0 and p.amounts.full > 0


def test_a_fast_fix_given_as_a_gift_is_a_gift_at_full_value_and_zero(billed):
    """DEV-118 — Director 14.10.2026: „GIFT - Dar“ for a whole piece of work, not only task by task; the statement
    shows what it was worth and the discount the gift has in Nastavenia."""
    db, root, owner, _v010, _t, v011, _tasks = billed
    v011.work_kind = st.WORK_GIFT
    db.flush()

    p = st.preview(db, v011, root)

    assert p.version_category == bc.GIFT
    assert _lines(p) == [(bc.GIFT, bc.KIND_CODE, True, ["2.1.1"])]
    line = p.lines[0].payload()
    assert line["discount_label"] == "pozornosť ICC" and Decimal(line["discount_pct"]) == 100
    assert Decimal(line["full_eur"]) > 0 and Decimal(line["amount_eur"]) == 0

    row = st.issue(db, v011, root, owner.id)

    assert row.work_kind == st.WORK_GIFT
    csv_rows = st.csv_text(row, v011, db.get(Project, v011.project_id)).splitlines()
    assert "Druh práce;dar — pozornosť ICC" in csv_rows


def test_a_task_that_names_no_task_of_this_plan_is_work_without_a_task(billed):
    db, root, _owner, v010, tasks, _v011, _t2 = billed

    def by_line(p):
        return {(ln.kind, ln.tasked): (ln.tokens, sorted(ln.tasks)) for ln in p.lines}

    before = by_line(st.preview(db, v010, root))
    db.delete(tasks[1])  # the commit still says (TASK #1.1.2), the plan no longer has it
    db.flush()
    after = by_line(st.preview(db, v010, root))

    export = dt.count_tokens(EXPORT.rstrip("\n"))
    assert before[(bc.KIND_CODE, True)][1] == ["1.1.1", "1.1.2"]
    assert after[(bc.KIND_CODE, True)] == (before[(bc.KIND_CODE, True)][0] - export, ["1.1.1"])
    assert after[(bc.KIND_CODE, False)][0] == before[(bc.KIND_CODE, False)][0] + export


# ── 3. discounts ──────────────────────────────────────────────────────────────


def test_nastavenia_give_a_warranty_fix_and_a_gift_100_percent_and_a_request_none(db_session):
    system_setting.invalidate_cache()

    assert bc.settings_discounts(db_session) == {bc.REQUEST: 0, bc.BUG: 100, bc.GIFT: 100}
    with pytest.raises(ValueError, match="od 0 do 100"):
        system_setting.upsert(db_session, "billing_discount_gift", "101")
    with pytest.raises(ValueError, match="od 0 do 100"):
        system_setting.upsert(db_session, "billing_discount_bug", "-1")


def test_a_version_counts_with_its_own_discount_and_every_change_is_kept(billed):
    db, root, owner, v010, tasks, _v011, _t2 = billed
    tasks[1].billing_category = bc.GIFT
    db.flush()

    first = bc.set_discounts(db, v010, {bc.GIFT: Decimal("50")}, owner.id)
    again = bc.set_discounts(db, v010, {bc.GIFT: Decimal("50")}, owner.id)
    bc.set_discounts(db, v010, {}, owner.id)  # back to Nastavenia

    assert first is not None and again is None, "the same discount again is no change"
    changes = bc.discount_changes(db, v010.id)
    assert [(c.previous_discount_gift, c.discount_gift) for c in changes] == [(Decimal("50"), None), (None, 50)]
    bc.set_discounts(db, v010, {bc.GIFT: Decimal("50")}, owner.id)
    gift = st.preview(db, v010, root).lines[-1]
    assert gift.discount_pct == 50 and gift.value.amount == gift.value.full - gift.value.discount > 0
    with pytest.raises(ValueError, match="od 0 do 100"):
        bc.set_discounts(db, v010, {bc.BUG: Decimal("100.01")}, owner.id)


# ── 4. an issued statement keeps its lines ────────────────────────────────────


def test_an_issued_statement_keeps_its_lines_whatever_changes_later(billed):
    db, root, owner, v010, tasks, _v011, _t2 = billed
    row = st.issue(db, v010, root, owner.id)
    issued = [dict(ln) for ln in row.lines]

    tasks[0].billing_category = bc.GIFT
    bc.set_discounts(db, v010, {bc.REQUEST: Decimal("10")}, owner.id)
    db.flush()
    db.refresh(row)

    assert row.lines == issued
    assert [ln["category"] for ln in issued] == [bc.REQUEST] * 3
    assert Decimal(row.amount_eur) == sum(Decimal(ln["amount_eur"]) for ln in issued)
    assert st.preview(db, v010, root).amounts.total != Decimal(row.amount_eur), "the preview counts anew"

    text = st.csv_text(row, v010, db.get(Project, v010.project_id)).splitlines()
    assert text[9].startswith("Riadok súpisu;Riadkov;Tokenov;Sadzba")
    assert text[10].startswith("Požiadavka zákazníka — kód a skúšky;")
    assert any(line.startswith("1.1.1;Súčty;Požiadavka zákazníka — kód a skúšky;") for line in text)


# ── 5. who wrote each line is kept, not counted again ─────────────────────────


def test_who_wrote_each_line_is_counted_once_per_delivery(billed, monkeypatch):
    db, root, _owner, v010, _t, _v011, _t2 = billed
    first = st.preview(db, v010, root)

    def no_blame(*_a, **_k):
        raise AssertionError("blame ran again for a delivery it already knows")

    monkeypatch.setattr(dt, "line_commits", no_blame)
    again = st.preview(db, v010, root)

    assert _lines(again) == _lines(first)
    kept = db.execute(
        select(DeliveredAuthorship).where(DeliveredAuthorship.head_sha == first.delivered_sha)
    ).scalar_one()
    assert kept.payload["rule"] == st.AUTHORSHIP_RULE


def test_a_kept_authorship_of_another_rule_is_counted_again(billed, monkeypatch):
    db, root, _owner, v010, _t, _v011, _t2 = billed
    p = st.preview(db, v010, root)
    kept = db.execute(select(DeliveredAuthorship).where(DeliveredAuthorship.head_sha == p.delivered_sha)).scalar_one()
    kept.payload = {"rule": st.AUTHORSHIP_RULE - 1, "files": {}}
    db.flush()
    calls = []
    real = dt.line_commits
    monkeypatch.setattr(dt, "line_commits", lambda *a: calls.append(a) or real(*a))

    assert _lines(st.preview(db, v010, root)) == _lines(p)
    assert len(calls) == 1
    db.refresh(kept)
    assert kept.payload["rule"] == st.AUTHORSHIP_RULE


# ── 6. the HTTP surface ───────────────────────────────────────────────────────


def test_reading_the_statement_keeps_who_wrote_each_line(client, billed, monkeypatch):
    """The reading routes commit — otherwise the authorship counted on reading is rolled back and blamed again."""
    db, _root, owner, v010, _tasks, _v011, _t2 = billed
    _as(db, owner)
    commits = []
    real = db.commit
    monkeypatch.setattr(db, "commit", lambda: commits.append(1) or real())

    assert client.get(f"/api/v1/versions/{v010.id}/delivery-statement").status_code == 200
    assert commits, "the statement's reading did not commit"
    commits.clear()
    slug = db.get(Project, v010.project_id).slug
    assert client.get(f"/api/v1/projects/{slug}/billing").status_code == 200
    assert commits, "the billing tab's reading did not commit"


def test_the_screen_sets_categories_for_many_tasks_and_the_discounts(client, billed):
    db, _root, owner, v010, tasks, v011, tasks_011 = billed
    _as(db, owner)
    ids = [str(t.id) for t in tasks]

    response = client.put(f"/api/v1/versions/{v010.id}/task-categories", json={"task_ids": ids, "category": "gift"})
    assert response.status_code == 200 and response.json() == {"changed": 2}

    view = client.get(f"/api/v1/versions/{v010.id}/delivery-statement").json()
    preview = view["preview"]
    assert [(t["label"], t["epic_title"], t["own_category"], t["category"]) for t in preview["tasks"]] == [
        ("1.1.1", "Faktúry", "gift", "gift"),
        ("1.1.2", "Faktúry", "gift", "gift"),
    ]
    assert {ln["category"] for ln in preview["lines"] if ln["tasked"]} == {"gift"}
    assert {k: Decimal(v) for k, v in preview["discounts"].items()} == {"request": 0, "bug": 100, "gift": 100}
    assert db.execute(select(DeliveredAuthorship)).scalars().first() is not None, "the reading kept the authorship"

    mixed = [ids[0], str(tasks_011[0].id)]
    refused = client.put(f"/api/v1/versions/{v010.id}/task-categories", json={"task_ids": mixed, "category": "bug"})
    assert refused.status_code == 422 and "nepatrí tejto verzii" in refused.json()["detail"]
    db.refresh(tasks[0])
    assert tasks[0].billing_category == "gift", "a refused change changed nothing"

    back = client.put(f"/api/v1/versions/{v010.id}/task-categories", json={"task_ids": ids[:1], "category": None})
    assert back.json() == {"changed": 1}

    assert client.put(f"/api/v1/versions/{v010.id}/billing-discounts", json={"gift": "101"}).status_code == 422
    changes = client.put(f"/api/v1/versions/{v010.id}/billing-discounts", json={"gift": "30"}).json()
    assert [(c["previous"]["gift"], Decimal(c["current"]["gift"])) for c in changes] == [(None, Decimal("30"))]
    assert client.get(f"/api/v1/versions/{v010.id}/delivery-statement").json()["discount_changes"][0]["changed_by"]

    plan = client.get(f"/api/v1/versions/{v011.id}/task-plan").json()
    assert plan["billing_default"] == "bug"
    assert plan["plan"][0]["feats"][0]["tasks"][0]["billing_category"] is None


def test_the_issued_statement_reads_back_with_its_lines(client, billed):
    db, _root, owner, v010, _tasks, _v011, _t2 = billed
    _as(db, owner)

    issued = client.post(f"/api/v1/versions/{v010.id}/delivery-statement").json()

    assert [ln["label"] for ln in issued["lines"]] == [
        "Požiadavka zákazníka — kód a skúšky",
        "Práca bez úlohy — kód a skúšky (Požiadavka zákazníka)",
        "Práca bez úlohy — dokumentácia (Požiadavka zákazníka)",
    ]
    assert [t["label"] for t in issued["lines"][0]["tasks"]] == ["1.1.1", "1.1.2"], "in the plan's order"


# ── 7. the agent proposes the category ────────────────────────────────────────


def test_the_agent_proposes_a_category_and_an_unknown_one_leaves_it_to_the_manager():
    def task(category):
        return TaskPlanTask(title="Export", task_type="backend", billing_category=category)

    assert task("gift").billing_category == "gift"
    assert task(" Bug ").billing_category == "bug"
    assert task("feature").billing_category is None, "an unknown proposal must not fail the whole plan"
    assert task(None).billing_category is None


def test_the_plan_pass_asks_for_the_category_and_every_commit_names_its_task():
    directive = orchestrator._task_plan_feat_directive("Export")
    assert "`billing_category`" in directive and '"billing_category":"request"' in directive

    brief = orchestrator._directive_for_build_task(
        Task(number=3, title="Export", description="", task_type="backend"), None, [], task_label="1.2.3"
    )
    assert "končí `(TASK #1.2.3)`" in brief
    assert dt.TASK_TAG_RE.search("feat(export): rows (TASK #1.2.3)").group(1) == "1.2.3"


def test_the_plan_write_keeps_the_proposed_category(db_session, billed):
    from backend.schemas.task import TaskCreate
    from backend.services import task as task_service

    db, _root, _owner, v010, tasks, _v011, _t2 = billed
    created = task_service.create(
        db, TaskCreate(feat_id=tasks[0].feat_id, title="Dar", task_type="backend", billing_category="gift")
    )

    assert created.billing_category == "gift"
    assert [s.category for s in bc.plan_tasks(db, v010.id)] == [None, None, "gift"]
