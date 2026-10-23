"""DEV-201 — a version does not become Hotovo while the CI of its last commit is still running; it waits for it.

23.10.2026 NEX Inbox 1.10.1: the Verifikácia gate waited 20 minutes for CI, then let the version pass „bez jeho
výsledku“ — and that CI took 49 minutes (its jobs waited 34–99 minutes for the project's only runner) and failed. The
version had „Hotovo“ while its checks were red. Holding the turn longer is no answer: the turn would block deploys of
the cockpit and die with a restart. So the wait moved from the turn into the version's state: still running after a
short wait → „čaká na kontrolu na GitHube“, no sign-off; the cockpit's CI watcher settles it when the run finishes —
green lets the PASS through, red takes the same way back to the fix as a red seen in time. Director 23.10.2026:
„Áno, súhlasím, implementuj DEV-201“.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest

from backend.config.settings import settings
from backend.services import orchestrator, pipeline_runner
from backend.services.orchestrator import OrchestratorError
from tests.test_orchestrator_v2_verifikacia import (
    _ban_deploy_calls,
    _make_version,
    _msgs,
    _seed_verifikacia,
    _stub_auditor,
    _stub_smoke,
    _verdict_pass,
)

RUNNING = [{"status": "in_progress", "conclusion": None, "databaseId": 42, "workflowName": "CI"}]
GREEN = [{"status": "completed", "conclusion": "success", "databaseId": 42, "workflowName": "CI"}]
RED = [{"status": "completed", "conclusion": "failure", "databaseId": 42, "workflowName": "CI"}]


def _github(monkeypatch, answer: dict) -> None:
    """GitHub as the one subprocess seam of the CI floor; ``answer["rows"]`` is what it says now, ``answer["down"]``
    makes it unreachable."""
    monkeypatch.setattr(orchestrator, "_project_has_ci", lambda root: True)
    monkeypatch.setattr(orchestrator, "_repo_head", lambda root: "deadbeefcafe")
    monkeypatch.setattr(orchestrator, "CI_RUN_APPEAR_INTERVAL", 0)
    monkeypatch.setattr(orchestrator, "CI_RUN_FINISH_INTERVAL", 0)

    async def fake_step(cmd, timeout):
        if "config" in cmd:
            return 0, "https://github.com/rauschiccsk/nex-demo.git\n"
        if answer.get("down"):
            return 1, "gh: could not connect"
        return 0, json.dumps(answer["rows"])

    monkeypatch.setattr(orchestrator, "_run_publish_step", fake_step)


async def _passed_while_ci_runs(db_session, monkeypatch, *, flow_type="new_version", dial=None):
    answer = {"rows": RUNNING}
    _github(monkeypatch, answer)
    version, _project = _make_version(db_session, project_dial=dial)
    _seed_verifikacia(db_session, version.id, flow_type=flow_type)
    _stub_auditor(monkeypatch, _verdict_pass())
    _stub_smoke(monkeypatch)
    _ban_deploy_calls(monkeypatch)
    state = await orchestrator.run_dispatch(db_session, version.id)
    return version, state, answer


def _verdicts(db_session, version_id):
    return [m for m in _msgs(db_session, version_id) if m.kind == "verdict"]


# --- the gate -------------------------------------------------------------------------------------------------------


async def test_a_ci_still_running_after_the_wait_leaves_the_version_waiting(db_session, monkeypatch):
    version, state, _github_now = await _passed_while_ci_runs(db_session, monkeypatch)

    assert (state.current_stage, state.status) == ("verifikacia", "awaiting_manazer")
    assert state.ci_pending_since is not None
    assert "schvalit" not in orchestrator.determine_available_actions(state)
    assert orchestrator._verifikacia_passed(db_session, version.id) is False
    assert "kontrolu na GitHube" in state.next_action
    waiting = [m for m in _msgs(db_session, version.id) if (m.payload or {}).get("ci_pending")]
    assert waiting and "CI" in waiting[-1].content


async def test_the_fast_fix_does_not_sign_off_while_its_ci_runs(db_session, monkeypatch):
    version, state, _github_now = await _passed_while_ci_runs(
        db_session, monkeypatch, flow_type="fast_fix", dial="plna"
    )

    assert state.status != "done" and state.current_stage == "verifikacia"
    assert state.ci_pending_since is not None


async def test_the_manager_cannot_sign_off_while_the_ci_runs(db_session, monkeypatch):
    version, _state, _github_now = await _passed_while_ci_runs(db_session, monkeypatch)

    with pytest.raises(OrchestratorError, match="kontrolu na GitHube"):
        await orchestrator.apply_action(db_session, version_id=version.id, action="schvalit", payload={})


async def test_a_turn_that_starts_ends_the_wait(db_session, monkeypatch):
    _version, state, _github_now = await _passed_while_ci_runs(db_session, monkeypatch)

    state.status = "agent_working"

    assert state.ci_pending_since is None


# --- the watcher ----------------------------------------------------------------------------------------------------


async def test_a_green_ci_lets_the_waiting_pass_through(db_session, monkeypatch):
    version, state, github = await _passed_while_ci_runs(db_session, monkeypatch)
    github["rows"] = GREEN

    settled = await orchestrator.settle_ci_waits(db_session)

    assert [s.version_id for s in settled] == [version.id]
    assert state.ci_pending_since is None and state.status == "awaiting_manazer"
    assert "schvalit" in orchestrator.determine_available_actions(state)
    assert orchestrator._verifikacia_passed(db_session, version.id) is True
    assert state.next_action == orchestrator.VERIFIKACIA_PASS_NEXT_ACTION
    assert any("dobehli" in m.content for m in _msgs(db_session, version.id) if m.kind == "notification")


async def test_a_green_ci_signs_the_fast_fix_off(db_session, monkeypatch):
    _version, state, github = await _passed_while_ci_runs(db_session, monkeypatch, flow_type="fast_fix", dial="plna")
    github["rows"] = GREEN

    await orchestrator.settle_ci_waits(db_session)

    assert (state.current_stage, state.status) == ("done", "done")


async def test_a_red_ci_takes_the_version_back_to_the_fix(db_session, monkeypatch):
    version, state, github = await _passed_while_ci_runs(db_session, monkeypatch)
    github["rows"] = RED

    await orchestrator.settle_ci_waits(db_session)

    last = _verdicts(db_session, version.id)[-1]
    assert (last.content, last.payload.get("engine_override")) == ("FAIL", "ci_red")
    assert orchestrator._verifikacia_passed(db_session, version.id) is False
    assert state.ci_pending_since is None
    assert (state.status, state.block_reason) == ("blocked", "decision_needed")


async def test_a_red_ci_sends_the_fast_fix_back_to_work(db_session, monkeypatch):
    _version, state, github = await _passed_while_ci_runs(db_session, monkeypatch, flow_type="fast_fix", dial="plna")
    github["rows"] = RED

    await orchestrator.settle_ci_waits(db_session)

    assert (state.current_stage, state.status) == ("programovanie", "agent_working")


async def test_a_ci_still_running_keeps_the_version_waiting(db_session, monkeypatch):
    _version, state, _github_now = await _passed_while_ci_runs(db_session, monkeypatch)
    since = state.ci_pending_since

    assert await orchestrator.settle_ci_waits(db_session) == []

    assert state.ci_pending_since == since


async def test_an_unreachable_github_keeps_the_version_waiting(db_session, monkeypatch):
    _version, state, github = await _passed_while_ci_runs(db_session, monkeypatch)
    github["down"] = True

    assert await orchestrator.settle_ci_waits(db_session) == []

    assert state.ci_pending_since is not None


async def test_a_run_stuck_past_the_cap_ends_the_wait_and_says_so(db_session, monkeypatch):
    version, state, _github_now = await _passed_while_ci_runs(db_session, monkeypatch)
    state.ci_pending_since = datetime.now(timezone.utc) - timedelta(hours=settings.ci_pending_cap_hours, minutes=1)
    db_session.flush()

    settled = await orchestrator.settle_ci_waits(db_session)

    assert [s.version_id for s in settled] == [version.id]
    assert state.ci_pending_since is None and "schvalit" in orchestrator.determine_available_actions(state)
    note = [m for m in _msgs(db_session, version.id) if m.kind == "notification"][-1]
    assert f"ani po {settings.ci_pending_cap_hours} h" in note.content


# --- what the watcher loop does with it ------------------------------------------------------------------------------


async def test_the_watcher_shows_what_changed_and_starts_the_fix(db_session, monkeypatch):
    version, state, github = await _passed_while_ci_runs(db_session, monkeypatch, flow_type="fast_fix", dial="plna")
    github["rows"] = RED
    sent: list[tuple] = []
    scheduled: list = []

    async def _broadcast(version_id, event):
        sent.append((version_id, event["type"]))

    monkeypatch.setattr(pipeline_runner.registry, "broadcast", _broadcast)
    monkeypatch.setattr(
        pipeline_runner, "schedule_dispatch", lambda version_id, directive=None: scheduled.append(version_id)
    )

    await pipeline_runner.settle_ci_waits(db_session)

    assert (version.id, "state_changed") in sent and (version.id, "message_added") in sent
    assert scheduled == [version.id]


async def test_the_ci_watch_loop_settles_the_waiting_versions_even_when_telegram_fails(monkeypatch):
    from backend import main
    from backend.services import ci_watch

    calls: list[str] = []

    async def _watch(_db):
        calls.append("watch")
        raise RuntimeError("Telegram is down")

    async def _settle(_db):
        calls.append("settle")

    async def _stop(_seconds):
        raise asyncio.CancelledError

    monkeypatch.setattr(ci_watch, "watch_once", _watch)
    monkeypatch.setattr(pipeline_runner, "settle_ci_waits", _settle)
    monkeypatch.setattr(main.asyncio, "sleep", _stop)

    with pytest.raises(asyncio.CancelledError):
        await main._ci_watch_loop()

    assert calls == ["watch", "settle"]
