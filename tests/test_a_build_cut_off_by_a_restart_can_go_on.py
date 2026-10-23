"""DEV-204 — a build a restart of the cockpit cut off can go on, in whichever phase it was.

23.10.2026 13:18 UTC the deploy of the cockpit 4.84.0 (allowed by the Director while NEX Inbox 1.10.2 was being
verified) restarted the backend. The recovery settled the build „čaká na Manažéra“ with „Fáza 'verifikacia' prerušená
reštartom — … Pokračuj.“ — but „Pokračovať“ was offered only in a paused Programovanie or after Dedo's fix; at
Verifikácia the screen showed „Upraviť“ and „Schváliť na Hotovo — Auditor ju overil“, which was not true and would have
been refused. Director: „Nemám tam možnosť Pokračovať“, „Áno, súhlasím, založ tiket a oprav to“.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import text

from backend.services import orchestrator
from backend.services.orchestrator import OrchestratorError
from tests.test_orchestrator_v2_dispatch_lifecycle import _arm_dispatch_state, _make_version, _msgs

MIGRATION = Path(__file__).resolve().parents[1] / "migrations" / "versions" / "139_resume_after_restart.py"


def _recovered(db_session, monkeypatch, stage: str, actor: str):
    monkeypatch.setattr(orchestrator, "_repo_head", lambda root: "h" * 40)
    monkeypatch.setattr(orchestrator, "_rev_list_count", lambda root, baseline: 0)
    monkeypatch.setattr(db_session, "commit", db_session.flush)  # the recovery commits; keep the test's savepoint
    version, _ = _make_version(db_session)
    _arm_dispatch_state(db_session, version, stage=stage, actor=actor)
    assert orchestrator.recover_orphaned_builds_on_startup(db_session) == 1
    return version, orchestrator._get_state(db_session, version.id)


@pytest.mark.parametrize(
    ("stage", "actor"), [("verifikacia", "auditor"), ("navrh", "ai_agent"), ("programovanie", "ai_agent")]
)
async def test_a_build_cut_off_by_a_restart_offers_to_go_on(db_session, monkeypatch, stage, actor):
    _version, state = _recovered(db_session, monkeypatch, stage, actor)

    assert state.resume_after_restart is True
    assert orchestrator.determine_available_actions(state) == {"pokracovat", "uprav"}


async def test_going_on_runs_the_cut_off_verification_again(db_session, monkeypatch):
    version, state = _recovered(db_session, monkeypatch, "verifikacia", "auditor")

    state = await orchestrator.apply_action(db_session, version_id=version.id, action="pokracovat", payload={})

    assert (state.current_stage, state.status, state.current_actor) == ("verifikacia", "agent_working", "auditor")
    assert state.resume_after_restart is False, "the button is spent by one press"
    resumed = [m for m in _msgs(db_session, version.id) if (m.payload or {}).get("restart_resume")]
    assert resumed and resumed[-1].content == "Stavba pokračuje."


async def test_without_a_restart_verification_still_does_not_take_go_on(db_session, monkeypatch):
    version, state = _recovered(db_session, monkeypatch, "verifikacia", "auditor")
    state.resume_after_restart = False
    db_session.flush()

    with pytest.raises(OrchestratorError, match="Programovanie"):
        await orchestrator.apply_action(db_session, version_id=version.id, action="pokracovat", payload={})


def test_a_build_the_recovery_settled_before_the_fix_gets_the_button_too(db_session, monkeypatch):
    """NEX Inbox 1.10.2 was recovered by 4.84.0, before the flag existed — the migration marks such a build."""
    _version, state = _recovered(db_session, monkeypatch, "verifikacia", "auditor")
    state.resume_after_restart = False
    db_session.flush()
    spec = importlib.util.spec_from_file_location("migration_139", MIGRATION)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    db_session.execute(text(migration.MARK_RECOVERED))
    db_session.expire_all()

    assert orchestrator._get_state(db_session, state.version_id).resume_after_restart is True


def test_a_build_that_went_on_after_its_recovery_is_not_marked(db_session, monkeypatch):
    version, state = _recovered(db_session, monkeypatch, "verifikacia", "auditor")
    state.resume_after_restart = False
    orchestrator._record_message(
        db_session,
        version_id=version.id,
        stage="verifikacia",
        author="system",
        recipient="manazer",
        kind="notification",
        content="later news",
        payload={"phase": "verifikacia"},
    )
    db_session.flush()
    spec = importlib.util.spec_from_file_location("migration_139", MIGRATION)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    db_session.execute(text(migration.MARK_RECOVERED))
    db_session.expire_all()

    assert orchestrator._get_state(db_session, state.version_id).resume_after_restart is False
