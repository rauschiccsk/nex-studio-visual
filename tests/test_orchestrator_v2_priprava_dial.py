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


def _seed_state(db_session, version_id, *, stage, actor, build_dial=None, iteration=0, flow_type="new_version"):
    state = PipelineState(
        version_id=version_id,
        flow_type=flow_type,
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
    # init prompt + read-first + step-by-step consult (one question at a time) + write the spec .md.
    assert "Načítaj zadanie a začni prípravu špecifikácie" in p
    assert "customer-requirements.md" in p
    # CR-V2-032: analysis + overview, then ONE question at a time — never a batch dump.
    assert "PO JEDNEJ" in p
    assert "NIKDY nevysýpaj všetky otázky naraz" in p
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


async def test_navrh_boundary_always_stops_even_at_plna(db_session, monkeypatch):
    # A (Director 2026-06-30): a new_version build STOPS at the Návrh boundary for the Manažér's
    # confirmation, INDEPENDENT of the dial — a mandatory phase gate even at plná autonómia (the Manažér
    # advances via 'schvalit'). Prevents an autonomous run from crossing a phase unattended.
    version, _ = _make_version(db_session, project_dial="plna")
    _seed_state(db_session, version.id, stage="navrh", actor="ai_agent")
    _stub_invoke(monkeypatch, lambda s: _gate_report(s, plan=_one_epic_plan()))
    state = await orchestrator.run_dispatch(db_session, version.id)
    assert state.current_stage == "navrh"
    assert state.status == "awaiting_manazer"  # mandatory gate — no auto-advance even at plná


async def test_programovanie_boundary_always_stops_even_at_plna(db_session, monkeypatch):
    # A: a new_version build STOPS at the Programovanie boundary even at plná (mandatory phase gate).
    version, _ = _make_version(db_session, project_dial="plna")
    _seed_state(db_session, version.id, stage="programovanie", actor="ai_agent")
    _stub_invoke(monkeypatch, _gate_report("programovanie"))
    state = await orchestrator.run_dispatch(db_session, version.id)
    assert state.current_stage == "programovanie"
    assert state.status == "awaiting_manazer"


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


def _stub_verifikacia_smoke(monkeypatch):
    """The Verifikácia round (CR-V2-014) runs _run_release_smoke; stub it (no docker) so these dial tests
    exercise only the PASS/FAIL → dial-settle wiring."""

    async def _fake(slug, version_label):
        return (True, "app booted + responds"), (True, "5 assertions", False)

    monkeypatch.setattr(orchestrator, "_run_release_smoke", _fake)


async def test_verifikacia_auto_signoff_requires_recorded_pass(db_session, monkeypatch):
    # no-silent-done invariant (CR-V2-014): at plná, the Verifikácia end stop reaches Hotovo ONLY through a
    # recorded Auditor PASS verdict. A non-PASS verdict (here fail-closed: no explicit verdict=true) does NOT
    # auto-sign-off to Hotovo — it loops the fix back to the AI Agent (Programovanie), never a silent done.
    version, _ = _make_version(db_session, project_dial="plna")
    _seed_state(db_session, version.id, stage="verifikacia", actor="auditor")
    _stub_verifikacia_smoke(monkeypatch)
    # the Auditor returns a verdict block WITHOUT verdict=true → fail-closed FAIL
    no_pass = PipelineStatusBlock(stage="verifikacia", kind="verdict", summary="nejasné", awaiting="manazer")
    _stub_invoke(monkeypatch, no_pass)
    state = await orchestrator.run_dispatch(db_session, version.id)
    assert state.current_stage != "done"  # never a silent done without a PASS verdict
    assert orchestrator._verifikacia_passed(db_session, version.id) is False


async def test_verifikacia_pass_new_version_stops_for_signoff_even_at_plna(db_session, monkeypatch):
    # A (Director 2026-06-30): a new_version PASS STOPS at the Verifikácia end for the Manažér's final
    # sign-off ('schvalit' → Hotovo), even at plná — NEVER an auto-Hotovo. The PASS is on record
    # (no-silent-done still holds), but Hotovo now needs the Manažér's explicit confirmation.
    version, _ = _make_version(db_session, project_dial="plna")
    _seed_state(db_session, version.id, stage="verifikacia", actor="auditor")
    _stub_verifikacia_smoke(monkeypatch)
    pass_block = PipelineStatusBlock(
        stage="verifikacia", kind="verdict", summary="overené", awaiting="manazer", verdict=True
    )
    _stub_invoke(monkeypatch, pass_block)
    state = await orchestrator.run_dispatch(db_session, version.id)
    assert state.current_stage == "verifikacia"
    assert state.status == "awaiting_manazer"  # mandatory end gate — Manažér signs off to Hotovo
    assert orchestrator._verifikacia_passed(db_session, version.id) is True


async def test_verifikacia_pass_fast_fix_auto_signs_off_to_hotovo(db_session, monkeypatch):
    # fast_fix keeps its zero-approval lane: a PASS auto-signs-off to Hotovo (terminal) at plná.
    version, _ = _make_version(db_session, project_dial="plna")
    _seed_state(db_session, version.id, stage="verifikacia", actor="auditor", flow_type="fast_fix")
    _stub_verifikacia_smoke(monkeypatch)
    pass_block = PipelineStatusBlock(
        stage="verifikacia", kind="verdict", summary="overené", awaiting="manazer", verdict=True
    )
    _stub_invoke(monkeypatch, pass_block)
    state = await orchestrator.run_dispatch(db_session, version.id)
    assert state.current_stage == "done"
    assert state.status == "done"
    assert orchestrator._verifikacia_passed(db_session, version.id) is True


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


def test_settle_boundary_navrh_new_version_always_stops_even_at_plna(db_session):
    # A: a new_version phase boundary returns False (STOP) regardless of the dial — mandatory gate.
    version, _ = _make_version(db_session, project_dial="plna")
    st = _seed_state(db_session, version.id, stage="navrh", actor="ai_agent")
    assert orchestrator._settle_phase_boundary(db_session, st) is False
    assert st.current_stage == "navrh"  # did NOT advance


def test_settle_boundary_fast_fix_plna_advances(db_session):
    # fast_fix keeps the dial (forced full-auto): the Programovanie boundary auto-advances to Verifikácia.
    version, _ = _make_version(db_session, project_dial="plna")
    st = _seed_state(db_session, version.id, stage="programovanie", actor="ai_agent", flow_type="fast_fix")
    assert orchestrator._settle_phase_boundary(db_session, st) is True
    assert st.current_stage == "verifikacia" and st.status == "agent_working"
