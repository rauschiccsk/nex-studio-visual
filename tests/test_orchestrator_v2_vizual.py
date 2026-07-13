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

from sqlalchemy import select

from backend.api.routes.pipeline import _board
from backend.db.models.foundation import User
from backend.db.models.pipeline import PipelineMessage, PipelineState
from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.services import orchestrator, vizual_sandbox
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
