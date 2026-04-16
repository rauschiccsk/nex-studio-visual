"""Tests for :mod:`backend.services.architect_message`.

Exercises every public CRUD entry point against the SAVEPOINT-isolated
session provided by ``tests/conftest.py``. Verifies:

* Happy-path list / get / create / update / delete.
* ``ValueError`` on missing ``id`` for get / update / delete.
* ``create`` persists every supplied column — usage/cost nullable by
  default, but accepted up-front for backfill.
* Update allow-list — only ``input_tokens``, ``output_tokens`` and
  ``cost_usd`` are applied; ``session_id``, ``role``, ``content``,
  ``id`` and ``created_at`` are preserved.
* PATCH semantics — omitted fields stay untouched.
* No UNIQUE constraints — the same session may carry many messages
  with the same role and identical content.
* List filters (``session_id``, ``role``) and pagination.
* List ordering is ``created_at ASC`` (conversation order, oldest
  first).
* ``delete`` removes the row and leaves siblings on the same session
  intact.
* No ``commit`` happens inside the service — the outer transaction
  rolls back cleanly at fixture teardown.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from backend.db.models.architect import ArchitectMessage, ArchitectSession
from backend.db.models.foundation import User
from backend.db.models.projects import Project
from backend.schemas.architect_message import (
    ArchitectMessageCreate,
    ArchitectMessageUpdate,
)
from backend.services import architect_message as service


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
    """Create and persist a Project for FK references."""
    if user is None:
        user = _make_user(db_session)
    suffix = uuid.uuid4().hex[:8]
    defaults = {
        "name": f"Project {suffix}",
        "slug": f"project-{suffix}",
        "category": "multimodule",
        "description": "Test project description",
        "created_by": user.id,
    }
    defaults.update(overrides)
    project = Project(**defaults)
    db_session.add(project)
    db_session.flush()
    return project


def _make_session(db_session, *, project=None, user=None, **overrides) -> ArchitectSession:
    """Create an ArchitectSession for FK references."""
    if user is None:
        user = _make_user(db_session)
    if project is None:
        project = _make_project(db_session, user=user)
    defaults = {
        "project_id": project.id,
        "created_by": user.id,
    }
    defaults.update(overrides)
    session_obj = ArchitectSession(**defaults)
    db_session.add(session_obj)
    db_session.flush()
    return session_obj


def _payload(session_id, **overrides) -> ArchitectMessageCreate:
    """Return an :class:`ArchitectMessageCreate` payload with sensible defaults."""
    defaults = {
        "session_id": session_id,
        "role": "user",
        "content": "Hello, architect!",
    }
    defaults.update(overrides)
    return ArchitectMessageCreate(**defaults)


class TestArchitectMessageService:
    """Synchronous CRUD coverage for the ArchitectMessage service."""

    # ------------------------------------------------------------------ create
    def test_create_message(self, db_session):
        """``create`` persists the row and returns an ORM instance with server defaults."""
        arch_session = _make_session(db_session)

        created = service.create(db_session, _payload(arch_session.id))

        assert isinstance(created, ArchitectMessage)
        assert created.id is not None
        assert created.created_at is not None
        assert created.updated_at is not None
        assert created.session_id == arch_session.id
        assert created.role == "user"
        assert created.content == "Hello, architect!"
        # Usage/cost default to None on creation.
        assert created.input_tokens is None
        assert created.output_tokens is None
        assert created.cost_usd is None

    def test_create_assistant_message(self, db_session):
        """``create`` accepts role='assistant'."""
        arch_session = _make_session(db_session)

        created = service.create(
            db_session,
            _payload(arch_session.id, role="assistant", content="Here is the answer."),
        )

        assert created.role == "assistant"
        assert created.content == "Here is the answer."

    def test_create_with_usage_and_cost(self, db_session):
        """``create`` applies every supplied field, including usage/cost."""
        arch_session = _make_session(db_session)

        created = service.create(
            db_session,
            _payload(
                arch_session.id,
                role="assistant",
                content="Assistant answer.",
                input_tokens=500,
                output_tokens=1200,
                cost_usd=Decimal("0.003600"),
            ),
        )

        assert created.input_tokens == 500
        assert created.output_tokens == 1200
        assert created.cost_usd == Decimal("0.003600")

    def test_create_many_messages_same_session(self, db_session):
        """No UNIQUE constraint — a session may carry many messages with identical content."""
        arch_session = _make_session(db_session)

        a = service.create(db_session, _payload(arch_session.id))
        b = service.create(db_session, _payload(arch_session.id))  # same role + content
        c = service.create(db_session, _payload(arch_session.id, role="assistant"))

        assert len({a.id, b.id, c.id}) == 3
        assert all(m.session_id == arch_session.id for m in (a, b, c))

    # ------------------------------------------------------------------- get
    def test_get_by_id(self, db_session):
        """``get_by_id`` returns the row when it exists."""
        arch_session = _make_session(db_session)
        created = service.create(db_session, _payload(arch_session.id))

        fetched = service.get_by_id(db_session, created.id)
        assert fetched.id == created.id
        assert fetched.session_id == arch_session.id

    def test_get_by_id_missing_raises(self, db_session):
        """``get_by_id`` raises ``ValueError`` for an unknown id."""
        with pytest.raises(ValueError, match="not found"):
            service.get_by_id(db_session, uuid.uuid4())

    # ---------------------------------------------------------------- update
    def test_update_applies_usage_and_cost(self, db_session):
        """``update`` patches every mutable field."""
        arch_session = _make_session(db_session)
        created = service.create(
            db_session,
            _payload(arch_session.id, role="assistant", content="Answer."),
        )

        updated = service.update(
            db_session,
            created.id,
            ArchitectMessageUpdate(
                input_tokens=500,
                output_tokens=1200,
                cost_usd=Decimal("0.003600"),
            ),
        )

        assert updated.id == created.id
        assert updated.input_tokens == 500
        assert updated.output_tokens == 1200
        assert updated.cost_usd == Decimal("0.003600")

    def test_update_partial_only_cost(self, db_session):
        """``update`` leaves omitted fields untouched (PATCH semantics)."""
        arch_session = _make_session(db_session)
        created = service.create(
            db_session,
            _payload(
                arch_session.id,
                role="assistant",
                content="Answer.",
                input_tokens=100,
                output_tokens=200,
                cost_usd=Decimal("0.001000"),
            ),
        )

        updated = service.update(
            db_session,
            created.id,
            ArchitectMessageUpdate(cost_usd=Decimal("0.005000")),
        )

        # Only cost changed; token counts preserved.
        assert updated.input_tokens == 100
        assert updated.output_tokens == 200
        assert updated.cost_usd == Decimal("0.005000")

    def test_update_preserves_immutable_fields(self, db_session):
        """``id``, ``session_id``, ``role``, ``content`` and ``created_at`` must not change across ``update``."""
        arch_session = _make_session(db_session)
        created = service.create(
            db_session,
            _payload(arch_session.id, role="user", content="Original question"),
        )

        original_id = created.id
        original_session_id = created.session_id
        original_role = created.role
        original_content = created.content
        original_created_at = created.created_at

        updated = service.update(
            db_session,
            created.id,
            ArchitectMessageUpdate(input_tokens=42),
        )

        assert updated.id == original_id
        assert updated.session_id == original_session_id
        assert updated.role == original_role
        assert updated.content == original_content
        assert updated.created_at == original_created_at

    def test_update_empty_payload_is_noop(self, db_session):
        """An :class:`ArchitectMessageUpdate` with no fields set leaves the row intact."""
        arch_session = _make_session(db_session)
        created = service.create(
            db_session,
            _payload(
                arch_session.id,
                role="assistant",
                content="Answer.",
                input_tokens=10,
                output_tokens=20,
                cost_usd=Decimal("0.000100"),
            ),
        )

        updated = service.update(db_session, created.id, ArchitectMessageUpdate())

        assert updated.input_tokens == 10
        assert updated.output_tokens == 20
        assert updated.cost_usd == Decimal("0.000100")

    def test_update_missing_raises(self, db_session):
        """``update`` on a non-existent id raises ``ValueError``."""
        with pytest.raises(ValueError, match="not found"):
            service.update(
                db_session,
                uuid.uuid4(),
                ArchitectMessageUpdate(input_tokens=1),
            )

    # ---------------------------------------------------------------- delete
    def test_delete(self, db_session):
        """``delete`` removes the row; subsequent lookup raises."""
        arch_session = _make_session(db_session)
        created = service.create(db_session, _payload(arch_session.id))

        service.delete(db_session, created.id)

        with pytest.raises(ValueError, match="not found"):
            service.get_by_id(db_session, created.id)

    def test_delete_missing_raises(self, db_session):
        """``delete`` on a non-existent id raises ``ValueError``."""
        with pytest.raises(ValueError, match="not found"):
            service.delete(db_session, uuid.uuid4())

    def test_delete_one_message_leaves_siblings_intact(self, db_session):
        """Deleting one message does not affect siblings on the same session."""
        arch_session = _make_session(db_session)
        a = service.create(db_session, _payload(arch_session.id))
        b = service.create(db_session, _payload(arch_session.id, role="assistant"))

        service.delete(db_session, a.id)

        assert service.get_by_id(db_session, b.id).id == b.id

    # ------------------------------------------------------------------ list
    def test_list_all(self, db_session):
        """``list_architect_messages`` returns every row when no filter is supplied."""
        arch_session = _make_session(db_session)
        created_ids: set = set()
        for _ in range(3):
            created_ids.add(service.create(db_session, _payload(arch_session.id)).id)

        rows = service.list_architect_messages(db_session)
        assert created_ids.issubset({r.id for r in rows})

    def test_list_filter_by_session(self, db_session):
        """``list_architect_messages(session_id=...)`` returns only that session's messages."""
        s1 = _make_session(db_session)
        s2 = _make_session(db_session)
        mine = service.create(db_session, _payload(s1.id))
        service.create(db_session, _payload(s2.id))

        rows = service.list_architect_messages(db_session, session_id=s1.id)
        assert all(r.session_id == s1.id for r in rows)
        assert any(r.id == mine.id for r in rows)

    def test_list_filter_by_role(self, db_session):
        """``role`` filter returns only messages with the given role."""
        arch_session = _make_session(db_session)
        user_msg = service.create(db_session, _payload(arch_session.id, role="user", content="Q"))
        assistant_msg = service.create(db_session, _payload(arch_session.id, role="assistant", content="A"))

        user_rows = service.list_architect_messages(db_session, session_id=arch_session.id, role="user")
        user_ids = {r.id for r in user_rows}
        assert user_msg.id in user_ids
        assert assistant_msg.id not in user_ids

        assistant_rows = service.list_architect_messages(db_session, session_id=arch_session.id, role="assistant")
        assistant_ids = {r.id for r in assistant_rows}
        assert assistant_msg.id in assistant_ids
        assert user_msg.id not in assistant_ids

    def test_list_combined_filters(self, db_session):
        """Multiple filters AND together."""
        s1 = _make_session(db_session)
        s2 = _make_session(db_session)

        match = service.create(db_session, _payload(s1.id, role="assistant", content="A1"))
        # Different session
        service.create(db_session, _payload(s2.id, role="assistant", content="A2"))
        # Different role
        service.create(db_session, _payload(s1.id, role="user", content="Q"))

        rows = service.list_architect_messages(db_session, session_id=s1.id, role="assistant")
        assert len(rows) == 1
        assert rows[0].id == match.id

    def test_list_ordered_by_created_at_asc(self, db_session):
        """Results are ordered oldest-first for chat-transcript rendering.

        Rows created inside a single transaction share the same ``NOW()``
        value (PostgreSQL ``now()`` is transaction-scoped), so the test
        overrides ``created_at`` explicitly to produce unambiguous
        ordering — the intent is to pin the service-layer ``ORDER BY
        created_at ASC`` contract, not to measure Postgres clock
        resolution.
        """
        arch_session = _make_session(db_session)

        base_time = datetime(2026, 4, 15, 10, 0, tzinfo=timezone.utc)
        m1 = service.create(db_session, _payload(arch_session.id, content="first"))
        m2 = service.create(db_session, _payload(arch_session.id, content="second"))
        m3 = service.create(db_session, _payload(arch_session.id, content="third"))
        m1.created_at = base_time
        m2.created_at = base_time + timedelta(minutes=1)
        m3.created_at = base_time + timedelta(minutes=2)
        db_session.flush()

        rows = service.list_architect_messages(db_session, session_id=arch_session.id)
        ids_in_order = [r.id for r in rows]
        assert ids_in_order.index(m1.id) < ids_in_order.index(m2.id) < ids_in_order.index(m3.id)

    def test_list_pagination(self, db_session):
        """``limit`` / ``offset`` restrict the result window."""
        arch_session = _make_session(db_session)
        for i in range(5):
            service.create(db_session, _payload(arch_session.id, content=f"msg {i}"))

        first_page = service.list_architect_messages(
            db_session,
            session_id=arch_session.id,
            limit=2,
            offset=0,
        )
        second_page = service.list_architect_messages(
            db_session,
            session_id=arch_session.id,
            limit=2,
            offset=2,
        )
        assert len(first_page) == 2
        assert len(second_page) == 2
        first_ids = {r.id for r in first_page}
        second_ids = {r.id for r in second_page}
        assert first_ids.isdisjoint(second_ids)

    # --------------------------------------------------------------- commit
    def test_service_does_not_commit(self, db_session):
        """Service calls only ``flush`` — rows vanish when the outer transaction rolls back.

        This asserts the contract that transaction control belongs to
        the router, not the service. The SAVEPOINT-isolated
        ``db_session`` fixture rolls back at teardown; a service that
        called ``commit`` would leak rows into the test database and
        break other tests.
        """
        arch_session = _make_session(db_session)
        created = service.create(db_session, _payload(arch_session.id))
        # ``in_transaction()`` must be True — commit would clear it.
        assert db_session.in_transaction()
        # Row is visible within the session after flush.
        assert service.get_by_id(db_session, created.id).id == created.id
