"""Spine STEP 1 — the conversation loop (Chrbtica): a NON-PHASE turn that REPLACES the phase automaton.

Proves the backend spine end-to-end against the real v2 branch DB:

1. **run_conversation_turn SETTLES, never advances a phase** — a normal partner reply → ``awaiting_manazer``;
   a ``ParseFailure`` → ``blocked`` / ``parse_exhaustion`` (with a readable notification); a ``kind=question``
   → ``blocked`` / ``agent_question``. It NEVER calls ``_settle_phase_boundary`` / ``_next_stage`` — the whole
   point of cutting the automaton.
2. **The runner routes by mode** — ``pipeline_runner._run`` dispatches a ``mode='conversation'`` build through
   ``run_conversation_turn`` (no auto-chain) and a phase build through ``run_dispatch``.
3. **The in-flight relay is mode-aware** (the adversarial MAJOR fix) — ``drain_relay_turn`` on a conversation
   build routes the drained Manažér message through ``run_conversation_turn``, NOT the phase automaton, so it
   settles ``awaiting_manazer`` / ``blocked`` and never touches ``_persist_priprava_spec`` /
   ``_settle_phase_boundary``. A settled build's relay still enqueues + records immediately (mode-agnostic).
4. **The start branch is ADDITIVE** — a ``mode='conversation'`` start persists the mode with the SAME build
   shape; a plain new_version start leaves ``mode`` NULL (every existing build/PROD row unaffected). The
   ``/action`` endpoint threads ``mode`` through its generic payload passthrough.
5. **Sole-writer / append-only** — the conversation path records only via ``_record_message`` (single
   constructor site); reads are seq-ordered and existing messages are never mutated.
"""

from __future__ import annotations

import uuid

import bcrypt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.core.security import get_current_user, require_ri_role
from backend.db.models.foundation import User
from backend.db.models.pipeline import PipelineMessage, PipelineState
from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.db.session import get_db
from backend.services import orchestrator, pipeline_runner
from backend.services.pipeline_status import ParseFailure, PipelineStatusBlock

# (pytest ``asyncio_mode = auto`` — async tests run without an explicit mark.)


@pytest.fixture(autouse=True)
def _clean_relay_queue():
    orchestrator._RELAY_QUEUES.clear()
    orchestrator._ENGINE_ACTIVE_SESSIONS.clear()
    yield
    orchestrator._RELAY_QUEUES.clear()
    orchestrator._ENGINE_ACTIVE_SESSIONS.clear()


# ── fixtures ──────────────────────────────────────────────────────────────────


def _make_version(db_session):
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


def _seed_conversation(db_session, version_id, *, status="agent_working"):
    """A spine build: mode='conversation', the SAME shape as a phase build (stage=priprava/actor=ai_agent)."""
    state = PipelineState(
        version_id=version_id,
        flow_type="new_version",
        current_stage="priprava",
        current_actor="ai_agent",
        status=status,
        next_action="rozhovor",
        mode="conversation",
        dispatch_in_flight=(status == "agent_working"),
    )
    db_session.add(state)
    db_session.flush()
    return state


def _seed_phase(db_session, version_id, *, stage="programovanie", status="agent_working"):
    state = PipelineState(
        version_id=version_id,
        flow_type="new_version",
        current_stage=stage,
        current_actor="ai_agent",
        status=status,
        next_action="phase",
        mode=None,  # NULL = phase automaton
        dispatch_in_flight=(status == "agent_working"),
    )
    db_session.add(state)
    db_session.flush()
    return state


def _reply_block(summary="tu je moja odpoveď"):
    return PipelineStatusBlock(stage="priprava", kind="answer", summary=summary, awaiting="manazer")


def _question_block(question="Akú menu má systém používať?"):
    return PipelineStatusBlock(
        stage="priprava", kind="question", summary="mám otázku", awaiting="manazer", question=question
    )


def _stub_turn(monkeypatch, result):
    """Drive ``invoke_agent_with_parse_retry`` from a single scripted result, capturing role/stage/prompt."""
    calls = []

    async def _fake(db, *, version_id, role, stage, prompt, **_kw):
        calls.append({"role": role, "stage": stage, "prompt": prompt})
        return result

    monkeypatch.setattr(orchestrator, "invoke_agent_with_parse_retry", _fake)
    return calls


def _msgs(db_session, version_id):
    return (
        db_session.execute(
            select(PipelineMessage).where(PipelineMessage.version_id == version_id).order_by(PipelineMessage.seq.asc())
        )
        .scalars()
        .all()
    )


# ── 1) run_conversation_turn SETTLES, never advances a phase ────────────────────


class TestConversationTurnSettles:
    async def test_normal_reply_settles_awaiting_manazer(self, db_session, monkeypatch):
        version, _ = _make_version(db_session)
        _seed_conversation(db_session, version.id)
        calls = _stub_turn(monkeypatch, _reply_block())

        out = await orchestrator.run_conversation_turn(db_session, version.id)

        assert out.status == "awaiting_manazer"  # settles for the Manažér
        assert out.current_stage == "priprava"  # NEVER advances a phase
        assert out.mode == "conversation"
        # the turn ran the AI partner at the conversation stage with the phase-free directive
        assert calls[0]["role"] == "ai_agent" and calls[0]["stage"] == "priprava"
        assert "rozhovore" in calls[0]["prompt"].lower()  # the _conversation_directive brief

    async def test_directive_overrides_the_conversation_brief(self, db_session, monkeypatch):
        version, _ = _make_version(db_session)
        _seed_conversation(db_session, version.id)
        calls = _stub_turn(monkeypatch, _reply_block())

        await orchestrator.run_conversation_turn(db_session, version.id, directive="Manažér napísal: sprav X")

        assert calls[0]["prompt"] == "Manažér napísal: sprav X"  # the framed message IS the prompt

    async def test_parse_failure_blocks_with_notification(self, db_session, monkeypatch):
        version, _ = _make_version(db_session)
        _seed_conversation(db_session, version.id)
        _stub_turn(monkeypatch, ParseFailure("no status block", raw="junk output"))

        out = await orchestrator.run_conversation_turn(db_session, version.id)

        assert out.status == "blocked"
        assert out.block_reason == "parse_exhaustion"
        # a readable system→manazer notification was recorded (never an empty screen)
        notes = [m for m in _msgs(db_session, version.id) if m.author == "system" and m.kind == "notification"]
        assert notes and notes[-1].payload.get("parse_failure_reason") == "no status block"

    async def test_question_blocks_agent_question(self, db_session, monkeypatch):
        version, _ = _make_version(db_session)
        _seed_conversation(db_session, version.id)
        _stub_turn(monkeypatch, _question_block("Akú menu?"))

        out = await orchestrator.run_conversation_turn(db_session, version.id)

        assert out.status == "blocked"
        assert out.block_reason == "agent_question"
        assert "Akú menu?" in out.next_action

    async def test_never_calls_phase_settle_machinery(self, db_session, monkeypatch):
        """The spine invariant: a conversation turn NEVER touches the phase automaton's settle/advance."""
        version, _ = _make_version(db_session)
        _seed_conversation(db_session, version.id)
        _stub_turn(monkeypatch, _reply_block())

        def _boom(*a, **k):  # pragma: no cover - asserts they're never called
            raise AssertionError("conversation loop must not touch the phase automaton")

        monkeypatch.setattr(orchestrator, "_settle_phase_boundary", _boom)
        monkeypatch.setattr(orchestrator, "_persist_priprava_spec", _boom)
        monkeypatch.setattr(orchestrator, "_next_stage", _boom)

        out = await orchestrator.run_conversation_turn(db_session, version.id)
        assert out.status == "awaiting_manazer"

    async def test_guard_returns_unchanged_when_not_working(self, db_session, monkeypatch):
        """Mirror run_dispatch's guard: a non-agent_working state has nothing to run — returned untouched."""
        version, _ = _make_version(db_session)
        _seed_conversation(db_session, version.id, status="awaiting_manazer")

        async def _must_not_invoke(*a, **k):  # pragma: no cover
            raise AssertionError("guard must short-circuit before invoking the partner")

        monkeypatch.setattr(orchestrator, "invoke_agent_with_parse_retry", _must_not_invoke)

        out = await orchestrator.run_conversation_turn(db_session, version.id)
        assert out.status == "awaiting_manazer"


# ── 2) sole-writer / append-only on a REAL invoke ───────────────────────────────


class TestConversationSoleWriterAppendOnly:
    async def test_real_invoke_records_via_record_message_seq_ordered(self, db_session, monkeypatch):
        """A real invoke (invoke_claude mocked to a valid structured block) records the partner's message via
        the single ``_record_message`` constructor site, seq-ordered, and settles awaiting_manazer."""
        version, _ = _make_version(db_session)
        _seed_conversation(db_session, version.id)

        async def _fake_claude(**_kw):
            # structured_output primary path → parses to a valid answer block (no fence needed).
            return ("odpoveď", None, {"stage": "priprava", "kind": "answer", "summary": "ahoj", "awaiting": "manazer"})

        monkeypatch.setattr(orchestrator, "invoke_claude", _fake_claude)

        out = await orchestrator.run_conversation_turn(db_session, version.id)

        assert out.status == "awaiting_manazer"
        msgs = _msgs(db_session, version.id)
        agent_msgs = [m for m in msgs if m.author == "ai_agent"]
        assert agent_msgs and agent_msgs[-1].kind == "answer"
        # append-only: strictly increasing seq, all under the valid conversation stage
        assert [m.seq for m in msgs] == sorted(m.seq for m in msgs)
        assert all(m.stage == "priprava" for m in msgs)


# ── 3) the runner routes by mode ────────────────────────────────────────────────


class _FakeRegistry:
    def __init__(self):
        self.events: list = []

    async def broadcast(self, vid, payload):
        self.events.append((vid, payload))

    def active_director_ids(self, vid):
        return {uuid.uuid4()}  # non-empty → _maybe_notify short-circuits (no telegram in these tests)

    def away_director_ids(self, vid):
        return set()


def _wire_runner(db_session, monkeypatch):
    fake_reg = _FakeRegistry()
    monkeypatch.setattr(pipeline_runner, "registry", fake_reg)
    monkeypatch.setattr(pipeline_runner, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)
    monkeypatch.setattr(db_session, "commit", db_session.flush)
    monkeypatch.setattr(db_session, "rollback", lambda: None)
    return fake_reg


class TestRunnerModeBranch:
    async def test_conversation_build_routes_to_conversation_turn(self, db_session, monkeypatch):
        version, _ = _make_version(db_session)
        _seed_conversation(db_session, version.id)
        _wire_runner(db_session, monkeypatch)
        seen = {"conv": 0, "dispatch": 0}

        async def _fake_conv(db, vid, on_event=None, directive=None, *, on_message=None):
            seen["conv"] += 1
            st = orchestrator._get_state(db, vid)
            st.status = "awaiting_manazer"
            db.flush()
            return st

        async def _fake_dispatch(db, vid, on_event=None, directive=None, *, on_message=None):  # pragma: no cover
            seen["dispatch"] += 1
            raise AssertionError("a conversation build must NOT route through run_dispatch")

        monkeypatch.setattr(orchestrator, "run_conversation_turn", _fake_conv)
        monkeypatch.setattr(orchestrator, "run_dispatch", _fake_dispatch)

        await pipeline_runner._run(version.id)
        assert seen == {"conv": 1, "dispatch": 0}  # conversation loop, exactly once (no auto-chain)

    async def test_phase_build_routes_to_run_dispatch(self, db_session, monkeypatch):
        version, _ = _make_version(db_session)
        _seed_phase(db_session, version.id)
        _wire_runner(db_session, monkeypatch)
        seen = {"conv": 0, "dispatch": 0}

        async def _fake_conv(db, vid, on_event=None, directive=None, *, on_message=None):  # pragma: no cover
            seen["conv"] += 1
            raise AssertionError("a phase build must NOT route through run_conversation_turn")

        async def _fake_dispatch(db, vid, on_event=None, directive=None, *, on_message=None):
            seen["dispatch"] += 1
            st = orchestrator._get_state(db, vid)
            st.status = "awaiting_manazer"
            db.flush()
            return st

        monkeypatch.setattr(orchestrator, "run_conversation_turn", _fake_conv)
        monkeypatch.setattr(orchestrator, "run_dispatch", _fake_dispatch)

        await pipeline_runner._run(version.id)
        assert seen == {"conv": 0, "dispatch": 1}


# ── 4) the in-flight relay is mode-aware (adversarial MAJOR fix) ─────────────────


class TestDrainRelayModeAware:
    async def test_drain_on_conversation_routes_to_conversation_turn(self, db_session, monkeypatch):
        version, _ = _make_version(db_session)
        _seed_conversation(db_session, version.id, status="awaiting_manazer")  # settled → drainable
        orchestrator._enqueue_relay(version.id, "skontroluj edge case X")
        seen = {}

        async def _fake_conv(db, vid, on_event=None, directive=None, *, on_message=None):
            seen["directive"] = directive
            st = orchestrator._get_state(db, vid)
            st.status = "awaiting_manazer"
            st.dispatch_in_flight = False
            db.flush()
            return st

        async def _fake_dispatch(*a, **k):  # pragma: no cover
            raise AssertionError("a conversation relay must NOT drain through the phase automaton")

        monkeypatch.setattr(orchestrator, "run_conversation_turn", _fake_conv)
        monkeypatch.setattr(orchestrator, "run_dispatch", _fake_dispatch)

        out = await orchestrator.drain_relay_turn(db_session, version.id)
        assert out is not None
        assert "skontroluj edge case X" in seen["directive"]  # the relayed message reaches the partner
        assert orchestrator.has_pending_relay(version.id) is False

    async def test_drain_on_phase_still_routes_to_run_dispatch(self, db_session, monkeypatch):
        version, _ = _make_version(db_session)
        _seed_phase(db_session, version.id, status="awaiting_manazer")
        orchestrator._enqueue_relay(version.id, "počas behu")
        seen = {"conv": 0, "dispatch": 0}

        async def _fake_conv(*a, **k):  # pragma: no cover
            seen["conv"] += 1
            raise AssertionError("a phase relay must NOT drain through the conversation loop")

        async def _fake_dispatch(db, vid, on_event=None, directive=None, *, on_message=None):
            seen["dispatch"] += 1
            st = orchestrator._get_state(db, vid)
            st.status = "awaiting_manazer"
            db.flush()
            return st

        monkeypatch.setattr(orchestrator, "run_conversation_turn", _fake_conv)
        monkeypatch.setattr(orchestrator, "run_dispatch", _fake_dispatch)

        await orchestrator.drain_relay_turn(db_session, version.id)
        assert seen == {"conv": 0, "dispatch": 1}

    async def test_real_conversation_drain_never_hits_phase_automaton(self, db_session, monkeypatch):
        """End-to-end MAJOR-fix guard: a real drained relay on a conversation build settles awaiting_manazer
        via run_conversation_turn and NEVER touches _persist_priprava_spec / _settle_phase_boundary."""
        version, _ = _make_version(db_session)
        _seed_conversation(db_session, version.id, status="awaiting_manazer")
        orchestrator._enqueue_relay(version.id, "over prosím X")

        async def _fake_claude(**_kw):
            return ("ok", None, {"stage": "priprava", "kind": "answer", "summary": "hotovo", "awaiting": "manazer"})

        def _boom(*a, **k):  # pragma: no cover
            raise AssertionError("conversation drain must not touch the phase automaton")

        monkeypatch.setattr(orchestrator, "invoke_claude", _fake_claude)
        monkeypatch.setattr(orchestrator, "_persist_priprava_spec", _boom)
        monkeypatch.setattr(orchestrator, "_settle_phase_boundary", _boom)

        out = await orchestrator.drain_relay_turn(db_session, version.id)
        assert out.status == "awaiting_manazer"
        assert out.current_stage == "priprava"  # never advanced

    async def test_relay_in_flight_conversation_enqueues_and_records(self, db_session, monkeypatch):
        """(k) a Manažér message during an in-flight conversation turn ENQUEUES (deferred) + is recorded
        immediately — the mode-agnostic single-writer rhythm (no concurrent dispatch)."""
        version, _ = _make_version(db_session)
        _seed_conversation(db_session, version.id, status="agent_working")  # dispatch_in_flight True

        async def _explode(*a, **k):  # pragma: no cover
            raise AssertionError("relay must not dispatch while a turn is in flight")

        monkeypatch.setattr(orchestrator, "apply_action", _explode)

        res = await orchestrator.relay_manazer_message(db_session, version_id=version.id, text="počas rozhovoru")
        assert res.deferred is True
        assert orchestrator.has_pending_relay(version.id) is True
        msgs = _msgs(db_session, version.id)
        assert any(m.author == "manazer" and (m.payload or {}).get("relay_queued") for m in msgs)


# ── 5) the ADDITIVE start branch (service + endpoint) ───────────────────────────


class TestStartModeBranch:
    async def test_conversation_start_persists_mode(self, db_session):
        version, _ = _make_version(db_session)
        state = await orchestrator.apply_action(
            db_session, version_id=version.id, action="start", payload={"mode": "conversation"}
        )
        assert state.mode == "conversation"
        # same build shape as a phase start (only mode differs) — armed for the runner's mode branch
        assert state.current_stage == "priprava" and state.current_actor == "ai_agent"
        assert state.status == "agent_working"
        assert state.flow_type == "new_version"

    async def test_plain_new_version_start_leaves_mode_null(self, db_session):
        """ADDITIVE: an existing new_version start is UNCHANGED — mode stays NULL (phase automaton)."""
        version, _ = _make_version(db_session)
        state = await orchestrator.apply_action(db_session, version_id=version.id, action="start")
        assert state.mode is None
        assert state.current_stage == "priprava" and state.status == "agent_working"

    async def test_unrecognised_mode_degrades_to_null(self, db_session):
        version, _ = _make_version(db_session)
        state = await orchestrator.apply_action(
            db_session, version_id=version.id, action="start", payload={"mode": "banana"}
        )
        assert state.mode is None  # only 'conversation' opts in; anything else = phase


# ── 6) the /action endpoint threads mode through its generic payload passthrough ─


@pytest.fixture()
def client(db_session, monkeypatch):
    from backend.api.routes.pipeline import router as pipeline_router

    async def _fake_claude(**_kw):
        return ""

    monkeypatch.setattr(orchestrator, "invoke_claude", _fake_claude)
    scheduled: list = []
    monkeypatch.setattr(pipeline_runner, "schedule_dispatch", lambda vid, directive=None: scheduled.append(vid))

    app = FastAPI()
    app.include_router(pipeline_router, prefix="/api/v1/pipeline")
    ri = User(
        username=f"u_{uuid.uuid4().hex[:8]}",
        email=f"{uuid.uuid4().hex[:8]}@test.local",
        password_hash=bcrypt.hashpw(b"test", bcrypt.gensalt(rounds=4)).decode(),
        role="ri",
        is_active=True,
    )
    db_session.add(ri)
    db_session.flush()

    def _override_db():
        yield db_session

    def _override_user():
        return ri

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = _override_user
    app.dependency_overrides[require_ri_role] = _override_user
    with TestClient(app) as c:
        c._ri = ri
        c._scheduled = scheduled
        yield c
    app.dependency_overrides.clear()


def test_action_endpoint_threads_mode_into_start(client, db_session):
    """The generic /action passthrough carries ``mode`` into apply_action's start — a conversation build is
    started with mode='conversation' persisted, and the background dispatch (which routes by mode) fires."""
    version, _ = _make_version(db_session)
    r = client.post(
        f"/api/v1/pipeline/{version.id}/action",
        json={"action": "start", "payload": {"mode": "conversation"}},
    )
    assert r.status_code == 200, r.text
    state = db_session.execute(select(PipelineState).where(PipelineState.version_id == version.id)).scalar_one()
    assert state.mode == "conversation"
    assert version.id in client._scheduled
