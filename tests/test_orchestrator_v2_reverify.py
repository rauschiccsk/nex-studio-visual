"""CR-V2-057 "Over znova" — the honest re-verify. When a version's recorded Verifikácia PASS is stale
(``version_verified`` == ``sha_drift``: HEAD moved past the verified commit), the Manažér re-verifies via the
``overit_znovu`` action, which re-enters Verifikácia and re-runs the independent Auditor against the CURRENT
HEAD (the shared round machinery). Fail-closed: rejected unless there is real drift, and only from a settled
state. No "just re-stamp the green" — an honest re-verify MUST re-run the Auditor.

Exercised against the real v2 branch DB (asyncio_mode=auto)."""

import uuid

import pytest

from backend.db.models.foundation import User
from backend.db.models.pipeline import PipelineState
from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.services import orchestrator


def _make_version(db):
    user = User(
        username=f"u_{uuid.uuid4().hex[:8]}",
        email=f"{uuid.uuid4().hex[:8]}@e.com",
        password_hash="x",
        role="ri",
    )
    db.add(user)
    db.flush()
    project = Project(
        name="P",
        slug=f"p-{uuid.uuid4().hex[:8]}",
        type="standard",
        auth_mode="password",
        description="d",
        created_by=user.id,
        source_path=None,
    )
    db.add(project)
    db.flush()
    v = Version(project_id=project.id, version_number="v0.1.0", name="dev")
    db.add(v)
    db.flush()
    return v


def _seed_state(db, version_id, *, current_stage="verifikacia", status="awaiting_manazer"):
    state = PipelineState(
        version_id=version_id,
        flow_type="new_version",
        current_stage=current_stage,
        current_actor="auditor",
        status=status,
        next_action="settled",
        iteration=1,
    )
    db.add(state)
    db.flush()
    return state


def _pass(db, version_id, **extra):
    orchestrator._record_message(
        db,
        version_id=version_id,
        stage="verifikacia",
        author="auditor",
        recipient="manazer",
        kind="verdict",
        content="PASS",
        payload={"verdict": "PASS", "phase": "verifikacia", **extra},
    )
    db.flush()


async def test_overit_znovu_reenters_verifikacia_when_drifted(db_session, monkeypatch):
    # A PASS bound to an OLD commit; HEAD has since moved → sha_drift. "Over znova" re-enters Verifikácia and
    # arms a fresh Auditor round (is_regate, iteration bumped, agent_working with the Auditor on turn).
    monkeypatch.setattr(orchestrator, "_repo_head", lambda _root: "newhead")
    v = _make_version(db_session)
    _pass(db_session, v.id, verified_sha="oldsha")
    _seed_state(db_session, v.id, current_stage="verifikacia", status="awaiting_manazer")
    assert orchestrator.version_verified(db_session, v.id) == (False, "sha_drift")

    new_state = await orchestrator.apply_action(db_session, version_id=v.id, action="overit_znovu", payload={})
    assert new_state.current_stage == "verifikacia"
    assert new_state.status == "agent_working"
    assert new_state.current_actor == "auditor"  # _begin_dispatch → STAGE_ACTOR['verifikacia']
    assert new_state.is_regate is True
    assert new_state.iteration == 2  # bumped from the seeded 1


async def test_overit_znovu_reenters_from_done(db_session, monkeypatch):
    # A finished (Hotovo) build whose PASS drifted also re-verifies: done → back into Verifikácia.
    monkeypatch.setattr(orchestrator, "_repo_head", lambda _root: "newhead")
    v = _make_version(db_session)
    _pass(db_session, v.id, verified_sha="oldsha")
    _seed_state(db_session, v.id, current_stage="done", status="done")

    new_state = await orchestrator.apply_action(db_session, version_id=v.id, action="overit_znovu", payload={})
    assert new_state.current_stage == "verifikacia"
    assert new_state.status == "agent_working"


async def test_overit_znovu_rejected_when_not_drifted(db_session, monkeypatch):
    # Verified against the current HEAD (sha_match) → nothing to re-verify → rejected (no wasted Auditor run).
    monkeypatch.setattr(orchestrator, "_repo_head", lambda _root: "samehead")
    v = _make_version(db_session)
    _pass(db_session, v.id, verified_sha="samehead")
    _seed_state(db_session, v.id, status="awaiting_manazer")
    assert orchestrator.version_verified(db_session, v.id) == (True, "sha_match")

    with pytest.raises(orchestrator.OrchestratorError, match="zastarané"):
        await orchestrator.apply_action(db_session, version_id=v.id, action="overit_znovu", payload={})


async def test_overit_znovu_rejected_without_a_pass(db_session, monkeypatch):
    # No PASS verdict at all → not verified (no_pass), not a drift → re-verify is not the right verb → rejected.
    monkeypatch.setattr(orchestrator, "_repo_head", lambda _root: "newhead")
    v = _make_version(db_session)
    _seed_state(db_session, v.id, status="awaiting_manazer")

    with pytest.raises(orchestrator.OrchestratorError, match="zastarané"):
        await orchestrator.apply_action(db_session, version_id=v.id, action="overit_znovu", payload={})


async def test_overit_znovu_rejected_mid_turn(db_session, monkeypatch):
    # Only a SETTLED version re-verifies — never while the agent is mid-turn (agent_working).
    monkeypatch.setattr(orchestrator, "_repo_head", lambda _root: "newhead")
    v = _make_version(db_session)
    _pass(db_session, v.id, verified_sha="oldsha")
    _seed_state(db_session, v.id, status="agent_working")

    with pytest.raises(orchestrator.OrchestratorError, match="ustálenej"):
        await orchestrator.apply_action(db_session, version_id=v.id, action="overit_znovu", payload={})
