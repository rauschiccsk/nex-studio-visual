"""Integration tests for the agent → Dedo escalation settle path (Director observation #6).

When the AI Agent emits a ``kind='framework_issue'`` status block (it hit a problem it CANNOT fix because the
fix needs a change to NEX Studio ITSELF, §15), the conversation/phase settle path must:

  * settle the build ``blocked`` / ``block_reason='framework_issue'`` with a "wait for Dedo" next_action;
  * record a readable ``system→manazer`` notification carrying ``payload.framework_issue=True`` + the Dedo
    message (the FE renders it red; delivery-A source);
  * DELIVER the message to Dedo (:func:`dedo_escalation.escalate_to_dedo`, mocked here — the delivery unit
    is tested separately in ``test_dedo_escalation``);
  * offer the Manažér NO recovery actions (``determine_available_actions`` returns EMPTY).

Pinned against the real v2 DB (test DB :9178, SAVEPOINT-isolated ``db_session`` — NEVER PROD). The agent
invocation + the delivery helper are monkeypatched (no ``claude`` subprocess, no channel write / Telegram).
"""

from __future__ import annotations

import uuid as _uuid

import pytest
from sqlalchemy import select

from backend.db.models.foundation import User
from backend.db.models.pipeline import PipelineMessage, PipelineState
from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.services import orchestrator
from backend.services.pipeline_status import PipelineStatusBlock

DEDO_MSG = "Smoke test nevie nabehnúť — build engine potrebuje docker socket mount. Treba upraviť NEX Studio."


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _seed_user(db, *, chat_id: str | None = "555777") -> User:
    u = User(
        username=f"fi_{_uuid.uuid4().hex[:8]}",
        email=f"fi_{_uuid.uuid4().hex[:8]}@test.local",
        password_hash="x",
        role="ri",
        telegram_chat_id=chat_id,
    )
    db.add(u)
    db.flush()
    return u


def _seed_project(db, *, owner: User) -> Project:
    suffix = _uuid.uuid4().hex[:8]
    project = Project(
        name=f"Framework Issue Proj {suffix}",
        slug=f"framework-issue-{suffix}",
        type="standard",
        auth_mode="password",
        description="Director obs #6 framework_issue escalation test project.",
        created_by=owner.id,
        owner_id=owner.id,
    )
    db.add(project)
    db.flush()
    return project


def _seed_conversation_state(db, version: Version) -> PipelineState:
    state = PipelineState(
        version_id=version.id,
        flow_type="new_version",
        current_stage="priprava",
        current_actor="ai_agent",
        status="agent_working",
        mode="conversation",
    )
    db.add(state)
    db.flush()
    return state


def _framework_issue_block() -> PipelineStatusBlock:
    return PipelineStatusBlock(
        stage="priprava",
        kind="framework_issue",
        summary="Eskalujem Dedovi — potrebná zmena NEX Studia.",
        awaiting="manazer",
        question=DEDO_MSG,
    )


def _install_capture(monkeypatch) -> dict:
    """Monkeypatch the delivery helper to a recorder; return the captured-kwargs holder."""
    captured: dict = {}

    async def _fake_escalate(**kwargs):
        captured.update(kwargs)
        return None

    monkeypatch.setattr(orchestrator.dedo_escalation, "escalate_to_dedo", _fake_escalate)
    return captured


# ---------------------------------------------------------------------------
# (i) The conversation spine settle (the v3 primary path)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_conversation_turn_framework_issue_settles_and_delivers(db_session, monkeypatch) -> None:
    owner = _seed_user(db_session, chat_id="555777")
    project = _seed_project(db_session, owner=owner)
    version = Version(project_id=project.id, version_number="2.0.0", status="active")
    db_session.add(version)
    db_session.flush()
    _seed_conversation_state(db_session, version)

    async def _fake_invoke(db, **kw):
        return _framework_issue_block()

    monkeypatch.setattr(orchestrator, "invoke_agent_with_parse_retry", _fake_invoke)
    captured = _install_capture(monkeypatch)

    state = await orchestrator.run_conversation_turn(db_session, version.id)

    # Settled blocked/framework_issue with a PLAIN-Slovak next_action — the manager sees "náš technický tím",
    # never the internal "Dedo" jargon (audit P0, 2026-07-12).
    assert state is not None
    assert state.status == "blocked"
    assert state.block_reason == "framework_issue"
    assert "technický tím" in state.next_action
    assert "Dedo" not in state.next_action

    # A system→manazer notification carries the flag + the Dedo message (the FE renders it red).
    notif = db_session.execute(
        select(PipelineMessage).where(
            PipelineMessage.version_id == version.id,
            PipelineMessage.kind == "notification",
        )
    ).scalar_one()
    assert notif.author == "system"
    assert notif.recipient == "manazer"
    assert notif.payload["framework_issue"] is True
    assert notif.payload["dedo_message"] == DEDO_MSG

    # The escalation was DELIVERED with the project/version/message + the owner's chat_id.
    assert captured["project_slug"] == project.slug
    assert captured["version_number"] == "2.0.0"
    assert captured["dedo_message"] == DEDO_MSG
    assert captured["owner_chat_id"] == "555777"


# ---------------------------------------------------------------------------
# (ii) No recovery actions — the Manažér cannot act on a framework_issue block
# ---------------------------------------------------------------------------


def test_determine_available_actions_empty_for_framework_issue(db_session) -> None:
    owner = _seed_user(db_session)
    project = _seed_project(db_session, owner=owner)
    version = Version(project_id=project.id, version_number="1.0.0", status="active")
    db_session.add(version)
    db_session.flush()
    state = _seed_conversation_state(db_session, version)
    state.status = "blocked"
    state.block_reason = "framework_issue"
    db_session.flush()

    # The Manažér cannot fix a NEX Studio bug (no Uprav / answer / decide) — but instead of a jargon-free
    # DEAD-END (empty set), they get the ONE action they DO have: re-send the report (audit P0, 2026-07-12).
    assert orchestrator.determine_available_actions(state) == {"nahlasit_znova"}

    # Sanity contrast: a plain agent_question block at the SAME (blocked, priprava) state DOES offer the
    # universal recovery actions (ask/uprav/answer). framework_issue is the ONLY blocked reason with none.
    state.block_reason = "agent_question"
    assert {"ask", "uprav", "answer"} <= orchestrator.determine_available_actions(state)


# ---------------------------------------------------------------------------
# (iii) The phase automaton (run_dispatch) settle path is wired too
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_dispatch_framework_issue_settles(db_session, monkeypatch) -> None:
    owner = _seed_user(db_session, chat_id=None)  # no chat_id → delivery still writes the file, skips TG
    project = _seed_project(db_session, owner=owner)
    version = Version(project_id=project.id, version_number="3.0.0", status="active")
    db_session.add(version)
    db_session.flush()
    # A phase-automaton build (mode NULL) in Príprava — the ONLY phase reaching run_dispatch's generic
    # single-turn settle (navrh/programovanie/verifikacia early-return to their own round runners).
    db_session.add(
        PipelineState(
            version_id=version.id,
            flow_type="new_version",
            current_stage="priprava",
            current_actor="ai_agent",
            status="agent_working",
        )
    )
    db_session.flush()

    async def _fake_invoke(db, **kw):
        return PipelineStatusBlock(
            stage="priprava",
            kind="framework_issue",
            summary="Eskalujem Dedovi.",
            awaiting="manazer",
            question=DEDO_MSG,
        )

    monkeypatch.setattr(orchestrator, "invoke_agent_with_parse_retry", _fake_invoke)
    captured = _install_capture(monkeypatch)

    state = await orchestrator.run_dispatch(db_session, version.id)

    assert state.status == "blocked"
    assert state.block_reason == "framework_issue"
    assert captured["project_slug"] == project.slug
    assert captured["owner_chat_id"] is None  # owner had no chat_id
