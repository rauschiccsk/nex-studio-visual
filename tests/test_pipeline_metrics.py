"""WS-D usage/time aggregation (CR-NS-036; metrics redesign) — ``pipeline_metrics``.

The per-EPIC/FEAT/TASK scope roll-up was retired in the metrics redesign in favour of the per-role
model. These cover what remains: the version grand total (:func:`aggregate_pipeline_usage`) and the
per-ROLE-OF-ORIGIN split (:func:`aggregate_usage_by_role`) — including the ``metrics_role`` override
(a record whose ``author`` is not the role whose tokens these are) and the per-model token split.
"""

import uuid

from backend.db.models.foundation import User
from backend.db.models.pipeline import PipelineMessage
from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.services.pipeline_metrics import aggregate_pipeline_usage, aggregate_usage_by_role


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
        category="singlemodule",
        description="d",
        created_by=user.id,
    )
    db_session.add(project)
    db_session.flush()
    version = Version(project_id=project.id, version_number=f"1.{uuid.uuid4().hex[:4]}.0")
    db_session.add(version)
    db_session.flush()
    return project, version


def _msg(db_session, version_id, *, payload, stage="build", author="implementer"):
    m = PipelineMessage(
        version_id=version_id,
        stage=stage,
        author=author,
        recipient="director",
        kind="gate_report",
        content="x",
        payload=payload,
    )
    db_session.add(m)
    db_session.flush()
    return m


def test_aggregate_version_grand_total(db_session):
    _, version = _project_version(db_session)
    # two build turns (one a parse-retry → parse_attempts 2)
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
    # a gate turn: usage None (envelope carried none) + timing → counts timing only (0 tokens, +7s)
    _msg(
        db_session,
        version.id,
        stage="gate_a",
        author="designer",
        payload={"usage": None, "timing": {"duration_seconds": 7.0, "parse_attempts": 1}},
    )
    # a plain system note: no usage AND no timing → skipped entirely (not a metered dispatch)
    _msg(db_session, version.id, stage="gate_a", author="system", payload={"deliverables": []})

    agg = aggregate_pipeline_usage(db_session, version.id)

    assert agg.version.input_tokens == 150  # gate message's usage was None
    assert agg.version.output_tokens == 60
    assert agg.version.duration_seconds == 15.0  # 5 + 3 + 7
    assert agg.version.messages == 3  # 2 build + 1 gate; the plain system note is not counted
    assert agg.version.parse_attempts == 4  # 1 + 2 + 1
    # per-model split: the gate's None usage folds 0 tokens under "_unknown"; the build turns under "m"
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
    assert aggregate_usage_by_role(db_session, version.id) == {}


def test_aggregate_by_role_groups_by_author_and_model(db_session):
    _, version = _project_version(db_session)
    _msg(
        db_session,
        version.id,
        author="implementer",
        payload={
            "usage": {"input_tokens": 100, "output_tokens": 40, "model": "claude-opus-4-8"},
            "timing": {"duration_seconds": 5.0, "parse_attempts": 1},
        },
    )
    _msg(
        db_session,
        version.id,
        author="designer",
        stage="gate_a",
        payload={
            "usage": {"input_tokens": 200, "output_tokens": 80, "model": "claude-sonnet-4-6"},
            "timing": {"duration_seconds": 9.0, "parse_attempts": 1},
        },
    )

    by_role = aggregate_usage_by_role(db_session, version.id)
    assert set(by_role) == {"implementer", "designer"}
    assert by_role["implementer"].input_tokens == 100
    assert by_role["implementer"].by_model["claude-opus-4-8"].output_tokens == 40
    assert by_role["designer"].duration_seconds == 9.0
    assert by_role["designer"].by_model["claude-sonnet-4-6"].input_tokens == 200


def test_aggregate_by_role_metrics_role_override(db_session):
    """Role-of-origin (``payload.metrics_role``) wins over the record's ``author`` — the engine fold/seed
    sites tag the worker whose tokens these are so they don't leak to coordinator/system (§1.1)."""
    _, version = _project_version(db_session)
    # a Coordinator-authored relay carrying a failed Designer's seeded tokens
    _msg(
        db_session,
        version.id,
        author="coordinator",
        payload={
            "usage": {"input_tokens": 70, "output_tokens": 30, "model": "m"},
            "timing": {"duration_seconds": 4.0, "parse_attempts": 1},
            "metrics_role": "designer",
        },
    )
    # a failed Implementer attempt recorded under author="system"
    _msg(
        db_session,
        version.id,
        author="system",
        payload={
            "usage": {"input_tokens": 24, "output_tokens": 9, "model": "m"},
            "timing": {"duration_seconds": 2.0, "parse_attempts": 3},
            "metrics_role": "implementer",
        },
    )

    by_role = aggregate_usage_by_role(db_session, version.id)
    # NOT attributed to coordinator / system — attributed to the role of origin
    assert "coordinator" not in by_role
    assert "system" not in by_role
    assert by_role["designer"].input_tokens == 70
    assert by_role["implementer"].input_tokens == 24
    # the version grand total is unchanged by the regrouping (same scan rule)
    assert aggregate_pipeline_usage(db_session, version.id).version.input_tokens == 94
