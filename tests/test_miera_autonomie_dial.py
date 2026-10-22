"""Tests for the Miera autonómie 4-level dial — CR-V2-008 (AUTON-1..6, OQ-9).

Covers the three deliverables of the dial:

* :func:`orchestrator.resolve_miera_autonomie` — the AUTON-6 override resolver
  (per-build → per-project → global, first non-NULL wins; NULL falls through;
  an unrecognised stored value degrades; ``fast_fix`` = full-auto regardless).
* :func:`orchestrator.dial_stops_at` — the pure schvaľovací-bod evaluator that
  REPLACES the v1 ``_maybe_autonomous_*`` predicates, including the two
  always-stop carve-outs (Špecifikácia approval + deploy) that fire at EVERY
  level (design §2.3, D3/D6).
* :func:`orchestrator.auditor_effort_for_level` — the OQ-9 dial→Auditor-effort
  coupling (deeper Auditor at higher autonomy).

The pure-logic tests need no DB; the resolver tests build project/version/
pipeline_state rows directly with valid v2 enum values (NOT the v1 ``start``
flow, which Milestone-A's enum rebuild rejects).
"""

import uuid

from backend.db.models.foundation import User
from backend.db.models.pipeline import PipelineState
from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.services import orchestrator
from backend.services import system_setting as system_setting_service

# ── pure dial logic (no DB) ────────────────────────────────────────────────


def test_dial_levels_are_the_four_presets():
    assert orchestrator.MIERA_AUTONOMIE_VALUES == (
        "plna",
        "len_na_konci",
        "pri_klucovych_bodoch",
        "po_kazdej_faze",
    )


def test_dial_stops_at_per_level_matches_design():
    # design §2.3 table: which dial-governed boundaries halt at each level.
    navrh = orchestrator.SCHVALOVACI_BOD_NAVRH
    prog = orchestrator.SCHVALOVACI_BOD_PROGRAMOVANIE
    verif = orchestrator.SCHVALOVACI_BOD_VERIFIKACIA

    # Plná autonómia — runs non-stop, no dial-governed stop fires.
    assert not orchestrator.dial_stops_at("plna", navrh)
    assert not orchestrator.dial_stops_at("plna", prog)
    assert not orchestrator.dial_stops_at("plna", verif)

    # Len na konci — only the build-done (Verifikácia) stop fires.
    assert not orchestrator.dial_stops_at("len_na_konci", navrh)
    assert not orchestrator.dial_stops_at("len_na_konci", prog)
    assert orchestrator.dial_stops_at("len_na_konci", verif)

    # Pri kľúčových bodoch — after Návrh + at build-done, NOT after Programovanie.
    assert orchestrator.dial_stops_at("pri_klucovych_bodoch", navrh)
    assert not orchestrator.dial_stops_at("pri_klucovych_bodoch", prog)
    assert orchestrator.dial_stops_at("pri_klucovych_bodoch", verif)

    # Po každej fáze — stops after each dial-governed phase.
    assert orchestrator.dial_stops_at("po_kazdej_faze", navrh)
    assert orchestrator.dial_stops_at("po_kazdej_faze", prog)
    assert orchestrator.dial_stops_at("po_kazdej_faze", verif)


def test_dial_always_stops_are_dial_independent():
    # The two carve-outs (design §2.3, D3/D6) stop at EVERY level, including plná.
    for level in orchestrator.MIERA_AUTONOMIE_VALUES:
        assert orchestrator.dial_stops_at(level, "approve_spec") is True
        assert orchestrator.dial_stops_at(level, "deploy") is True


def test_dial_stops_at_unknown_level_degrades_to_default():
    # An unrecognised level behaves like the default (plná) for dial-governed boundaries,
    # but the always-stops still fire.
    assert orchestrator.dial_stops_at("garbage", orchestrator.SCHVALOVACI_BOD_NAVRH) is False
    assert orchestrator.dial_stops_at("garbage", "approve_spec") is True


def test_dial_stops_at_unknown_boundary_never_stops():
    # A boundary that is neither an always-stop nor dial-governed is an internal step → no stop.
    assert orchestrator.dial_stops_at("po_kazdej_faze", "some_internal_step") is False


def test_auditor_effort_scales_inverse_to_oversight():
    # OQ-9: higher autonomy → deeper (higher-effort) Auditor.
    assert orchestrator.auditor_effort_for_level("plna") == "max"
    assert orchestrator.auditor_effort_for_level("len_na_konci") == "max"
    assert orchestrator.auditor_effort_for_level("pri_klucovych_bodoch") == "high"
    assert orchestrator.auditor_effort_for_level("po_kazdej_faze") == "high"
    # unknown degrades to the default's effort
    assert orchestrator.auditor_effort_for_level("garbage") == "max"


# ── resolver (DB-backed, AUTON-6 override order) ────────────────────────────


def _seed_project_version(db_session, *, project_dial=None, flow_type="new_version", build_dial=None):
    """Create a User + Project (+ optional per-project dial) + Version + PipelineState
    (+ optional per-build dial), using valid v2 enum values. Returns the version id."""
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
    version = Version(project_id=project.id, version_number=f"1.{uuid.uuid4().int % 10**4}.0")
    db_session.add(version)
    db_session.flush()
    state = PipelineState(
        version_id=version.id,
        flow_type=flow_type,
        current_stage="priprava",
        current_actor="ai_agent",
        status="agent_working",
        next_action="",
        miera_autonomie=build_dial,
    )
    db_session.add(state)
    db_session.flush()
    return version.id


def test_resolver_global_default_when_all_null(db_session):
    # No per-build, no per-project, no system_settings row → the DEFAULT_SETTINGS global (plná).
    version_id = _seed_project_version(db_session)
    assert orchestrator.resolve_miera_autonomie(db_session, version_id) == "plna"


def test_resolver_project_overrides_global(db_session):
    # NULL per-build → falls through to the per-project value.
    version_id = _seed_project_version(db_session, project_dial="po_kazdej_faze")
    assert orchestrator.resolve_miera_autonomie(db_session, version_id) == "po_kazdej_faze"


def test_resolver_build_beats_project_beats_global(db_session):
    # The per-build value wins over the per-project value (which itself would beat the global).
    version_id = _seed_project_version(db_session, project_dial="po_kazdej_faze", build_dial="pri_klucovych_bodoch")
    assert orchestrator.resolve_miera_autonomie(db_session, version_id) == "pri_klucovych_bodoch"


def test_resolver_null_build_falls_through_to_project(db_session):
    # Explicit: NULL at the build layer inherits the project layer.
    version_id = _seed_project_version(db_session, project_dial="len_na_konci", build_dial=None)
    assert orchestrator.resolve_miera_autonomie(db_session, version_id) == "len_na_konci"


def test_resolver_unrecognised_build_value_degrades_through(db_session):
    # A stored value that is not a recognised preset is treated as NULL → falls through to project.
    version_id = _seed_project_version(db_session, project_dial="len_na_konci", build_dial="bogus")
    assert orchestrator.resolve_miera_autonomie(db_session, version_id) == "len_na_konci"


def test_resolver_system_settings_row_overrides_default(db_session):
    # A system_settings row for the global layer wins when both override columns are NULL.
    system_setting_service.upsert(db_session, "miera_autonomie", "pri_klucovych_bodoch")
    try:
        version_id = _seed_project_version(db_session)
        assert orchestrator.resolve_miera_autonomie(db_session, version_id) == "pri_klucovych_bodoch"
    finally:
        system_setting_service.invalidate_cache("miera_autonomie")


def test_resolver_fast_fix_is_always_full_auto(db_session):
    # design §2.3: fast-fix = dial at full-auto, overriding every override layer.
    version_id = _seed_project_version(
        db_session, flow_type="fast_fix", project_dial="po_kazdej_faze", build_dial="po_kazdej_faze"
    )
    assert orchestrator.resolve_miera_autonomie(db_session, version_id) == "plna"
