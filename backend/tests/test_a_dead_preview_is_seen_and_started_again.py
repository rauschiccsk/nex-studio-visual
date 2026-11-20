"""DEV-127 — the cockpit sees a dead live preview, says why, starts it again with one click, and never offers to
approve a vizuál nobody can look at.

Director 15.10.2026: „Spravil som, ponúka schváliť vizuál … Ale URL na vizual nefunguje. Vypisuje "404 page not
found"“. The preview of NEX Manager 1.3.0 died seven minutes after „Vizuál je pripravený" (28.09.2026); the cockpit
kept offering „Schváliť vizuál" for seventeen days and had no way to start it again.
"""

from __future__ import annotations

import subprocess

import pytest

from backend.services import orchestrator, vizual_sandbox
from backend.services.orchestrator import VIZUAL_PREVIEW_DOWN, OrchestratorError
from tests.test_orchestrator_v2_vizual import _make_version, _seed_state

STACK = (
    "\x1b[31mfailed to load config from /app/vite.vizual.config.mts\x1b[39m\n"
    "\x1b[31mError: Živý náhľad potrebuje texty vydaní, ale priečinok /docs/specs/versions neexistuje.\x1b[39m\n"
    "    at nacitajNovinky (file:///app/node_modules/.vite-temp/x.mjs:21:11)\n"
    "    at ModuleJob.run (node:internal/modules/esm/module_job:325:25)\n"
)


def _docker(monkeypatch, *, running: str | None, logs: str = "", answers: bool = True):
    """``docker inspect`` answers ``running`` ("true"/"false"; None = no such container); ``docker logs`` → ``logs``;
    ``docker exec`` (DEV-447: the readiness probe) succeeds when the preview ``answers``."""

    def _run(argv, **_):
        if argv[1] == "exec":
            return subprocess.CompletedProcess(argv, 0 if answers else 1, "", "")
        if argv[1] == "inspect":
            if running is None:
                return subprocess.CompletedProcess(argv, 1, "", "Error: No such object")
            return subprocess.CompletedProcess(argv, 0, f"{running}\n", "")
        if argv[1] == "logs":
            return subprocess.CompletedProcess(argv, 0, "", logs)
        raise AssertionError(f"unexpected docker call {argv}")

    monkeypatch.setattr(vizual_sandbox.subprocess, "run", _run)


# ── the sandbox says what it is ─────────────────────────────────────────────────────────────────────────────────


def test_a_running_preview_gives_its_address(monkeypatch) -> None:
    _docker(monkeypatch, running="true")
    assert vizual_sandbox.preview_state("nex-manager") == {
        "state": "running",
        "url": "https://vizual-nex-manager.isnex.eu",
        "reason": None,
    }


def test_a_running_preview_that_does_not_answer_yet_is_starting_without_its_address(monkeypatch) -> None:
    # DEV-447 — 20.11.2026 Orthodox Hermenia: the strip offered the link at once; the opened preview said it was
    # still being prepared (Vite was bundling its libraries).
    _docker(monkeypatch, running="true", answers=False)
    assert vizual_sandbox.preview_state("nex-manager") == {"state": "starting", "url": None, "reason": None}


def test_a_stopped_preview_says_the_last_error_it_left_without_colours_or_stack(monkeypatch) -> None:
    _docker(monkeypatch, running="false", logs=STACK)
    assert vizual_sandbox.preview_state("nex-manager") == {
        "state": "stopped",
        "url": None,
        "reason": "Error: Živý náhľad potrebuje texty vydaní, ale priečinok /docs/specs/versions neexistuje.",
    }


def test_a_long_error_is_cut_to_a_readable_length(monkeypatch) -> None:
    _docker(monkeypatch, running="false", logs="Error: " + "x" * 1000 + "\n")
    assert len(vizual_sandbox.preview_state("nex-manager")["reason"]) == 300


def test_a_preview_never_started_is_missing(monkeypatch) -> None:
    _docker(monkeypatch, running=None)
    assert vizual_sandbox.preview_state("nex-manager") == {"state": "missing", "url": None, "reason": None}


# ── the cockpit asks only where a preview is walked ─────────────────────────────────────────────────────────────


def _preview(monkeypatch, states: list[str], spins: list[str] | None = None, *, fail: Exception | None = None):
    """``preview_state`` answers the next of ``states``; ``spin_up`` records its slug (or raises ``fail``)."""
    answers = iter(states)

    def _state(slug, *, private=False):
        state = next(answers)
        reason = "Error: konfigurácia náhľadu spadla" if state == "stopped" else None
        return {
            "state": state,
            "url": f"https://vizual-{slug}.isnex.eu" if state == "running" else None,
            "reason": reason,
        }

    def _spin(slug, frontend_path=None, *, private=False, fresh=False):
        if spins is not None:
            spins.append(slug if fresh else f"{slug} (reused if running)")
        if fail:
            raise fail
        return f"https://vizual-{slug}.isnex.eu"

    monkeypatch.setattr(vizual_sandbox, "preview_state", _state)
    monkeypatch.setattr(vizual_sandbox, "spin_up", _spin)


@pytest.mark.asyncio
async def test_outside_the_vizual_step_no_preview_is_needed_and_docker_is_not_asked(db_session, monkeypatch) -> None:
    version, _ = _make_version(db_session)
    _seed_state(db_session, version.id, stage="programovanie", actor="ai_agent")
    _preview(monkeypatch, [])
    assert await orchestrator.vizual_preview(db_session, version.id) == {
        "needed": False,
        "state": "not_needed",
        "url": None,
        "reason": None,
    }


@pytest.mark.asyncio
async def test_a_mockup_preview_needs_no_container(db_session, monkeypatch) -> None:
    version, _ = _make_version(db_session)
    _seed_state(db_session, version.id, stage="vizual", actor="ai_agent")
    monkeypatch.setattr(orchestrator, "has_vizual_mockup", lambda db, vid: True)
    _preview(monkeypatch, [])
    assert (await orchestrator.vizual_preview(db_session, version.id))["needed"] is False


@pytest.mark.asyncio
async def test_in_the_vizual_step_a_dead_preview_is_seen_with_its_reason(db_session, monkeypatch) -> None:
    version, project = _make_version(db_session)
    _seed_state(db_session, version.id, stage="vizual", actor="ai_agent")
    _preview(monkeypatch, ["stopped"])
    assert await orchestrator.vizual_preview(db_session, version.id) == {
        "needed": True,
        "state": "stopped",
        "url": None,
        "reason": "Error: konfigurácia náhľadu spadla",
    }


@pytest.mark.asyncio
async def test_in_the_vizual_step_a_preview_still_preparing_is_starting_without_a_link(db_session, monkeypatch) -> None:
    # DEV-447: the strip says „Náhľad sa pripravuje…“ and offers the link only once the preview answers.
    version, project = _make_version(db_session)
    _seed_state(db_session, version.id, stage="vizual", actor="ai_agent")
    _preview(monkeypatch, ["starting"])
    assert await orchestrator.vizual_preview(db_session, version.id) == {
        "needed": True,
        "state": "starting",
        "url": None,
        "reason": None,
    }


@pytest.mark.asyncio
async def test_start_again_brings_a_dead_preview_back(db_session, monkeypatch) -> None:
    version, project = _make_version(db_session)
    _seed_state(db_session, version.id, stage="vizual", actor="ai_agent")
    spins: list[str] = []
    _preview(monkeypatch, ["running"], spins)
    after = await orchestrator.restart_vizual_preview(db_session, version.id)
    assert spins == [project.slug]
    assert (after["state"], after["url"]) == ("running", f"https://vizual-{project.slug}.isnex.eu")


@pytest.mark.asyncio
async def test_start_again_replaces_a_running_preview_too(db_session, monkeypatch) -> None:
    """DEV-419 — 17.11.2026 NEX Manager 1.7.0: a preview that „ran“ served a blank page; the button returned at once
    for a running preview. The Manažér presses it when a preview does not work: it is built anew, running or not."""
    version, project = _make_version(db_session)
    _seed_state(db_session, version.id, stage="vizual", actor="ai_agent")
    spins: list[str] = []
    _preview(monkeypatch, ["running"], spins)
    assert (await orchestrator.restart_vizual_preview(db_session, version.id))["state"] == "running"
    assert spins == [project.slug]


@pytest.mark.asyncio
async def test_start_again_outside_the_vizual_step_is_refused(db_session, monkeypatch) -> None:
    version, _ = _make_version(db_session)
    _seed_state(db_session, version.id, stage="programovanie", actor="ai_agent")
    _preview(monkeypatch, [])
    with pytest.raises(OrchestratorError, match="len v kroku Vizuál"):
        await orchestrator.restart_vizual_preview(db_session, version.id)


@pytest.mark.asyncio
async def test_a_preview_that_will_not_start_says_why(db_session, monkeypatch) -> None:
    version, _ = _make_version(db_session)
    _seed_state(db_session, version.id, stage="vizual", actor="ai_agent")
    _preview(monkeypatch, ["stopped", "stopped"], fail=RuntimeError("container exited immediately"))
    with pytest.raises(OrchestratorError) as caught:
        await orchestrator.restart_vizual_preview(db_session, version.id)
    assert str(caught.value) == "Živý náhľad sa nepodarilo spustiť: Error: konfigurácia náhľadu spadla"


# ── a vizuál is approved by looking at it ────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("dead", ["stopped", "missing"])
async def test_the_vizual_is_not_approved_while_its_preview_is_dead(db_session, monkeypatch, dead) -> None:
    version, _ = _make_version(db_session)
    state = _seed_state(db_session, version.id, stage="vizual", actor="ai_agent")
    state.status = "awaiting_manazer"
    db_session.flush()
    _preview(monkeypatch, [dead])
    with pytest.raises(OrchestratorError) as caught:
        await orchestrator.apply_action(db_session, version_id=version.id, action="schvalit")
    assert str(caught.value) == VIZUAL_PREVIEW_DOWN
    assert state.current_stage == "vizual" and state.pending_vizual_signoff is not True


@pytest.mark.asyncio
async def test_the_vizual_waits_while_its_preview_is_still_preparing(db_session, monkeypatch) -> None:
    # DEV-447: not „nebeží — spusti ho“ — there is nothing to start, only to wait.
    version, _ = _make_version(db_session)
    state = _seed_state(db_session, version.id, stage="vizual", actor="ai_agent")
    state.status = "awaiting_manazer"
    db_session.flush()
    _preview(monkeypatch, ["starting"])
    with pytest.raises(OrchestratorError) as caught:
        await orchestrator.apply_action(db_session, version_id=version.id, action="schvalit")
    assert str(caught.value) == orchestrator.VIZUAL_PREVIEW_STARTING
    assert state.current_stage == "vizual" and state.pending_vizual_signoff is not True
