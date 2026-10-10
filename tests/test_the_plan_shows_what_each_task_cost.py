"""DEV-49 — the plan shows, at every EPIC, FEAT and TASK, how long the agent worked, what it cost and its tokens.

Director 10.10.2026: „Pri komunikácii s Poradcom … na konci každej správy je napísané ‚Trvalo 26 s 0,68 €'. Chcel by
som niečo podobné pre každý EPIC, FEAT a TASK napríklad takto: ‚Trvalo: 26 s cena: 0,68 € 1200 tokenov'.“

The figures come from the same reading of a turn as Náklady (``pipeline_metrics._fold``) and the same price lists;
the prices below are chosen so that rounding each turn up would give a different total than rounding the node once.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

import pytest

from backend.api.routes.versions import get_task_plan
from backend.db.models.foundation import User
from backend.db.models.model_price import ModelPrice
from backend.db.models.projects import Project
from backend.db.models.tasks import Epic, Feat, Task
from backend.db.models.versions import Version
from backend.services import metrics, orchestrator

MODEL = "claude-opus-5-5"


def _price_list(db) -> None:
    """USD per million tokens: input 5, output 25, cache read 0.5, cache write 10; 1 € = 1.25 $."""
    db.add(
        ModelPrice(
            model=MODEL,
            valid_from=datetime(2020, 1, 1, tzinfo=timezone.utc),
            input_usd=5.0,
            output_usd=25.0,
            cache_read_usd=0.5,
            cache_write_usd=10.0,
            eur_usd=1.25,
            rate_date=date(2026, 10, 10),
            rate_source="test",
            observations=10,
            max_deviation=0.0,
            checked_until=datetime(2099, 1, 1, tzinfo=timezone.utc),
        )
    )
    db.flush()


def _turn(db, version, *, task=None, seconds, parts=None, usage="parts", stage="programovanie", phase=None):
    payload: dict = {"timing": {"duration_seconds": seconds, "parse_attempts": 1}}
    if task is not None:
        payload["task_id"] = str(task.id)
    if phase:
        payload["phase"] = phase
    if usage == "parts":
        payload["usage"] = {
            "input_tokens": sum(p["input_tokens"] for p in parts),
            "output_tokens": sum(p["output_tokens"] for p in parts),
            "model": MODEL,
            "parts": [{"model": MODEL, **p} for p in parts],
        }
    elif usage == "old":  # recorded before ICCINT-168: input/output only, no cache — cannot be priced
        payload["usage"] = {"input_tokens": 10, "output_tokens": 20, "model": MODEL}
    else:  # the turn ran, but what it spent was never captured
        payload["usage"] = None
    orchestrator._record_message(
        db,
        version_id=version.id,
        stage=stage,
        author="ai_agent",
        recipient="manazer",
        kind="gate_report",
        content="ťah",
        payload=payload,
    )


def _part(input_tokens=0, output_tokens=0, cache_read_tokens=0, cache_write_tokens=0):
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_tokens": cache_read_tokens,
        "cache_write_tokens": cache_write_tokens,
    }


@pytest.fixture
def plan(db_session):
    db = db_session
    _price_list(db)
    suffix = uuid.uuid4().hex[:8]
    owner = User(username=f"u_{suffix}", email=f"{suffix}@t.sk", password_hash="x", role="ri")
    db.add(owner)
    db.flush()
    project = Project(
        name="Career", slug=f"plan-{suffix}", type="standard", auth_mode="password", description="", created_by=owner.id
    )
    db.add(project)
    db.flush()
    version = Version(project_id=project.id, version_number="0.1.0", name="0.1.0", status="active")
    db.add(version)
    db.flush()
    epic = Epic(project_id=project.id, version_id=version.id, number=1, title="Ponuky", status="in_progress")
    db.add(epic)
    db.flush()
    f1 = Feat(epic_id=epic.id, number=1, title="Sťahovanie", status="in_progress")
    f2 = Feat(epic_id=epic.id, number=2, title="Triedenie", status="done")
    db.add_all([f1, f2])
    db.flush()
    t1 = Task(feat_id=f1.id, number=1, title="Detail ponuky", task_type="backend", status="done")
    t2 = Task(feat_id=f1.id, number=2, title="Bez záznamu", task_type="backend", status="done")
    t3 = Task(feat_id=f1.id, number=3, title="Nezačatá", task_type="backend", status="todo")
    t4 = Task(feat_id=f2.id, number=1, title="Triedenie", task_type="backend", status="done")
    db.add_all([t1, t2, t3, t4])
    db.flush()

    # T1: 1.6012 € + 0.8712 € = 2.4724 € → 2.48 € rounded UP once at the node. Rounding each turn up would make
    # 2.49 €, rounding to the nearest cent 2.47 € — three different answers, so the test tells them apart.
    _turn(db, version, task=t1, seconds=114.0, parts=[_part(1000, 20000, 993_000, 100_000)])
    _turn(db, version, task=t1, seconds=96.5, parts=[_part(0, 4000, 1_978_000, 0)])
    # T2: the turn ran 30 s, its spend was never captured.
    _turn(db, version, task=t2, seconds=30.0, usage=None)
    # T4: 0.40 € exactly (output 20 000 × 25 $ / 1M = 0.50 $ = 0.40 €).
    _turn(db, version, task=t4, seconds=60.0, parts=[_part(0, 20000, 0, 0)])
    # A plan pass of Návrh — no task; it belongs to the phase in Náklady, not to the plan.
    _turn(db, version, seconds=500.0, parts=[_part(0, 400_000, 0, 0)], stage="programovanie", phase="navrh")
    db.flush()
    return db, version, owner, {"t1": t1, "t2": t2, "t3": t3, "t4": t4}


def _nodes(response):
    epic = response.plan[0]
    f1, f2 = epic["feats"]
    tasks = {t["title"]: t for t in f1["tasks"] + f2["tasks"]}
    return epic, f1, f2, tasks


def test_a_task_shows_its_working_time_its_price_rounded_once_and_all_its_tokens(plan):
    db, version, owner, _tasks = plan

    _epic, _f1, _f2, tasks = _nodes(get_task_plan(version.id, db, owner))

    assert tasks["Detail ponuky"]["spend"] == {
        "seconds": 210.5,
        "turns": 2,
        "tokens": {
            "input": 1000,
            "output": 24000,
            "cache_read": 2_971_000,
            "cache_write": 100_000,
            "total": 3_096_000,
        },
        "eur": 2.48,
        "eur_complete": True,
        "unpriced": [],
    }


def test_spend_that_was_never_captured_makes_the_price_incomplete_and_says_why(plan):
    db, version, owner, _tasks = plan

    _epic, _f1, _f2, tasks = _nodes(get_task_plan(version.id, db, owner))

    spend = tasks["Bez záznamu"]["spend"]
    assert (spend["seconds"], spend["eur"], spend["eur_complete"]) == (30.0, None, False)
    assert spend["unpriced"] == [metrics.UNKNOWN_SPEND_REASON]


def test_an_untouched_task_shows_nothing(plan):
    db, version, owner, _tasks = plan

    _epic, _f1, _f2, tasks = _nodes(get_task_plan(version.id, db, owner))

    assert tasks["Nezačatá"]["spend"] is None


def test_a_feat_and_an_epic_add_up_their_children_and_stay_honest_about_gaps(plan):
    db, version, owner, _tasks = plan

    epic, f1, f2, _tasks = _nodes(get_task_plan(version.id, db, owner))

    assert (f1["spend"]["seconds"], f1["spend"]["eur"], f1["spend"]["eur_complete"]) == (240.5, 2.48, False)
    assert (f2["spend"]["seconds"], f2["spend"]["eur"], f2["spend"]["eur_complete"]) == (60.0, 0.4, True)
    # 2.4724 + 0.40 = 2.8724 € → 2.88 €, still marked incomplete because of the uncaptured turn below it.
    assert (epic["spend"]["seconds"], epic["spend"]["eur"], epic["spend"]["eur_complete"]) == (300.5, 2.88, False)
    assert epic["spend"]["tokens"]["total"] == 3_096_000 + 20000
    assert epic["spend"]["turns"] == 4, "plán z Návrhu (ťah bez úlohy) sa do úloh nepočíta"


def test_the_plan_and_naklady_read_a_turn_the_same_way(plan):
    """Every turn of the plan is in Náklady too: the epic's tokens equal the version total minus the plan pass."""
    from backend.services import pipeline_metrics

    db, version, owner, _tasks = plan

    epic, _f1, _f2, _tasks = _nodes(get_task_plan(version.id, db, owner))
    whole = pipeline_metrics.aggregate_pipeline_usage(db, version.id).version

    assert whole.output_tokens - 400_000 == epic["spend"]["tokens"]["output"]
    assert whole.duration_seconds - 500.0 == epic["spend"]["seconds"]
