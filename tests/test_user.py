"""Tests for the User model."""

import uuid

import pytest
from sqlalchemy.exc import IntegrityError, ProgrammingError

from backend.db.models.foundation import User


def _make_user(**overrides) -> User:
    """Create a User instance with sensible defaults."""
    defaults = {
        "username": f"user_{uuid.uuid4().hex[:8]}",
        "email": f"{uuid.uuid4().hex[:8]}@example.com",
        "password_hash": "hashed_password_placeholder",
        "role": "ri",
    }
    defaults.update(overrides)
    return User(**defaults)


class TestUserModel:
    """Unit tests for User ORM model."""

    def test_create_user(self, db_session):
        """Can insert a valid user."""
        user = _make_user(username="testuser", email="test@example.com")
        db_session.add(user)
        db_session.flush()

        assert user.id is not None
        assert user.created_at is not None
        assert user.updated_at is not None
        assert user.is_active is True

    def test_is_active_defaults_true(self, db_session):
        """is_active should default to True via server_default."""
        user = _make_user()
        db_session.add(user)
        db_session.flush()

        # Re-read from DB to verify server_default
        db_session.expire(user)
        assert user.is_active is True

    def test_unique_username(self, db_session):
        """Duplicate username must be rejected."""
        u1 = _make_user(username="duplicate")
        db_session.add(u1)
        db_session.flush()

        u2 = _make_user(username="duplicate")
        db_session.add(u2)
        with pytest.raises((IntegrityError, ProgrammingError)):
            db_session.flush()
        db_session.rollback()

    def test_shared_email_allowed(self, db_session):
        """DEV-321: two accounts may share one email — only the username is unique."""
        u1 = _make_user(email="dup@example.com")
        db_session.add(u1)
        db_session.flush()

        u2 = _make_user(email="dup@example.com")
        db_session.add(u2)
        db_session.flush()
        assert u1.id != u2.id

    def test_role_check_constraint(self, db_session):
        """Invalid role value must be rejected by CHECK constraint."""
        user = _make_user(role="admin")
        db_session.add(user)
        with pytest.raises((IntegrityError, ProgrammingError)):
            db_session.flush()
        db_session.rollback()

    @pytest.mark.parametrize("role", ["ri", "ha", "shu"])
    def test_all_valid_roles(self, db_session, role):
        """All three role values must be accepted."""
        user = _make_user(role=role)
        db_session.add(user)
        db_session.flush()
        assert user.role == role

    def test_username_not_nullable(self, db_session):
        """username=NULL must be rejected."""
        user = _make_user(username=None)
        db_session.add(user)
        with pytest.raises((IntegrityError, ProgrammingError)):
            db_session.flush()
        db_session.rollback()

    def test_email_not_nullable(self, db_session):
        """email=NULL must be rejected."""
        user = _make_user(email=None)
        db_session.add(user)
        with pytest.raises((IntegrityError, ProgrammingError)):
            db_session.flush()
        db_session.rollback()

    def test_password_hash_not_nullable(self, db_session):
        """password_hash=NULL must be rejected."""
        user = _make_user(password_hash=None)
        db_session.add(user)
        with pytest.raises((IntegrityError, ProgrammingError)):
            db_session.flush()
        db_session.rollback()

    def test_role_not_nullable(self, db_session):
        """role=NULL must be rejected."""
        user = _make_user(role=None)
        db_session.add(user)
        with pytest.raises((IntegrityError, ProgrammingError)):
            db_session.flush()
        db_session.rollback()
