"""Tests for the async dispatch background runner (CR-NS-018 fix-round).

``pipeline_runner._run`` owns its own session + broadcasting. Here we drive it
with a monkeypatched ``orchestrator.run_dispatch`` and a fake WS registry, and
neutralise the session-lifecycle calls (``close``/``commit``/``rollback``) that
don't translate to the SAVEPOINT-isolated test session — what's under test is
the runner's *logic*: new-message diff broadcasting + the unexpected-exception →
``blocked`` fallback.
"""

import json
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
        self.away: bool = False  # E6 (CR-NS-038): when True, a present Director is NOT active

    async def broadcast(self, vid, payload):
        self.events.append((vid, payload))

    def present_director_ids(self, vid):
        return self.present

    def active_director_ids(self, vid):
        # present AND not away (E6) — what the notify gate reads
        return set() if self.away else self.present

    def away_director_ids(self, vid):
        # Class J (CR-NS-074): user ids with an away socket — the nudge recipients. The fake models
        # away as a single bool over all present sockets, so away → every present id, else empty.
        return self.present if self.away else set()


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


async def test_run_broadcasts_state_and_incremental_messages(db_session, monkeypatch):
    """CR-NS-018: each message is broadcast via on_message the moment it's recorded
    (incremental), not batched at round end; plus the settled state_changed."""
    version = _make_version(db_session)
    _seed_working_state(db_session, version.id)
    fake_reg = _wire_runner(db_session, monkeypatch)

    async def fake_run_dispatch(db, vid, on_event=None, directive=None, gate_e_dispatch=None, on_message=None):
        for author in ("coordinator", "designer"):
            msg = orchestrator._record_message(
                db, version_id=vid, stage="kickoff", author=author, recipient="director", kind="kickoff", content="x"
            )
            await on_message(msg)  # incremental broadcast, mid-dispatch (before the settle)
        st = db.execute(select(PipelineState).where(PipelineState.version_id == vid)).scalar_one()
        st.status = "awaiting_director"
        st.next_action = "Director: posúdiť."
        db.flush()
        return st

    monkeypatch.setattr(orchestrator, "run_dispatch", fake_run_dispatch)

    await pipeline_runner._run(version.id)

    kinds = [p["type"] for _, p in fake_reg.events]
    # incremental: BOTH message_added frames arrive DURING the dispatch, before the
    # end-of-run state_changed — not batched after it.
    assert kinds == ["message_added", "message_added", "state_changed"]
    added = [p["message"] for _, p in fake_reg.events if p["type"] == "message_added"]
    assert [m["author"] for m in added] == ["coordinator", "designer"]
    state_evt = next(p for _, p in fake_reg.events if p["type"] == "state_changed")
    assert state_evt["state"]["status"] == "awaiting_director"


async def test_run_fast_fix_auto_chain_continues_until_settled(db_session, monkeypatch):
    """CR-NS-097: run_dispatch returning agent_working (a fast_fix auto-advance) makes _run CONTINUE the
    chain in the SAME task — broadcasting each intermediate state — until it settles awaiting_director.
    Models the one-touch flow: kickoff→build (agent_working) → build→release (agent_working) → release
    settles (awaiting_director). The Director's only touch is the later uat_accept."""
    version = _make_version(db_session)
    state = _seed_working_state(db_session, version.id)
    state.flow_type = "fast_fix"
    db_session.flush()
    fake_reg = _wire_runner(db_session, monkeypatch)

    steps = iter([("build", "agent_working"), ("release", "agent_working"), ("release", "awaiting_director")])

    async def fake_run_dispatch(db, vid, on_event=None, directive=None, gate_e_dispatch=None, on_message=None):
        st = db.execute(select(PipelineState).where(PipelineState.version_id == vid)).scalar_one()
        st.current_stage, st.status = next(steps)
        db.flush()
        return st

    monkeypatch.setattr(orchestrator, "run_dispatch", fake_run_dispatch)

    await pipeline_runner._run(version.id)

    settled = db_session.execute(select(PipelineState).where(PipelineState.version_id == version.id)).scalar_one()
    assert settled.current_stage == "release" and settled.status == "awaiting_director"
    # one state_changed per advance (build, release) + the final settle — the board steps live.
    state_evts = [p["state"] for _, p in fake_reg.events if p["type"] == "state_changed"]
    assert [s["current_stage"] for s in state_evts] == ["build", "release", "release"]
    assert [s["status"] for s in state_evts] == ["agent_working", "agent_working", "awaiting_director"]


async def test_run_new_version_does_not_auto_chain(db_session, monkeypatch):
    """Regression: a settled (awaiting_director) run_dispatch result never triggers the auto-chain loop —
    exactly one dispatch, one state_changed (new_version/cr/bug are unaffected by CR-NS-097)."""
    version = _make_version(db_session)
    _seed_working_state(db_session, version.id)  # flow_type=new_version
    fake_reg = _wire_runner(db_session, monkeypatch)
    calls = {"n": 0}

    async def fake_run_dispatch(db, vid, on_event=None, directive=None, gate_e_dispatch=None, on_message=None):
        calls["n"] += 1
        st = db.execute(select(PipelineState).where(PipelineState.version_id == vid)).scalar_one()
        st.status = "awaiting_director"
        db.flush()
        return st

    monkeypatch.setattr(orchestrator, "run_dispatch", fake_run_dispatch)

    await pipeline_runner._run(version.id)

    assert calls["n"] == 1  # no continuation loop
    assert len([p for _, p in fake_reg.events if p["type"] == "state_changed"]) == 1


async def test_run_unexpected_exception_marks_blocked(db_session, monkeypatch):
    version = _make_version(db_session)
    _seed_working_state(db_session, version.id)
    fake_reg = _wire_runner(db_session, monkeypatch)

    async def boom(db, vid, on_event=None, directive=None, gate_e_dispatch=None, on_message=None):
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


async def _settle_awaiting(db, vid, on_event=None, directive=None, gate_e_dispatch=None, on_message=None):
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


async def test_notify_fires_when_director_present_but_away(db_session, monkeypatch):
    # E6 (CR-NS-038) + Class J (CR-NS-074): board open but the Director marked "away" →
    # active_director_ids is empty → the nudge fires to the AWAY Director's OWN chat (777),
    # NOT the project owner (111). The away-target is the primary recipient; owner is fallback only.
    version = _make_version(db_session)
    _seed_working_state(db_session, version.id)
    _set_owner_with_chat(db_session, version, chat_id="111")  # a DIFFERENT owner chat
    away_user = User(
        username=f"away_{uuid.uuid4().hex[:8]}",
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        password_hash="hashed_password_placeholder",
        role="ri",
        telegram_chat_id="777",
    )
    db_session.add(away_user)
    db_session.flush()
    fake_reg = _wire_runner(db_session, monkeypatch)
    fake_reg.present = {away_user.id}  # board socket open …
    fake_reg.away = True  # … but stepped away
    monkeypatch.setattr(orchestrator, "run_dispatch", _settle_awaiting)
    sent = _capture_sends(monkeypatch)

    await pipeline_runner._run(version.id)

    assert len(sent) == 1 and sent[0][1] == "777"  # the away Director, not the owner


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

    async def fake_run_dispatch(db, vid, on_event=None, directive=None, gate_e_dispatch=None, on_message=None):
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


# ── incremental per-message broadcast (CR-NS-018) ──────────────────────────────


def _status_block(stage, kind="gate_report", summary="ok", awaiting="director", **extra):
    body = {"stage": stage, "kind": kind, "summary": summary, "awaiting": awaiting}
    body.update(extra)
    return f"<<<PIPELINE_STATUS>>>\n{json.dumps(body)}\n<<<END_PIPELINE_STATUS>>>"


class _SeqClaude:
    """invoke_claude stand-in returning a fixed sequence of stdout (last repeats)."""

    def __init__(self, responses):
        self.responses = responses
        self.n = 0

    async def __call__(self, *, prompt, **kwargs):
        r = self.responses[min(self.n, len(self.responses) - 1)]
        self.n += 1
        return r


def _seed_state(db_session, version_id, stage, actor):
    state = PipelineState(
        version_id=version_id,
        flow_type="new_version",
        current_stage=stage,
        current_actor=actor,
        status="agent_working",
        next_action="working",
    )
    db_session.add(state)
    db_session.flush()
    return state


async def test_run_streams_dispatch_messages_in_order_with_parity(db_session, monkeypatch):
    """A real gate_a dispatch (designer report → coordinator verify judgment) broadcasts
    one message_added per turn, in order, AND live frame count == committed dispatch rows
    (the parity check that guards the dropped end-batch from silently losing a message)."""
    version = _make_version(db_session)
    _seed_state(db_session, version.id, "gate_a", "designer")
    fake_reg = _wire_runner(db_session, monkeypatch)
    seq = _SeqClaude(
        [
            _status_block("gate_a", "gate_report", summary="14 endpoints"),  # designer report (worker)
            _status_block("gate_a", "gate_report", summary="verify ok"),  # coordinator verify judgment
            _status_block("gate_a", "done", summary="gate_a prešla — schváľ"),  # coordinator synthesis (CR-NS-053)
        ]
    )
    monkeypatch.setattr(orchestrator, "invoke_claude", seq)
    monkeypatch.setattr(orchestrator, "verify_mechanical", lambda slug, block: None)

    await pipeline_runner._run(version.id)

    added = [p["message"] for _, p in fake_reg.events if p["type"] == "message_added"]
    # designer report → coordinator verify judgment → coordinator synthesis (CR-NS-053 §A.2 site 1)
    assert [m["author"] for m in added] == ["designer", "coordinator", "coordinator"]  # incremental, in order
    rows = db_session.execute(select(PipelineMessage).where(PipelineMessage.version_id == version.id)).scalars().all()
    assert len(added) == len(rows)  # parity — no dispatch message dropped
    ids = [m["id"] for m in added]
    assert len(ids) == len(set(ids))  # no duplicate message_added
    seqs = [m["seq"] for m in added]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)  # authoritative monotonic order


async def test_run_gate_e_round_streams_every_turn(db_session, monkeypatch):
    """Coverage guard: a real gate_e round (Customer question → Designer answer) streams
    on_message for EVERY turn — proves on_message reaches the gate_e customer + designer
    invoke sites (so dropping the end batch loses nothing on this path)."""
    version = _make_version(db_session)
    _seed_state(db_session, version.id, "gate_e", "customer")
    fake_reg = _wire_runner(db_session, monkeypatch)
    seq = _SeqClaude(
        [
            _status_block("gate_e", "question", summary="?", question="Je reset hesla pokrytý?"),
            _status_block("gate_e", "answer", summary="áno, §4.2", awaiting="none"),
        ]
    )
    monkeypatch.setattr(orchestrator, "invoke_claude", seq)
    monkeypatch.setattr(orchestrator, "verify_mechanical", lambda slug, block: None)

    await pipeline_runner._run(version.id)

    added = [p["message"] for _, p in fake_reg.events if p["type"] == "message_added"]
    assert [m["author"] for m in added] == ["customer", "designer"]  # both turns streamed live
    rows = db_session.execute(select(PipelineMessage).where(PipelineMessage.version_id == version.id)).scalars().all()
    assert len(added) == len(rows)  # parity on the gate_e path too


async def test_on_message_commits_once_per_message(db_session, monkeypatch):
    """Commit cadence spy (NOT a durability claim under the SAVEPOINT harness): db.commit
    fires once per on_message + the final settle commit."""
    version = _make_version(db_session)
    _seed_working_state(db_session, version.id)
    _wire_runner(db_session, monkeypatch)  # maps commit→flush
    commits = {"n": 0}
    flush = db_session.flush

    def _counting_commit():
        commits["n"] += 1
        return flush()

    monkeypatch.setattr(db_session, "commit", _counting_commit)

    async def fake_run_dispatch(db, vid, on_event=None, directive=None, gate_e_dispatch=None, on_message=None):
        for author in ("coordinator", "designer"):
            msg = orchestrator._record_message(
                db, version_id=vid, stage="kickoff", author=author, recipient="director", kind="kickoff", content="x"
            )
            await on_message(msg)
        st = db.execute(select(PipelineState).where(PipelineState.version_id == vid)).scalar_one()
        st.status = "awaiting_director"
        db.flush()
        return st

    monkeypatch.setattr(orchestrator, "run_dispatch", fake_run_dispatch)

    await pipeline_runner._run(version.id)

    assert commits["n"] == 3  # 2 on_message commits + 1 final settle commit


async def test_crash_after_message_keeps_it_and_settles_blocked(db_session, monkeypatch):
    """Incremental persistence (the bonus): a mid-round crash AFTER a message streamed
    leaves that message persisted, settles blocked, and broadcasts the blocked message."""
    version = _make_version(db_session)
    _seed_working_state(db_session, version.id)
    fake_reg = _wire_runner(db_session, monkeypatch)

    async def fake_run_dispatch(db, vid, on_event=None, directive=None, gate_e_dispatch=None, on_message=None):
        msg = orchestrator._record_message(
            db,
            version_id=vid,
            stage="kickoff",
            author="coordinator",
            recipient="director",
            kind="kickoff",
            content="partial",
        )
        await on_message(msg)  # streamed + committed before the crash
        raise RuntimeError("boom mid-round")

    monkeypatch.setattr(orchestrator, "run_dispatch", fake_run_dispatch)

    await pipeline_runner._run(version.id)

    state = db_session.execute(select(PipelineState).where(PipelineState.version_id == version.id)).scalar_one()
    assert state.status == "blocked"  # always-settle, never agent_working
    msgs = db_session.execute(select(PipelineMessage).where(PipelineMessage.version_id == version.id)).scalars().all()
    assert any(m.author == "coordinator" and m.content == "partial" for m in msgs)  # pre-crash message kept
    assert any(m.author == "system" for m in msgs)  # blocked notification recorded
    added = [p["message"] for _, p in fake_reg.events if p["type"] == "message_added"]
    assert any(m["author"] == "coordinator" for m in added)  # streamed incrementally
    assert any(m["author"] == "system" for m in added)  # crash-path blocked message broadcast once


# ── single-flight per version (CR-NS-027 Part 0) ──────────────────────────────


async def test_schedule_dispatch_single_flight_per_version(monkeypatch):
    """CR-NS-027: while one _run is in-flight for a version, a 2nd schedule_dispatch for the SAME
    version is skipped — never a 2nd concurrent loop (the incident: two tasks building on one
    baseline). The per-version entry clears when the task completes.

    Relies on schedule_dispatch being SYNCHRONOUS (it does the check-and-set + create_task before
    returning, so the entry is written before our first ``sleep(0)``); that synchrony is also what
    makes the guard race-free on the single-threaded event loop."""
    import asyncio

    vid = uuid.uuid4()
    started = {"n": 0}
    gate = asyncio.Event()

    async def _fake_run(version_id, directive=None, gate_e_dispatch=None):
        started["n"] += 1
        await gate.wait()  # hold the task in-flight

    monkeypatch.setattr(pipeline_runner, "_run", _fake_run)
    pipeline_runner._ACTIVE_DISPATCH.pop(vid, None)  # clean slate (module-global)

    pipeline_runner.schedule_dispatch(vid)
    await asyncio.sleep(0)  # let the first task start + await the gate
    task = pipeline_runner._ACTIVE_DISPATCH.get(vid)
    assert task is not None and started["n"] == 1

    pipeline_runner.schedule_dispatch(vid)  # duplicate while in-flight
    await asyncio.sleep(0)
    assert started["n"] == 1  # the 2nd call was skipped — no 2nd loop
    assert pipeline_runner._ACTIVE_DISPATCH.get(vid) is task  # still the same in-flight task

    gate.set()
    await task
    await asyncio.sleep(0)  # drain the done-callbacks
    assert vid not in pipeline_runner._ACTIVE_DISPATCH  # cleared on completion → a fresh dispatch is allowed
