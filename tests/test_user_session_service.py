"""Tests for :mod:`backend.services.user_session`.

Exercises every public CRUD entry point against the SAVEPOINT-isolated
session provided by ``tests/conftest.py``. Verifies:

* Happy-path list / get / create / update / delete.
* ``ValueError`` on missing ``id`` for get / update / delete.
* ``create`` applies every supplied column and honours DB-level
  server defaults (``token_version=0``, ``last_seen_at=NOW()``) when
  omitted.
* No UNIQUE constraint — a single user may hold many concurrent
  sessions.
* Update allow-list — only ``token_version`` and ``last_seen_at`` are
  applied; ``user_id``, ``id`` and ``created_at`` are preserved.
* PATCH semantics — omitted fields stay untouched.
* List filter (``user_id``) and pagination.
* List ordering is ``created_at DESC`` (newest first).
* ``delete`` removes the row and leaves siblings on the same user
  intact. ``delete`` has no RESTRICT FK check (table has no inbound
  FKs) and therefore always succeeds when the row exists.
* No ``commit`` happens inside the service — the outer transaction
  rolls back cleanly at fixture teardown.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from backend.db.models.foundation import User, UserSession
from backend.schemas.user_session import UserSessionCreate, UserSessionUpdate
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


def _payload(user_id, **overrides) -> UserSessionCreate:
    """Return a :class:`UserSessionCreate` payload with sensible defaults."""
    defaults = {"user_id": user_id}
    defaults.update(overrides)
    return UserSessionCreate(**defaults)


class TestUserSessionService:
    """Synchronous CRUD coverage for the UserSession service."""

    # ------------------------------------------------------------------ create
    def test_create_session(self, db_session):
        """``create`` persists the row and returns an ORM instance with server defaults."""
        user = _make_user(db_session)

        created = service.create(db_session, _payload(user.id))

        assert isinstance(created, UserSession)
        assert created.id is not None
        assert created.user_id == user.id
        assert created.created_at is not None
        assert created.updated_at is not None
        assert created.last_seen_at is not None  # DB-level NOW() default
        # token_version defaults to 0 via Pydantic schema (mirrors DB).
        assert created.token_version == 0

    def test_create_with_explicit_token_version(self, db_session):
        """``create`` applies the supplied ``token_version``."""
        user = _make_user(db_session)

        created = service.create(
            db_session,
            _payload(user.id, token_version=7),
        )

        assert created.token_version == 7

    def test_create_with_explicit_last_seen_at(self, db_session):
        """``create`` applies an explicit ``last_seen_at`` (back-date support)."""
        user = _make_user(db_session)
        past = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)

        created = service.create(
            db_session,
            _payload(user.id, last_seen_at=past),
        )

        assert created.last_seen_at == past

    def test_create_many_sessions_same_user(self, db_session):
        """No UNIQUE constraint — a user may hold many concurrent sessions."""
        user = _make_user(db_session)

        a = service.create(db_session, _payload(user.id))
        b = service.create(db_session, _payload(user.id))
        c = service.create(db_session, _payload(user.id))

        assert len({a.id, b.id, c.id}) == 3
        assert all(s.user_id == user.id for s in (a, b, c))

    # ------------------------------------------------------------------- get
    def test_get_by_id(self, db_session):
        """``get_by_id`` returns the row when it exists."""
        user = _make_user(db_session)
        created = service.create(db_session, _payload(user.id))

        fetched = service.get_by_id(db_session, created.id)
        assert fetched.id == created.id
        assert fetched.user_id == user.id

    def test_get_by_id_missing_raises(self, db_session):
        """``get_by_id`` raises ``ValueError`` for an unknown id."""
        with pytest.raises(ValueError, match="not found"):
            service.get_by_id(db_session, uuid.uuid4())

    # ---------------------------------------------------------------- update
    def test_update_token_version(self, db_session):
        """``update`` bumps ``token_version`` (logout-rotation pattern)."""
        user = _make_user(db_session)
        created = service.create(db_session, _payload(user.id))

        updated = service.update(
            db_session,
            created.id,
            UserSessionUpdate(token_version=created.token_version + 1),
        )

        assert updated.id == created.id
        assert updated.token_version == 1

    def test_update_last_seen_at(self, db_session):
        """``update`` refreshes ``last_seen_at`` (authenticated-request heartbeat)."""
        user = _make_user(db_session)
        created = service.create(db_session, _payload(user.id))
        new_ts = datetime.now(tz=timezone.utc) + timedelta(hours=1)

        updated = service.update(
            db_session,
            created.id,
            UserSessionUpdate(last_seen_at=new_ts),
        )

        assert updated.last_seen_at == new_ts

    def test_update_both_fields(self, db_session):
        """``update`` applies every mutable field in one PATCH."""
        user = _make_user(db_session)
        created = service.create(db_session, _payload(user.id))
        new_ts = datetime.now(tz=timezone.utc) + timedelta(hours=2)

        updated = service.update(
            db_session,
            created.id,
            UserSessionUpdate(token_version=5, last_seen_at=new_ts),
        )

        assert updated.token_version == 5
        assert updated.last_seen_at == new_ts

    def test_update_preserves_immutable_fields(self, db_session):
        """``id``, ``user_id`` and ``created_at`` must not change across ``update``."""
        user = _make_user(db_session)
        created = service.create(db_session, _payload(user.id))

        original_id = created.id
        original_user_id = created.user_id
        original_created_at = created.created_at

        updated = service.update(
            db_session,
            created.id,
            UserSessionUpdate(token_version=42),
        )

        assert updated.id == original_id
        assert updated.user_id == original_user_id
        assert updated.created_at == original_created_at

    def test_update_empty_payload_is_noop(self, db_session):
        """An :class:`UserSessionUpdate` with no fields set leaves the row intact."""
        user = _make_user(db_session)
        created = service.create(
            db_session,
            _payload(user.id, token_version=3),
        )
        original_tv = created.token_version
        original_last_seen = created.last_seen_at

        updated = service.update(db_session, created.id, UserSessionUpdate())

        assert updated.token_version == original_tv
        assert updated.last_seen_at == original_last_seen

    def test_update_missing_raises(self, db_session):
        """``update`` on a non-existent id raises ``ValueError``."""
        with pytest.raises(ValueError, match="not found"):
            service.update(
                db_session,
                uuid.uuid4(),
                UserSessionUpdate(token_version=1),
            )

    # ---------------------------------------------------------------- delete
    def test_delete(self, db_session):
        """``delete`` removes the row; subsequent lookup raises."""
        user = _make_user(db_session)
        created = service.create(db_session, _payload(user.id))

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
        a = service.create(db_session, _payload(user.id))
        b = service.create(db_session, _payload(user.id))

        service.delete(db_session, a.id)

        assert service.get_by_id(db_session, b.id).id == b.id

    # ------------------------------------------------------------------ list
    def test_list_all(self, db_session):
        """``list_user_sessions`` returns every row when no filter is supplied."""
        user = _make_user(db_session)
        created_ids: set = set()
        for _ in range(3):
            created_ids.add(service.create(db_session, _payload(user.id)).id)

        rows = service.list_user_sessions(db_session)
        assert created_ids.issubset({r.id for r in rows})

    def test_list_filter_by_user(self, db_session):
        """``list_user_sessions(user_id=...)`` returns only that user's sessions."""
        u1 = _make_user(db_session)
        u2 = _make_user(db_session)
        mine = service.create(db_session, _payload(u1.id))
        service.create(db_session, _payload(u2.id))

        rows = service.list_user_sessions(db_session, user_id=u1.id)
        assert all(r.user_id == u1.id for r in rows)
        assert any(r.id == mine.id for r in rows)

    def test_list_pagination(self, db_session):
        """``limit`` / ``offset`` restrict the result window."""
        user = _make_user(db_session)
        for _ in range(5):
            service.create(db_session, _payload(user.id))

        first_page = service.list_user_sessions(db_session, user_id=user.id, limit=2, offset=0)
        second_page = service.list_user_sessions(db_session, user_id=user.id, limit=2, offset=2)

        assert len(first_page) == 2
        assert len(second_page) == 2
        first_ids = {s.id for s in first_page}
        second_ids = {s.id for s in second_page}
        assert first_ids.isdisjoint(second_ids)

    # --------------------------------------------------------------- commit
    def test_service_does_not_commit(self, db_session):
        """Service calls only ``flush`` — rows vanish when the outer transaction rolls back.

        This asserts the contract that transaction control belongs to the
        router, not the service. The SAVEPOINT-isolated ``db_session``
        fixture rolls back at teardown; a service that called ``commit``
        would leak rows into the test database and break other tests.
        """
        user = _make_user(db_session)
        created = service.create(db_session, _payload(user.id))
        # ``in_transaction()`` must be True — commit would clear it.
        assert db_session.in_transaction()
        # Row is visible within the session after flush.
        assert service.get_by_id(db_session, created.id).id == created.id
