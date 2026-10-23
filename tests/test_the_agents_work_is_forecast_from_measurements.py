"""DEV-210 — how long the agent will work is forecast by the cockpit from what it measured; the human estimate is gone.

23.10.2026 the plan of NEX Manager 1.4.0 said „odhad ~3260 min (~54.3 h)“ — the sum of ``estimated_minutes`` the
agent was told to write as „a realistic estimate for a capable HUMAN developer“. Its tasks took the agent about an
hour. Director: „Ja potrebujem odhad, že agent približne koľko to bude robiť“, „Ludské hodnoty treba zrušiť, lebo
táto požiadavka vznikla, keď sme plánovali účtovať zákazky v hodinách“, „Áno, súhlasím, založ tiket a implementuj“.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from backend.db.models.tasks import Epic, Feat, Task
from backend.db.models.versions import Version
from backend.services import build_forecast, orchestrator, pipeline_status
from backend.services.build_forecast import BuildForecast, MeasuredBuild
from tests.test_build_robustness_crash import _make_version
from tests.test_dedo_proposal_before_the_work_starts import _bearer

# --- the human estimate is gone ----------------------------------------------------------------------------------


def test_the_agent_is_not_asked_for_human_minutes():
    for prompt in (orchestrator._task_plan_skeleton_directive(), orchestrator._task_plan_feat_directive("Schéma")):
        assert "estimated_minutes" not in prompt
        assert "ĽUDSKÉHO" not in prompt


def test_the_plan_keeps_no_human_minutes():
    for model in (
        pipeline_status.TaskPlanTask,
        pipeline_status.TaskPlanFeat,
        pipeline_status.TaskPlanSkeletonFeat,
    ):
        assert "estimated_minutes" not in model.model_fields
    assert not hasattr(Task, "estimated_minutes") and not hasattr(Feat, "estimated_minutes")


def test_a_plan_that_still_sends_minutes_is_not_refused():
    """An agent session that began before the change may still write the field — it is ignored, never a failure."""
    task = pipeline_status.TaskPlanTask.model_validate({"title": "t", "task_type": "backend", "estimated_minutes": 90})
    assert task.title == "t"


# --- the rule ----------------------------------------------------------------------------------------------------

T0 = datetime(2026, 10, 1, tzinfo=timezone.utc)


def _build(minutes, *, project=None, day=0, finished=True) -> MeasuredBuild:
    return MeasuredBuild(uuid.uuid4(), project or uuid.uuid4(), T0 + timedelta(days=day), tuple(minutes), finished)


def test_before_the_first_task_the_pace_is_the_projects_own_last_builds():
    project = uuid.uuid4()
    earlier = [
        _build([30] * 10, project=project, day=0),
        _build([4] * 5, project=project, day=1),
        _build([6] * 5, project=project, day=2),
        _build([50] * 30, day=3),
    ]

    assert build_forecast.history_pace(project, earlier) == 5
    assert build_forecast.estimate([], 10, 5) == 50


def test_a_new_project_goes_by_the_latest_builds_of_any_project():
    earlier = [_build([2] * 10, day=0), _build([4] * 10, day=1), _build([8] * 10, day=2)]

    assert build_forecast.history_pace(uuid.uuid4(), earlier) == 4
    assert build_forecast.history_pace(uuid.uuid4(), earlier[:1]) is None, "ten tasks are not a history"


def test_the_builds_own_pace_takes_over_as_its_tasks_finish():
    # (3 × 2 + 6 × 5) / (3 + 6) = 4 minutes for each of the 7 tasks left
    assert build_forecast.estimate([2, 2, 2], 7, 5) == pytest.approx(6 + 7 * 4)


def test_without_history_three_measured_tasks_are_enough():
    assert build_forecast.estimate([3, 5], 8, None) is None
    assert build_forecast.estimate([3, 5, 4], 7, None) == pytest.approx(12 + 7 * 4)


def test_the_spread_is_how_far_the_same_forecast_was_off_on_finished_builds():
    project = uuid.uuid4()
    steady = [_build([4] * 10, project=project, day=d) for d in range(7)]
    # a project whose builds alternate 8 and 4 minutes a task: replayed with its last two builds' pace (6), each is
    # off by half or a quarter — and the first, going by the 8 alone, by all of it
    alternating = [_build([8 if d % 2 == 0 else 4] * 10, project=project, day=d) for d in range(8)]

    assert build_forecast.typical_error(steady, 0.0) == 0.0
    assert build_forecast.typical_error(steady[:5], 0.0) is None, "four replayed builds are not a measurement"
    assert build_forecast.typical_error(alternating, 0.0) == 0.5


@pytest.mark.parametrize(
    ("forecast", "text", "remaining"),
    [
        (BuildForecast(minutes=72, spent=0, error=0.32, tasks_done=0, tasks_total=20), "asi 1 h 10 min ± 30 %", None),
        (
            BuildForecast(minutes=38, spent=12, error=0.11, tasks_done=10, tasks_total=20),
            "asi 40 min ± 10 %",
            "zostáva asi 25 min",
        ),
        (BuildForecast(minutes=7, spent=0, error=None, tasks_done=0, tasks_total=3), "asi 7 min", None),
        (BuildForecast(minutes=64, spent=64, error=None, tasks_done=20, tasks_total=20), "1 h (namerané)", None),
    ],
)
def test_the_forecast_reads_like_the_director_asked(forecast, text, remaining):
    assert (forecast.text, forecast.remaining_text) == (text, remaining)


# --- measured in the cockpit's own records -----------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_cached_history(monkeypatch):
    monkeypatch.setattr(build_forecast.settings, "build_forecast_history_ttl_seconds", 0)


def _version_with_tasks(db, project, *, day: int, number: str, minutes: list, todo: int = 0) -> Version:
    """A version whose plan has a task per entry of ``minutes`` (done, with the agent's measured turn) plus ``todo``."""
    version = Version(project_id=project.id, version_number=number, created_at=T0 + timedelta(days=day))
    db.add(version)
    db.flush()
    epic = Epic(project_id=project.id, version_id=version.id, number=day + 1, title="E", status="in_progress")
    db.add(epic)
    db.flush()
    feat = Feat(epic_id=epic.id, number=1, title="F", status="in_progress")
    db.add(feat)
    db.flush()
    for i, worked in enumerate([*minutes, *([None] * todo)], start=1):
        task = Task(
            feat_id=feat.id, number=i, title=f"T{i}", task_type="backend", status="todo" if worked is None else "done"
        )
        db.add(task)
        db.flush()
        if worked is not None:
            orchestrator._record_message(
                db,
                version_id=version.id,
                stage="programovanie",
                author="system",
                recipient="manazer",
                kind="notification",
                content="hotovo",
                payload={
                    "task_id": str(task.id),
                    "phase": "programovanie",
                    "timing": {"duration_seconds": worked * 60},
                },
            )
    db.flush()
    return version


def test_a_build_is_forecast_from_its_projects_measured_builds(db_session):
    first, project = _make_version(db_session)
    _version_with_tasks(db_session, project, day=0, number="0.1.0", minutes=[4, 4, 4])
    _version_with_tasks(db_session, project, day=1, number="0.2.0", minutes=[4, 4, 4])
    current = _version_with_tasks(db_session, project, day=2, number="0.3.0", minutes=[], todo=10)

    forecast = build_forecast.forecast(db_session, current.id)

    assert (forecast.minutes, forecast.tasks_done, forecast.text) == (40, 0, "asi 40 min")


def test_the_forecast_follows_the_build_as_its_tasks_finish(db_session):
    _first, project = _make_version(db_session)
    _version_with_tasks(db_session, project, day=0, number="0.1.0", minutes=[4, 4, 4])
    current = _version_with_tasks(db_session, project, day=2, number="0.3.0", minutes=[2] * 5, todo=5)

    forecast = build_forecast.forecast(db_session, current.id)

    # (5 × 2 + 6 × 4) / 11 per task left
    assert forecast.minutes == pytest.approx(10 + 5 * 34 / 11)
    assert (forecast.spent, forecast.remaining_text) == (10, "zostáva asi 15 min")


def test_only_builds_before_it_set_its_pace(db_session):
    """A later version of the project, built meanwhile at another pace, does not move this one's forecast."""
    _first, project = _make_version(db_session)
    _version_with_tasks(db_session, project, day=0, number="0.1.0", minutes=[4, 4, 4])
    current = _version_with_tasks(db_session, project, day=2, number="0.3.0", minutes=[], todo=10)
    _version_with_tasks(db_session, project, day=3, number="0.4.0", minutes=[20, 20, 20])

    assert build_forecast.forecast(db_session, current.id).minutes == 40


def test_a_task_still_at_work_is_not_counted_as_done(db_session):
    _first, project = _make_version(db_session)
    _version_with_tasks(db_session, project, day=0, number="0.1.0", minutes=[4, 4, 4])
    current = _version_with_tasks(db_session, project, day=2, number="0.3.0", minutes=[2] * 5, todo=5)
    running = (
        db_session.query(Task)
        .join(Feat, Feat.id == Task.feat_id)
        .join(Epic, Epic.id == Feat.epic_id)
        .filter(Epic.version_id == current.id, Task.status == "todo")
        .order_by(Task.number)
        .first()
    )
    running.status = "in_progress"
    orchestrator._record_message(
        db_session,
        version_id=current.id,
        stage="programovanie",
        author="system",
        recipient="manazer",
        kind="notification",
        content="pracuje",
        payload={"task_id": str(running.id), "phase": "programovanie", "timing": {"duration_seconds": 30 * 60}},
    )
    db_session.flush()

    forecast = build_forecast.forecast(db_session, current.id)

    assert (forecast.spent, forecast.tasks_done) == (10, 5)


def test_nothing_measured_is_no_forecast_rather_than_a_guess(db_session):
    _first, project = _make_version(db_session)
    current = _version_with_tasks(db_session, project, day=0, number="0.9.0", minutes=[], todo=4)

    assert build_forecast.forecast(db_session, current.id) is None


def test_the_task_plan_screen_carries_the_forecast(client, db_session):
    from backend.db.models.foundation import User

    _first, project = _make_version(db_session)
    _version_with_tasks(db_session, project, day=0, number="0.1.0", minutes=[4, 4, 4])
    current = _version_with_tasks(db_session, project, day=2, number="0.3.0", minutes=[2] * 5, todo=5)
    db_session.commit()
    owner = db_session.get(User, project.created_by)

    answer = client.get(f"/api/v1/versions/{current.id}/task-plan", headers=_bearer(owner))

    assert answer.status_code == 200, answer.text
    assert answer.json()["forecast"] == {"text": "asi 25 min", "remaining_text": "zostáva asi 15 min"}


def test_the_plan_document_says_the_forecast_and_no_human_minutes(db_session):
    _first, project = _make_version(db_session)
    _version_with_tasks(db_session, project, day=0, number="0.1.0", minutes=[4, 4, 4])
    current = _version_with_tasks(db_session, project, day=2, number="0.3.0", minutes=[], todo=10)

    text = orchestrator._render_task_plan_md(db_session, current, project, "navrh")

    assert "**Súhrn:** 1 epicov · 1 featov · 10 úloh · práca agenta na úlohách asi 40 min" in text, text
    assert " min" not in text.replace("asi 40 min", ""), "a task or feat still carries minutes of its own"
