"""WS-D usage/time aggregation (CR-NS-036) — ``pipeline_metrics.aggregate_pipeline_usage``.

Builds a version with one EPIC → FEAT → two TASKs, seeds ``PipelineMessage`` payloads carrying the
WS-D ``usage`` / ``timing`` blocks the orchestrator writes, and asserts the roll-up sums correctly
per TASK → FEAT → EPIC plus the version grand total (including gate overhead that has no task_id).
"""

import uuid

from backend.db.models.foundation import User
from backend.db.models.pipeline import PipelineMessage
from backend.db.models.projects import Project
from backend.db.models.tasks import Epic, Feat, Task
from backend.db.models.versions import Version
from backend.services.pipeline_metrics import aggregate_pipeline_usage


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


def test_aggregate_rolls_up_task_feat_epic_and_version(db_session):
    project, version = _project_version(db_session)
    epic = Epic(project_id=project.id, version_id=version.id, number=1, title="E1")
    db_session.add(epic)
    db_session.flush()
    feat = Feat(epic_id=epic.id, number=1, title="F1")
    db_session.add(feat)
    db_session.flush()
    t1 = Task(feat_id=feat.id, number=1, title="T1", task_type="backend")
    t2 = Task(feat_id=feat.id, number=2, title="T2", task_type="backend")
    db_session.add_all([t1, t2])
    db_session.flush()

    # two build turns for t1 (one of them a parse-retry → parse_attempts 2), one for t2
    _msg(
        db_session,
        version.id,
        payload={
            "task_id": str(t1.id),
            "usage": {"input_tokens": 100, "output_tokens": 40, "model": "m"},
            "timing": {"duration_seconds": 5.0, "parse_attempts": 1},
        },
    )
    _msg(
        db_session,
        version.id,
        payload={
            "task_id": str(t1.id),
            "usage": {"input_tokens": 50, "output_tokens": 20, "model": "m"},
            "timing": {"duration_seconds": 3.0, "parse_attempts": 2},
        },
    )
    _msg(
        db_session,
        version.id,
        payload={
            "task_id": str(t2.id),
            "usage": {"input_tokens": 10, "output_tokens": 5, "model": "m"},
            "timing": {"duration_seconds": 1.0, "parse_attempts": 1},
        },
    )
    # a gate turn: usage None (envelope carried none) + timing, no task_id → version overhead only
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

    # TASK roll-up: t1 = sum of its two turns
    assert agg.by_task[t1.id].input_tokens == 150
    assert agg.by_task[t1.id].output_tokens == 60
    assert agg.by_task[t1.id].duration_seconds == 8.0
    assert agg.by_task[t1.id].messages == 2
    assert agg.by_task[t2.id].input_tokens == 10

    # FEAT roll-up: t1 + t2
    assert agg.by_feat[feat.id].input_tokens == 160
    assert agg.by_feat[feat.id].output_tokens == 65
    assert agg.by_feat[feat.id].duration_seconds == 9.0
    assert agg.by_feat[feat.id].messages == 3

    # EPIC roll-up: == the one feat
    assert agg.by_epic[epic.id].input_tokens == 160
    assert agg.by_epic[epic.id].messages == 3

    # VERSION total: every metered message incl. the gate overhead (0 tokens, +7s); system note skipped
    assert agg.version.input_tokens == 160  # gate message's usage was None
    assert agg.version.output_tokens == 65
    assert agg.version.duration_seconds == 16.0  # 8 + 1 + 7
    assert agg.version.messages == 4  # 3 build + 1 gate; the plain system note is not counted


def test_aggregate_empty_version_is_zero(db_session):
    _, version = _project_version(db_session)
    agg = aggregate_pipeline_usage(db_session, version.id)
    assert agg.version.input_tokens == 0
    assert agg.version.output_tokens == 0
    assert agg.version.duration_seconds == 0.0
    assert agg.version.messages == 0
    assert agg.by_task == {} and agg.by_feat == {} and agg.by_epic == {}
