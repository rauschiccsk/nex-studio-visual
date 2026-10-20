"""Tests for the Project model."""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, ProgrammingError

from backend.db.models.foundation import User
from backend.db.models.projects import Project


def _make_user(db_session, **overrides) -> User:
    """Create and persist a User for FK references."""
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
    """Create a Project instance with sensible defaults."""
    if user is None:
        user = _make_user(db_session)
    defaults = {
        "name": f"Project {uuid.uuid4().hex[:8]}",
        "slug": f"project-{uuid.uuid4().hex[:8]}",
        "type": "standard",
        "auth_mode": "password",
        "description": "Test project description",
        "created_by": user.id,
    }
    defaults.update(overrides)
    return Project(**defaults)


class TestProjectModel:
    """Unit tests for Project ORM model."""

    def test_create_project(self, db_session):
        """Can insert a valid project."""
        project = _make_project(db_session)
        db_session.add(project)
        db_session.flush()

        assert project.id is not None
        assert project.created_at is not None
        assert project.updated_at is not None

    def test_status_defaults_active(self, db_session):
        """status should default to 'active' via server_default."""
        project = _make_project(db_session)
        db_session.add(project)
        db_session.flush()

        db_session.expire(project)
        assert project.status == "active"

    def test_guardian_enabled_defaults_false(self, db_session):
        """guardian_enabled should default to False via server_default."""
        project = _make_project(db_session)
        db_session.add(project)
        db_session.flush()

        db_session.expire(project)
        assert project.guardian_enabled is False

    def test_unique_name(self, db_session):
        """Duplicate name must be rejected."""
        user = _make_user(db_session)
        p1 = _make_project(db_session, user=user, name="Duplicate Name")
        db_session.add(p1)
        db_session.flush()

        p2 = _make_project(db_session, user=user, name="Duplicate Name")
        db_session.add(p2)
        with pytest.raises((IntegrityError, ProgrammingError)):
            db_session.flush()
        db_session.rollback()

    def test_unique_slug(self, db_session):
        """Duplicate slug must be rejected."""
        user = _make_user(db_session)
        p1 = _make_project(db_session, user=user, slug="dup-slug")
        db_session.add(p1)
        db_session.flush()

        p2 = _make_project(db_session, user=user, slug="dup-slug")
        db_session.add(p2)
        with pytest.raises((IntegrityError, ProgrammingError)):
            db_session.flush()
        db_session.rollback()

    def test_type_check_constraint(self, db_session):
        """Invalid type value must be rejected."""
        project = _make_project(db_session, type="invalid")
        db_session.add(project)
        with pytest.raises((IntegrityError, ProgrammingError)):
            db_session.flush()
        db_session.rollback()

    @pytest.mark.parametrize(
        ("type_value", "ports"), [("standard", {}), ("web", {"frontend_port": 14961, "web_port": 14965})]
    )
    def test_valid_types(self, db_session, type_value, ports):
        """All valid type values must be accepted — a Web project with its web's port (DEV-160)."""
        project = _make_project(db_session, type=type_value, **ports)
        db_session.add(project)
        db_session.flush()
        assert project.type == type_value

    def test_auth_mode_check_constraint(self, db_session):
        """Invalid auth_mode value must be rejected."""
        project = _make_project(db_session, auth_mode="invalid")
        db_session.add(project)
        with pytest.raises((IntegrityError, ProgrammingError)):
            db_session.flush()
        db_session.rollback()

    @pytest.mark.parametrize("auth_mode", ["password", "token"])
    def test_valid_auth_modes(self, db_session, auth_mode):
        """All valid auth_mode values must be accepted."""
        project = _make_project(db_session, auth_mode=auth_mode)
        db_session.add(project)
        db_session.flush()
        assert project.auth_mode == auth_mode

    def test_status_check_constraint(self, db_session):
        """Invalid status value must be rejected."""
        project = _make_project(db_session, status="deleted")
        db_session.add(project)
        with pytest.raises((IntegrityError, ProgrammingError)):
            db_session.flush()
        db_session.rollback()

    @pytest.mark.parametrize("status", ["active", "archived", "paused"])
    def test_valid_statuses(self, db_session, status):
        """All valid status values must be accepted."""
        project = _make_project(db_session, status=status)
        db_session.add(project)
        db_session.flush()
        assert project.status == status

    def test_nullable_ports(self, db_session):
        """Port columns can be NULL."""
        project = _make_project(
            db_session,
            backend_port=None,
            frontend_port=None,
            db_port=None,
        )
        db_session.add(project)
        db_session.flush()
        assert project.backend_port is None

    def test_ports_with_values(self, db_session):
        """Port columns accept integer values."""
        project = _make_project(
            db_session,
            backend_port=9176,
            frontend_port=9177,
            db_port=9178,
        )
        db_session.add(project)
        db_session.flush()
        assert project.backend_port == 9176
        assert project.frontend_port == 9177
        assert project.db_port == 9178

    def test_name_not_nullable(self, db_session):
        """name=NULL must be rejected."""
        project = _make_project(db_session, name=None)
        db_session.add(project)
        with pytest.raises((IntegrityError, ProgrammingError)):
            db_session.flush()
        db_session.rollback()

    def test_slug_not_nullable(self, db_session):
        """slug=NULL must be rejected."""
        project = _make_project(db_session, slug=None)
        db_session.add(project)
        with pytest.raises((IntegrityError, ProgrammingError)):
            db_session.flush()
        db_session.rollback()

    def test_type_not_nullable(self, db_session):
        """type=NULL must be rejected."""
        project = _make_project(db_session, type=None)
        db_session.add(project)
        with pytest.raises((IntegrityError, ProgrammingError)):
            db_session.flush()
        db_session.rollback()

    def test_auth_mode_not_nullable(self, db_session):
        """auth_mode=NULL must be rejected."""
        project = _make_project(db_session, auth_mode=None)
        db_session.add(project)
        with pytest.raises((IntegrityError, ProgrammingError)):
            db_session.flush()
        db_session.rollback()

    def test_description_not_nullable(self, db_session):
        """description=NULL must be rejected."""
        project = _make_project(db_session, description=None)
        db_session.add(project)
        with pytest.raises((IntegrityError, ProgrammingError)):
            db_session.flush()
        db_session.rollback()

    def test_created_by_not_nullable(self, db_session):
        """created_by=NULL must be rejected."""
        project = _make_project(db_session, created_by=None)
        db_session.add(project)
        with pytest.raises((IntegrityError, ProgrammingError)):
            db_session.flush()
        db_session.rollback()

    def test_created_by_fk_restrict(self, db_session):
        """Deleting a user referenced by project.created_by must be blocked (RESTRICT)."""
        user = _make_user(db_session)
        project = _make_project(db_session, user=user)
        db_session.add(project)
        db_session.flush()

        # Must use raw SQL — ORM session.delete() sets FK to NULL first
        with pytest.raises((IntegrityError, ProgrammingError)):
            db_session.execute(
                text("DELETE FROM users WHERE id = :id"),
                {"id": str(user.id)},
            )
        db_session.rollback()

    def test_created_by_fk_valid(self, db_session):
        """created_by must reference an existing user."""
        fake_user_id = uuid.uuid4()
        project = _make_project(db_session, created_by=fake_user_id)
        db_session.add(project)
        with pytest.raises((IntegrityError, ProgrammingError)):
            db_session.flush()
        db_session.rollback()
