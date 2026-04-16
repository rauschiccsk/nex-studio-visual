"""Tests for :mod:`backend.services.project_member`.

Exercises every public CRUD entry point against the SAVEPOINT-isolated
session provided by ``tests/conftest.py``. Verifies:

* Happy-path list / get / create / update / delete.
* ``ValueError`` on duplicate ``(project_id, user_id)`` natural key.
* ``ValueError`` on missing ``id`` for get / update / delete.
* ``update`` is a no-op — :class:`ProjectMember` has no mutable
  columns — but still raises :class:`ValueError` on an unknown id and
  returns the unmodified row otherwise.
* Immutable fields (``id``, ``project_id``, ``user_id``,
  ``created_at``) stay unchanged across :func:`update`.
* List filters (``project_id``, ``user_id``) and pagination.
* ``delete`` removes the row — ``project_members`` has no inbound
  FKs so no RESTRICT guard is needed.
* No ``commit`` happens inside the service — the outer transaction
  rolls back cleanly at fixture teardown.
"""

from __future__ import annotations

import uuid

import pytest

from backend.db.models.foundation import User
from backend.db.models.projects import Project, ProjectMember
from backend.schemas.project_member import (
    ProjectMemberCreate,
    ProjectMemberUpdate,
)
from backend.services import project_member as service


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
        "category": "singlemodule",
        "description": "Test project description",
        "created_by": user.id,
    }
    defaults.update(overrides)
    project = Project(**defaults)
    db_session.add(project)
    db_session.flush()
    return project


def _payload(project_id, user_id) -> ProjectMemberCreate:
    """Return a :class:`ProjectMemberCreate` payload."""
    return ProjectMemberCreate(project_id=project_id, user_id=user_id)


class TestProjectMemberService:
    """Synchronous CRUD coverage for the ProjectMember service."""

    # ------------------------------------------------------------------ create
    def test_create_member(self, db_session):
        """``create`` persists the row and returns an ORM instance with server defaults."""
        user = _make_user(db_session)
        project = _make_project(db_session, user=user)
        member_user = _make_user(db_session)

        created = service.create(db_session, _payload(project.id, member_user.id))

        assert isinstance(created, ProjectMember)
        assert created.id is not None
        assert created.created_at is not None
        assert created.updated_at is not None
        assert created.project_id == project.id
        assert created.user_id == member_user.id

    def test_create_duplicate_natural_key_raises(self, db_session):
        """``UNIQUE(project_id, user_id)`` — duplicate pair rejected pre-flush."""
        project = _make_project(db_session)
        member_user = _make_user(db_session)
        service.create(db_session, _payload(project.id, member_user.id))

        with pytest.raises(ValueError, match="already exists"):
            service.create(db_session, _payload(project.id, member_user.id))

    def test_create_same_user_different_project_allowed(self, db_session):
        """The same user may be a member of multiple projects."""
        user = _make_user(db_session)
        project_a = _make_project(db_session, user=user)
        project_b = _make_project(db_session, user=user)
        member_user = _make_user(db_session)

        a = service.create(db_session, _payload(project_a.id, member_user.id))
        b = service.create(db_session, _payload(project_b.id, member_user.id))

        assert a.id != b.id
        assert a.user_id == b.user_id == member_user.id
        assert a.project_id != b.project_id

    def test_create_same_project_different_user_allowed(self, db_session):
        """The same project may have multiple members."""
        project = _make_project(db_session)
        user_a = _make_user(db_session)
        user_b = _make_user(db_session)

        a = service.create(db_session, _payload(project.id, user_a.id))
        b = service.create(db_session, _payload(project.id, user_b.id))

        assert a.id != b.id
        assert a.project_id == b.project_id == project.id
        assert a.user_id != b.user_id

    # ------------------------------------------------------------------- get
    def test_get_by_id(self, db_session):
        """``get_by_id`` returns the row when it exists."""
        project = _make_project(db_session)
        member_user = _make_user(db_session)
        created = service.create(db_session, _payload(project.id, member_user.id))

        fetched = service.get_by_id(db_session, created.id)
        assert fetched.id == created.id
        assert fetched.project_id == project.id
        assert fetched.user_id == member_user.id

    def test_get_by_id_missing_raises(self, db_session):
        """``get_by_id`` raises ``ValueError`` for an unknown id."""
        with pytest.raises(ValueError, match="not found"):
            service.get_by_id(db_session, uuid.uuid4())

    # ---------------------------------------------------------------- update
    def test_update_is_noop(self, db_session):
        """``update`` is a no-op — ProjectMember has no mutable fields.

        The natural key ``(project_id, user_id)`` is immutable and the
        :class:`ProjectMemberUpdate` schema exposes no fields; the
        service simply confirms the row exists and returns it
        unchanged.
        """
        project = _make_project(db_session)
        member_user = _make_user(db_session)
        created = service.create(db_session, _payload(project.id, member_user.id))

        original_id = created.id
        original_project_id = created.project_id
        original_user_id = created.user_id
        original_created_at = created.created_at

        updated = service.update(
            db_session,
            created.id,
            ProjectMemberUpdate(),
        )

        assert updated.id == original_id
        assert updated.project_id == original_project_id
        assert updated.user_id == original_user_id
        assert updated.created_at == original_created_at

    def test_update_missing_raises(self, db_session):
        """``update`` on a non-existent id raises ``ValueError``."""
        with pytest.raises(ValueError, match="not found"):
            service.update(
                db_session,
                uuid.uuid4(),
                ProjectMemberUpdate(),
            )

    # ---------------------------------------------------------------- delete
    def test_delete(self, db_session):
        """``delete`` removes the row; subsequent lookup raises."""
        project = _make_project(db_session)
        member_user = _make_user(db_session)
        created = service.create(db_session, _payload(project.id, member_user.id))

        service.delete(db_session, created.id)

        with pytest.raises(ValueError, match="not found"):
            service.get_by_id(db_session, created.id)

    def test_delete_missing_raises(self, db_session):
        """``delete`` on a non-existent id raises ``ValueError``."""
        with pytest.raises(ValueError, match="not found"):
            service.delete(db_session, uuid.uuid4())

    def test_delete_one_member_leaves_others_intact(self, db_session):
        """Deleting one membership does not affect others on the same project."""
        project = _make_project(db_session)
        user_a = _make_user(db_session)
        user_b = _make_user(db_session)
        member_a = service.create(db_session, _payload(project.id, user_a.id))
        member_b = service.create(db_session, _payload(project.id, user_b.id))

        service.delete(db_session, member_a.id)

        assert service.get_by_id(db_session, member_b.id).id == member_b.id

    # ------------------------------------------------------------------ list
    def test_list_all(self, db_session):
        """``list_project_members`` returns every row when no filter is supplied."""
        project = _make_project(db_session)
        created_ids: set = set()
        for _ in range(3):
            member_user = _make_user(db_session)
            created_ids.add(service.create(db_session, _payload(project.id, member_user.id)).id)

        rows = service.list_project_members(db_session)
        assert created_ids.issubset({r.id for r in rows})

    def test_list_filter_by_project(self, db_session):
        """``list_project_members(project_id=...)`` returns only that project's members."""
        user = _make_user(db_session)
        project_a = _make_project(db_session, user=user)
        project_b = _make_project(db_session, user=user)
        member_user = _make_user(db_session)
        in_a = service.create(db_session, _payload(project_a.id, member_user.id))
        service.create(db_session, _payload(project_b.id, member_user.id))

        rows = service.list_project_members(db_session, project_id=project_a.id)
        assert all(r.project_id == project_a.id for r in rows)
        assert any(r.id == in_a.id for r in rows)

    def test_list_filter_by_user(self, db_session):
        """``list_project_members(user_id=...)`` returns only that user's memberships.

        This is the core query behind DESIGN.md §4.1 project
        visibility enforcement — "which projects can this user see".
        """
        user = _make_user(db_session)
        project_a = _make_project(db_session, user=user)
        project_b = _make_project(db_session, user=user)
        member_user = _make_user(db_session)
        other_user = _make_user(db_session)

        in_a = service.create(db_session, _payload(project_a.id, member_user.id))
        in_b = service.create(db_session, _payload(project_b.id, member_user.id))
        service.create(db_session, _payload(project_a.id, other_user.id))

        rows = service.list_project_members(db_session, user_id=member_user.id)
        assert all(r.user_id == member_user.id for r in rows)
        result_ids = {r.id for r in rows}
        assert in_a.id in result_ids
        assert in_b.id in result_ids

    def test_list_filter_by_project_and_user(self, db_session):
        """Combined filters converge on the natural key — at most one row."""
        project = _make_project(db_session)
        member_user = _make_user(db_session)
        created = service.create(db_session, _payload(project.id, member_user.id))

        rows = service.list_project_members(
            db_session,
            project_id=project.id,
            user_id=member_user.id,
        )
        assert len(rows) == 1
        assert rows[0].id == created.id

    def test_list_ordered_by_created_at_desc(self, db_session):
        """Results are ordered newest-first so recent joiners appear on top.

        Rows created inside a single transaction share the same
        ``NOW()`` value (PostgreSQL ``now()`` is transaction-scoped),
        so the test overrides ``created_at`` explicitly to produce
        unambiguous ordering — the intent is to pin the service-layer
        ``ORDER BY created_at DESC`` contract, not to measure Postgres
        clock resolution.
        """
        from datetime import datetime, timedelta, timezone

        project = _make_project(db_session)
        base_time = datetime(2026, 4, 15, 10, 0, tzinfo=timezone.utc)
        u1 = _make_user(db_session)
        u2 = _make_user(db_session)
        u3 = _make_user(db_session)
        m1 = service.create(db_session, _payload(project.id, u1.id))
        m2 = service.create(db_session, _payload(project.id, u2.id))
        m3 = service.create(db_session, _payload(project.id, u3.id))
        m1.created_at = base_time
        m2.created_at = base_time + timedelta(minutes=1)
        m3.created_at = base_time + timedelta(minutes=2)
        db_session.flush()

        rows = service.list_project_members(db_session, project_id=project.id)
        ids_in_order = [r.id for r in rows]
        assert ids_in_order.index(m3.id) < ids_in_order.index(m2.id) < ids_in_order.index(m1.id)

    def test_list_pagination(self, db_session):
        """``limit`` / ``offset`` restrict the result window."""
        project = _make_project(db_session)
        for _ in range(5):
            member_user = _make_user(db_session)
            service.create(db_session, _payload(project.id, member_user.id))

        first_page = service.list_project_members(
            db_session,
            project_id=project.id,
            limit=2,
            offset=0,
        )
        second_page = service.list_project_members(
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
        project = _make_project(db_session)
        member_user = _make_user(db_session)
        created = service.create(db_session, _payload(project.id, member_user.id))
        # ``in_transaction()`` must be True — commit would clear it.
        assert db_session.in_transaction()
        # Row is visible within the session after flush.
        assert service.get_by_id(db_session, created.id).id == created.id
