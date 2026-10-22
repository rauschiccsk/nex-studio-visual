"""Tests for :mod:`backend.services.user_session`.

Sessions are made by the login (``backend/services/auth.py``); the service reads, lists and deletes them — DEV-155
removed its create and update, which only the removed routes called. Verifies, against the SAVEPOINT-isolated session
of ``tests/conftest.py``:

* Happy-path list / get / delete.
* ``ValueError`` on a missing ``id`` for get / delete.
* List filter (``user_id``) and pagination; ordering ``created_at DESC`` (newest first).
* ``delete`` removes the row and leaves siblings on the same user intact.
"""

from __future__ import annotations

import uuid

import pytest

from backend.db.models.foundation import User, UserSession
from backend.services import user_session as service


def _make_user(db_session, **overrides) -> User:
    """Create and persist a :class:`User` for FK references."""
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


def _session(db_session, user_id) -> UserSession:
    """A session as the login makes it (``backend/services/auth.py``) — the service only reads, lists and deletes."""
    session = UserSession(user_id=user_id, token_version=1)
    db_session.add(session)
    db_session.flush()
    return session


class TestUserSessionService:
    """Synchronous CRUD coverage for the UserSession service."""

    # ------------------------------------------------------------------ create

    # ------------------------------------------------------------------- get
    def test_get_by_id(self, db_session):
        """``get_by_id`` returns the row when it exists."""
        user = _make_user(db_session)
        created = _session(db_session, user.id)

        fetched = service.get_by_id(db_session, created.id)
        assert fetched.id == created.id
        assert fetched.user_id == user.id

    def test_get_by_id_missing_raises(self, db_session):
        """``get_by_id`` raises ``ValueError`` for an unknown id."""
        with pytest.raises(ValueError, match="not found"):
            service.get_by_id(db_session, uuid.uuid4())

    # ---------------------------------------------------------------- update

    # ---------------------------------------------------------------- delete
    def test_delete(self, db_session):
        """``delete`` removes the row; subsequent lookup raises."""
        user = _make_user(db_session)
        created = _session(db_session, user.id)

        service.delete(db_session, created.id)

        with pytest.raises(ValueError, match="not found"):
            service.get_by_id(db_session, created.id)

    def test_delete_missing_raises(self, db_session):
        """``delete`` on a non-existent id raises ``ValueError``."""
        with pytest.raises(ValueError, match="not found"):
            service.delete(db_session, uuid.uuid4())

    def test_delete_one_session_leaves_siblings_intact(self, db_session):
        """Deleting one session does not affect siblings on the same user."""
        user = _make_user(db_session)
        a = _session(db_session, user.id)
        b = _session(db_session, user.id)

        service.delete(db_session, a.id)

        assert service.get_by_id(db_session, b.id).id == b.id

    # ------------------------------------------------------------------ list
    def test_list_all(self, db_session):
        """``list_user_sessions`` returns every row when no filter is supplied."""
        user = _make_user(db_session)
        created_ids: set = set()
        for _ in range(3):
            created_ids.add(_session(db_session, user.id).id)

        rows = service.list_user_sessions(db_session)
        assert created_ids.issubset({r.id for r in rows})

    def test_list_filter_by_user(self, db_session):
        """``list_user_sessions(user_id=...)`` returns only that user's sessions."""
        u1 = _make_user(db_session)
        u2 = _make_user(db_session)
        mine = _session(db_session, u1.id)
        _session(db_session, u2.id)

        rows = service.list_user_sessions(db_session, user_id=u1.id)
        assert all(r.user_id == u1.id for r in rows)
        assert any(r.id == mine.id for r in rows)

    def test_list_pagination(self, db_session):
        """``limit`` / ``offset`` restrict the result window."""
        user = _make_user(db_session)
        for _ in range(5):
            _session(db_session, user.id)

        first_page = service.list_user_sessions(db_session, user_id=user.id, limit=2, offset=0)
        second_page = service.list_user_sessions(db_session, user_id=user.id, limit=2, offset=2)

        assert len(first_page) == 2
        assert len(second_page) == 2
        first_ids = {s.id for s in first_page}
        second_ids = {s.id for s in second_page}
        assert first_ids.isdisjoint(second_ids)

    # --------------------------------------------------------------- commit
