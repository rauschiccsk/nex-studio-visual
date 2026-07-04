"""Integration tests for the spine STEP 1 conversation COLD-START (the FIRST Manažér message starts the rozhovor).

A freshly-created conversation version has NO ``pipeline_state``, so nothing ever calls
``apply_action('start')`` — the Riadiace-centrum composer would relay straight into "Pipeline not started".
The HOT-FIX makes the Manažér's FIRST message START the conversation: it is routed through ``start`` with
``mode='conversation'`` and carries itself as the ``directive`` (the kickoff). These pin that path against
the real v2 DB (test DB :9178, SAVEPOINT-isolated ``db_session`` — NEVER PROD :9198):

  * **With a directive** → a ``pipeline_state`` is created (``mode='conversation'``, ``current_stage='priprava'``,
    ``status='agent_working'``, partner dispatched), and the kickoff message content == the Manažér's first
    message (``author='manazer'``, ``kind='kickoff'``, ``stage='priprava'``), stamped in the payload too.
  * **With an EMPTY / absent / whitespace directive** → still cold-starts, but with the generic kickoff content
    and NO directive in the payload.
  * **ADDITIVE regression** — a ``start`` WITHOUT ``mode='conversation'`` is byte-identical to before: ``mode``
    NULL (phase automaton), generic kickoff, no directive.
"""

from __future__ import annotations

import uuid as _uuid

import pytest
from sqlalchemy import select

from backend.db.models.foundation import User
from backend.db.models.pipeline import PipelineMessage
from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.services import orchestrator

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _seed_user(db) -> User:
    u = User(
        username=f"cc_{_uuid.uuid4().hex[:8]}",
        email=f"cc_{_uuid.uuid4().hex[:8]}@test.local",
        password_hash="x",
        role="ri",
    )
    db.add(u)
    db.flush()
    return u


def _seed_project(db, *, creator: User) -> Project:
    suffix = _uuid.uuid4().hex[:8]
    project = Project(
        name=f"Cold Start Proj {suffix}",
        slug=f"cold-start-{suffix}",
        type="standard",
        auth_mode="password",
        description="STEP 1 conversation cold-start test project.",
        created_by=creator.id,
        source_path=None,
    )
    db.add(project)
    db.flush()
    return project


def _seed_version(db, project: Project, version_number: str = "2.0.0") -> Version:
    version = Version(project_id=project.id, version_number=version_number, status="active")
    db.add(version)
    db.flush()
    return version


def _kickoff(db, version_id) -> PipelineMessage:
    return db.execute(
        select(PipelineMessage).where(
            PipelineMessage.version_id == version_id,
            PipelineMessage.kind == "kickoff",
        )
    ).scalar_one()


# ---------------------------------------------------------------------------
# (i) With a directive — the FIRST message IS the kickoff, the partner is dispatched
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_conversation_cold_start_first_message_is_kickoff(db_session) -> None:
    creator = _seed_user(db_session)
    project = _seed_project(db_session, creator=creator)
    version = _seed_version(db_session, project)
    # Cold-start precondition: a freshly-created conversation version has NO pipeline_state yet.
    assert orchestrator._get_state(db_session, version.id) is None

    first_message = "Ahoj, chcem appku na evidenciu faktúr. Poď to spolu navrhnúť."
    state = await orchestrator.apply_action(
        db_session,
        version_id=version.id,
        action="start",
        payload={"mode": "conversation", "directive": first_message},
    )

    # A conversation build in Príprava, partner dispatched.
    assert state.mode == "conversation"
    assert state.flow_type == "new_version"  # mode is orthogonal to flow_type — cold-start is not fast_fix
    assert state.current_stage == "priprava"
    assert state.current_actor == "ai_agent"
    assert state.status == "agent_working"
    assert state.dispatch_in_flight is True  # partner dispatched (_begin_dispatch armed the single-flight flag)

    # The kickoff message IS the Manažér's first message — the partner reads it first from the log.
    kickoff = _kickoff(db_session, version.id)
    assert kickoff.author == "manazer"
    assert kickoff.kind == "kickoff"
    assert kickoff.stage == "priprava"
    assert kickoff.content == first_message
    assert kickoff.payload["directive"] == first_message
    assert kickoff.payload["phase"] == "priprava"


# ---------------------------------------------------------------------------
# (ii) Empty / absent / whitespace directive — still cold-starts with the generic kickoff
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({"mode": "conversation", "directive": ""}, id="empty-string"),
        pytest.param({"mode": "conversation"}, id="absent-directive"),
        pytest.param({"mode": "conversation", "directive": "   \n  "}, id="whitespace-only"),
    ],
)
@pytest.mark.asyncio
async def test_conversation_cold_start_without_directive_uses_generic_kickoff(db_session, payload) -> None:
    creator = _seed_user(db_session)
    project = _seed_project(db_session, creator=creator)
    version = _seed_version(db_session, project)

    state = await orchestrator.apply_action(
        db_session,
        version_id=version.id,
        action="start",
        payload=payload,
    )

    # An empty first message still cold-starts the rozhovor — just with the generic kickoff.
    assert state.mode == "conversation"
    assert state.current_stage == "priprava"
    assert state.status == "agent_working"

    kickoff = _kickoff(db_session, version.id)
    assert kickoff.content == "Spustiť tvorbu špecifikácie."
    assert kickoff.author == "manazer"
    assert kickoff.stage == "priprava"
    assert "directive" not in kickoff.payload  # an empty first message stamps NO directive


# ---------------------------------------------------------------------------
# (iii) ADDITIVE regression — a start WITHOUT mode='conversation' stays byte-identical
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_plain_start_without_mode_is_unchanged(db_session) -> None:
    creator = _seed_user(db_session)
    project = _seed_project(db_session, creator=creator)
    version = _seed_version(db_session, project)

    state = await orchestrator.apply_action(
        db_session,
        version_id=version.id,
        action="start",
        payload={},  # legacy new_version: no mode, no directive
    )

    assert state.mode is None  # phase automaton, NOT the conversation loop
    assert state.flow_type == "new_version"
    assert state.current_stage == "priprava"
    assert state.status == "agent_working"

    kickoff = _kickoff(db_session, version.id)
    assert kickoff.content == "Spustiť tvorbu špecifikácie."
    assert "directive" not in kickoff.payload  # a plain new_version stamps no directive
