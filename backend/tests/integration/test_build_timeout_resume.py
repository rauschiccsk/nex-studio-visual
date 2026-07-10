"""Integration tests for the build-TIMEOUT resume fix (cockpit-timeout-and-activity-fix.md, Bug 1).

A Programovanie build agent that TIMES OUT mid-task settles the round ``awaiting_manazer`` ("review &
continue") with tasks STILL remaining. The state-only ``determine_available_actions`` ALWAYS offers
``schvalit`` at a settled ``programovanie`` — but ``schvalit`` ADVANCES programovanie → verifikacia, which
FINISHES a half-built version (a footgun). These pin the board finalizer that gates ``schvalit`` vs
``pokracovat`` on the DB-derived tasks-remaining signal:

  * **Tasks remain** — the board offers ``pokracovat`` (resume the build loop) and NOT ``schvalit``.
  * **All tasks done** — the board offers ``schvalit`` (advance to Verifikácia) and NOT ``pokracovat``.
  * **Resume** — ``apply_action("pokracovat")`` RESUMES the per-task build loop from an
    ``awaiting_manazer``-with-todo state (not only from ``paused``).

Runs against the real v2 DB (test DB :9178, SAVEPOINT-isolated via the ``db_session`` fixture).
"""

from __future__ import annotations

import uuid as _uuid

import pytest

from backend.api.routes.pipeline import _board
from backend.db.models.foundation import User
from backend.db.models.pipeline import PipelineState
from backend.db.models.projects import Project
from backend.db.models.tasks import Epic, Feat, Task
from backend.db.models.versions import Version
from backend.services import orchestrator

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _seed_user(db) -> User:
    u = User(
        username=f"bt_{_uuid.uuid4().hex[:8]}",
        email=f"bt_{_uuid.uuid4().hex[:8]}@test.local",
        password_hash="x",
        role="ri",
    )
    db.add(u)
    db.flush()
    return u


def _seed_project(db, *, creator: User) -> Project:
    suffix = _uuid.uuid4().hex[:8]
    project = Project(
        name=f"Build Timeout Proj {suffix}",
        slug=f"build-timeout-{suffix}",
        type="standard",
        auth_mode="password",
        description="Bug 1 build-timeout resume test project.",
        created_by=creator.id,
    )
    db.add(project)
    db.flush()
    return project


def _seed_version(db, project: Project, version_number: str = "1.1.0") -> Version:
    version = Version(project_id=project.id, version_number=version_number, status="active")
    db.add(version)
    db.flush()
    return version


def _seed_programovanie_state(
    db, version: Version, *, mode: str | None = None, status: str = "awaiting_manazer"
) -> PipelineState:
    """A settled Programovanie build (the state after a timeout settled the round awaiting_manazer)."""
    state = PipelineState(
        version_id=version.id,
        flow_type="new_version",
        current_stage="programovanie",
        current_actor="ai_agent",
        status=status,
        mode=mode,
    )
    db.add(state)
    db.flush()
    return state


def _seed_task(db, version: Version, *, number: int, status: str) -> Task:
    """Seed one Epic→Feat→Task chain with the Task at the given lifecycle status."""
    epic = Epic(project_id=version.project_id, version_id=version.id, number=number, title=f"Epic {number}")
    db.add(epic)
    db.flush()
    feat = Feat(epic_id=epic.id, number=number, title=f"Feat {number}")
    db.add(feat)
    db.flush()
    task = Task(
        feat_id=feat.id,
        number=number,
        title=f"Task {number}",
        task_type="backend",
        status=status,
    )
    db.add(task)
    db.flush()
    return task


# ---------------------------------------------------------------------------
# (i) Tasks remain → pokracovat, NOT schvalit
# ---------------------------------------------------------------------------


def test_board_offers_pokracovat_not_schvalit_when_tasks_remain(db_session) -> None:
    creator = _seed_user(db_session)
    project = _seed_project(db_session, creator=creator)
    version = _seed_version(db_session, project)
    _seed_programovanie_state(db_session, version)
    # One task DONE + one still TODO → the build is half-built (a timeout mid-plan).
    _seed_task(db_session, version, number=1, status="done")
    _seed_task(db_session, version, number=2, status="todo")

    board = _board(db_session, version.id)

    assert board.all_tasks_done is False
    assert "pokracovat" in board.available_actions  # clean "Pokračovať v stavbe"
    assert "schvalit" not in board.available_actions  # the FINISH footgun is gone
    # uprav / ask stay in both cases.
    assert "uprav" in board.available_actions
    assert "ask" in board.available_actions


# ---------------------------------------------------------------------------
# (ii) All tasks done → schvalit, NOT pokracovat (today's advance behaviour)
# ---------------------------------------------------------------------------


def test_board_offers_schvalit_not_pokracovat_when_all_tasks_done(db_session) -> None:
    creator = _seed_user(db_session)
    project = _seed_project(db_session, creator=creator)
    version = _seed_version(db_session, project)
    _seed_programovanie_state(db_session, version)
    _seed_task(db_session, version, number=1, status="done")
    _seed_task(db_session, version, number=2, status="done")

    board = _board(db_session, version.id)

    assert board.all_tasks_done is True
    assert "schvalit" in board.available_actions  # advance to Verifikácia, as today
    assert "pokracovat" not in board.available_actions  # nothing left to resume
    assert "uprav" in board.available_actions


# ---------------------------------------------------------------------------
# (iii) apply_action("pokracovat") RESUMES from awaiting_manazer-with-todo (not only from paused)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pokracovat_resumes_build_from_awaiting_manazer_with_todo(db_session, monkeypatch) -> None:
    creator = _seed_user(db_session)
    project = _seed_project(db_session, creator=creator)
    version = _seed_version(db_session, project)
    _seed_programovanie_state(db_session, version, status="awaiting_manazer")
    _seed_task(db_session, version, number=1, status="todo")

    # Fake the dispatch so no real agent turn spawns — we only assert the resume was armed.
    dispatched: list = []
    monkeypatch.setattr(orchestrator, "_begin_dispatch", lambda db, st: dispatched.append(st.version_id))

    resumed = await orchestrator.apply_action(db_session, version_id=version.id, action="pokracovat")

    # It did NOT raise (the awaiting_manazer guard let pokracovat through) and re-dispatched the build loop —
    # current_stage stays programovanie; the runner routes programovanie → _run_build_round.
    assert resumed.current_stage == "programovanie"
    assert dispatched == [version.id]
