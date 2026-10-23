"""Tests for the Feat model."""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, ProgrammingError

from backend.db.models.foundation import User
from backend.db.models.projects import Project
from backend.db.models.tasks import Epic, Feat


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


def _make_project(db_session, **overrides) -> Project:
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
    return Feat(**defaults)


class TestFeatModel:
    """Unit tests for Feat ORM model."""

    def test_create_feat(self, db_session):
        """Can insert a valid feat."""
        feat = _make_feat(db_session)
        db_session.add(feat)
        db_session.flush()

        assert feat.id is not None
        assert feat.created_at is not None
        assert feat.updated_at is not None

    def test_status_defaults_todo(self, db_session):
        """status should default to 'todo' via server_default."""
        feat = _make_feat(db_session)
        db_session.add(feat)
        db_session.flush()

        db_session.expire(feat)
        assert feat.status == "todo"

    def test_auto_fix_count_defaults_zero(self, db_session):
        """auto_fix_count should default to 0 via server_default."""
        feat = _make_feat(db_session)
        db_session.add(feat)
        db_session.flush()

        db_session.expire(feat)
        assert feat.auto_fix_count == 0

    def test_description_defaults_to_empty(self, db_session):
        """description defaults to empty string (NOT NULL, DEFAULT '')."""
        feat = _make_feat(db_session)
        db_session.add(feat)
        db_session.flush()

        db_session.expire(feat)
        assert feat.description == ""

    def test_description_with_value(self, db_session):
        """description can hold text."""
        feat = _make_feat(db_session, description="Some detailed description")
        db_session.add(feat)
        db_session.flush()
        assert feat.description == "Some detailed description"

    def test_unique_epic_number(self, db_session):
        """Duplicate (epic_id, number) must be rejected."""
        epic = _make_epic(db_session)
        f1 = _make_feat(db_session, epic=epic, number=1)
        db_session.add(f1)
        db_session.flush()

        f2 = _make_feat(db_session, epic=epic, number=1)
        db_session.add(f2)
        with pytest.raises((IntegrityError, ProgrammingError)):
            db_session.flush()
        db_session.rollback()

    def test_same_number_different_epics(self, db_session):
        """Same number in different epics is allowed."""
        e1 = _make_epic(db_session)
        e2 = _make_epic(db_session, number=2)
        f1 = _make_feat(db_session, epic=e1, number=1)
        f2 = _make_feat(db_session, epic=e2, number=1)
        db_session.add_all([f1, f2])
        db_session.flush()
        assert f1.number == f2.number

    def test_status_check_constraint(self, db_session):
        """Invalid status value must be rejected."""
        feat = _make_feat(db_session, status="invalid")
        db_session.add(feat)
        with pytest.raises((IntegrityError, ProgrammingError)):
            db_session.flush()
        db_session.rollback()

    @pytest.mark.parametrize("status", ["todo", "in_progress", "done", "failed"])
    def test_valid_statuses(self, db_session, status):
        """All valid status values must be accepted."""
        epic = _make_epic(db_session)
        feat = _make_feat(db_session, epic=epic, status=status, number=1)
        db_session.add(feat)
        db_session.flush()
        assert feat.status == status

    def test_epic_id_not_nullable(self, db_session):
        """epic_id=NULL must be rejected."""
        feat = _make_feat(db_session, epic_id=None)
        db_session.add(feat)
        with pytest.raises((IntegrityError, ProgrammingError)):
            db_session.flush()
        db_session.rollback()

    def test_number_not_nullable(self, db_session):
        """number=NULL must be rejected."""
        feat = _make_feat(db_session, number=None)
        db_session.add(feat)
        with pytest.raises((IntegrityError, ProgrammingError)):
            db_session.flush()
        db_session.rollback()

    def test_title_not_nullable(self, db_session):
        """title=NULL must be rejected."""
        feat = _make_feat(db_session, title=None)
        db_session.add(feat)
        with pytest.raises((IntegrityError, ProgrammingError)):
            db_session.flush()
        db_session.rollback()

    def test_epic_id_fk_invalid(self, db_session):
        """epic_id must reference an existing epic."""
        feat = _make_feat(db_session, epic_id=uuid.uuid4())
        db_session.add(feat)
        with pytest.raises((IntegrityError, ProgrammingError)):
            db_session.flush()
        db_session.rollback()

    def test_epic_cascade_delete(self, db_session):
        """Deleting an epic cascades to its feats."""
        epic = _make_epic(db_session)
        feat = _make_feat(db_session, epic=epic)
        db_session.add(feat)
        db_session.flush()

        db_session.execute(
            text("DELETE FROM epics WHERE id = :id"),
            {"id": str(epic.id)},
        )
        db_session.flush()

        result = db_session.execute(
            text("SELECT id FROM feats WHERE id = :id"),
            {"id": str(feat.id)},
        )
        assert result.scalar() is None
