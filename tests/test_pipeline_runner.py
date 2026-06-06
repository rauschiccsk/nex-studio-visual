"""Tests for the async dispatch background runner (CR-NS-018 fix-round).

``pipeline_runner._run`` owns its own session + broadcasting. Here we drive it
with a monkeypatched ``orchestrator.run_dispatch`` and a fake WS registry, and
neutralise the session-lifecycle calls (``close``/``commit``/``rollback``) that
don't translate to the SAVEPOINT-isolated test session — what's under test is
the runner's *logic*: new-message diff broadcasting + the unexpected-exception →
``blocked`` fallback.
"""

import uuid

from sqlalchemy import select

from backend.db.models.foundation import User
from backend.db.models.pipeline import PipelineMessage, PipelineState
from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.services import claude_agent, notify, orchestrator, pipeline_runner


class _FakeRegistry:
    def __init__(self):
        self.events: list = []
        self.present: set = set()

    async def broadcast(self, vid, payload):
        self.events.append((vid, payload))

    def present_director_ids(self, vid):
        return self.present


def _make_version(db_session) -> Version:
    user = User(
        username=f"u_{uuid.uuid4().hex[:8]}",
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        password_hash="hashed_password_placeholder",
        role="ri",
    )
    db_session.add(user)
    db_session.flush()
    project = Project(
        name=f"P {uuid.uuid4().hex[:8]}",
        slug=f"p-{uuid.uuid4().hex[:8]}",
        category="singlemodule",
        description="d",
        created_by=user.id,
    )
    db_session.add(project)
    db_session.flush()
    version = Version(project_id=project.id, version_number=f"1.{uuid.uuid4().hex[:4]}.0")
    db_session.add(version)
    db_session.flush()
    return version


def _seed_working_state(db_session, version_id) -> PipelineState:
    state = PipelineState(
        version_id=version_id,
        flow_type="new_version",
        current_stage="kickoff",
        current_actor="coordinator",
        status="agent_working",
        next_action="working",
    )
    db_session.add(state)
    db_session.add(
        PipelineMessage(
            version_id=version_id,
            stage="kickoff",
            author="director",
            recipient="coordinator",
            kind="kickoff",
            content="start",
        )
    )
    db_session.flush()
    return state


def _wire_runner(db_session, monkeypatch) -> _FakeRegistry:
    fake_reg = _FakeRegistry()
    monkeypatch.setattr(pipeline_runner, "registry", fake_reg)
    monkeypatch.setattr(pipeline_runner, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)
    monkeypatch.setattr(db_session, "commit", db_session.flush)
    monkeypatch.setattr(db_session, "rollback", lambda: None)
    return fake_reg


def _set_owner_with_chat(db_session, version, chat_id="555000111"):
    """Give the version's project an owner that has a telegram_chat_id."""
    owner = User(
        username=f"own_{uuid.uuid4().hex[:8]}",
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        password_hash="hashed_password_placeholder",
        role="ri",
        telegram_chat_id=chat_id,
    )
    db_session.add(owner)
    db_session.flush()
    project = db_session.get(Project, db_session.get(Version, version.id).project_id)
    project.owner_id = owner.id
    db_session.flush()
    return owner


async def test_run_broadcasts_state_and_only_new_messages(db_session, monkeypatch):
    version = _make_version(db_session)
    _seed_working_state(db_session, version.id)
    fake_reg = _wire_runner(db_session, monkeypatch)

    async def fake_run_dispatch(db, vid, on_event=None, directive=None, gate_e_dispatch=None):
        st = db.execute(select(PipelineState).where(PipelineState.version_id == vid)).scalar_one()
        st.status = "awaiting_director"
        st.next_action = "Director: posúdiť."
        db.add(
            PipelineMessage(
                version_id=vid,
                stage="kickoff",
                author="coordinator",
                recipient="director",
                kind="kickoff",
                content="discovery done",
            )
        )
        db.flush()
        return st

    monkeypatch.setattr(orchestrator, "run_dispatch", fake_run_dispatch)

    await pipeline_runner._run(version.id)

    types = [p["type"] for _, p in fake_reg.events]
    assert "state_changed" in types
    state_evt = next(p for _, p in fake_reg.events if p["type"] == "state_changed")
    assert state_evt["state"]["status"] == "awaiting_director"
    # Only the NEW coordinator message is broadcast — the pre-existing director
    # kickoff message is not re-emitted.
    added = [p["message"] for _, p in fake_reg.events if p["type"] == "message_added"]
    assert len(added) == 1
    assert added[0]["author"] == "coordinator"


async def test_run_unexpected_exception_marks_blocked(db_session, monkeypatch):
    version = _make_version(db_session)
    _seed_working_state(db_session, version.id)
    fake_reg = _wire_runner(db_session, monkeypatch)

    async def boom(db, vid, on_event=None, directive=None, gate_e_dispatch=None):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(orchestrator, "run_dispatch", boom)

    await pipeline_runner._run(version.id)

    state = db_session.execute(select(PipelineState).where(PipelineState.version_id == version.id)).scalar_one()
    assert state.status == "blocked"
    # a system notification was recorded + a state_changed broadcast emitted
    sys_msgs = (
        db_session.execute(
            select(PipelineMessage).where(
                PipelineMessage.version_id == version.id,
                PipelineMessage.author == "system",
            )
        )
        .scalars()
        .all()
    )
    assert len(sys_msgs) == 1
    assert any(p["type"] == "state_changed" for _, p in fake_reg.events)


# ── presence-aware Telegram notify (CR-NS-018 Phase 5a) ───────────────────────


async def _settle_awaiting(db, vid, on_event=None, directive=None, gate_e_dispatch=None):
    st = db.execute(select(PipelineState).where(PipelineState.version_id == vid)).scalar_one()
    st.status = "awaiting_director"
    st.next_action = "Director: posúdiť fázu."
    db.flush()
    return st


def _capture_sends(monkeypatch):
    sent: list = []

    async def _send(message, chat_id):
        sent.append((message, chat_id))

    monkeypatch.setattr(notify, "send_telegram", _send)
    return sent


async def test_notify_fires_on_awaiting_when_no_presence(db_session, monkeypatch):
    version = _make_version(db_session)
    _seed_working_state(db_session, version.id)
    _set_owner_with_chat(db_session, version, chat_id="999")
    _wire_runner(db_session, monkeypatch)
    monkeypatch.setattr(orchestrator, "run_dispatch", _settle_awaiting)
    sent = _capture_sends(monkeypatch)

    await pipeline_runner._run(version.id)

    assert len(sent) == 1
    msg, chat = sent[0]
    assert chat == "999"
    assert "na rade" in msg
    # generic nudge — no machine tokens (actor/stage codes) leak into human text
    for token in ("coordinator", "kickoff", "gate_a", "current_actor", "agent_working"):
        assert token not in msg


async def test_notify_suppressed_when_director_present(db_session, monkeypatch):
    version = _make_version(db_session)
    _seed_working_state(db_session, version.id)
    _set_owner_with_chat(db_session, version, chat_id="999")
    fake_reg = _wire_runner(db_session, monkeypatch)
    fake_reg.present = {uuid.uuid4()}  # a Director has a live board socket
    monkeypatch.setattr(orchestrator, "run_dispatch", _settle_awaiting)
    sent = _capture_sends(monkeypatch)

    await pipeline_runner._run(version.id)

    assert sent == []


async def test_notify_noop_when_no_chat_id(db_session, monkeypatch):
    version = _make_version(db_session)  # project owner_id stays None
    _seed_working_state(db_session, version.id)
    _wire_runner(db_session, monkeypatch)
    monkeypatch.setattr(orchestrator, "run_dispatch", _settle_awaiting)
    sent = _capture_sends(monkeypatch)

    await pipeline_runner._run(version.id)

    assert sent == []


# ── live agent activity stream (CR-NS-018) ────────────────────────────────────


async def test_run_streams_agent_activity(db_session, monkeypatch):
    version = _make_version(db_session)
    _seed_working_state(db_session, version.id)  # kickoff / coordinator
    fake_reg = _wire_runner(db_session, monkeypatch)

    async def fake_run_dispatch(db, vid, on_event=None, directive=None, gate_e_dispatch=None):
        # the agent emits a tool event mid-run → runner translates + broadcasts
        await on_event(
            {
                "type": "assistant",
                "message": {"content": [{"type": "tool_use", "name": "Read", "input": {"file_path": "docs/spec.md"}}]},
            }
        )
        st = db.execute(select(PipelineState).where(PipelineState.version_id == vid)).scalar_one()
        st.status = "awaiting_director"
        st.next_action = "ok"
        db.flush()
        return st

    monkeypatch.setattr(orchestrator, "run_dispatch", fake_run_dispatch)

    await pipeline_runner._run(version.id)

    activity = [p for _, p in fake_reg.events if p["type"] == "agent_activity"]
    assert len(activity) == 1
    assert activity[0]["line"] == "číta docs/spec.md"
    assert activity[0]["actor"] == "coordinator"
    assert activity[0]["stage"] == "kickoff"
    assert activity[0]["kind"] == "tool"


# ── robustness: a persistent transient dispatch settles blocked (CR-NS-018) ─────


async def test_run_persistent_transient_settles_blocked(db_session, monkeypatch):
    """The live incident: API 529 throughout a dispatch must end at `blocked`, never
    stay `agent_working`. invoke_claude's bounded retry terminates → invoke_agent →
    ParseFailure → run_dispatch settles blocked."""
    version = _make_version(db_session)
    _seed_working_state(db_session, version.id)  # kickoff / coordinator / agent_working
    _wire_runner(db_session, monkeypatch)

    async def _always_529(**kwargs):
        raise claude_agent.ClaudeAgentError("API Error 529 Overloaded")

    async def _no_sleep(_seconds):
        return None

    monkeypatch.setattr(claude_agent, "_invoke_once", _always_529)
    monkeypatch.setattr(claude_agent.asyncio, "sleep", _no_sleep)
    monkeypatch.setattr(orchestrator, "verify_mechanical", lambda slug, block: None)

    await pipeline_runner._run(version.id)

    state = db_session.execute(select(PipelineState).where(PipelineState.version_id == version.id)).scalar_one()
    assert state.status == "blocked"  # never stuck at agent_working
    assert state.status != "agent_working"


# ── activity frames carry the real role; active_role steps the rail (CR-NS-018) ─


async def test_activity_callback_uses_real_role_and_active_role(db_session, monkeypatch):
    fake_reg = _FakeRegistry()
    monkeypatch.setattr(pipeline_runner, "registry", fake_reg)
    vid = uuid.uuid4()
    cb = pipeline_runner._activity_callback(vid, "gate_e", "customer")

    # a one-shot active-role signal → a frame on the real role (steps the rail)
    await cb({"type": "active_role", "_role": "designer"})
    # a tool event tagged with the real role → frame actor = that role, not fallback
    await cb(
        {
            "type": "assistant",
            "message": {"content": [{"type": "tool_use", "name": "Read", "input": {"file_path": "docs/spec.md"}}]},
            "_role": "coordinator",
        }
    )

    frames = [p for _, p in fake_reg.events if p["type"] == "agent_activity"]
    assert frames[0]["actor"] == "designer" and frames[0]["kind"] == "status"
    assert frames[1]["actor"] == "coordinator" and frames[1]["line"] == "číta docs/spec.md"
