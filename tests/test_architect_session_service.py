"""Tests for :mod:`backend.services.architect_session`.

Exercises every public CRUD entry point against the SAVEPOINT-isolated
session provided by ``tests/conftest.py``. Verifies:

* Happy-path list / get / create / update / delete.
* ``ValueError`` on missing ``id`` for get / update / delete.
* Server defaults (``status='active'``, ``closed_at=None``) on create.
* Update allow-list — only ``module_id``, ``status`` and ``closed_at``
  are applied; ``project_id``, ``created_by``, ``id`` and
  ``created_at`` are preserved.
* Auto-stamp ``closed_at`` when ``status`` transitions from ``active``
  to ``closed`` without an explicit ``closed_at`` in the payload.
* Explicit ``closed_at`` wins over the auto-stamp.
* No auto-stamp when already ``closed`` or when ``closed_at`` is
  re-sent explicitly.
* List filters (``project_id``, ``module_id``, ``status``,
  ``created_by``) and pagination.
* ``delete`` removes the row — the single inbound FK
  (``architect_messages.session_id``) uses ``ON DELETE CASCADE`` so no
  RESTRICT guard is needed.
* No ``commit`` happens inside the service — the outer transaction
  rolls back cleanly at fixture teardown.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from backend.db.models.architect import ArchitectSession
from backend.db.models.foundation import User
from backend.db.models.projects import Project, ProjectModule
from backend.schemas.architect_session import (
    ArchitectSessionCreate,
    ArchitectSessionUpdate,
)
from backend.services import architect_session as service


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


def _make_module(db_session, *, project: Project | None = None, **overrides) -> ProjectModule:
    """Create a ProjectModule for FK references."""
    if project is None:
        project = _make_project(db_session)
    suffix = uuid.uuid4().hex[:4].upper()
    defaults = {
        "project_id": project.id,
        "code": f"M{suffix}",
        "name": f"Module {suffix}",
        "category": "General",
    }
    defaults.update(overrides)
    module = ProjectModule(**defaults)
    db_session.add(module)
    db_session.flush()
    return module


def _payload(project_id, created_by, **overrides) -> ArchitectSessionCreate:
    """Return an :class:`ArchitectSessionCreate` payload with sensible defaults."""
    defaults = {
        "project_id": project_id,
        "created_by": created_by,
    }
    defaults.update(overrides)
    return ArchitectSessionCreate(**defaults)


class TestArchitectSessionService:
    """Synchronous CRUD coverage for the ArchitectSession service."""

    # ------------------------------------------------------------------ create
    def test_create_session(self, db_session):
        """``create`` persists the row and returns an ORM instance with server defaults."""
        user = _make_user(db_session)
        project = _make_project(db_session, user=user)

        created = service.create(db_session, _payload(project.id, user.id))

        assert isinstance(created, ArchitectSession)
        assert created.id is not None
        assert created.created_at is not None
        assert created.updated_at is not None
        assert created.project_id == project.id
        assert created.created_by == user.id
        assert created.module_id is None
        # default from Pydantic schema / DB server_default
        assert created.status == "active"
        assert created.closed_at is None

    def test_create_with_module(self, db_session):
        """``create`` accepts a module_id for module-scoped sessions."""
        user = _make_user(db_session)
        project = _make_project(db_session, user=user)
        module = _make_module(db_session, project=project)

        created = service.create(
            db_session,
            _payload(project.id, user.id, module_id=module.id),
        )

        assert created.module_id == module.id

    def test_create_with_explicit_status_and_closed_at(self, db_session):
        """``create`` applies every supplied field."""
        user = _make_user(db_session)
        project = _make_project(db_session, user=user)
        ts = datetime(2026, 4, 1, 12, 0, tzinfo=timezone.utc)

        created = service.create(
            db_session,
            _payload(
                project.id,
                user.id,
                status="closed",
                closed_at=ts,
            ),
        )

        assert created.status == "closed"
        assert created.closed_at == ts

    def test_create_multiple_sessions_same_project_user(self, db_session):
        """No UNIQUE constraint — a user may open many sessions on the same project."""
        user = _make_user(db_session)
        project = _make_project(db_session, user=user)

        a = service.create(db_session, _payload(project.id, user.id))
        b = service.create(db_session, _payload(project.id, user.id))

        assert a.id != b.id
        assert a.project_id == b.project_id == project.id
        assert a.created_by == b.created_by == user.id

    # ------------------------------------------------------------------- get
    def test_get_by_id(self, db_session):
        """``get_by_id`` returns the row when it exists."""
        user = _make_user(db_session)
        project = _make_project(db_session, user=user)
        created = service.create(db_session, _payload(project.id, user.id))

        fetched = service.get_by_id(db_session, created.id)
        assert fetched.id == created.id
        assert fetched.project_id == project.id

    def test_get_by_id_missing_raises(self, db_session):
        """``get_by_id`` raises ``ValueError`` for an unknown id."""
        with pytest.raises(ValueError, match="not found"):
            service.get_by_id(db_session, uuid.uuid4())

    # ---------------------------------------------------------------- update
    def test_update_applies_allowed_fields(self, db_session):
        """``update`` patches every mutable field."""
        user = _make_user(db_session)
        project = _make_project(db_session, user=user)
        module = _make_module(db_session, project=project)
        ts = datetime(2026, 4, 10, 9, 0, tzinfo=timezone.utc)

        created = service.create(db_session, _payload(project.id, user.id))

        updated = service.update(
            db_session,
            created.id,
            ArchitectSessionUpdate(
                module_id=module.id,
                status="closed",
                closed_at=ts,
            ),
        )

        assert updated.id == created.id
        assert updated.module_id == module.id
        assert updated.status == "closed"
        assert updated.closed_at == ts

    def test_update_partial_status_only(self, db_session):
        """``update`` leaves omitted fields untouched (PATCH semantics)."""
        user = _make_user(db_session)
        project = _make_project(db_session, user=user)
        module = _make_module(db_session, project=project)

        created = service.create(
            db_session,
            _payload(project.id, user.id, module_id=module.id),
        )

        # Only status — module_id stays, closed_at auto-stamped (see below).
        updated = service.update(
            db_session,
            created.id,
            ArchitectSessionUpdate(status="closed"),
        )

        assert updated.status == "closed"
        assert updated.module_id == module.id

    def test_update_preserves_immutable_fields(self, db_session):
        """``id``, ``project_id``, ``created_by``, ``created_at`` must not change across ``update``."""
        user = _make_user(db_session)
        project = _make_project(db_session, user=user)
        created = service.create(db_session, _payload(project.id, user.id))

        original_id = created.id
        original_project_id = created.project_id
        original_created_by = created.created_by
        original_created_at = created.created_at

        updated = service.update(
            db_session,
            created.id,
            ArchitectSessionUpdate(status="closed"),
        )

        assert updated.id == original_id
        assert updated.project_id == original_project_id
        assert updated.created_by == original_created_by
        assert updated.created_at == original_created_at

    def test_update_auto_stamps_closed_at_on_transition_to_closed(self, db_session):
        """Transition from ``active`` → ``closed`` without explicit ``closed_at`` auto-stamps it."""
        user = _make_user(db_session)
        project = _make_project(db_session, user=user)
        created = service.create(db_session, _payload(project.id, user.id))
        assert created.status == "active"
        assert created.closed_at is None

        before = datetime.now(tz=timezone.utc)
        updated = service.update(
            db_session,
            created.id,
            ArchitectSessionUpdate(status="closed"),
        )
        after = datetime.now(tz=timezone.utc)

        assert updated.status == "closed"
        assert updated.closed_at is not None
        # ``closed_at`` should fall in the [before, after] window.
        assert before <= updated.closed_at <= after

    def test_update_explicit_closed_at_wins_over_auto_stamp(self, db_session):
        """Explicit ``closed_at`` in the payload takes precedence over the auto-stamp."""
        user = _make_user(db_session)
        project = _make_project(db_session, user=user)
        created = service.create(db_session, _payload(project.id, user.id))

        explicit_ts = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
        updated = service.update(
            db_session,
            created.id,
            ArchitectSessionUpdate(status="closed", closed_at=explicit_ts),
        )

        assert updated.status == "closed"
        assert updated.closed_at == explicit_ts

    def test_update_no_auto_stamp_when_already_closed(self, db_session):
        """Updating an already-closed session without a ``closed_at`` does not overwrite it."""
        user = _make_user(db_session)
        project = _make_project(db_session, user=user)
        original_ts = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
        created = service.create(
            db_session,
            _payload(project.id, user.id, status="closed", closed_at=original_ts),
        )

        # Re-set status to closed — transition ``closed`` → ``closed`` must
        # not overwrite the existing ``closed_at``.
        updated = service.update(
            db_session,
            created.id,
            ArchitectSessionUpdate(status="closed"),
        )

        assert updated.status == "closed"
        assert updated.closed_at == original_ts

    def test_update_no_auto_stamp_when_status_not_changing_to_closed(self, db_session):
        """Updating an ``active`` session without touching ``status`` leaves ``closed_at`` alone."""
        user = _make_user(db_session)
        project = _make_project(db_session, user=user)
        module = _make_module(db_session, project=project)
        created = service.create(db_session, _payload(project.id, user.id))

        updated = service.update(
            db_session,
            created.id,
            ArchitectSessionUpdate(module_id=module.id),
        )

        assert updated.status == "active"
        assert updated.closed_at is None
        assert updated.module_id == module.id

    def test_update_missing_raises(self, db_session):
        """``update`` on a non-existent id raises ``ValueError``."""
        with pytest.raises(ValueError, match="not found"):
            service.update(
                db_session,
                uuid.uuid4(),
                ArchitectSessionUpdate(status="closed"),
            )

    # ---------------------------------------------------------------- delete
    def test_delete(self, db_session):
        """``delete`` removes the row; subsequent lookup raises."""
        user = _make_user(db_session)
        project = _make_project(db_session, user=user)
        created = service.create(db_session, _payload(project.id, user.id))

        service.delete(db_session, created.id)

        with pytest.raises(ValueError, match="not found"):
            service.get_by_id(db_session, created.id)

    def test_delete_missing_raises(self, db_session):
        """``delete`` on a non-existent id raises ``ValueError``."""
        with pytest.raises(ValueError, match="not found"):
            service.delete(db_session, uuid.uuid4())

    def test_delete_one_session_leaves_others_intact(self, db_session):
        """Deleting one session does not affect siblings on the same project."""
        user = _make_user(db_session)
        project = _make_project(db_session, user=user)
        a = service.create(db_session, _payload(project.id, user.id))
        b = service.create(db_session, _payload(project.id, user.id))

        service.delete(db_session, a.id)

        assert service.get_by_id(db_session, b.id).id == b.id

    # ------------------------------------------------------------------ list
    def test_list_all(self, db_session):
        """``list_architect_sessions`` returns every row when no filter is supplied."""
        user = _make_user(db_session)
        project = _make_project(db_session, user=user)
        created_ids: set = set()
        for _ in range(3):
            created_ids.add(service.create(db_session, _payload(project.id, user.id)).id)

        rows = service.list_architect_sessions(db_session)
        assert created_ids.issubset({r.id for r in rows})

    def test_list_filter_by_project(self, db_session):
        """``list_architect_sessions(project_id=...)`` returns only that project's sessions."""
        user = _make_user(db_session)
        p1 = _make_project(db_session, user=user)
        p2 = _make_project(db_session, user=user)
        in_a = service.create(db_session, _payload(p1.id, user.id))
        service.create(db_session, _payload(p2.id, user.id))

        rows = service.list_architect_sessions(db_session, project_id=p1.id)
        assert all(r.project_id == p1.id for r in rows)
        assert any(r.id == in_a.id for r in rows)

    def test_list_filter_by_module(self, db_session):
        """``module_id`` filter returns only sessions scoped to that module."""
        user = _make_user(db_session)
        project = _make_project(db_session, user=user)
        module = _make_module(db_session, project=project)
        scoped = service.create(
            db_session,
            _payload(project.id, user.id, module_id=module.id),
        )
        foundation = service.create(db_session, _payload(project.id, user.id))

        rows = service.list_architect_sessions(db_session, module_id=module.id)
        ids = {r.id for r in rows}
        assert scoped.id in ids
        assert foundation.id not in ids

    def test_list_filter_by_status(self, db_session):
        """``status`` filter returns only rows with the given status."""
        user = _make_user(db_session)
        project = _make_project(db_session, user=user)
        active = service.create(db_session, _payload(project.id, user.id))
        closed = service.create(
            db_session,
            _payload(project.id, user.id, status="closed"),
        )

        active_rows = service.list_architect_sessions(db_session, status="active")
        active_ids = {r.id for r in active_rows}
        assert active.id in active_ids
        assert closed.id not in active_ids

        closed_rows = service.list_architect_sessions(db_session, status="closed")
        closed_ids = {r.id for r in closed_rows}
        assert closed.id in closed_ids
        assert active.id not in closed_ids

    def test_list_filter_by_created_by(self, db_session):
        """``created_by`` filter returns only rows opened by the given user."""
        u1 = _make_user(db_session)
        u2 = _make_user(db_session)
        project = _make_project(db_session, user=u1)
        mine = service.create(db_session, _payload(project.id, u1.id))
        # Add u2 as creator — FK to users only, not project_members.
        service.create(db_session, _payload(project.id, u2.id))

        rows = service.list_architect_sessions(db_session, created_by=u1.id)
        ids = {r.id for r in rows}
        assert mine.id in ids
        assert all(r.created_by == u1.id for r in rows)

    def test_list_combined_filters(self, db_session):
        """Multiple filters AND together."""
        user = _make_user(db_session)
        project = _make_project(db_session, user=user)
        module = _make_module(db_session, project=project)

        match = service.create(
            db_session,
            _payload(project.id, user.id, module_id=module.id, status="active"),
        )
        # Different module
        service.create(db_session, _payload(project.id, user.id, status="active"))
        # Different status
        service.create(
            db_session,
            _payload(project.id, user.id, module_id=module.id, status="closed"),
        )

        rows = service.list_architect_sessions(
            db_session,
            project_id=project.id,
            module_id=module.id,
            status="active",
        )
        assert len(rows) == 1
        assert rows[0].id == match.id

    def test_list_ordered_by_created_at_desc(self, db_session):
        """Results are ordered newest-first.

        Rows created inside a single transaction share the same ``NOW()``
        value (PostgreSQL ``now()`` is transaction-scoped), so the test
        overrides ``created_at`` explicitly to produce unambiguous
        ordering — the intent is to pin the service-layer ``ORDER BY
        created_at DESC`` contract, not to measure Postgres clock
        resolution.
        """
        user = _make_user(db_session)
        project = _make_project(db_session, user=user)

        base_time = datetime(2026, 4, 15, 10, 0, tzinfo=timezone.utc)
        s1 = service.create(db_session, _payload(project.id, user.id))
        s2 = service.create(db_session, _payload(project.id, user.id))
        s3 = service.create(db_session, _payload(project.id, user.id))
        s1.created_at = base_time
        s2.created_at = base_time + timedelta(minutes=1)
        s3.created_at = base_time + timedelta(minutes=2)
        db_session.flush()

        rows = service.list_architect_sessions(db_session, project_id=project.id)
        ids_in_order = [r.id for r in rows]
        assert ids_in_order.index(s3.id) < ids_in_order.index(s2.id) < ids_in_order.index(s1.id)

    def test_list_pagination(self, db_session):
        """``limit`` / ``offset`` restrict the result window."""
        user = _make_user(db_session)
        project = _make_project(db_session, user=user)
        for _ in range(5):
            service.create(db_session, _payload(project.id, user.id))

        first_page = service.list_architect_sessions(
            db_session,
            project_id=project.id,
            limit=2,
            offset=0,
        )
        second_page = service.list_architect_sessions(
            db_session,
            project_id=project.id,
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
        user = _make_user(db_session)
        project = _make_project(db_session, user=user)
        created = service.create(db_session, _payload(project.id, user.id))
        # ``in_transaction()`` must be True — commit would clear it.
        assert db_session.in_transaction()
        # Row is visible within the session after flush.
        assert service.get_by_id(db_session, created.id).id == created.id
