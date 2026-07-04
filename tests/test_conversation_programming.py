"""STEP 4 — Programovanie in the conversation register (step4-programovanie-design.md).

After the task plan is materialized, "Spustiť stavbu" (``spustit_stavbu``) MOVES a conversation build's
``current_stage`` priprava→programovanie (mode STAYS ``'conversation'``) and dispatches the EXISTING,
UNCHANGED ``_run_build_round`` self-checking loop — routed by STAGE. The completion tail (MD-B) returns the
stage to ``priprava`` (back to the rozhovor) with ONE plain completion notification; kontrola is STEP 5.
Exercised against the real v2 branch DB (4-phase CHECKs). Proves, per the design's verification plan:

* **(a) trigger gating** — offered ONLY when conversation + spec-approved + plan-materialized + NOT
  build-started (state-only offer + board post-filter + authoritative ``apply_action`` guards).
* **(b) routing** — a conversation build at ``programovanie`` routes through ``run_dispatch`` →
  ``_run_build_round``; at ``priprava`` it routes through ``run_conversation_turn`` (STEP-3 invariant holds);
  a legacy (mode NULL) build always routes through ``run_dispatch``.
* **(c) mid-build question** — settles ``blocked`` / ``agent_question``; ``answer`` re-dispatches (the
  EXISTING blocked/answer machinery, no parallel surface).
* **(d) token-stop via the RUNNER** — a conversation build routed through the runner pauses at the task
  boundary when the token cap is crossed.
* **(e) completion** — ``get_next_todo_task`` None → ``current_stage`` back to ``priprava`` +
  ``awaiting_manazer`` + ONE ``programming_complete`` notification, with NO ``_settle_phase_boundary``.
* **(f) drain_relay_turn** — a mid-build conversation relay drains through ``run_dispatch``, not the
  conversation loop.
* **(g) sole-writer / append-only** — the full build records through ``_record_message`` with monotonic seq.
* **MAJOR-1 / MAJOR-2** — the board omits ``schvalit`` (and ``verdict``) for a conversation build;
  ``apply_action('schvalit')`` RAISES for a conversation build (before the legacy stage guard).
* **MINOR-1** — the board omits ``approve_spec`` once a conversation Špecifikácia is approved.
* **MINOR-3** — the ``start_build`` marker is an audit breadcrumb, NOT the trigger: ``dispatch_directive``
  returns ``None`` for ``spustit_stavbu`` (the trigger is the durable stage), and the marker never
  mis-routes as a ``compose_plan`` directive.

Legacy (mode NULL) new_version/fast_fix builds stay BYTE-IDENTICAL: the completion tail's legacy branch still
calls ``_settle_phase_boundary`` and does not return to priprava. ``invoke_agent_with_parse_retry`` /
``verify_mechanical`` / ``_repo_head`` are monkeypatched (no live ``claude`` CLI, no real git).
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from backend.api.routes import pipeline as pipeline_routes
from backend.db.models.foundation import User
from backend.db.models.pipeline import PipelineMessage, PipelineState
from backend.db.models.projects import Project
from backend.db.models.tasks import Epic, Feat, Task
from backend.db.models.versions import Version
from backend.services import orchestrator, pipeline_runner
from backend.services import system_setting as system_setting_service
from backend.services.orchestrator import OrchestratorError
from backend.services.pipeline_status import PipelineStatusBlock

# (pytest ``asyncio_mode = auto`` — async tests run without an explicit mark.)

_TOKEN_KEY = "programovanie_token_stop_millions"


@pytest.fixture(autouse=True)
def _clean_process_state():
    """Clear the process-global relay queues, engine-session set, and the typed-setting cache around every
    test (they survive SAVEPOINT rollback) so nothing leaks between tests."""
    orchestrator._RELAY_QUEUES.clear()
    orchestrator._ENGINE_ACTIVE_SESSIONS.clear()
    system_setting_service._cache.clear()
    yield
    orchestrator._RELAY_QUEUES.clear()
    orchestrator._ENGINE_ACTIVE_SESSIONS.clear()
    system_setting_service._cache.clear()


# ── fixtures ────────────────────────────────────────────────────────────────


def _make_version(db_session, *, source_path=None, project_dial=None):
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
        source_path=source_path,  # None → _repo_head / _write_task_plan_doc are graceful no-ops
        miera_autonomie=project_dial,
    )
    db_session.add(project)
    db_session.flush()
    version = Version(project_id=project.id, version_number=f"1.{uuid.uuid4().hex[:4]}.0")
    db_session.add(version)
    db_session.flush()
    return version, project


def _seed_priprava(db_session, version_id, *, status="awaiting_manazer", mode="conversation"):
    """A settled conversation build in the priprava register (the SAME shape a spine build carries)."""
    state = PipelineState(
        version_id=version_id,
        flow_type="new_version",
        current_stage="priprava",
        current_actor="ai_agent",
        status=status,
        next_action="rozhovor",
        mode=mode,
        dispatch_in_flight=(status == "agent_working"),
    )
    db_session.add(state)
    db_session.flush()
    return state


def _seed_programovanie(db_session, version_id, *, status="agent_working", mode="conversation", build_dial=None):
    """A build parked at the programovanie stage. ``mode='conversation'`` → the STEP-4 path; ``mode=None`` →
    the legacy phase automaton (byte-identity control)."""
    state = PipelineState(
        version_id=version_id,
        flow_type="new_version",
        current_stage="programovanie",
        current_actor="ai_agent",
        status=status,
        next_action="working",
        mode=mode,
        miera_autonomie=build_dial,
        dispatch_in_flight=(status == "agent_working"),
    )
    db_session.add(state)
    db_session.flush()
    return state


def _approve_spec(db_session, version_id):
    """Record the durable kind='approval' Špecifikácia freeze signal (what orchestrator.spec_approved reads)."""
    db_session.add(
        PipelineMessage(
            version_id=version_id,
            stage="priprava",
            author="manazer",
            recipient="ai_agent",
            kind="approval",
            content="Špecifikácia schválená.",
            payload={"phase": "priprava", "approve_spec": True},
        )
    )
    db_session.flush()


def _seed_tasks(db_session, version, project, titles):
    """ONE epic + ONE feat + a Task per title (all ``todo``), returned in plan order."""
    epic = Epic(project_id=project.id, version_id=version.id, number=1, title="Foundation", status="planned")
    db_session.add(epic)
    db_session.flush()
    feat = Feat(epic_id=epic.id, number=1, title="Schema", status="todo")
    db_session.add(feat)
    db_session.flush()
    tasks = []
    for i, title in enumerate(titles, start=1):
        t = Task(feat_id=feat.id, number=i, title=title, task_type="backend", status="todo")
        db_session.add(t)
        tasks.append(t)
    db_session.flush()
    return epic, feat, tasks


def _mark_first_task_started(db_session, version_id):
    """Advance one Task past ``todo`` so ``_build_started`` reads True (a build already in flight)."""
    t = _tasks(db_session, version_id)[0]
    t.status = "in_progress"
    db_session.flush()


def _msgs(db_session, version_id):
    return (
        db_session.execute(
            select(PipelineMessage).where(PipelineMessage.version_id == version_id).order_by(PipelineMessage.seq.asc())
        )
        .scalars()
        .all()
    )


def _tasks(db_session, version_id):
    return (
        db_session.execute(
            select(Task)
            .join(Feat, Feat.id == Task.feat_id)
            .join(Epic, Epic.id == Feat.epic_id)
            .where(Epic.version_id == version_id)
            .order_by(Task.number)
        )
        .scalars()
        .all()
    )


def _board_actions(db_session, version_id):
    return pipeline_routes._board(db_session, version_id).available_actions


def _programming_complete_notes(db_session, version_id):
    return [m for m in _msgs(db_session, version_id) if m.payload and m.payload.get("programming_complete")]


def _start_build_markers(db_session, version_id):
    return [m for m in _msgs(db_session, version_id) if m.payload and m.payload.get("start_build")]


# ── invoke / mechanical-verify / git stubs (mirror test_orchestrator_v2_programovanie) ──


def _done_block(summary="hotovo"):
    return PipelineStatusBlock(
        stage="programovanie", kind="gate_report", summary=summary, awaiting="manazer", commits=["a" * 40]
    )


def _question_block(question="Ktorý provider použiť?"):
    return PipelineStatusBlock(
        stage="programovanie", kind="question", summary="neistota", awaiting="manazer", question=question
    )


def _stub_turns(monkeypatch, blocks):
    """Drive ``invoke_agent_with_parse_retry`` from a scripted list (one per dispatched turn); a short list
    re-uses its last entry. Captures role/stage/prompt of every call."""
    calls = []
    seq = list(blocks)

    async def _fake(db, *, version_id, role, stage, prompt, **_kw):
        calls.append({"role": role, "stage": stage, "prompt": prompt})
        return seq[len(calls) - 1] if len(calls) <= len(seq) else seq[-1]

    monkeypatch.setattr(orchestrator, "invoke_agent_with_parse_retry", _fake)
    return calls


def _stub_mech(monkeypatch, reasons):
    """Drive ``verify_mechanical`` from a scripted list (``None`` = pass, str = fail); a short list re-uses
    its last entry. Decouples the per-task gate from real git."""
    seq = list(reasons)
    state = {"n": 0}

    def _fake(slug, block, baseline_sha=None):
        state["n"] += 1
        return seq[state["n"] - 1] if state["n"] <= len(seq) else seq[-1]

    monkeypatch.setattr(orchestrator, "verify_mechanical", _fake)


def _no_baseline_git(monkeypatch):
    """Deterministic repo-HEAD read (a fixed sha) so the loop captures a baseline without real git."""
    monkeypatch.setattr(orchestrator, "_repo_head", lambda root: "b" * 40)


def _seed_usage(db_session, version_id, *, input_tokens, output_tokens):
    """A metered gate_report so ``aggregate_pipeline_usage`` counts these tokens (the append-only log IS the
    ledger — no separate counter)."""
    return orchestrator._record_message(
        db_session,
        version_id=version_id,
        stage="programovanie",
        author="ai_agent",
        recipient="manazer",
        kind="gate_report",
        content="work",
        payload={
            "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens, "model": "claude-opus-4-8"},
            "timing": {"duration_seconds": 1.0, "parse_attempts": 1},
        },
    )


# ── runner wiring (SAVEPOINT-safe drive of pipeline_runner._run) ──────────────


class _FakeRegistry:
    """Minimal stand-in for the WS registry. ``active_director_ids`` returns a present director so
    ``_maybe_notify`` short-circuits (no telegram path exercised)."""

    def __init__(self):
        self.events: list = []

    async def broadcast(self, vid, payload):
        self.events.append((vid, payload))

    def present_director_ids(self, vid):
        return {"d"}

    def active_director_ids(self, vid):
        return {"d"}  # a director is on-board → _maybe_notify returns early (no out-of-band ping)

    def away_director_ids(self, vid):
        return set()


def _wire_runner(db_session, monkeypatch):
    reg = _FakeRegistry()
    monkeypatch.setattr(pipeline_runner, "registry", reg)
    monkeypatch.setattr(pipeline_runner, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)
    monkeypatch.setattr(db_session, "commit", db_session.flush)
    monkeypatch.setattr(db_session, "rollback", lambda: None)
    return reg


def _stub_routers(monkeypatch):
    """Stub BOTH orchestrator routers to record which the runner/drain selected, then settle awaiting_manazer
    (so no auto-chain / relay drain follows)."""
    routed: list[str] = []

    async def _fake_dispatch(db, vid, on_event=None, directive=None, *, on_message=None):
        routed.append("dispatch")
        st = db.execute(select(PipelineState).where(PipelineState.version_id == vid)).scalar_one()
        st.status = "awaiting_manazer"
        db.flush()
        return st

    async def _fake_conversation(db, vid, on_event=None, directive=None, *, on_message=None):
        routed.append("conversation")
        st = db.execute(select(PipelineState).where(PipelineState.version_id == vid)).scalar_one()
        st.status = "awaiting_manazer"
        db.flush()
        return st

    monkeypatch.setattr(orchestrator, "run_dispatch", _fake_dispatch)
    monkeypatch.setattr(orchestrator, "run_conversation_turn", _fake_conversation)
    return routed


# ── (a) trigger gating: state-only offer + board post-filter + apply_action guards ──


class TestSpustitStavbuGating:
    def test_determine_offers_spustit_stavbu_at_priprava(self, db_session):
        # State-only (like zostav_plan / schvalit) — offered UNCONDITIONALLY at a settled priprava, no DB read.
        version, _ = _make_version(db_session)
        state = _seed_priprava(db_session, version.id)
        assert "spustit_stavbu" in orchestrator.determine_available_actions(state)

    def test_board_offers_only_when_conversation_spec_plan_not_started(self, db_session):
        version, project = _make_version(db_session)
        _seed_priprava(db_session, version.id)
        # not spec-approved, no plan → post-filtered out.
        assert "spustit_stavbu" not in _board_actions(db_session, version.id)
        _approve_spec(db_session, version.id)
        # spec approved but plan NOT materialized → still out.
        assert "spustit_stavbu" not in _board_actions(db_session, version.id)
        _seed_tasks(db_session, version, project, ["T1"])
        # conversation + spec + plan materialized + not started → OFFERED.
        assert "spustit_stavbu" in _board_actions(db_session, version.id)
        # a task moved past todo → build STARTED → out (resume via Pokračovať, not a 2nd Spustiť stavbu).
        _mark_first_task_started(db_session, version.id)
        assert "spustit_stavbu" not in _board_actions(db_session, version.id)

    def test_board_hides_spustit_stavbu_on_legacy_build(self, db_session):
        # mode NULL → determine still offers it (state-only), but the board post-filter drops it (not conversation).
        version, project = _make_version(db_session)
        _seed_priprava(db_session, version.id, mode=None)
        _approve_spec(db_session, version.id)
        _seed_tasks(db_session, version, project, ["T1"])
        assert "spustit_stavbu" not in _board_actions(db_session, version.id)

    async def test_apply_raises_when_not_conversation(self, db_session):
        version, project = _make_version(db_session)
        _seed_priprava(db_session, version.id, mode=None)
        _approve_spec(db_session, version.id)
        _seed_tasks(db_session, version, project, ["T1"])
        with pytest.raises(OrchestratorError, match="rozhovorovom"):
            await orchestrator.apply_action(db_session, version_id=version.id, action="spustit_stavbu")

    async def test_apply_raises_when_spec_not_approved(self, db_session):
        version, project = _make_version(db_session)
        _seed_priprava(db_session, version.id)
        _seed_tasks(db_session, version, project, ["T1"])  # plan but NO spec approval
        with pytest.raises(OrchestratorError, match="schválení Špecifikácie"):
            await orchestrator.apply_action(db_session, version_id=version.id, action="spustit_stavbu")

    async def test_apply_raises_when_plan_not_materialized(self, db_session):
        version, _ = _make_version(db_session)
        _seed_priprava(db_session, version.id)
        _approve_spec(db_session, version.id)  # spec but NO plan
        with pytest.raises(OrchestratorError, match="zostavení plánu"):
            await orchestrator.apply_action(db_session, version_id=version.id, action="spustit_stavbu")

    async def test_apply_raises_when_build_already_started(self, db_session):
        version, project = _make_version(db_session)
        _seed_priprava(db_session, version.id)
        _approve_spec(db_session, version.id)
        _seed_tasks(db_session, version, project, ["T1"])
        _mark_first_task_started(db_session, version.id)  # a task in_progress
        with pytest.raises(OrchestratorError, match="už beží"):
            await orchestrator.apply_action(db_session, version_id=version.id, action="spustit_stavbu")


# ── (a) trigger + MINOR-3: apply_action arms the build + breadcrumb (not the trigger) ──


class TestSpustitStavbuArmsBuild:
    async def test_apply_moves_to_programovanie_arms_working_and_breadcrumb(self, db_session):
        version, project = _make_version(db_session)
        _seed_priprava(db_session, version.id)
        _approve_spec(db_session, version.id)
        _seed_tasks(db_session, version, project, ["T1"])

        state = await orchestrator.apply_action(db_session, version_id=version.id, action="spustit_stavbu")

        assert state.status == "agent_working"  # _begin_dispatch armed the turn
        assert state.current_stage == "programovanie"  # phase MOVED (the durable trigger)
        assert state.mode == "conversation"  # mode UNCHANGED — still a conversation build
        assert state.current_actor == "ai_agent"
        assert state.dispatch_in_flight is True
        marker = _msgs(db_session, version.id)[-1]
        assert marker.kind == "directive" and marker.author == "manazer" and marker.recipient == "ai_agent"
        assert marker.payload.get("start_build") is True and marker.stage == "programovanie"
        # MINOR-3: the in-memory dispatch directive is None for spustit_stavbu (the trigger is the STAGE, not
        # the marker); and the start_build breadcrumb is NOT a compose_plan marker (never mis-routes to the plan).
        assert orchestrator.dispatch_directive(db_session, version.id, "spustit_stavbu", {}, "programovanie") is None
        assert orchestrator._pending_compose_plan_marker(db_session, version.id) is False


# ── (b) routing: mode + stage decide run_dispatch vs run_conversation_turn ────


class TestRunnerRouting:
    async def test_runner_routes_conversation_programovanie_to_run_dispatch(self, db_session, monkeypatch):
        version, _ = _make_version(db_session)
        _seed_programovanie(db_session, version.id)  # conversation + programovanie + working
        _wire_runner(db_session, monkeypatch)
        routed = _stub_routers(monkeypatch)
        await pipeline_runner._run(version.id)
        assert routed == ["dispatch"]  # mid-build conversation → the phase-dispatch path (→ _run_build_round)

    async def test_runner_routes_conversation_priprava_to_conversation_turn(self, db_session, monkeypatch):
        version, _ = _make_version(db_session)
        _seed_priprava(db_session, version.id, status="agent_working")
        _wire_runner(db_session, monkeypatch)
        routed = _stub_routers(monkeypatch)
        await pipeline_runner._run(version.id)
        assert routed == ["conversation"]  # STEP-3 invariant: the priprava register stays the conversation loop

    async def test_runner_routes_legacy_programovanie_to_run_dispatch(self, db_session, monkeypatch):
        version, _ = _make_version(db_session)
        _seed_programovanie(db_session, version.id, mode=None)  # legacy phase automaton
        _wire_runner(db_session, monkeypatch)
        routed = _stub_routers(monkeypatch)
        await pipeline_runner._run(version.id)
        assert routed == ["dispatch"]  # mode NULL always uses run_dispatch (byte-identical)


# ── (c) mid-build question via the EXISTING blocked/answer machinery ──────────


class TestMidBuildQuestion:
    async def test_question_settles_blocked_and_answer_redispatches(self, db_session, monkeypatch):
        version, project = _make_version(db_session)
        _seed_programovanie(db_session, version.id)
        _seed_tasks(db_session, version, project, ["T1"])
        _no_baseline_git(monkeypatch)
        _stub_turns(monkeypatch, [_question_block("Ktorý provider?")])

        state = await orchestrator.run_dispatch(db_session, version.id)  # routes on stage → _run_build_round

        assert state.status == "blocked" and state.block_reason == "agent_question"
        assert state.current_stage == "programovanie"  # still mid-build, not advanced
        assert "Ktorý provider" in state.next_action
        # the board offers ``answer`` (a blocked agent_question), never schvalit.
        actions = _board_actions(db_session, version.id)
        assert "answer" in actions and "schvalit" not in actions

        out = await orchestrator.apply_action(
            db_session, version_id=version.id, action="answer", payload={"text": "claude_code"}
        )
        assert out.status == "agent_working"  # re-dispatched (existing machinery)
        assert out.current_stage == "programovanie"  # answer during the build stays in programovanie


# ── (d) token-stop THROUGH the runner ─────────────────────────────────────────


class TestTokenStopViaRunner:
    async def test_conversation_build_pauses_at_cap_via_runner(self, db_session, monkeypatch):
        version, project = _make_version(db_session)
        _seed_programovanie(db_session, version.id, build_dial="plna")
        _seed_tasks(db_session, version, project, ["T1"])  # a todo task exists behind the cap
        _seed_usage(db_session, version.id, input_tokens=700_000, output_tokens=400_000)  # 1.1M ≥ 1M
        system_setting_service.upsert(db_session, _TOKEN_KEY, "1")
        _wire_runner(db_session, monkeypatch)

        # The token-stop must return BEFORE get_next_todo_task — routed through run_dispatch → _run_build_round.
        def _must_not_reach(db, vid):  # pragma: no cover
            raise AssertionError("token-stop must pause BEFORE get_next_todo_task")

        monkeypatch.setattr(orchestrator.task_service, "get_next_todo_task", _must_not_reach)

        await pipeline_runner._run(version.id)

        state = db_session.execute(select(PipelineState).where(PipelineState.version_id == version.id)).scalar_one()
        assert state.status == "paused"  # the runner routed the conversation build through the build loop
        assert state.current_stage == "programovanie"
        stops = [m for m in _msgs(db_session, version.id) if m.payload and m.payload.get("token_stop")]
        assert len(stops) == 1


# ── (e) completion tail (MD-B): back to priprava, no _settle_phase_boundary ───


class TestCompletionTail:
    async def test_conversation_completion_returns_to_priprava_no_settle(self, db_session, monkeypatch):
        version, project = _make_version(db_session, project_dial="plna")
        _seed_programovanie(db_session, version.id, build_dial="plna")
        _seed_tasks(db_session, version, project, ["T1"])
        _no_baseline_git(monkeypatch)
        _stub_turns(monkeypatch, [_done_block()])
        _stub_mech(monkeypatch, [None])

        # The conversation completion must NEVER touch the phase automaton's dial-settle.
        def _boom(*a, **k):  # pragma: no cover
            raise AssertionError("conversation completion must not call _settle_phase_boundary")

        monkeypatch.setattr(orchestrator, "_settle_phase_boundary", _boom)

        state = await orchestrator.run_dispatch(db_session, version.id)

        assert state.current_stage == "priprava"  # returned to the conversation register
        assert state.status == "awaiting_manazer"
        assert state.mode == "conversation"
        assert all(t.status == "done" for t in _tasks(db_session, version.id))
        # ONE plain completion notification — no verdict, no schvaľovací bod.
        notes = _programming_complete_notes(db_session, version.id)
        assert len(notes) == 1
        assert notes[0].author == "system" and notes[0].recipient == "manazer" and notes[0].kind == "notification"
        assert "rozhovore" in notes[0].content.lower()

    async def test_legacy_completion_still_calls_settle_byte_identical(self, db_session, monkeypatch):
        # Byte-identity control: a legacy (mode NULL) programovanie completion STILL calls the dial-settle and
        # does NOT return to priprava; the po_kazdej_faze / dial-independent gate stops at programovanie.
        version, project = _make_version(db_session, project_dial="po_kazdej_faze")
        _seed_programovanie(db_session, version.id, mode=None)
        _seed_tasks(db_session, version, project, ["T1"])
        _no_baseline_git(monkeypatch)
        _stub_turns(monkeypatch, [_done_block()])
        _stub_mech(monkeypatch, [None])

        called = {"n": 0}
        real_settle = orchestrator._settle_phase_boundary

        def _spy(db, state):
            called["n"] += 1
            return real_settle(db, state)

        monkeypatch.setattr(orchestrator, "_settle_phase_boundary", _spy)

        state = await orchestrator.run_dispatch(db_session, version.id)

        assert called["n"] == 1  # the legacy tail STILL runs the dial-settle
        assert state.current_stage == "programovanie"  # stops here (NOT returned to priprava)
        assert state.status == "awaiting_manazer"
        assert _programming_complete_notes(db_session, version.id) == []  # no conversation completion note


# ── (f) drain_relay_turn: a mid-build relay drains through run_dispatch ───────


class TestDrainRelayMidBuild:
    async def test_drain_relay_mid_build_routes_to_run_dispatch(self, db_session, monkeypatch):
        version, project = _make_version(db_session)
        _seed_programovanie(db_session, version.id, status="agent_working")
        _seed_tasks(db_session, version, project, ["T1"])
        orchestrator._enqueue_relay(version.id, "pokračuj prosím")
        routed = _stub_routers(monkeypatch)

        await orchestrator.drain_relay_turn(db_session, version.id)

        assert routed == ["dispatch"]  # mid-build conversation relay → run_dispatch, NOT the conversation loop

    async def test_drain_relay_priprava_routes_to_conversation_turn(self, db_session, monkeypatch):
        # Control: a conversation build still in the priprava register drains through run_conversation_turn.
        version, _ = _make_version(db_session)
        _seed_priprava(db_session, version.id, status="agent_working")
        orchestrator._enqueue_relay(version.id, "otázka")
        routed = _stub_routers(monkeypatch)

        await orchestrator.drain_relay_turn(db_session, version.id)

        assert routed == ["conversation"]  # priprava register → the conversation loop, not run_dispatch


# ── (g) sole-writer / append-only: the whole build records via _record_message ──


class TestSoleWriterAppendOnly:
    async def test_full_conversation_build_append_only_single_writer(self, db_session, monkeypatch):
        version, project = _make_version(db_session)
        _seed_priprava(db_session, version.id)
        _approve_spec(db_session, version.id)
        _seed_tasks(db_session, version, project, ["T1", "T2"])
        _no_baseline_git(monkeypatch)
        _stub_turns(monkeypatch, [_done_block()])
        _stub_mech(monkeypatch, [None])

        # apply_action is the SOLE mutator of current_stage on the Manažér action.
        st = await orchestrator.apply_action(db_session, version_id=version.id, action="spustit_stavbu")
        assert st.current_stage == "programovanie" and st.status == "agent_working"

        # run the build to completion (routed on stage → _run_build_round; the completion returns to priprava).
        st = await orchestrator.run_dispatch(db_session, version.id)
        assert st.current_stage == "priprava" and st.status == "awaiting_manazer"
        assert all(t.status == "done" for t in _tasks(db_session, version.id))

        msgs = _msgs(db_session, version.id)
        seqs = [m.seq for m in msgs]
        assert seqs == sorted(seqs)  # append-only, monotonic
        assert len(seqs) == len(set(seqs))  # unique — no rewrites
        assert len(_start_build_markers(db_session, version.id)) == 1
        assert len(_programming_complete_notes(db_session, version.id)) == 1


# ── MAJOR / MINOR: schvalit + approve_spec suppressed on the conversation register ──


class TestSchvalitSuppressed:
    def test_board_omits_schvalit_and_verdict_for_conversation(self, db_session):
        # MAJOR-1: the two-layer belt — the board post-filter drops schvalit (and, defensively, verdict).
        version, _ = _make_version(db_session)
        _seed_programovanie(db_session, version.id, status="awaiting_manazer")
        actions = _board_actions(db_session, version.id)
        assert "schvalit" not in actions
        assert "verdict" not in actions

    def test_board_offers_schvalit_for_legacy_build(self, db_session):
        # Control: a legacy (mode NULL) settled programovanie STILL offers schvalit (byte-identical).
        version, _ = _make_version(db_session)
        _seed_programovanie(db_session, version.id, status="awaiting_manazer", mode=None)
        assert "schvalit" in _board_actions(db_session, version.id)

    async def test_apply_schvalit_raises_for_conversation_build(self, db_session):
        # MAJOR-2: apply_action('schvalit') RAISES for a conversation build BEFORE the legacy stage-guard —
        # so a stale board / forged call can never corrupt the conversation build into the Auditor phase.
        version, _ = _make_version(db_session)
        _seed_programovanie(db_session, version.id, status="awaiting_manazer")
        with pytest.raises(OrchestratorError, match="rozhovorovom režime"):
            await orchestrator.apply_action(db_session, version_id=version.id, action="schvalit")

    async def test_apply_schvalit_still_advances_legacy_build(self, db_session):
        # Control: schvalit on a legacy settled programovanie still advances the phase automaton (byte-identical).
        version, _ = _make_version(db_session)
        state = _seed_programovanie(db_session, version.id, status="awaiting_manazer", mode=None)
        # The Programovanie→Verifikácia sign-off has no empty-plan gate (that is navrh-only), so no task seed needed.
        out = await orchestrator.apply_action(db_session, version_id=version.id, action="schvalit")
        assert out.current_stage == "verifikacia"  # advanced Programovanie → Verifikácia
        assert state is out


class TestApproveSpecSuppressed:
    def test_board_omits_approve_spec_after_conversation_spec_approved(self, db_session):
        # MINOR-1: approve_spec is OFFERED pre-approval (the real end-Príprava stop) and DROPPED post-approval.
        version, _ = _make_version(db_session)
        _seed_priprava(db_session, version.id)
        assert "approve_spec" in _board_actions(db_session, version.id)
        _approve_spec(db_session, version.id)
        assert "approve_spec" not in _board_actions(db_session, version.id)

    def test_board_keeps_approve_spec_for_legacy_priprava(self, db_session):
        # Control: a legacy (mode NULL) priprava still offers approve_spec regardless of any approval marker
        # (the conversation-only MINOR filter does not touch the phase automaton).
        version, _ = _make_version(db_session)
        _seed_priprava(db_session, version.id, mode=None)
        assert "approve_spec" in _board_actions(db_session, version.id)
