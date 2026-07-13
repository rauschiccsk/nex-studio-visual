"""CR-1 (nex-studio-visual) — the ``vizual`` live-preview phase plumbing + the MINIMAL Vizuál round.

Exercised against the real v4-branch DB (the stage CHECKs include ``'vizual'``):

* **Boundary walk** — a ``new_version`` build advances ``navrh → vizual → programovanie``, STOPPING for a
  ``schvalit`` at EACH phase boundary (mandatory gate even at ``plná`` autonómia; spec §3.A/§3.E).
* **``_run_vizual_round`` (MINIMAL)** — spins the isolated live-preview sandbox up, records ONE
  ``system → manazer`` preview-URL notification, and settles ``awaiting_manazer``. NO AI-Agent turn here
  (the "AI applies the change" HMR loop is a later CR-1 sub-task). ``vizual_sandbox.spin_up`` is
  monkeypatched — the test NEVER spawns real docker.

``invoke_agent_with_parse_retry`` is monkeypatched (no live ``claude`` CLI); the unit drives
``run_dispatch`` / ``apply_action`` directly, the same entry points the background runner + the API call.
"""

import uuid

import pytest
from sqlalchemy import select

from backend.api.routes.pipeline import _board
from backend.db.models.foundation import User
from backend.db.models.pipeline import PipelineMessage, PipelineState
from backend.db.models.projects import Project
from backend.db.models.tasks import Epic, Feat, Task
from backend.db.models.versions import Version
from backend.services import orchestrator, pipeline_runner, vizual_sandbox
from backend.services.orchestrator import OrchestratorError
from backend.services.pipeline_status import ParseFailure, PipelineStatusBlock

# (pytest ``asyncio_mode = auto`` — async tests run without an explicit mark.)


def _make_version(db_session, *, project_dial=None):
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
        miera_autonomie=project_dial,
    )
    db_session.add(project)
    db_session.flush()
    version = Version(project_id=project.id, version_number=f"1.{uuid.uuid4().hex[:4]}.0")
    db_session.add(version)
    db_session.flush()
    return version, project


def _seed_state(db_session, version_id, *, stage, actor, flow_type="new_version"):
    state = PipelineState(
        version_id=version_id,
        flow_type=flow_type,
        current_stage=stage,
        current_actor=actor,
        status="agent_working",
        next_action="working",
    )
    db_session.add(state)
    db_session.flush()
    return state


def _stub_invoke(monkeypatch, block):
    async def _fake(db, *, version_id, role, stage, prompt, **_kw):
        return block(stage) if callable(block) else block

    monkeypatch.setattr(orchestrator, "invoke_agent_with_parse_retry", _fake)


def _stub_invoke_capture(monkeypatch, block):
    """Monkeypatch ``invoke_agent_with_parse_retry`` to RETURN ``block`` and RECORD each call (role/stage/
    prompt) — lets a test assert whether (and how) the AI turn was dispatched. No live ``claude`` CLI."""
    calls: list[dict] = []

    async def _fake(db, *, version_id, role, stage, prompt, **kw):
        calls.append({"role": role, "stage": stage, "prompt": prompt, **kw})
        return block(stage) if callable(block) else block

    monkeypatch.setattr(orchestrator, "invoke_agent_with_parse_retry", _fake)
    return calls


def _gate_report(stage, **extra):
    return PipelineStatusBlock(stage=stage, kind="gate_report", summary="ok", awaiting="manazer", **extra)


def _one_epic_plan():
    return {
        "epics": [
            {
                "title": "Foundation",
                "feats": [
                    {
                        "title": "Schéma",
                        "description": "DB schéma",
                        "tasks": [{"title": "GL tabuľky", "task_type": "migration", "description": "hlavná kniha"}],
                    }
                ],
            }
        ]
    }


def _msgs(db_session, version_id):
    return (
        db_session.execute(
            select(PipelineMessage).where(PipelineMessage.version_id == version_id).order_by(PipelineMessage.seq.asc())
        )
        .scalars()
        .all()
    )


def _patch_spin_up(monkeypatch, calls=None):
    """Monkeypatch ``vizual_sandbox.spin_up`` (the module attribute the round references) — no real docker."""

    def _fake(slug, frontend_path=None):
        if calls is not None:
            calls["slug"] = slug
        return f"https://vizual-{slug}.isnex.eu"

    monkeypatch.setattr(vizual_sandbox, "spin_up", _fake)


# ── The MINIMAL Vizuál round ─────────────────────────────────────────────────


async def test_vizual_round_spins_up_and_awaits_manazer(db_session, monkeypatch):
    version, project = _make_version(db_session)
    _seed_state(db_session, version.id, stage="vizual", actor="ai_agent")
    calls: dict[str, str] = {}
    _patch_spin_up(monkeypatch, calls)

    state = await orchestrator.run_dispatch(db_session, version.id)

    # Settles for the Manažér to WALK the live preview — no AI-Agent turn, phase does not advance.
    assert state.current_stage == "vizual"
    assert state.status == "awaiting_manazer"
    # The sandbox was spun up for THIS project's slug.
    assert calls["slug"] == project.slug
    # Exactly one system → manazer preview-URL notification, with the URL in content AND payload.
    url = f"https://vizual-{project.slug}.isnex.eu"
    notes = [m for m in _msgs(db_session, version.id) if m.payload and m.payload.get("vizual_url")]
    assert len(notes) == 1
    note = notes[0]
    assert note.author == "system" and note.recipient == "manazer"
    assert note.kind == "notification" and note.stage == "vizual"
    assert note.payload["vizual_url"] == url
    assert url in note.content


async def test_vizual_round_sandbox_failure_blocks_without_crashing(db_session, monkeypatch):
    # A sandbox spin-up failure must NEVER crash the pipeline — settle blocked/system_error with a plain note.
    version, _ = _make_version(db_session)
    _seed_state(db_session, version.id, stage="vizual", actor="ai_agent")

    def _boom(slug, frontend_path=None):
        raise RuntimeError("docker unavailable")

    monkeypatch.setattr(vizual_sandbox, "spin_up", _boom)

    state = await orchestrator.run_dispatch(db_session, version.id)

    assert state.current_stage == "vizual"
    assert state.status == "blocked"
    assert state.block_reason == "system_error"
    # An honest system → manazer note is recorded (no raw crash surfaced to the Manažér).
    errs = [m for m in _msgs(db_session, version.id) if m.payload and m.payload.get("vizual_error")]
    assert errs and errs[-1].author == "system" and errs[-1].stage == "vizual"


# ── The change-request loop: a Manažér directive DISPATCHES the AI to edit the live FE ────────


async def test_vizual_directive_dispatches_ai_and_awaits_manazer(db_session, monkeypatch):
    # A Manažér change-request (directive set) DISPATCHES the AI turn and settles back to the Manažér — the
    # stage never advances here (only ``schvalit`` moves vizual → programovanie).
    version, _ = _make_version(db_session)
    state = _seed_state(db_session, version.id, stage="vizual", actor="ai_agent")
    _patch_spin_up(monkeypatch)
    calls = _stub_invoke_capture(monkeypatch, lambda s: _gate_report(s))

    settled = await orchestrator._run_vizual_round(db_session, state, directive="make the total bigger")

    # The AI turn was dispatched exactly once, as the ai_agent on the vizual stage, carrying the request.
    assert len(calls) == 1
    assert calls[0]["role"] == "ai_agent" and calls[0]["stage"] == "vizual"
    assert "make the total bigger" in calls[0]["prompt"]
    # Hands the turn back to the Manažér; the stage is unchanged.
    assert settled.current_stage == "vizual"
    assert settled.status == "awaiting_manazer"


async def test_vizual_no_directive_does_not_dispatch_ai(db_session, monkeypatch):
    # A FRESH entry (directive None) must NOT run the AI turn — it only spins the preview up + settles.
    version, _ = _make_version(db_session)
    state = _seed_state(db_session, version.id, stage="vizual", actor="ai_agent")
    _patch_spin_up(monkeypatch)
    calls = _stub_invoke_capture(monkeypatch, lambda s: _gate_report(s))

    settled = await orchestrator._run_vizual_round(db_session, state)

    assert calls == []  # no AI turn dispatched
    assert settled.status == "awaiting_manazer"
    # The preview-URL notification was recorded (the sub-task-3 entry behaviour).
    notes = [m for m in _msgs(db_session, version.id) if m.payload and m.payload.get("vizual_url")]
    assert len(notes) == 1


async def test_vizual_directive_parse_failure_blocks_without_crashing(db_session, monkeypatch):
    # A ParseFailure from the AI turn settles blocked/parse_exhaustion (readable note) — never a crash.
    version, _ = _make_version(db_session)
    state = _seed_state(db_session, version.id, stage="vizual", actor="ai_agent")
    _patch_spin_up(monkeypatch)
    _stub_invoke_capture(monkeypatch, ParseFailure(reason="no status block"))

    settled = await orchestrator._run_vizual_round(db_session, state, directive="make it red")

    assert settled.status == "blocked"
    assert settled.block_reason == "parse_exhaustion"
    # An honest system → manazer note names the parse reason (never an empty screen).
    fails = [m for m in _msgs(db_session, version.id) if m.payload and m.payload.get("parse_failure_reason")]
    assert fails and fails[-1].author == "system" and fails[-1].stage == "vizual"


async def test_vizual_url_notification_recorded_once_not_respammed(db_session, monkeypatch):
    # The preview-URL notification is announced ONCE (first entry) — the change-request loop must not re-spam it.
    version, _ = _make_version(db_session)
    state = _seed_state(db_session, version.id, stage="vizual", actor="ai_agent")
    _patch_spin_up(monkeypatch)
    _stub_invoke_capture(monkeypatch, lambda s: _gate_report(s))

    # Turn 1: fresh entry → records the URL note.
    await orchestrator._run_vizual_round(db_session, state)
    # Turn 2: a change-request re-enters the round (spin_up is idempotent) → must NOT re-record the URL.
    await orchestrator._run_vizual_round(db_session, state, directive="tweak the header")

    notes = [m for m in _msgs(db_session, version.id) if m.payload and m.payload.get("vizual_url")]
    assert len(notes) == 1


# ── Boundary walk: navrh → vizual → programovanie, schvalit at each hop ───────


async def test_new_version_walks_navrh_vizual_programovanie_stopping_each_boundary(db_session, monkeypatch):
    # Even at plná autonómia a new_version STOPS at every phase boundary for the Manažér's 'schvalit'.
    version, _ = _make_version(db_session, project_dial="plna")
    _seed_state(db_session, version.id, stage="navrh", actor="ai_agent")

    # Návrh completes with a materialized plan → mandatory stop at the Návrh boundary.
    _stub_invoke(monkeypatch, lambda s: _gate_report(s, plan=_one_epic_plan()))
    state = await orchestrator.run_dispatch(db_session, version.id)
    assert state.current_stage == "navrh"
    assert state.status == "awaiting_manazer"

    # Schváliť Návrh → advance to Vizuál (the vizual round is armed: agent_working).
    state = await orchestrator.apply_action(db_session, version_id=version.id, action="schvalit")
    assert state.current_stage == "vizual"
    assert state.status == "agent_working"

    # Run the Vizuál round (monkeypatched sandbox) → mandatory stop at the Vizuál boundary.
    _patch_spin_up(monkeypatch)
    state = await orchestrator.run_dispatch(db_session, version.id)
    assert state.current_stage == "vizual"
    assert state.status == "awaiting_manazer"

    # Schváliť Vizuál → advance to Programovanie (the build round is armed: agent_working).
    state = await orchestrator.apply_action(db_session, version_id=version.id, action="schvalit")
    assert state.current_stage == "programovanie"
    assert state.status == "agent_working"


def test_settle_boundary_vizual_new_version_always_stops(db_session):
    # Belt to the round's own settle: even if the Vizuál boundary went through the shared dial, a new_version
    # STOPS there (mandatory gate) rather than auto-continuing into Programovanie.
    version, _ = _make_version(db_session, project_dial="plna")
    st = _seed_state(db_session, version.id, stage="vizual", actor="ai_agent")
    assert orchestrator._settle_phase_boundary(db_session, st) is False
    assert st.current_stage == "vizual"  # did NOT advance


def test_next_stage_inserts_vizual_between_navrh_and_programovanie():
    assert orchestrator._next_stage("navrh", "new_version") == "vizual"
    assert orchestrator._next_stage("vizual", "new_version") == "programovanie"


# ── The board surfaces vizual_url for the cockpit Vizuál iframe (CR-1) ─────────


async def test_board_surfaces_vizual_url_after_round(db_session, monkeypatch):
    # The cockpit Vizuál page reads board.vizual_url — the LATEST vizual notification's preview URL. Absent
    # before the round runs, present (== the announced URL) after the fresh-entry round records it.
    version, project = _make_version(db_session)
    _seed_state(db_session, version.id, stage="vizual", actor="ai_agent")
    _patch_spin_up(monkeypatch)

    # No vizual preview recorded yet → the board carries no URL (None, honest-by-construction).
    assert _board(db_session, version.id).vizual_url is None

    # Fresh entry into the stage records the preview-URL notification.
    await orchestrator.run_dispatch(db_session, version.id)

    url = f"https://vizual-{project.slug}.isnex.eu"
    assert _board(db_session, version.id).vizual_url == url


# ── CR-1: the CONVERSATION-flow ENTRY into Vizuál — `spustit_vizual` (mirror of `spustit_stavbu`) ─────
#
# NEW projects run in the CONVERSATION flow (mode='conversation'), which stays at current_stage='priprava'
# and never reaches the phase-automaton `vizual` boundary. `spustit_vizual` is the explicit STEP that MOVES a
# conversation build priprava→vizual (mode unchanged) so `_run_vizual_round` runs — the conversation-flow twin
# of `spustit_stavbu`. These tests exercise the ENTRY (offer + guards + phase move + routing), not the round
# itself (covered above). ``vizual_sandbox.spin_up`` is monkeypatched — no real docker.


def _seed_conv_priprava(db_session, version_id, *, status="awaiting_manazer", mode="conversation"):
    """A settled CONVERSATION build in the priprava register (the spec/plan gating window for spustit_vizual)."""
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


def _approve_spec(db_session, version_id):
    """The durable kind='approval' Špecifikácia freeze signal (what orchestrator.spec_approved reads)."""
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


def _seed_tasks(db_session, version, project, titles=("T1",)):
    """ONE epic + ONE feat + a Task per title (all ``todo``) → navrh_plan_materialized True, _build_started False."""
    epic = Epic(project_id=project.id, version_id=version.id, number=1, title="Foundation", status="planned")
    db_session.add(epic)
    db_session.flush()
    feat = Feat(epic_id=epic.id, number=1, title="Schema", status="todo")
    db_session.add(feat)
    db_session.flush()
    for i, title in enumerate(titles, start=1):
        db_session.add(Task(feat_id=feat.id, number=i, title=title, task_type="backend", status="todo"))
    db_session.flush()


def _board_actions(db_session, version_id):
    return _board(db_session, version_id).available_actions


def _seed_ready_conv_build(db_session):
    """A conversation build parked at a settled priprava with an approved spec + materialized plan (not started)
    — the exact point at which BOTH build-launch verbs (spustit_vizual, spustit_stavbu) become offerable."""
    version, project = _make_version(db_session)
    _seed_conv_priprava(db_session, version.id)
    _approve_spec(db_session, version.id)
    _seed_tasks(db_session, version, project)
    return version, project


class _FakeRegistry:
    async def broadcast(self, *_a, **_kw):
        return None

    def active_director_ids(self, _vid):
        return {"d"}  # a director is on-board → _maybe_notify returns early (no out-of-band ping)

    def present_director_ids(self, _vid):
        return {"d"}

    def away_director_ids(self, _vid):
        return set()


def _wire_runner(db_session, monkeypatch):
    """Point the background runner at the test session (mirror of test_conversation_programming._wire_runner)."""
    monkeypatch.setattr(pipeline_runner, "registry", _FakeRegistry())
    monkeypatch.setattr(pipeline_runner, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)
    monkeypatch.setattr(db_session, "commit", db_session.flush)
    monkeypatch.setattr(db_session, "rollback", lambda: None)


def _stub_routers(monkeypatch):
    """Stub BOTH orchestrator routers to RECORD which one the runner selected, then settle awaiting_manazer."""
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


# (a) apply_action('spustit_vizual') moves a ready conversation build priprava→vizual + arms the working turn.
async def test_spustit_vizual_enters_vizual_and_arms_working(db_session):
    version, _ = _seed_ready_conv_build(db_session)

    state = await orchestrator.apply_action(db_session, version_id=version.id, action="spustit_vizual")

    assert state.current_stage == "vizual"  # phase MOVED (the durable trigger)
    assert state.status == "agent_working"  # _begin_dispatch armed the turn
    assert state.mode == "conversation"  # mode UNCHANGED — still a conversation build
    assert state.current_actor == "ai_agent"
    assert state.dispatch_in_flight is True
    # A durable manazer→ai_agent breadcrumb rides the vizual stage (audit only — NOT the trigger).
    marker = _msgs(db_session, version.id)[-1]
    assert marker.kind == "directive" and marker.author == "manazer" and marker.recipient == "ai_agent"
    assert marker.payload.get("start_vizual") is True and marker.stage == "vizual"
    # The FRESH entry carries NO in-memory directive (the round just spins the preview up; change-requests
    # arrive LATER as relayed messages) — so dispatch_directive is None, mirroring spustit_stavbu.
    assert orchestrator.dispatch_directive(db_session, version.id, "spustit_vizual", {}, "vizual") is None


# (a′) the authoritative apply_action guards (mirror of spustit_stavbu) — each raises its own Slovak message.
async def test_spustit_vizual_guards(db_session):
    # not conversation → raise
    version, project = _make_version(db_session)
    _seed_conv_priprava(db_session, version.id, mode=None)
    _approve_spec(db_session, version.id)
    _seed_tasks(db_session, version, project)
    with pytest.raises(OrchestratorError, match="rozhovorovom"):
        await orchestrator.apply_action(db_session, version_id=version.id, action="spustit_vizual")

    # spec not approved → raise
    version, project = _make_version(db_session)
    _seed_conv_priprava(db_session, version.id)
    _seed_tasks(db_session, version, project)
    with pytest.raises(OrchestratorError, match="schválení Špecifikácie"):
        await orchestrator.apply_action(db_session, version_id=version.id, action="spustit_vizual")

    # plan not materialized → raise
    version, _ = _make_version(db_session)
    _seed_conv_priprava(db_session, version.id)
    _approve_spec(db_session, version.id)
    with pytest.raises(OrchestratorError, match="zostavení plánu"):
        await orchestrator.apply_action(db_session, version_id=version.id, action="spustit_vizual")

    # already IN vizual → raise (a re-click is a no-op; change-requests flow through the chat relay)
    version, _ = _seed_ready_conv_build(db_session)
    st = orchestrator._get_state(db_session, version.id)
    st.current_stage = "vizual"
    st.status = "awaiting_manazer"
    db_session.flush()
    with pytest.raises(OrchestratorError, match="Vizuál už beží"):
        await orchestrator.apply_action(db_session, version_id=version.id, action="spustit_vizual")


# (b) the board offers spustit_vizual (and still spustit_stavbu) after the plan, and NOT spustit_vizual at vizual.
async def test_board_offers_vizual_alongside_stavbu_then_hides_at_vizual(db_session, monkeypatch):
    version, _ = _seed_ready_conv_build(db_session)

    # At the settled priprava (spec approved + plan materialized + not started): BOTH build-launch verbs.
    actions = _board_actions(db_session, version.id)
    assert "spustit_vizual" in actions
    assert "spustit_stavbu" in actions

    # Enter Vizuál and settle the round → spustit_vizual SELF-HIDES; spustit_stavbu STAYS (proceed-to-build path).
    _patch_spin_up(monkeypatch)
    await orchestrator.apply_action(db_session, version_id=version.id, action="spustit_vizual")
    settled = await orchestrator.run_dispatch(db_session, version.id)
    assert settled.current_stage == "vizual" and settled.status == "awaiting_manazer"
    at_vizual = _board_actions(db_session, version.id)
    assert "spustit_vizual" not in at_vizual  # hidden once IN vizual (current_stage == 'vizual' post-filter)
    assert "spustit_stavbu" in at_vizual  # still offered so the Manažér can proceed to the build


# (c) the runner routes a conversation build at current_stage=='vizual' to run_dispatch, NOT run_conversation_turn.
async def test_runner_routes_conversation_vizual_to_run_dispatch(db_session, monkeypatch):
    version, _ = _make_version(db_session)
    state = _seed_state(db_session, version.id, stage="vizual", actor="ai_agent")
    state.mode = "conversation"  # a CONVERSATION build parked at vizual (proves the vizual exclusion, not mode=NULL)
    db_session.flush()
    _wire_runner(db_session, monkeypatch)
    routed = _stub_routers(monkeypatch)

    await pipeline_runner._run(version.id)

    # A conversation build at vizual takes the phase-dispatch path (→ _run_vizual_round), never the conv loop.
    assert routed == ["dispatch"]


# (d) spustit_stavbu STILL works from current_stage=='vizual' → programovanie (its gates don't check the stage).
async def test_spustit_stavbu_from_vizual_advances_to_programovanie(db_session, monkeypatch):
    version, _ = _seed_ready_conv_build(db_session)
    _patch_spin_up(monkeypatch)

    st = await orchestrator.apply_action(db_session, version_id=version.id, action="spustit_vizual")
    assert st.current_stage == "vizual"
    st = await orchestrator.run_dispatch(db_session, version.id)  # settle the vizual round → awaiting_manazer
    assert st.current_stage == "vizual" and st.status == "awaiting_manazer"

    # From vizual, "Spustiť stavbu" proceeds to the build (build not started; gates unchanged).
    st = await orchestrator.apply_action(db_session, version_id=version.id, action="spustit_stavbu")
    assert st.current_stage == "programovanie"
    assert st.status == "agent_working"


# ── #3 (Director 2026-07-13): Vizuál commits are squashed to ONE at approval, not per change ──────────
def test_vizual_directive_tells_the_ai_not_to_commit_each_change(db_session):
    version, _ = _make_version(db_session)
    brief = orchestrator._vizual_directive(db_session, version.id, "make the header bigger")
    # The AI only WRITES the FE (HMR reflects it live); it must NOT commit each change.
    assert "NEcommituj" in brief
    assert "COMMITni" not in brief  # the old per-change-commit instruction is gone


def test_commit_vizual_changes_squashes_the_session_into_one_commit(tmp_path):
    import subprocess

    def git(*args):
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True, capture_output=True)

    git("init", "-q")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    fe = tmp_path / "frontend"
    fe.mkdir()
    (fe / "App.tsx").write_text("v1")
    git("add", "-A")
    git("commit", "-q", "-m", "init")

    def head():
        return subprocess.run(
            ["git", "-C", str(tmp_path), "rev-parse", "HEAD"], capture_output=True, text=True
        ).stdout.strip()

    base = head()
    # Nothing changed → NO empty commit.
    orchestrator._commit_vizual_changes(tmp_path)
    assert head() == base

    # Several accumulated (uncommitted) FE tweaks → exactly ONE commit.
    (fe / "App.tsx").write_text("v2")
    (fe / "New.tsx").write_text("new component")
    orchestrator._commit_vizual_changes(tmp_path)
    assert head() != base
    log = subprocess.run(["git", "-C", str(tmp_path), "log", "--oneline"], capture_output=True, text=True).stdout
    assert log.count("\n") == 2  # init + the ONE squashed vizual commit
    assert "vizuálne úpravy" in log


def test_commit_vizual_changes_noop_without_checkout(tmp_path):
    # No .git → best-effort no-op, never raises.
    orchestrator._commit_vizual_changes(tmp_path)
