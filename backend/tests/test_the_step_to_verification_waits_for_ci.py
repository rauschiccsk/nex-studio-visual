"""DEV-261 — after Programovanie the cockpit offers „Prejsť na overenie“ only once the CI of the commit it pushed is
green; while that CI runs the step waits, and a red one is offered back to the agent for a fix instead.

30.10.2026 NEX Knowledge 0.2.0: the fix after Verifikácia (task #13.1.1) ended 14:13, the cockpit pushed the main branch
and CI started — and „Prejsť na overenie“ was on the screen at once. The Director: „Pri projekte nex-knowledge CI ešte
stále beží ale kokpit už ponúka prejsť na overenie.“ Nothing wrong would have been signed off (Verifikácia waits for CI,
DEV-201), but a red CI would have cost a whole Auditor round to find what the cockpit knew minutes after the push.
The wait is the one Verifikácia has: the version's state holds it, the CI watcher settles it every minute.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.api.routes.pipeline import _board
from backend.config.settings import settings
from backend.db.models.pipeline import PipelineMessage
from backend.services import orchestrator
from backend.services.orchestrator import OrchestratorError
from backend.tests.integration.test_fix_round_is_remeasured import _collect, _finish_all_tasks, _plain_plan, _seed
from tests.test_a_version_waits_for_its_ci import GREEN, RED, RUNNING, _github


async def _at_the_gate(db_session, monkeypatch, rows=RUNNING, *, has_ci=True):
    answer = {"rows": rows}
    _github(monkeypatch, answer)
    monkeypatch.setattr(orchestrator, "_project_has_ci", lambda root: has_ci)
    version, state = _seed(db_session)
    _plain_plan(db_session, version)
    _finish_all_tasks(db_session, version.id)
    await orchestrator._run_build_round(db_session, state, on_message=_collect([]))
    return version, state, answer


def _notes(db_session, version_id) -> list[PipelineMessage]:
    return [
        m
        for m in db_session.query(PipelineMessage).filter(PipelineMessage.version_id == version_id).all()
        if (m.payload or {}).get("ci_gate")
    ]


# --- the gate -------------------------------------------------------------------------------------------------------


async def test_the_step_waits_while_the_ci_of_the_pushed_commit_runs(db_session, monkeypatch):
    version, state, _gh = await _at_the_gate(db_session, monkeypatch)

    assert (state.current_stage, state.status) == ("programovanie", "awaiting_manazer")
    assert state.ci_pending_since is not None
    assert state.next_action == orchestrator.PROGRAMOVANIE_CI_PENDING_NEXT_ACTION
    assert "schvalit" not in orchestrator.determine_available_actions(state)
    board = _board(db_session, version.id)
    assert "schvalit" not in board.available_actions
    assert board.ci_gate is not None and board.ci_gate.state == "pending"
    assert board.ci_gate.message == orchestrator.PROGRAMOVANIE_CI_PENDING_NEXT_ACTION


async def test_a_project_without_ci_is_offered_the_step_at_once(db_session, monkeypatch):
    version, state, _gh = await _at_the_gate(db_session, monkeypatch, has_ci=False)

    assert state.status == "awaiting_manazer" and state.ci_pending_since is None
    assert state.next_action == orchestrator.PROGRAMOVANIE_NEXT_ACTION
    assert "schvalit" in _board(db_session, version.id).available_actions
    assert _board(db_session, version.id).ci_gate is None


async def test_the_manager_cannot_step_to_verification_while_it_runs(db_session, monkeypatch):
    version, _state, _gh = await _at_the_gate(db_session, monkeypatch)

    with pytest.raises(OrchestratorError) as refused:
        await orchestrator.apply_action(db_session, version_id=version.id, action="schvalit", payload={})
    assert str(refused.value) == orchestrator.PROGRAMOVANIE_CI_PENDING_REFUSAL


# --- the watcher ----------------------------------------------------------------------------------------------------


async def test_a_ci_still_running_keeps_the_step_waiting(db_session, monkeypatch):
    version, state, _gh = await _at_the_gate(db_session, monkeypatch)

    assert await orchestrator.settle_ci_waits(db_session) == []
    assert state.ci_pending_since is not None and _notes(db_session, version.id) == []


async def test_a_green_ci_offers_the_step(db_session, monkeypatch):
    version, state, github = await _at_the_gate(db_session, monkeypatch)
    github["rows"] = GREEN

    settled = await orchestrator.settle_ci_waits(db_session)

    assert [s.version_id for s in settled] == [version.id]
    assert state.ci_pending_since is None and state.status == "awaiting_manazer"
    assert state.next_action == orchestrator.PROGRAMOVANIE_NEXT_ACTION
    assert "schvalit" in _board(db_session, version.id).available_actions
    [note] = _notes(db_session, version.id)
    assert note.payload["ci_gate"] == "green" and note.content.startswith("Kontroly na GitHube po Programovaní prešli")


async def test_a_red_ci_offers_the_fix_instead_of_the_step(db_session, monkeypatch):
    version, state, github = await _at_the_gate(db_session, monkeypatch)
    github["rows"] = RED

    await orchestrator.settle_ci_waits(db_session)

    assert state.ci_pending_since is None and state.status == "awaiting_manazer"
    assert state.next_action == orchestrator.PROGRAMOVANIE_CI_RED_NEXT_ACTION
    [note] = _notes(db_session, version.id)
    assert note.payload["ci_gate"] == "red" and note.payload["sha"] == "deadbeefcafe"
    assert note.content.startswith("Kontroly na GitHube po Programovaní zlyhali")
    detail = orchestrator.programovanie_ci_red(db_session, version.id)
    assert detail == note.payload["ci"]
    board = _board(db_session, version.id)
    assert "schvalit" not in board.available_actions and "opravit_podla_ci" in board.available_actions
    assert board.ci_gate is not None and board.ci_gate.state == "red" and detail in board.ci_gate.message
    with pytest.raises(OrchestratorError) as refused:
        await orchestrator.apply_action(db_session, version_id=version.id, action="schvalit", payload={})
    assert str(refused.value) == orchestrator.PROGRAMOVANIE_CI_RED_REFUSAL


async def test_the_fix_of_a_red_ci_goes_back_to_the_agent_with_what_failed(db_session, monkeypatch):
    version, state, github = await _at_the_gate(db_session, monkeypatch)
    github["rows"] = RED
    await orchestrator.settle_ci_waits(db_session)
    detail = orchestrator.programovanie_ci_red(db_session, version.id)

    await orchestrator.apply_action(db_session, version_id=version.id, action="opravit_podla_ci", payload={})

    assert state.status == "agent_working" and state.current_stage == "programovanie"
    returned = [
        m
        for m in db_session.query(PipelineMessage).filter(PipelineMessage.version_id == version.id).all()
        if m.kind == "return"
    ]
    assert returned and returned[-1].content == orchestrator.CI_RED_FIX_BRIEF.format(detail=detail)
    assert orchestrator.task_service.get_next_todo_task(db_session, version.id) is not None


async def test_the_fix_of_a_red_ci_is_not_offered_without_one(db_session, monkeypatch):
    version, _state, github = await _at_the_gate(db_session, monkeypatch)
    github["rows"] = GREEN
    await orchestrator.settle_ci_waits(db_session)

    assert "opravit_podla_ci" not in _board(db_session, version.id).available_actions
    with pytest.raises(OrchestratorError) as refused:
        await orchestrator.apply_action(db_session, version_id=version.id, action="opravit_podla_ci", payload={})
    assert str(refused.value) == orchestrator.CI_RED_FIX_NOT_OFFERED


async def test_a_red_ci_of_an_older_commit_holds_nothing(db_session, monkeypatch):
    version, _state, github = await _at_the_gate(db_session, monkeypatch)
    github["rows"] = RED
    await orchestrator.settle_ci_waits(db_session)

    monkeypatch.setattr(orchestrator, "_repo_head", lambda root: "0123456789ab")

    assert orchestrator.programovanie_ci_red(db_session, version.id) is None
    assert "schvalit" in _board(db_session, version.id).available_actions


async def test_a_github_that_cannot_be_asked_stops_holding_the_step_after_the_run_should_have_appeared(
    db_session, monkeypatch
):
    version, state, github = await _at_the_gate(db_session, monkeypatch)
    github["down"] = True

    assert await orchestrator.settle_ci_waits(db_session) == []  # a moment after the push: not yet a reason
    state.ci_pending_since = datetime.now(timezone.utc) - timedelta(seconds=orchestrator.CI_RUN_APPEAR_TIMEOUT + 5)
    await orchestrator.settle_ci_waits(db_session)

    assert state.ci_pending_since is None and state.next_action == orchestrator.PROGRAMOVANIE_NEXT_ACTION
    [note] = _notes(db_session, version.id)
    assert note.payload["ci_gate"] == "unknown" and "Verifikácia ich skontroluje" in note.content


async def test_a_ci_that_never_ends_stops_holding_the_step_at_the_cap(db_session, monkeypatch):
    version, state, _gh = await _at_the_gate(db_session, monkeypatch)
    state.ci_pending_since = datetime.now(timezone.utc) - timedelta(hours=settings.ci_pending_cap_hours, minutes=1)

    await orchestrator.settle_ci_waits(db_session)

    assert state.ci_pending_since is None and "schvalit" in orchestrator.determine_available_actions(state)
    [note] = _notes(db_session, version.id)
    assert note.payload["ci_gate"] == "overdue" and f"{settings.ci_pending_cap_hours} h" in note.content


async def test_a_wait_left_on_a_build_that_moved_on_is_dropped_without_a_word(db_session, monkeypatch):
    version, state, github = await _at_the_gate(db_session, monkeypatch)
    github["rows"] = RED
    state.status = "paused"  # e.g. the Manažér's fix off a Verifikácia wait put the build back here, paused

    await orchestrator.settle_ci_waits(db_session)

    assert state.ci_pending_since is None and state.status == "paused"
    assert _notes(db_session, version.id) == []
