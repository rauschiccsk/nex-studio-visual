"""DEV-484 — the daily pruning of the CI runners' Docker (DEV-435) runs although the cockpit is deployed several times a
day: it remembers when it last ran and waits only for the rest of the day.

21.11.2026: in 72 hours the backend's log had no line „CI runner cache retention“ — the loop slept a whole day after
every start of the backend and the cockpit was deployed four times that day. Director: „Súhlasím“.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from backend.services import maintenance

DAY = 24 * 3600
GRACE = 300
NOW = datetime(2026, 11, 21, 18, 0, tzinfo=timezone.utc)
JOB = "test-job-dev-484"


def test_a_job_that_never_ran_runs_after_the_grace(db_session):
    assert maintenance.seconds_until_due(db_session, JOB, DAY, GRACE, now=NOW) == GRACE


def test_a_job_that_ran_two_hours_ago_waits_the_rest_of_the_day(db_session):
    maintenance.mark_run(db_session, JOB, at=NOW - timedelta(hours=2))

    assert maintenance.seconds_until_due(db_session, JOB, DAY, GRACE, now=NOW) == 22 * 3600


def test_a_job_overdue_runs_after_the_grace_not_at_once(db_session):
    maintenance.mark_run(db_session, JOB, at=NOW - timedelta(hours=30))

    assert maintenance.seconds_until_due(db_session, JOB, DAY, GRACE, now=NOW) == GRACE


def test_a_run_is_remembered_and_moves_the_next_one(db_session):
    maintenance.mark_run(db_session, JOB, at=NOW - timedelta(hours=30))
    maintenance.mark_run(db_session, JOB, at=NOW)

    assert maintenance.last_run(db_session, JOB) == NOW
    assert maintenance.seconds_until_due(db_session, JOB, DAY, GRACE, now=NOW + timedelta(hours=1)) == 23 * 3600


def test_the_loop_waits_for_what_is_left_and_marks_the_run(db_session, monkeypatch):
    """The loop's two steps: how long it waits comes from the database, and the pruning is marked as run."""
    import backend.main as main
    from backend.services import orchestrator

    class _Session:
        def __enter__(self):
            return db_session

        def __exit__(self, *exc):
            return False

    pruned: list[bool] = []
    monkeypatch.setattr(main, "SessionLocal", _Session)
    monkeypatch.setattr(orchestrator, "prune_ci_runner_caches", lambda: pruned.append(True))
    maintenance.mark_run(db_session, maintenance.CI_RUNNER_PRUNE, at=datetime.now(timezone.utc) - timedelta(hours=2))

    assert 21.9 * 3600 < main._ci_runner_prune_wait() <= 22 * 3600

    main._ci_runner_prune_and_mark()

    assert pruned == [True]
    assert main._ci_runner_prune_wait() > 23.9 * 3600
