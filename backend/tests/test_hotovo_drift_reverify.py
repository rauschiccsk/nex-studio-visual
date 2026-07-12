"""Re-verify a drifted conversation-build Hotovo — auto re-anchor on green (audit #8, Director 2026-07-12).

A conversation build reaches deployability through the manager's SHA-anchored ``Hotovo`` signature. If the
code later moves past the signed commit (a fast-fix, a manual commit) WITHOUT a new version, the signature
drifts (``version_verified`` → ``hotovo_drift``): the version is no longer deployable. Before this fix that
was a DEAD END — the board offered ``overit_znovu`` only for the phase-build ``sha_drift`` shape, so the
drifted conversation build could neither deploy (not verified) nor re-verify (no button).

The chosen recovery (Director: one-click auto re-sign): ``overit_znovu`` re-opens the partner's honest
self-check against HEAD; on a GREEN runtime floor it AUTO re-anchors the Hotovo signature to the new commit
(no second click); on a RED floor it stays re-opened so the manager fixes it. The self-check's runtime-floor
gate (K-3) — NOT the partner's advisory prose — is the objective gate, exactly like the manual Hotovo.
"""

from __future__ import annotations

import types
import uuid as _uuid

import pytest
from sqlalchemy import select

from backend.db.models.foundation import User
from backend.db.models.pipeline import PipelineMessage, PipelineState
from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.services import orchestrator


def _seed(db, *, mode: str = "conversation", stage: str = "priprava", status: str = "agent_working"):
    suffix = _uuid.uuid4().hex[:8]
    user = User(username=f"hd_{suffix}", email=f"hd_{suffix}@test.local", password_hash="x", role="ri")
    db.add(user)
    db.flush()
    project = Project(
        name=f"Hotovo Drift {suffix}",
        slug=f"hotovo-drift-{suffix}",
        type="standard",
        auth_mode="password",
        description="audit #8 drifted-Hotovo re-verify test project.",
        created_by=user.id,
    )
    db.add(project)
    db.flush()
    version = Version(project_id=project.id, version_number="2.0.0", status="active")
    db.add(version)
    db.flush()
    state = PipelineState(
        version_id=version.id,
        flow_type="new_version",
        mode=mode,
        current_stage=stage,
        current_actor="ai_agent",
        status=status,
    )
    db.add(state)
    db.flush()
    return version, state


def _record_check_marker(db, version_id, *, auto_hotovo: bool) -> None:
    """The durable trigger the kontrola round reads — ``skontrolovat`` records it without auto_hotovo;
    ``overit_znovu`` on a hotovo_drift records it WITH auto_hotovo."""
    payload = {"phase": "priprava", "check": True}
    if auto_hotovo:
        payload["auto_hotovo"] = True
    orchestrator._record_message(
        db,
        version_id=version_id,
        stage="priprava",
        author="manazer",
        recipient="ai_agent",
        kind="directive",
        content="check",
        payload=payload,
    )


def _stub_git(monkeypatch) -> None:
    """No real git in the test — a fixed HEAD sha + no-op note/tag so ``_apply_hotovo_signoff`` is exercised."""
    monkeypatch.setattr(orchestrator, "_write_release_note_to_disk", lambda *a, **k: None)
    monkeypatch.setattr(orchestrator, "_commit_release_note", lambda *a, **k: None)
    monkeypatch.setattr(orchestrator, "_repo_head", lambda *a, **k: "deadbeefcafe")
    monkeypatch.setattr(orchestrator, "_git_tag_version", lambda *a, **k: None)


def _signoff(db, version_id):
    """The manager Hotovo signature marker (``payload.hotovo``), if present."""
    return db.execute(
        select(PipelineMessage)
        .where(
            PipelineMessage.version_id == version_id,
            PipelineMessage.kind == "notification",
            PipelineMessage.payload["hotovo"].astext == "true",
        )
        .order_by(PipelineMessage.seq.desc())
        .limit(1)
    ).scalar_one_or_none()


@pytest.mark.asyncio
async def test_green_reverify_auto_reanchors_hotovo(db_session, monkeypatch) -> None:
    """A GREEN self-check triggered by ``overit_znovu`` (auto_hotovo marker) re-signs Hotovo to the CURRENT
    commit and settles ``done`` — no second click (Director's chosen one-click behaviour)."""
    version, state = _seed(db_session)
    _record_check_marker(db_session, version.id, auto_hotovo=True)
    _stub_git(monkeypatch)

    async def _green_smoke(_slug, _label, _coverage):
        return (True, "boot ok"), (True, "acceptance ok", False)

    async def _partner(*_a, **_kw):
        return types.SimpleNamespace(kind="gate_report")

    monkeypatch.setattr(orchestrator, "_run_release_smoke", _green_smoke)
    monkeypatch.setattr(orchestrator, "invoke_agent_with_parse_retry", _partner)

    settled = await orchestrator._run_conversation_kontrola_round(db_session, state)

    # Auto re-anchored: terminal done + a fresh SHA-anchored Hotovo signature at the current HEAD.
    assert settled.status == "done"
    assert settled.current_stage == "done"
    sig = _signoff(db_session, version.id)
    assert sig is not None
    assert sig.payload["hotovo_sha"] == "deadbeefcafe"  # re-anchored to the (faked) current HEAD


@pytest.mark.asyncio
async def test_red_reverify_does_not_sign_stays_reopened(db_session, monkeypatch) -> None:
    """A RED runtime floor NEVER auto-signs (kontrola signs nothing red) — even with the auto_hotovo marker it
    settles re-opened (``awaiting_manazer`` at ``priprava``) so the manager sees + fixes the failure."""
    version, state = _seed(db_session)
    _record_check_marker(db_session, version.id, auto_hotovo=True)
    _stub_git(monkeypatch)

    async def _red_smoke(_slug, _label, _coverage):
        return (False, "boot FAIL: app did not boot"), None

    async def _partner(*_a, **_kw):
        return types.SimpleNamespace(kind="gate_report")

    monkeypatch.setattr(orchestrator, "_run_release_smoke", _red_smoke)
    monkeypatch.setattr(orchestrator, "invoke_agent_with_parse_retry", _partner)

    settled = await orchestrator._run_conversation_kontrola_round(db_session, state)

    assert settled.status == "awaiting_manazer"
    assert settled.current_stage == "priprava"  # re-opened, NOT signed to done
    assert _signoff(db_session, version.id) is None


@pytest.mark.asyncio
async def test_normal_skontrolovat_green_is_unchanged(db_session, monkeypatch) -> None:
    """Regression guard: a NORMAL ``skontrolovat`` (no auto_hotovo marker) on a GREEN floor still settles
    ``awaiting_manazer`` (the manager reviews + clicks Hotovo) — the auto-sign branch must be inert without
    the flag, so the primary conversation flow is byte-for-byte unchanged."""
    version, state = _seed(db_session)
    _record_check_marker(db_session, version.id, auto_hotovo=False)
    _stub_git(monkeypatch)

    async def _green_smoke(_slug, _label, _coverage):
        return (True, "boot ok"), (True, "acceptance ok", False)

    async def _partner(*_a, **_kw):
        return types.SimpleNamespace(kind="gate_report")

    monkeypatch.setattr(orchestrator, "_run_release_smoke", _green_smoke)
    monkeypatch.setattr(orchestrator, "invoke_agent_with_parse_retry", _partner)

    settled = await orchestrator._run_conversation_kontrola_round(db_session, state)

    assert settled.status == "awaiting_manazer"
    assert settled.current_stage == "priprava"
    assert _signoff(db_session, version.id) is None  # NOT auto-signed — the manager still signs Hotovo


@pytest.mark.asyncio
async def test_overit_znovu_on_hotovo_drift_reopens_with_auto_flag(db_session, monkeypatch) -> None:
    """The ``overit_znovu`` handler on a ``hotovo_drift`` conversation build re-opens the self-check: it moves
    to ``priprava`` and records a durable check marker flagged ``auto_hotovo`` (the round then auto-signs)."""
    version, state = _seed(db_session, stage="done", status="done")
    monkeypatch.setattr(orchestrator, "version_verified", lambda *a, **k: (False, "hotovo_drift"))

    await orchestrator.apply_action(db_session, version_id=version.id, action="overit_znovu")

    db_session.refresh(state)
    assert state.current_stage == "priprava"  # re-opened into the conversation register
    marker = db_session.execute(
        select(PipelineMessage)
        .where(
            PipelineMessage.version_id == version.id,
            PipelineMessage.kind == "directive",
            PipelineMessage.payload["check"].astext == "true",
        )
        .order_by(PipelineMessage.seq.desc())
        .limit(1)
    ).scalar_one()
    assert marker.payload.get("auto_hotovo") is True
