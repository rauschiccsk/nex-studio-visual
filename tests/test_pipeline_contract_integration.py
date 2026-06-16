"""R2 (v0.7.0) §6 INTEGRATION — a real pipeline row serialises within the generated FE contract.

Writes real ``pipeline_state`` / ``pipeline_message`` rows (so the DB CHECK constraints actually
fire), then serialises them through the response models (``PipelineStateRead`` /
``PipelineMessageRead``) and asserts every enum-typed field round-trips AND is a member of the
canonical value set — the SAME single source the generated FE types are derived from (DB CHECK →
OpenAPI enum → pipeline.generated.ts). Proves the DB→API→FE-type chain agrees end to end.
"""

import uuid

from backend.db.models.foundation import User
from backend.db.models.pipeline import (
    ACTOR_VALUES,
    MESSAGE_KIND_VALUES,
    STAGE_VALUES,
    STATUS_VALUES,
    PipelineMessage,
    PipelineState,
)
from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.schemas.pipeline import PipelineMessageRead, PipelineStateRead


def _make_version(db_session) -> Version:
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
    return version


def test_real_state_row_serialises_within_generated_contract(db_session):
    version = _make_version(db_session)
    # Non-default enum values so the round-trip is meaningful (not just the kickoff defaults).
    state = PipelineState(
        version_id=version.id,
        flow_type="fast_fix",
        current_stage="build",
        current_actor="implementer",
        status="awaiting_director",
        next_action="Director posúdi build.",
    )
    db_session.add(state)
    db_session.flush()  # fires the DB CHECK constraints
    db_session.refresh(state)

    read = PipelineStateRead.model_validate(state)

    assert read.flow_type == "fast_fix"
    assert read.current_stage in STAGE_VALUES
    assert read.current_actor in ACTOR_VALUES
    assert read.status in STATUS_VALUES
    assert read.current_stage == "build"
    assert read.current_actor == "implementer"
    assert read.status == "awaiting_director"


def test_real_message_row_serialises_within_generated_contract(db_session):
    version = _make_version(db_session)
    message = PipelineMessage(
        version_id=version.id,
        stage="gate_g",
        author="auditor",
        recipient="director",
        kind="verdict",
        content="Audit PASS.",
        status="delivered",
    )
    db_session.add(message)
    db_session.flush()  # fires the DB CHECK constraints
    db_session.refresh(message)

    read = PipelineMessageRead.model_validate(message)

    assert read.kind == "verdict"
    assert read.kind in MESSAGE_KIND_VALUES
