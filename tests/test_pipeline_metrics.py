"""WS-D usage/time aggregation (CR-NS-036; v2 metrics per-phase basis, CR-V2-029) — ``pipeline_metrics``.

The per-EPIC/FEAT/TASK scope roll-up was retired in the metrics redesign; the v1 per-ROLE-OF-ORIGIN
split was replaced by a per-PHASE split (CR-V2-029). These cover what remains: the version grand total
(:func:`aggregate_pipeline_usage`) and the per-PHASE split (:func:`aggregate_usage_by_phase`) —
including the per-turn ``payload.phase`` stamp (CR-V2-009; wins over the message's ``stage``) and the
per-model token split. Fixtures use the v2 4-phase stage/actor vocabulary.
"""

import uuid

from backend.db.models.foundation import User
from backend.db.models.pipeline import PipelineMessage
from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.services.pipeline_metrics import aggregate_pipeline_usage, aggregate_usage_by_phase


def _project_version(db_session):
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
        type="standard",
        auth_mode="password",
        description="d",
        created_by=user.id,
    )
    db_session.add(project)
    db_session.flush()
    version = Version(project_id=project.id, version_number=f"1.{uuid.uuid4().hex[:4]}.0")
    db_session.add(version)
    db_session.flush()
    return project, version


def _msg(db_session, version_id, *, payload, stage="programovanie", author="ai_agent"):
    m = PipelineMessage(
        version_id=version_id,
        stage=stage,
        author=author,
        recipient="manazer",
        kind="gate_report",
        content="x",
        payload=payload,
    )
    db_session.add(m)
    db_session.flush()
    return m


def test_aggregate_version_grand_total(db_session):
    _, version = _project_version(db_session)
    # two programovanie turns (one a parse-retry → parse_attempts 2)
    _msg(
        db_session,
        version.id,
        payload={
            "usage": {"input_tokens": 100, "output_tokens": 40, "model": "m"},
            "timing": {"duration_seconds": 5.0, "parse_attempts": 1},
        },
    )
    _msg(
        db_session,
        version.id,
        payload={
            "usage": {"input_tokens": 50, "output_tokens": 20, "model": "m"},
            "timing": {"duration_seconds": 3.0, "parse_attempts": 2},
        },
    )
    # a navrh turn: usage None (envelope carried none) + timing → counts timing only (0 tokens, +7s)
    _msg(
        db_session,
        version.id,
        stage="navrh",
        payload={"usage": None, "timing": {"duration_seconds": 7.0, "parse_attempts": 1}},
    )
    # a plain system note: no usage AND no timing → skipped entirely (not a metered dispatch)
    _msg(db_session, version.id, stage="navrh", author="system", payload={"deliverables": []})

    agg = aggregate_pipeline_usage(db_session, version.id)

    assert agg.version.input_tokens == 150  # navrh message's usage was None
    assert agg.version.output_tokens == 60
    assert agg.version.duration_seconds == 15.0  # 5 + 3 + 7
    assert agg.version.messages == 3  # 2 programovanie + 1 navrh; the plain system note is not counted
    assert agg.version.parse_attempts == 4  # 1 + 2 + 1
    # per-model split: the navrh None usage folds 0 tokens under "_unknown"; the programovanie turns under "m"
    assert agg.version.by_model["m"].input_tokens == 150
    assert agg.version.by_model["_unknown"].input_tokens == 0


def test_aggregate_empty_version_is_zero(db_session):
    _, version = _project_version(db_session)
    agg = aggregate_pipeline_usage(db_session, version.id)
    assert agg.version.input_tokens == 0
    assert agg.version.output_tokens == 0
    assert agg.version.duration_seconds == 0.0
    assert agg.version.messages == 0
    assert agg.version.parse_attempts == 0
    assert agg.version.by_model == {}
    assert aggregate_usage_by_phase(db_session, version.id) == {}


def test_aggregate_by_phase_groups_by_phase_stamp_and_model(db_session):
    """The per-turn ``payload.phase`` stamp (CR-V2-009) is the grouping key, tokens split by model."""
    _, version = _project_version(db_session)
    _msg(
        db_session,
        version.id,
        stage="programovanie",
        author="ai_agent",
        payload={
            "usage": {"input_tokens": 100, "output_tokens": 40, "model": "claude-opus-4-8"},
            "timing": {"duration_seconds": 5.0, "parse_attempts": 1},
            "phase": "programovanie",
        },
    )
    _msg(
        db_session,
        version.id,
        stage="navrh",
        author="ai_agent",
        payload={
            "usage": {"input_tokens": 200, "output_tokens": 80, "model": "claude-sonnet-4-6"},
            "timing": {"duration_seconds": 9.0, "parse_attempts": 1},
            "phase": "navrh",
        },
    )

    by_phase = aggregate_usage_by_phase(db_session, version.id)
    assert set(by_phase) == {"programovanie", "navrh"}
    assert by_phase["programovanie"].input_tokens == 100
    assert by_phase["programovanie"].by_model["claude-opus-4-8"].output_tokens == 40
    assert by_phase["navrh"].duration_seconds == 9.0
    assert by_phase["navrh"].by_model["claude-sonnet-4-6"].input_tokens == 200


def test_aggregate_by_phase_stamp_wins_over_stage(db_session):
    """``payload.phase`` overrides ``msg.stage`` — a helper spawned during Programovanie whose record
    happens to carry a different stage still lands in its spawning phase (CR-V2-029)."""
    _, version = _project_version(db_session)
    # a turn recorded under stage=verifikacia but stamped phase=programovanie (the spawning phase)
    _msg(
        db_session,
        version.id,
        stage="verifikacia",
        author="ai_agent",
        payload={
            "usage": {"input_tokens": 70, "output_tokens": 30, "model": "m"},
            "timing": {"duration_seconds": 4.0, "parse_attempts": 1},
            "phase": "programovanie",
        },
    )
    # a turn with NO phase stamp → falls back to its DB stage (verifikacia)
    _msg(
        db_session,
        version.id,
        stage="verifikacia",
        author="auditor",
        payload={
            "usage": {"input_tokens": 24, "output_tokens": 9, "model": "m"},
            "timing": {"duration_seconds": 2.0, "parse_attempts": 3},
        },
    )

    by_phase = aggregate_usage_by_phase(db_session, version.id)
    assert by_phase["programovanie"].input_tokens == 70  # stamp wins over stage
    assert by_phase["verifikacia"].input_tokens == 24  # no stamp → stage fallback
    # the version grand total is unchanged by the regrouping (same scan rule)
    assert aggregate_pipeline_usage(db_session, version.id).version.input_tokens == 94
