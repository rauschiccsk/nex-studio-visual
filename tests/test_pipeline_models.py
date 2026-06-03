"""Tests for the F-007 pipeline models (CR-NS-018 Phase 1)."""

import uuid

import pytest
from sqlalchemy.exc import IntegrityError, ProgrammingError

from backend.db.models.foundation import User
from backend.db.models.pipeline import PipelineMessage, PipelineState
from backend.db.models.projects import Project
from backend.db.models.versions import Version


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


def _state(version, **overrides) -> PipelineState:
    defaults = {
        "version_id": version.id,
        "flow_type": "new_version",
        "current_stage": "kickoff",
        "current_actor": "coordinator",
        "status": "agent_working",
        "next_action": "Coordinator robí discovery.",
    }
    defaults.update(overrides)
    return PipelineState(**defaults)


def _message(version, **overrides) -> PipelineMessage:
    defaults = {
        "version_id": version.id,
        "stage": "gate_a",
        "author": "designer",
        "recipient": "director",
        "kind": "gate_report",
        "content": "development-spec.md hotové",
        "status": "delivered",
    }
    defaults.update(overrides)
    return PipelineMessage(**defaults)


# ── PipelineState ─────────────────────────────────────────────────────────────


class TestPipelineState:
    def test_persists_with_defaults(self, db_session):
        version = _make_version(db_session)
        state = _state(version)
        db_session.add(state)
        db_session.flush()
        db_session.refresh(state)
        assert state.id is not None
        assert state.is_regate is False
        assert state.iteration == 0
        assert state.created_at is not None

    def test_version_id_unique(self, db_session):
        version = _make_version(db_session)
        db_session.add(_state(version))
        db_session.flush()
        db_session.add(_state(version, current_stage="gate_a"))
        with pytest.raises(IntegrityError):
            db_session.flush()

    @pytest.mark.parametrize(
        "field,bad",
        [
            ("flow_type", "weekly"),
            ("current_stage", "gate_z"),
            ("current_actor", "intern"),
            ("status", "napping"),
        ],
    )
    def test_check_rejects_bad_enum(self, db_session, field, bad):
        version = _make_version(db_session)
        db_session.add(_state(version, **{field: bad}))
        with pytest.raises((IntegrityError, ProgrammingError)):
            db_session.flush()

    def test_fk_cascade_on_version_delete(self, db_session):
        version = _make_version(db_session)
        state = _state(version)
        db_session.add(state)
        db_session.flush()
        state_id = state.id

        db_session.delete(version)
        db_session.flush()
        db_session.expire_all()

        assert db_session.get(PipelineState, state_id) is None


# ── PipelineMessage ───────────────────────────────────────────────────────────


class TestPipelineMessage:
    def test_persists(self, db_session):
        version = _make_version(db_session)
        msg = _message(version, payload={"deliverables": ["a.md"]})
        db_session.add(msg)
        db_session.flush()
        db_session.refresh(msg)
        assert msg.id is not None
        assert msg.created_at is not None
        assert msg.payload == {"deliverables": ["a.md"]}

    @pytest.mark.parametrize(
        "field,bad",
        [
            ("stage", "gate_z"),
            ("author", "robot"),
            ("recipient", "robot"),
            ("kind", "rumor"),
            ("status", "shrugged"),
        ],
    )
    def test_check_rejects_bad_enum(self, db_session, field, bad):
        version = _make_version(db_session)
        db_session.add(_message(version, **{field: bad}))
        with pytest.raises((IntegrityError, ProgrammingError)):
            db_session.flush()

    def test_system_author_allowed(self, db_session):
        version = _make_version(db_session)
        db_session.add(_message(version, author="system", kind="notification"))
        db_session.flush()  # no raise

    def test_fk_cascade_on_version_delete(self, db_session):
        version = _make_version(db_session)
        msg = _message(version)
        db_session.add(msg)
        db_session.flush()
        msg_id = msg.id

        db_session.delete(version)
        db_session.flush()
        db_session.expire_all()

        assert db_session.get(PipelineMessage, msg_id) is None
