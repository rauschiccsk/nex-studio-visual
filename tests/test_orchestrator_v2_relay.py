"""CR-V2-015 — Promote /debug-terminal to a first-class AI Agent PTY session (SPIKE-IO Model B relay).

SPIKE-IO confirmed **Model B**: the AI Agent is ONE warm, disk-logged, browser-OBSERVABLE ``claude``
session per project that the ENGINE drives via ``invoke_claude(... --resume)`` AND the Manažér
watches/talks to live — with a **SINGLE WRITER**. A Manažér message typed in the (read-only) AI Agent tab
is NOT keystroked into the CLI: it is ENQUEUED and RELAYED by the engine as the next ``--resume`` turn.

These units PROVE the single-writer invariant three ways and assert the durable-log persistence:

1. **The engine marks the session busy for the live CLI write** — :func:`orchestrator.invoke_agent` and the
   task-plan pass wrap their ``invoke_claude`` in :func:`orchestrator._engine_session_active`, so
   :func:`orchestrator.is_session_engine_busy` is True exactly during the turn (and a concurrent
   ``agent_terminal.write_input`` is refused right then), False after.
2. **The PTY layer refuses a concurrent second writer** — :func:`agent_terminal.write_input` raises
   :class:`agent_terminal.WriteRejectedError` while the engine owns the session's ``claude_session_id`` (the
   break-glass debug-attach PTY can never corrupt session memory mid-turn — SPIKE-IO Risk (a)); it proceeds
   when the session is idle.
3. **A relayed Manažér message becomes an engine turn, never a PTY write** — when the build is settled the
   relay dispatches as an ``ask``/``answer`` turn through the sole-mutator ``apply_action``; when a turn is
   in flight the relay is ENQUEUED and the runner drains it as the next ``--resume`` turn (no concurrent
   writer). No relay path ever calls ``write_input``.

Plus: the durable PTY disk log persists across a simulated BE restart (registry cleared → log replay still
returns the pre-restart history, the cross-restart auto-resume safeguard is intact).
"""

from __future__ import annotations

import uuid

import pytest

from backend.db.models.foundation import User
from backend.db.models.orchestrator import OrchestratorSession
from backend.db.models.pipeline import PipelineMessage, PipelineState
from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.services import agent_terminal as terminal_service
from backend.services import orchestrator

# (pytest ``asyncio_mode = auto`` — async tests run without an explicit mark.)


# ── fixtures ────────────────────────────────────────────────────────────────────


def _make_version(db_session, *, build_dial=None):
    user = User(
        username=f"u_{uuid.uuid4().hex[:8]}",
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        password_hash="x",
        role="ri",
    )
    db_session.add(user)
    db_session.flush()
    project = Project(
        name=f"P {uuid.uuid4().hex[:8]}",
        slug=f"p-{uuid.uuid4().hex[:8]}",
        type="standard",
        auth_mode="password",
        description="d",
        created_by=user.id,
        source_path=None,  # library/no-checkout → _begin_dispatch's _repo_head is a graceful no-op
    )
    db_session.add(project)
    db_session.flush()
    version = Version(project_id=project.id, version_number=f"1.{uuid.uuid4().hex[:4]}.0")
    db_session.add(version)
    db_session.flush()
    return version, project


def _seed_state(db_session, version_id, *, stage="programovanie", actor="ai_agent", status="agent_working"):
    state = PipelineState(
        version_id=version_id,
        flow_type="new_version",
        current_stage=stage,
        current_actor=actor,
        status=status,
        next_action="x",
        dispatch_in_flight=(status == "agent_working"),
    )
    db_session.add(state)
    db_session.flush()
    return state


@pytest.fixture(autouse=True)
def _clean_engine_state():
    """Reset the in-process single-writer + relay registries before/after every test (no cross-test leak)."""
    orchestrator._ENGINE_ACTIVE_SESSIONS.clear()
    orchestrator._RELAY_QUEUES.clear()
    terminal_service._sessions.clear()
    yield
    orchestrator._ENGINE_ACTIVE_SESSIONS.clear()
    orchestrator._RELAY_QUEUES.clear()
    terminal_service._sessions.clear()


# ── 1) single-writer registry semantics ─────────────────────────────────────────


class TestEngineBusyRegistry:
    def test_idle_session_is_not_busy(self):
        sid = uuid.uuid4()
        assert orchestrator.is_session_engine_busy(sid) is False

    def test_active_cm_marks_busy_then_clears(self):
        sid = uuid.uuid4()
        with orchestrator._engine_session_active(sid):
            assert orchestrator.is_session_engine_busy(sid) is True
        assert orchestrator.is_session_engine_busy(sid) is False
        # key removed at zero — no leak
        assert sid not in orchestrator._ENGINE_ACTIVE_SESSIONS

    def test_reentrant_nesting_stays_busy_until_outermost_exit(self):
        """A turn spanning parse-retries re-enters invoke_agent → re-enters the CM. The count must keep the
        session busy until the OUTERMOST exit (else a mid-retry window would silently allow a second writer)."""
        sid = uuid.uuid4()
        with orchestrator._engine_session_active(sid):
            with orchestrator._engine_session_active(sid):
                assert orchestrator.is_session_engine_busy(sid) is True
            # inner exited, outer still active → still busy
            assert orchestrator.is_session_engine_busy(sid) is True
        assert orchestrator.is_session_engine_busy(sid) is False

    def test_distinct_sessions_are_independent(self):
        a, b = uuid.uuid4(), uuid.uuid4()
        with orchestrator._engine_session_active(a):
            assert orchestrator.is_session_engine_busy(a) is True
            assert orchestrator.is_session_engine_busy(b) is False


# ── 2) PTY write_input refuses a concurrent second writer ────────────────────────


class TestWriteInputSingleWriterGuard:
    """The chief two-writer proof: ``write_input`` is REFUSED while the engine drives the session's
    ``claude_session_id``; it proceeds when the session is idle. No path writes the PTY stdin concurrently
    with an engine turn."""

    @pytest.mark.asyncio
    async def test_write_refused_while_engine_drives_the_same_claude_session(self, monkeypatch):
        claude_sid = uuid.uuid4()
        runtime_id = uuid.uuid4()
        written: list[bytes] = []

        class _FakeProc:
            def write(self, data):
                written.append(data)

        runtime = terminal_service._RuntimeSession(
            id=runtime_id,
            user_id=uuid.uuid4(),
            role="ai-agent",
            project_slug="p",
            process=_FakeProc(),
            claude_session_id=claude_sid,
        )
        terminal_service._sessions[runtime_id] = runtime

        # Engine is driving claude_sid → the debug-attach PTY write must be REFUSED (no second writer).
        with orchestrator._engine_session_active(claude_sid):
            with pytest.raises(terminal_service.WriteRejectedError):
                await terminal_service.write_input(runtime_id, b"hello")
        # The refused keystroke never reached the PTY.
        assert written == []

    @pytest.mark.asyncio
    async def test_write_proceeds_when_engine_is_idle(self):
        claude_sid = uuid.uuid4()
        runtime_id = uuid.uuid4()
        written: list[bytes] = []

        class _FakeProc:
            def write(self, data):
                written.append(data)

        runtime = terminal_service._RuntimeSession(
            id=runtime_id,
            user_id=uuid.uuid4(),
            role="ai-agent",
            project_slug="p",
            process=_FakeProc(),
            claude_session_id=claude_sid,
        )
        terminal_service._sessions[runtime_id] = runtime

        # Engine NOT driving → the out-of-band break-glass write proceeds exactly as before.
        await terminal_service.write_input(runtime_id, b"hello")
        assert written == [b"hello"]


# ── 3) the engine marks the session busy DURING the live invoke_claude turn ──────


class TestEngineTurnMarksBusy:
    """Proves the busy window is exactly the live CLI write: a write attempt issued FROM INSIDE the
    monkeypatched ``invoke_claude`` (i.e. mid-turn) is refused; after the turn settles it is allowed."""

    @pytest.mark.asyncio
    async def test_invoke_agent_marks_session_busy_during_the_turn(self, db_session, monkeypatch):
        version, project = _make_version(db_session)
        # Pre-mint the (project, ai_agent) orchestrator session so we know the claude_session_id up front.
        claude_sid, _ = orchestrator._resolve_orch_session(db_session, project.slug, orchestrator.AI_AGENT_ROLE)
        db_session.flush()

        observed_busy_mid_turn = {}

        async def _fake_invoke_claude(**kwargs):
            # We are MID-TURN here — the engine must report the session busy so a concurrent write is refused.
            observed_busy_mid_turn["busy"] = orchestrator.is_session_engine_busy(claude_sid)
            return ("no status block", None, None)

        monkeypatch.setattr(orchestrator, "invoke_claude", _fake_invoke_claude)

        result = await orchestrator.invoke_agent(
            db_session,
            version_id=version.id,
            role=orchestrator.AI_AGENT_ROLE,
            stage="programovanie",
            prompt="do something",
        )
        # The turn produced no parseable block (that is fine — we only care about the busy window).
        assert result is not None
        # Mid-turn the session WAS busy …
        assert observed_busy_mid_turn["busy"] is True
        # … and after the turn settled it is released (no leak — the safeguard is window-tight).
        assert orchestrator.is_session_engine_busy(claude_sid) is False

    @pytest.mark.asyncio
    async def test_busy_released_even_when_invoke_claude_raises(self, db_session, monkeypatch):
        version, project = _make_version(db_session)
        claude_sid, _ = orchestrator._resolve_orch_session(db_session, project.slug, orchestrator.AI_AGENT_ROLE)
        db_session.flush()

        async def _boom(**kwargs):
            raise orchestrator.ClaudeAgentError("simulated timeout")

        monkeypatch.setattr(orchestrator, "invoke_claude", _boom)

        result = await orchestrator.invoke_agent(
            db_session,
            version_id=version.id,
            role=orchestrator.AI_AGENT_ROLE,
            stage="programovanie",
            prompt="do something",
        )
        # The failure is handled (ParseFailure), and the busy flag is released by the CM's finally.
        assert result is not None
        assert orchestrator.is_session_engine_busy(claude_sid) is False
        assert claude_sid not in orchestrator._ENGINE_ACTIVE_SESSIONS


# ── 4) relay queue semantics ─────────────────────────────────────────────────────


class TestRelayQueue:
    def test_enqueue_pop_fifo(self):
        vid = uuid.uuid4()
        assert orchestrator.has_pending_relay(vid) is False
        orchestrator._enqueue_relay(vid, "first")
        orchestrator._enqueue_relay(vid, "second")
        assert orchestrator.has_pending_relay(vid) is True
        assert orchestrator.pop_relay_message(vid) == "first"
        assert orchestrator.pop_relay_message(vid) == "second"
        assert orchestrator.pop_relay_message(vid) is None
        assert orchestrator.has_pending_relay(vid) is False
        # drained empty queue is removed (no leak)
        assert vid not in orchestrator._RELAY_QUEUES

    @pytest.mark.asyncio
    async def test_relay_in_flight_is_deferred_and_enqueued_not_dispatched(self, db_session, monkeypatch):
        """A Manažér message arriving WHILE a turn is in flight must ENQUEUE (no concurrent dispatch)."""
        version, _ = _make_version(db_session)
        _seed_state(db_session, version.id, status="agent_working")  # dispatch_in_flight = True

        # apply_action must NOT be reached on the deferred path (else it would be a second concurrent turn).
        async def _explode(*a, **k):  # pragma: no cover - asserts it's never called
            raise AssertionError("relay must not dispatch while a turn is in flight")

        monkeypatch.setattr(orchestrator, "apply_action", _explode)

        res = await orchestrator.relay_manazer_message(db_session, version_id=version.id, text="počas behu")
        assert res.deferred is True
        assert res.action is None
        assert orchestrator.has_pending_relay(version.id) is True
        assert orchestrator.pop_relay_message(version.id) == "počas behu"
        # the Manažér's message is recorded for the audit trail / read-only view
        msgs = db_session.execute(
            PipelineMessage.__table__.select().where(PipelineMessage.version_id == version.id)
        ).all()
        assert any(m.author == "manazer" and m.payload and m.payload.get("relay_queued") for m in msgs)

    @pytest.mark.asyncio
    async def test_relay_settled_dispatches_as_ask(self, db_session, monkeypatch):
        """A relay on a SETTLED build (awaiting) dispatches immediately as an ``ask`` turn (no queue)."""
        version, _ = _make_version(db_session)
        _seed_state(db_session, version.id, status="awaiting_manazer")

        captured = {}

        async def _fake_apply(db, *, version_id, action, payload=None):
            captured["action"] = action
            captured["payload"] = payload
            return orchestrator._get_state(db, version_id)

        monkeypatch.setattr(orchestrator, "apply_action", _fake_apply)

        res = await orchestrator.relay_manazer_message(db_session, version_id=version.id, text="otázka")
        assert res.deferred is False
        assert res.action == "ask"
        assert captured["action"] == "ask"
        assert captured["payload"] == {"text": "otázka"}
        assert orchestrator.has_pending_relay(version.id) is False

    @pytest.mark.asyncio
    async def test_relay_settled_blocked_question_dispatches_as_answer(self, db_session, monkeypatch):
        """When the agent is blocked on its own question, a relay maps to ``answer`` (honours the board flow)."""
        version, _ = _make_version(db_session)
        state = _seed_state(db_session, version.id, status="awaiting_manazer")
        state.status = "blocked"
        state.block_reason = "agent_question"
        db_session.flush()

        captured = {}

        async def _fake_apply(db, *, version_id, action, payload=None):
            captured["action"] = action
            return orchestrator._get_state(db, version_id)

        monkeypatch.setattr(orchestrator, "apply_action", _fake_apply)

        res = await orchestrator.relay_manazer_message(db_session, version_id=version.id, text="odpoveď")
        assert res.action == "answer"
        assert captured["action"] == "answer"

    @pytest.mark.asyncio
    async def test_relay_empty_text_rejected(self, db_session):
        version, _ = _make_version(db_session)
        _seed_state(db_session, version.id, status="awaiting_manazer")
        with pytest.raises(orchestrator.OrchestratorError):
            await orchestrator.relay_manazer_message(db_session, version_id=version.id, text="   ")

    @pytest.mark.asyncio
    async def test_relay_no_pipeline_rejected(self, db_session):
        version, _ = _make_version(db_session)  # no PipelineState
        with pytest.raises(orchestrator.OrchestratorError):
            await orchestrator.relay_manazer_message(db_session, version_id=version.id, text="hi")

    # ICCINT-167: Konzultácia na hotovej verzii je preč — správa tam nemá komu ísť. Odmietne sa ešte pred
    # zápisom, s vetou, ktorá ukáže cestu ďalej (Poradca, nová verzia), namiesto správy bez odpovede.
    @pytest.mark.asyncio
    async def test_relay_on_a_finished_version_is_refused_and_records_nothing(self, db_session):
        version, _ = _make_version(db_session)
        _seed_state(db_session, version.id, stage="done", status="done")
        before = db_session.query(PipelineMessage).filter(PipelineMessage.version_id == version.id).count()
        with pytest.raises(orchestrator.OrchestratorError, match="Poradcovi"):
            await orchestrator.relay_manazer_message(db_session, version_id=version.id, text="Prečo to padá?")
        after = db_session.query(PipelineMessage).filter(PipelineMessage.version_id == version.id).count()
        assert after == before
        state = db_session.query(PipelineState).filter(PipelineState.version_id == version.id).one()
        assert (state.current_stage, state.status) == ("done", "done")

    @pytest.mark.asyncio
    async def test_a_message_queued_as_the_build_finished_is_not_dispatched(self, db_session, monkeypatch):
        version, _ = _make_version(db_session)
        _seed_state(db_session, version.id, stage="done", status="done")
        monkeypatch.setattr(orchestrator, "pop_relay_message", lambda vid: "neskoro")

        async def _never(*a, **k):
            raise AssertionError("na hotovej verzii sa ťah nespúšťa")

        monkeypatch.setattr(orchestrator, "run_dispatch", _never)
        state = await orchestrator.drain_relay_turn(db_session, version.id)
        assert (state.current_stage, state.status) == ("done", "done")


# ── 5) a relayed message becomes an engine --resume turn, never a PTY write ───────


class TestRelayBecomesEngineTurn:
    @pytest.mark.asyncio
    async def test_drain_relay_turn_runs_an_engine_turn_with_the_manazer_message(self, db_session, monkeypatch):
        """``drain_relay_turn`` (called by the runner after a dispatch settles) pops the queued message and
        runs it as a NEXT engine turn via the same run_dispatch path — the message text reaches the agent as
        the prompt, and NO ``write_input`` is ever called (single writer)."""
        version, project = _make_version(db_session)
        _seed_state(db_session, version.id, stage="programovanie", status="awaiting_manazer")
        orchestrator._enqueue_relay(version.id, "skontroluj edge case X")

        seen = {}

        async def _fake_run_dispatch(db, vid, on_event=None, directive=None, *, gate_e_dispatch=None, on_message=None):
            seen["directive"] = directive
            st = orchestrator._get_state(db, vid)
            st.status = "awaiting_manazer"
            st.dispatch_in_flight = False
            db.flush()
            return st

        # If anything tried to write the PTY during a relay, this would fire.
        async def _no_write(*a, **k):  # pragma: no cover
            raise AssertionError("a relay must never write to the PTY (single writer)")

        monkeypatch.setattr(orchestrator, "run_dispatch", _fake_run_dispatch)
        monkeypatch.setattr(terminal_service, "write_input", _no_write)

        out = await orchestrator.drain_relay_turn(db_session, version.id)
        assert out is not None
        # the Manažér's text is threaded into the engine turn prompt (the relay, not keystrokes)
        assert "skontroluj edge case X" in seen["directive"]
        # the queue is drained
        assert orchestrator.has_pending_relay(version.id) is False

    @pytest.mark.asyncio
    async def test_drain_relay_turn_noop_when_empty(self, db_session):
        version, _ = _make_version(db_session)
        _seed_state(db_session, version.id, status="awaiting_manazer")
        assert await orchestrator.drain_relay_turn(db_session, version.id) is None


# ── 6) debug-attach is gated while the engine drives the session ─────────────────


class TestDebugAttachGate:
    """The break-glass ``/debug-terminal`` must not open a write-capable PTY while the engine drives the
    session (a second concurrent writer). The route enforces this via ``is_session_engine_busy`` on the
    resolved ``OrchestratorSession.claude_session_id``; here we assert that exact predicate against a real
    OrchestratorSession row so the route's 409 gate is grounded in real state."""

    def test_engine_busy_predicate_reflects_the_orch_session_uuid(self, db_session):
        version, project = _make_version(db_session)
        claude_sid, _ = orchestrator._resolve_orch_session(db_session, project.slug, orchestrator.AI_AGENT_ROLE)
        db_session.flush()
        orch = db_session.execute(
            OrchestratorSession.__table__.select().where(
                OrchestratorSession.project_slug == project.slug,
                OrchestratorSession.role == orchestrator.AI_AGENT_ROLE,
            )
        ).one()
        # idle → attach would be allowed
        assert orchestrator.is_session_engine_busy(orch.claude_session_id) is False
        # engine driving → the route's gate fires (409)
        with orchestrator._engine_session_active(orch.claude_session_id):
            assert orchestrator.is_session_engine_busy(orch.claude_session_id) is True


# ── 7) durable PTY log persists across a simulated BE restart ────────────────────


class TestDurableLogAcrossRestart:
    def test_disk_log_survives_registry_clear(self, tmp_path, monkeypatch):
        """The durable disk log is the cross-restart audit trail: clearing the in-memory runtime registry
        (== a BE restart) must NOT lose the log — ``_replay_log`` still returns the pre-restart history
        (which ``attach`` replays before auto-resuming via ``--resume``)."""
        monkeypatch.setattr(terminal_service, "TERMINAL_LOG_DIR", tmp_path / "terminal-logs")
        session_id = uuid.uuid4()
        terminal_service._create_log_file(session_id)
        terminal_service._append_chunk_to_log(session_id, b"warm context turn 1\n")
        terminal_service._append_chunk_to_log(session_id, b"warm context turn 2\n")

        # Simulate a BE restart: the in-memory registry is wiped.
        terminal_service._sessions.clear()

        # The durable log is intact → the full visual history is replayable post-restart.
        replayed = b"".join(terminal_service._replay_log(session_id))
        assert b"warm context turn 1" in replayed
        assert b"warm context turn 2" in replayed
