"""CR-V2-011 — Návrh phase (ONE design DOCUMENT; the task plan is NOT built here).

Exercised against the real v2 branch DB (4-phase CHECKs). The Návrh round:

* **Design-doc turn** — ``run_dispatch`` injects the Návrh brief (read the approved Špecifikácia, write ONE
  coherent design ``.md``), persists + verifies the design-doc artifact, and surfaces the AI Agent's
  clarification questions at the post-Návrh stop (the SEAM the Auditor's upfront review hooks into in
  CR-V2-013).
* **NO task plan in Návrh** — nex-studio-visual (Director 2026-07-13): the EPIC→FEAT→TASK plan is no longer
  generated in Návrh. The Vizuál step keeps refining the app AFTER Návrh, so a plan built now would be stale;
  the plan is built at the START of Programovanie (``_run_build_round`` entry) from the FINAL design + the
  Manažér's Vizuál changes. Any inline plan the design turn happens to emit is IGNORED. The plan-generation +
  plan-pass-failure tests now live in ``test_orchestrator_v2_programovanie.py`` (build-round entry).
* **Dial-governed stop** — the post-Návrh schvaľovací bod fires per the Miera autonómie dial; a new_version
  build ALWAYS stops there (mandatory phase gate, even at plná) for the Manažér's 'schvalit' → Vizuál.

The design-doc turn is stubbed via ``invoke_agent_with_parse_retry`` (no live ``claude``).
"""

import uuid
from pathlib import Path

from sqlalchemy import select

from backend.db.models.foundation import User
from backend.db.models.pipeline import PipelineMessage, PipelineState
from backend.db.models.projects import Project
from backend.db.models.tasks import Epic
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


def _seed_navrh(db_session, version_id, *, build_dial=None):
    state = PipelineState(
        version_id=version_id,
        flow_type="new_version",
        current_stage="navrh",
        current_actor="ai_agent",
        status="agent_working",
        next_action="working",
        miera_autonomie=build_dial,
    )
    db_session.add(state)
    db_session.flush()
    return state


def _msgs(db_session, version_id):
    return (
        db_session.execute(
            select(PipelineMessage).where(PipelineMessage.version_id == version_id).order_by(PipelineMessage.seq.asc())
        )
        .scalars()
        .all()
    )


def _epics(db_session, version_id):
    return db_session.execute(select(Epic).where(Epic.version_id == version_id)).scalars().all()


# ── Stub the design-doc turn (invoke_agent_with_parse_retry) ──────────────────


def _stub_design_turn(monkeypatch, block):
    """Make the design-doc turn (``invoke_agent_with_parse_retry``) return *block* and capture its prompt.

    CR-V2-013 added a SECOND ``invoke_agent_with_parse_retry`` call inside the Návrh round — the Auditor's
    upfront review (role=auditor). These navrh-focused tests assert only the AI-Agent DESIGN turn, so the
    fake captures ONLY the ``ai_agent`` turn and returns a clean PASS ``verdict`` for the ``auditor`` turn
    (a no-hole upfront review → the dial governs the post-Návrh stop exactly as before this CR)."""
    captured = {}

    async def _fake(db, *, version_id, role, stage, prompt, **_kw):
        if role == orchestrator.AUDITOR_ROLE:
            # No-hole upfront review (PASS) — does not perturb the dial-governed post-Návrh stop.
            return PipelineStatusBlock(
                stage="navrh", kind="verdict", summary="bez medzery", awaiting="manazer", verdict=True
            )
        captured["prompt"] = prompt
        captured["stage"] = stage
        captured["role"] = role
        return block

    monkeypatch.setattr(orchestrator, "invoke_agent_with_parse_retry", _fake)
    return captured


def _design_done():
    """A design-doc turn that finished the doc (kind=done). Návrh no longer folds a task plan in."""
    return PipelineStatusBlock(stage="navrh", kind="done", summary="návrh hotový", awaiting="manazer")


# ── Návrh round: design-doc turn + brief ─────────────────────────────────────


async def test_navrh_brief_instructs_one_design_doc_with_plan_last(db_session, monkeypatch):
    version, _ = _make_version(db_session)
    _seed_navrh(db_session, version.id)
    captured = _stub_design_turn(monkeypatch, _design_done())
    await orchestrator.run_dispatch(db_session, version.id)
    p = captured["prompt"]
    assert captured["stage"] == "navrh" and captured["role"] == "ai_agent"
    assert "JEDEN koherentný návrhový dokument" in p  # ONE design doc, not a multi-doc tree
    assert "specification.md" in p  # reads the approved Špecifikácia
    assert "design.md" in p  # writes the design doc
    assert "EPIC → FEAT → TASK" in p and "POSLEDNÁ časť" in p  # the task plan is the doc's last part


async def test_navrh_question_settles_blocked_before_plan(db_session, monkeypatch):
    # A design ambiguity → the AI Agent asks; the phase does NOT advance. No plan is materialized in Návrh.
    version, _ = _make_version(db_session)
    _seed_navrh(db_session, version.id)
    q = PipelineStatusBlock(
        stage="navrh", kind="question", summary="need detail", awaiting="manazer", question="Aký dátový model?"
    )
    _stub_design_turn(monkeypatch, q)
    state = await orchestrator.run_dispatch(db_session, version.id)
    assert state.status == "blocked"
    assert state.block_reason == "agent_question"
    assert state.current_stage == "navrh"
    assert not _epics(db_session, version.id)  # no plan materialized


# ── Návrh produces the design DOCUMENT ONLY — no task plan (moved to Programovanie) ──


async def test_navrh_design_doc_only_no_plan_materialized(db_session, monkeypatch, tmp_path):
    # nex-studio-visual (Director 2026-07-13): Návrh produces the design DOCUMENT ONLY. NO EPIC→FEAT→TASK
    # plan is materialized here — it is built at Programovanie start (from the final design + Vizuál changes).
    version, _ = _make_version(db_session, source_path=str(tmp_path), project_dial="po_kazdej_faze")
    _seed_navrh(db_session, version.id)
    # the design doc on disk (the agent claims it; the engine verifies it)
    rel = orchestrator._navrh_design_doc_rel(version.version_number)
    doc = Path(tmp_path) / rel
    doc.parent.mkdir(parents=True, exist_ok=True)
    doc.write_text("# Návrh\n\n## Prehľad\n...", encoding="utf-8")
    _stub_design_turn(
        monkeypatch,
        PipelineStatusBlock(stage="navrh", kind="done", summary="ok", awaiting="manazer", deliverables=[rel]),
    )
    state = await orchestrator.run_dispatch(db_session, version.id)
    # po_kazdej_faze → the Návrh schvaľovací bod stops for the Manažér (does not advance)
    assert state.current_stage == "navrh" and state.status == "awaiting_manazer"
    # NO plan materialized — the design doc is the only Návrh deliverable
    assert not _epics(db_session, version.id)
    # the durable design-doc artifact note exists (the Vývoj → Návrh tab reads it)
    notes = [m for m in _msgs(db_session, version.id) if m.payload and m.payload.get("navrh_design_doc")]
    assert notes and notes[-1].payload["path"] == rel


async def test_navrh_inline_plan_is_ignored(db_session, monkeypatch):
    # If the design turn HAPPENS to carry an inline plan, it is IGNORED — Návrh materializes NO plan (the
    # authoritative EPIC→FEAT→TASK plan is always built fresh at Programovanie start, never from a Návrh turn).
    version, _ = _make_version(db_session, project_dial="po_kazdej_faze")
    _seed_navrh(db_session, version.id)
    inline = PipelineStatusBlock(
        stage="navrh",
        kind="gate_report",
        summary="malý návrh + plán",
        awaiting="manazer",
        plan={
            "epics": [
                {
                    "title": "Foundation",
                    "feats": [
                        {
                            "title": "Schema",
                            "description": "DB",
                            "tasks": [{"title": "users", "task_type": "migration"}],
                        }
                    ],
                }
            ]
        },
        cross_cutting_rules="## Invarianty\n- jedna firma",
    )
    _stub_design_turn(monkeypatch, inline)

    # the incremental plan passes must NOT run in Návrh — make invoke_claude explode if reached.
    async def _boom(*a, **k):
        raise AssertionError("Návrh must not run the task-plan passes — the plan is built at Programovanie")

    monkeypatch.setattr(orchestrator, "invoke_claude", _boom)
    state = await orchestrator.run_dispatch(db_session, version.id)
    assert state.current_stage == "navrh" and state.status == "awaiting_manazer"
    assert not _epics(db_session, version.id)  # the inline plan was ignored


# ── Design-doc artifact gate ──────────────────────────────────────────────────


async def test_navrh_missing_design_doc_blocks(db_session, monkeypatch, tmp_path):
    # Checkout exists but the agent never wrote design.md → blocked, the phase does NOT close.
    version, _ = _make_version(db_session, source_path=str(tmp_path))
    _seed_navrh(db_session, version.id)
    rel = orchestrator._navrh_design_doc_rel(version.version_number)
    _stub_design_turn(
        monkeypatch,
        PipelineStatusBlock(stage="navrh", kind="done", summary="ok", awaiting="manazer", deliverables=[rel]),
    )
    state = await orchestrator.run_dispatch(db_session, version.id)
    assert state.status == "blocked"
    assert state.block_reason == "agent_error"
    assert state.current_stage == "navrh"
    assert not _epics(db_session, version.id)


async def test_navrh_no_checkout_records_db_only_artifact(db_session, monkeypatch):
    # A library/no-checkout project: the design lives in the gate_report payload (DB audit trail); the
    # artifact note is still recorded. No plan is materialized in Návrh (built at Programovanie start).
    version, _ = _make_version(db_session, source_path=None, project_dial="po_kazdej_faze")
    _seed_navrh(db_session, version.id)
    _stub_design_turn(monkeypatch, _design_done())
    state = await orchestrator.run_dispatch(db_session, version.id)
    assert state.current_stage == "navrh" and state.status == "awaiting_manazer"
    notes = [m for m in _msgs(db_session, version.id) if m.payload and m.payload.get("navrh_design_doc")]
    assert notes  # DB-only artifact note recorded
    assert not _epics(db_session, version.id)  # no plan materialized in Návrh


# ── Dial governs the post-Návrh stop ──────────────────────────────────────────


async def test_navrh_boundary_always_stops_new_version_even_at_plna(db_session, monkeypatch):
    # A (Director 2026-06-30): a new_version build STOPS at the Návrh schvaľovací bod for the Manažér's
    # confirmation ('schvalit' → Vizuál), INDEPENDENT of the dial — mandatory phase gate even at plná. The
    # design doc is the only Návrh deliverable; the plan is built later, at Programovanie start.
    version, _ = _make_version(db_session, project_dial="plna")
    _seed_navrh(db_session, version.id)
    _stub_design_turn(monkeypatch, _design_done())
    state = await orchestrator.run_dispatch(db_session, version.id)
    assert state.current_stage == "navrh"
    assert state.status == "awaiting_manazer"  # mandatory gate — no auto-advance even at plná
    assert not _epics(db_session, version.id)  # design doc only — no plan in Návrh


async def test_navrh_per_build_dial_beats_project(db_session, monkeypatch):
    # AUTON-6 override order in the Návrh settle: per-build po_kazdej_faze beats per-project plna → stop.
    version, _ = _make_version(db_session, project_dial="plna")
    _seed_navrh(db_session, version.id, build_dial="po_kazdej_faze")
    _stub_design_turn(monkeypatch, _design_done())
    state = await orchestrator.run_dispatch(db_session, version.id)
    assert state.current_stage == "navrh" and state.status == "awaiting_manazer"


# ── Pure helpers / conventions ────────────────────────────────────────────────


def test_navrh_design_doc_rel_path_convention():
    assert orchestrator._navrh_design_doc_rel("0.1.0") == "docs/specs/versions/v0.1.0/design.md"


def test_task_plan_skeleton_directive_mandates_coarse_granularity():
    """CR-V2-036: the skeleton pass decides the FEAT count, so the coarse-granularity rule + the hard cap
    must be stated HERE (not only in the per-feat task pass — too late). Without it the agent over-
    decomposed (46 feats > MAX_PLAN_FEATS) and the engine rejected the whole plan. (The skeleton pass now
    runs at Programovanie entry, but the directive itself is unchanged.)"""
    d = orchestrator._task_plan_skeleton_directive()
    assert "HRUBOZRNNÁ" in d  # coarse granularity mandated in the skeleton pass
    assert "modul ≈ úloha" in d
    assert str(orchestrator.MAX_PLAN_FEATS) in d  # the cap is named so the agent stays well under it
