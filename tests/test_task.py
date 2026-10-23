"""Tests for the Task model."""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, ProgrammingError

from backend.db.models.foundation import User
from backend.db.models.projects import Project
from backend.db.models.tasks import Epic, Feat, Task


def _make_user(db_session, **overrides) -> User:
    defaults = {
        "username": f"user_{uuid.uuid4().hex[:8]}",
        "email": f"{uuid.uuid4().hex[:8]}@example.com",
        "password_hash": "hashed_password_placeholder",
        "role": "ri",
    }
    defaults.update(overrides)
    user = User(**defaults)
    db_session.add(user)
    db_session.flush()
    return user


def _make_project(db_session, *, user: User | None = None, **overrides) -> Project:
    if user is None:
        user = _make_user(db_session)
    defaults = {
        "name": f"Project {uuid.uuid4().hex[:8]}",
        "slug": f"project-{uuid.uuid4().hex[:8]}",
        "type": "standard",
        "auth_mode": "password",
        "description": "Test project",
        "created_by": user.id,
    }
    defaults.update(overrides)
    project = Project(**defaults)
    db_session.add(project)
    db_session.flush()
    return project


def _make_epic(db_session, *, project: Project | None = None, **overrides) -> Epic:
    if project is None:
        project = _make_project(db_session)
    defaults = {
        "project_id": project.id,
        "number": 1,
        "title": "Epic title",
    }
    defaults.update(overrides)
    epic = Epic(**defaults)
    db_session.add(epic)
    db_session.flush()
    return epic


def _make_feat(db_session, *, epic: Epic | None = None, **overrides) -> Feat:
    if epic is None:
        epic = _make_epic(db_session)
    defaults = {
        "epic_id": epic.id,
        "number": 1,
        "title": "Feat title",
    }
    defaults.update(overrides)
    feat = Feat(**defaults)
    db_session.add(feat)
    db_session.flush()
    return feat


def _make_task(db_session, *, feat: Feat | None = None, **overrides) -> Task:
    if feat is None:
        feat = _make_feat(db_session)
    defaults = {
        "feat_id": feat.id,
        "number": 1,
        "title": "Task title",
        "task_type": "backend",
    }
    defaults.update(overrides)
    return Task(**defaults)


class TestTaskModel:
    """Unit tests for the Task ORM model."""

    def test_create_task(self, db_session):
        """Can insert a valid task."""
        task = _make_task(db_session)
        db_session.add(task)
        db_session.flush()

        assert task.id is not None
        assert task.created_at is not None
        assert task.updated_at is not None

    def test_status_defaults_todo(self, db_session):
        """status defaults to 'todo' via server_default."""
        task = _make_task(db_session)
        db_session.add(task)
        db_session.flush()

        db_session.expire(task)
        assert task.status == "todo"

    def test_description_defaults_empty(self, db_session):
        """description defaults to '' via server_default."""
        task = _make_task(db_session)
        db_session.add(task)
        db_session.flush()

        db_session.expire(task)
        assert task.description == ""

    def test_description_with_value(self, db_session):
        """description can hold text."""
        task = _make_task(db_session, description="Detailed description")
        db_session.add(task)
        db_session.flush()
        assert task.description == "Detailed description"

    def test_unique_feat_number(self, db_session):
        """Duplicate (feat_id, number) must be rejected."""
        feat = _make_feat(db_session)
        t1 = _make_task(db_session, feat=feat, number=1)
        db_session.add(t1)
        db_session.flush()

        t2 = _make_task(db_session, feat=feat, number=1)
        db_session.add(t2)
        with pytest.raises((IntegrityError, ProgrammingError)):
            db_session.flush()
        db_session.rollback()

    def test_same_number_different_feats(self, db_session):
        """Same number in different feats is allowed."""
        f1 = _make_feat(db_session)
        f2 = _make_feat(db_session, epic=_make_epic(db_session), number=1)
        t1 = _make_task(db_session, feat=f1, number=1)
        t2 = _make_task(db_session, feat=f2, number=1)
        db_session.add_all([t1, t2])
        db_session.flush()
        assert t1.id != t2.id

    def test_status_check_constraint(self, db_session):
        """Invalid status value must be rejected."""
        task = _make_task(db_session, status="invalid")
        db_session.add(task)
        with pytest.raises((IntegrityError, ProgrammingError)):
            db_session.flush()
        db_session.rollback()

    @pytest.mark.parametrize("status", ["todo", "in_progress", "done", "failed"])
    def test_valid_statuses(self, db_session, status):
        """All valid status values must be accepted."""
        task = _make_task(db_session, status=status)
        db_session.add(task)
        db_session.flush()
        assert task.status == status

    def test_task_type_check_constraint(self, db_session):
        """Invalid task_type value must be rejected."""
        task = _make_task(db_session, task_type="unknown")
        db_session.add(task)
        with pytest.raises((IntegrityError, ProgrammingError)):
            db_session.flush()
        db_session.rollback()

    @pytest.mark.parametrize("task_type", ["backend", "frontend", "migration", "test", "docs"])
    def test_valid_task_types(self, db_session, task_type):
        """All valid task_type values must be accepted."""
        task = _make_task(db_session, task_type=task_type)
        db_session.add(task)
        db_session.flush()
        assert task.task_type == task_type

    def test_nullable_fields(self, db_session):
        """Optional fields can be NULL."""
        task = _make_task(
            db_session,
            estimated_minutes=None,
            checklist_type=None,
        )
        db_session.add(task)
        db_session.flush()

        assert task.estimated_minutes is None
        assert task.checklist_type is None

    def test_checklist_type_with_value(self, db_session):
        """checklist_type can hold string values."""
        task = _make_task(db_session, checklist_type="service")
        db_session.add(task)
        db_session.flush()
        assert task.checklist_type == "service"

    def test_baseline_sha_defaults_null_and_holds_sha(self, db_session):
        """CR-NS-020 CR-1: baseline_sha is nullable (dormant in CR-1) and holds a 40-char SHA."""
        task = _make_task(db_session)
        db_session.add(task)
        db_session.flush()
        assert task.baseline_sha is None

        task.baseline_sha = "a" * 40
        db_session.flush()
        db_session.refresh(task)
        assert task.baseline_sha == "a" * 40

    def test_title_not_nullable(self, db_session):
        """title=NULL must be rejected."""
        task = _make_task(db_session, title=None)
        db_session.add(task)
        with pytest.raises((IntegrityError, ProgrammingError)):
            db_session.flush()
        db_session.rollback()

    def test_feat_id_not_nullable(self, db_session):
        """feat_id=NULL must be rejected."""
        task = _make_task(db_session, feat_id=None)
        db_session.add(task)
        with pytest.raises((IntegrityError, ProgrammingError)):
            db_session.flush()
        db_session.rollback()

    def test_number_not_nullable(self, db_session):
        """number=NULL must be rejected."""
        task = _make_task(db_session, number=None)
        db_session.add(task)
        with pytest.raises((IntegrityError, ProgrammingError)):
            db_session.flush()
        db_session.rollback()

    def test_task_type_not_nullable(self, db_session):
        """task_type=NULL must be rejected."""
        task = _make_task(db_session, task_type=None)
        db_session.add(task)
        with pytest.raises((IntegrityError, ProgrammingError)):
            db_session.flush()
        db_session.rollback()

    def test_feat_id_fk_invalid(self, db_session):
        """feat_id must reference an existing feat."""
        task = _make_task(db_session, feat_id=uuid.uuid4())
        db_session.add(task)
        with pytest.raises((IntegrityError, ProgrammingError)):
            db_session.flush()
        db_session.rollback()

    def test_feat_cascade_delete(self, db_session):
        """Deleting a feat cascades to its tasks."""
        feat = _make_feat(db_session)
        task = _make_task(db_session, feat=feat)
        db_session.add(task)
        db_session.flush()

        task_id = task.id
        db_session.execute(
            text("DELETE FROM feats WHERE id = :id"),
            {"id": str(feat.id)},
        )
        db_session.flush()

        result = db_session.execute(
            text("SELECT id FROM tasks WHERE id = :id"),
            {"id": str(task_id)},
        )
        assert result.scalar() is None
