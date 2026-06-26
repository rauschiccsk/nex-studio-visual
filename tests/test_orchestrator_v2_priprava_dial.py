"""CR-V2-010 — Príprava phase (Zadanie→Špecifikácia dialogue) + Milestone-C dial-settle wiring.

Two deliverables, both exercised here against the real v2 branch DB (4-phase CHECKs):

* **Príprava round** — ``run_dispatch`` injects the interactive spec-dialogue brief in Príprava (read
  the Zadanie, systematize, ask clarifying questions BEFORE any design, propose, write the Špecifikácia
  .md), persists + verifies the Špecifikácia artifact, and never advances past the ALWAYS-mandatory
  ``approve_spec`` stop on its own.
* **Dial-settle wiring** (SHARED, owned by CR-V2-010, inherited by 011/012) — ``run_dispatch`` consults
  the Miera autonómie dial at each settled phase boundary: ``plna`` auto-continues Návrh → Programovanie →
  Verifikácia; ``po_kazdej_faze`` stops at each; the end-Príprava ``approve_spec`` always stops; the
  Verifikácia end sign-off preserves the no-silent-done invariant.

``invoke_agent_with_parse_retry`` is monkeypatched (no live ``claude`` CLI). The unit drives
``run_dispatch`` directly, the same entry point the background runner calls.
"""

import uuid
from pathlib import Path

from backend.db.models.foundation import User
from backend.db.models.pipeline import PipelineMessage, PipelineState
from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.services import orchestrator
from backend.services.pipeline_status import PipelineStatusBlock

# (pytest ``asyncio_mode = auto`` — async tests run without an explicit mark.)


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
        source_path=source_path,
        miera_autonomie=project_dial,
    )
    db_session.add(project)
    db_session.flush()
    version = Version(project_id=project.id, version_number=f"1.{uuid.uuid4().hex[:4]}.0")
    db_session.add(version)
    db_session.flush()
    return version, project


def _seed_state(db_session, version_id, *, stage, actor, build_dial=None, iteration=0):
    state = PipelineState(
        version_id=version_id,
        flow_type="new_version",
        current_stage=stage,
        current_actor=actor,
        status="agent_working",
        next_action="working",
        miera_autonomie=build_dial,
        iteration=iteration,
    )
    db_session.add(state)
    db_session.flush()
    return state


def _stub_invoke(monkeypatch, block):
    """Make ``invoke_agent_with_parse_retry`` return *block* (or call a factory) — no live CLI, and capture
    the prompt the orchestrator built so the Príprava-brief assertions can read it."""
    captured = {}

    async def _fake(db, *, version_id, role, stage, prompt, **_kw):
        captured["prompt"] = prompt
        captured["stage"] = stage
        captured["role"] = role
        return block(stage) if callable(block) else block

    monkeypatch.setattr(orchestrator, "invoke_agent_with_parse_retry", _fake)
    return captured


def _gate_report(stage, **extra):
    return PipelineStatusBlock(stage=stage, kind="gate_report", summary="ok", awaiting="manazer", **extra)


def _msgs(db_session, version_id):
    from sqlalchemy import select

    return (
        db_session.execute(
            select(PipelineMessage).where(PipelineMessage.version_id == version_id).order_by(PipelineMessage.seq.asc())
        )
        .scalars()
        .all()
    )


# ── Príprava round: the interactive spec-dialogue brief ──────────────────────


async def test_priprava_brief_instructs_read_then_ask_then_write_spec(db_session, monkeypatch):
    version, _ = _make_version(db_session)
    _seed_state(db_session, version.id, stage="priprava", actor="ai_agent")
    captured = _stub_invoke(monkeypatch, _gate_report("priprava", deliverables=[]))
    await orchestrator.run_dispatch(db_session, version.id)
    p = captured["prompt"]
    # init prompt + read-first + ask-until-understood (no design until understood) + write the spec .md.
    assert "Načítaj zadanie a začni prípravu špecifikácie" in p
    assert "customer-requirements.md" in p
    assert "ŽIADNY návrh, kým nie je každý detail pochopený" in p
    assert "kind=question" in p  # ask + STOP when anything is unclear (before any design)
    assert "specification.md" in p
    assert "Schváliť špecifikáciu" in p  # the always-mandatory end-Príprava stop named in the brief


async def test_priprava_question_settles_blocked_before_any_design(db_session, monkeypatch):
    # The Gate: a vague Zadanie → the AI Agent asks BEFORE designing. A question block settles ``blocked``
    # (the Manažér answers); the phase does NOT advance to Návrh.
    version, _ = _make_version(db_session)
    _seed_state(db_session, version.id, stage="priprava", actor="ai_agent")
    q = PipelineStatusBlock(
        stage="priprava", kind="question", summary="need detail", awaiting="manazer", question="Aký auth model?"
    )
    _stub_invoke(monkeypatch, q)
    state = await orchestrator.run_dispatch(db_session, version.id)
    assert state.status == "blocked"
    assert state.block_reason == "agent_question"
    assert state.current_stage == "priprava"  # no design began


async def test_priprava_gate_report_persists_spec_artifact(db_session, monkeypatch, tmp_path):
    # The AI Agent writes the Špecifikácia .md to disk; the engine verifies + records the durable artifact.
    version, project = _make_version(db_session, source_path=str(tmp_path))
    _seed_state(db_session, version.id, stage="priprava", actor="ai_agent")
    rel = orchestrator._priprava_spec_rel(version.version_number)
    spec_file = Path(tmp_path) / rel
    spec_file.parent.mkdir(parents=True, exist_ok=True)
    spec_file.write_text("# Špecifikácia\n\n## Prehľad\n...", encoding="utf-8")
    _stub_invoke(monkeypatch, _gate_report("priprava", deliverables=[rel]))
    state = await orchestrator.run_dispatch(db_session, version.id)
    # ALWAYS-mandatory approve_spec stop: Príprava settles awaiting the Manažér, never auto-advances to Návrh.
    assert state.current_stage == "priprava"
    assert state.status == "awaiting_manazer"
    # the durable artifact record exists + is readable (the Vývoj → Príprava tab reads it)
    notes = [m for m in _msgs(db_session, version.id) if m.payload and m.payload.get("priprava_spec")]
    assert notes and notes[-1].payload["path"] == rel
    assert spec_file.read_text(encoding="utf-8").startswith("# Špecifikácia")


async def test_priprava_missing_spec_artifact_blocks(db_session, monkeypatch, tmp_path):
    # Checkout exists but the agent never wrote the spec file → blocked, the phase does NOT close.
    version, _ = _make_version(db_session, source_path=str(tmp_path))
    _seed_state(db_session, version.id, stage="priprava", actor="ai_agent")
    rel = orchestrator._priprava_spec_rel(version.version_number)
    _stub_invoke(monkeypatch, _gate_report("priprava", deliverables=[rel]))  # claims it, but it's not on disk
    state = await orchestrator.run_dispatch(db_session, version.id)
    assert state.status == "blocked"
    assert state.block_reason == "agent_error"
    assert state.current_stage == "priprava"


async def test_priprava_no_checkout_records_db_only_artifact(db_session, monkeypatch):
    # A library/no-checkout project: the spec lives in the gate_report report payload (DB audit trail);
    # the artifact note is still recorded and the phase still stops at approve_spec.
    version, _ = _make_version(db_session, source_path=None)
    _seed_state(db_session, version.id, stage="priprava", actor="ai_agent")
    _stub_invoke(monkeypatch, _gate_report("priprava", deliverables=[]))
    state = await orchestrator.run_dispatch(db_session, version.id)
    assert state.status == "awaiting_manazer" and state.current_stage == "priprava"
    notes = [m for m in _msgs(db_session, version.id) if m.payload and m.payload.get("priprava_spec")]
    assert notes  # DB-only artifact note recorded


# ── Dial-settle wiring (SHARED Milestone-C deliverable) ──────────────────────


async def test_plna_auto_continues_navrh_to_programovanie(db_session, monkeypatch):
    # Plná autonómia: a settled Návrh boundary auto-advances to Programovanie (no Manažér stop).
    version, _ = _make_version(db_session, project_dial="plna")
    _seed_state(db_session, version.id, stage="navrh", actor="ai_agent")
    _stub_invoke(monkeypatch, lambda s: _gate_report(s, plan=_one_epic_plan()))
    state = await orchestrator.run_dispatch(db_session, version.id)
    assert state.current_stage == "programovanie"
    assert state.status == "agent_working"  # auto-chain continues into the next phase


async def test_plna_auto_continues_programovanie_to_verifikacia(db_session, monkeypatch):
    version, _ = _make_version(db_session, project_dial="plna")
    _seed_state(db_session, version.id, stage="programovanie", actor="ai_agent")
    _stub_invoke(monkeypatch, _gate_report("programovanie"))
    state = await orchestrator.run_dispatch(db_session, version.id)
    assert state.current_stage == "verifikacia"
    assert state.status == "agent_working"


async def test_po_kazdej_faze_stops_at_navrh(db_session, monkeypatch):
    # Po každej fáze: the Návrh boundary STOPS for the Manažér (does not advance).
    version, _ = _make_version(db_session, project_dial="po_kazdej_faze")
    _seed_state(db_session, version.id, stage="navrh", actor="ai_agent")
    _stub_invoke(monkeypatch, _gate_report("navrh", plan=_one_epic_plan()))
    state = await orchestrator.run_dispatch(db_session, version.id)
    assert state.current_stage == "navrh"
    assert state.status == "awaiting_manazer"


async def test_po_kazdej_faze_stops_at_programovanie(db_session, monkeypatch):
    version, _ = _make_version(db_session, project_dial="po_kazdej_faze")
    _seed_state(db_session, version.id, stage="programovanie", actor="ai_agent")
    _stub_invoke(monkeypatch, _gate_report("programovanie"))
    state = await orchestrator.run_dispatch(db_session, version.id)
    assert state.current_stage == "programovanie"
    assert state.status == "awaiting_manazer"


async def test_priprava_approve_spec_always_stops_even_at_plna(db_session, monkeypatch):
    # The Špecifikácia approval is dial-INDEPENDENT: even at plná autonómia Príprava stops (never
    # auto-advances to Návrh). Verified at the dispatch settle (not just the pure dial logic).
    version, _ = _make_version(db_session, project_dial="plna")
    _seed_state(db_session, version.id, stage="priprava", actor="ai_agent")
    _stub_invoke(monkeypatch, _gate_report("priprava", deliverables=[]))
    state = await orchestrator.run_dispatch(db_session, version.id)
    assert state.current_stage == "priprava"  # did NOT auto-advance to navrh despite plná
    assert state.status == "awaiting_manazer"
    # and the pure dial logic agrees.
    assert orchestrator.dial_stops_at("plna", "approve_spec") is True


async def test_verifikacia_auto_signoff_requires_recorded_pass(db_session, monkeypatch):
    # no-silent-done invariant under the dial: at plná, the Verifikácia end stop auto-signs-off to Hotovo
    # ONLY when a PASS verdict is on record; absent one it STOPS regardless of the dial.
    version, _ = _make_version(db_session, project_dial="plna")
    _seed_state(db_session, version.id, stage="verifikacia", actor="auditor")
    _stub_invoke(monkeypatch, _gate_report("verifikacia"))  # no PASS verdict recorded
    state = await orchestrator.run_dispatch(db_session, version.id)
    assert state.current_stage == "verifikacia"
    assert state.status == "awaiting_manazer"  # STOP — never a silent done without verification


async def test_verifikacia_auto_signoff_to_done_with_recorded_pass(db_session, monkeypatch):
    # With a recorded Auditor PASS, plná auto-signs-off the Verifikácia end stop to Hotovo (terminal).
    version, _ = _make_version(db_session, project_dial="plna")
    _seed_state(db_session, version.id, stage="verifikacia", actor="auditor")
    db_session.add(
        PipelineMessage(
            version_id=version.id,
            stage="verifikacia",
            author="auditor",
            recipient="manazer",
            kind="verdict",
            content="PASS",
            payload={"verdict": "PASS", "phase": "verifikacia"},
        )
    )
    db_session.flush()
    _stub_invoke(monkeypatch, _gate_report("verifikacia"))
    state = await orchestrator.run_dispatch(db_session, version.id)
    assert state.current_stage == "done"
    assert state.status == "done"


async def test_per_build_dial_beats_project_in_settle(db_session, monkeypatch):
    # The settle path resolves the dial through the AUTON-6 override order: a per-build po_kazdej_faze beats
    # a per-project plna → the Návrh boundary stops.
    version, _ = _make_version(db_session, project_dial="plna")
    _seed_state(db_session, version.id, stage="navrh", actor="ai_agent", build_dial="po_kazdej_faze")
    _stub_invoke(monkeypatch, _gate_report("navrh", plan=_one_epic_plan()))
    state = await orchestrator.run_dispatch(db_session, version.id)
    assert state.current_stage == "navrh" and state.status == "awaiting_manazer"


def _one_epic_plan():
    """A minimal EPIC→FEAT→TASK plan so a Návrh gate_report passes the navrh-plan content rule."""
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


# ── _settle_phase_boundary unit (pure-ish) ───────────────────────────────────


def test_settle_boundary_priprava_never_auto_continues(db_session):
    version, _ = _make_version(db_session, project_dial="plna")
    st = _seed_state(db_session, version.id, stage="priprava", actor="ai_agent")
    assert orchestrator._settle_phase_boundary(db_session, st) is False  # approve_spec always stops


def test_settle_boundary_navrh_plna_advances(db_session):
    version, _ = _make_version(db_session, project_dial="plna")
    st = _seed_state(db_session, version.id, stage="navrh", actor="ai_agent")
    assert orchestrator._settle_phase_boundary(db_session, st) is True
    assert st.current_stage == "programovanie" and st.status == "agent_working"
