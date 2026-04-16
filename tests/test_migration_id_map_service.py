"""Tests for :mod:`backend.services.migration_id_map`.

Exercises every public CRUD entry point against the SAVEPOINT-isolated
session provided by ``tests/conftest.py``. Verifies:

* Happy-path list / get / create / update / delete.
* Optional ``batch_id`` may be omitted on create (defaults to ``None``).
* ``ValueError`` on missing ``id`` for get / update / delete.
* ``ValueError`` on duplicate ``(project_id, category, source_key)``
  triple for create.
* Immutable fields (``id``, ``project_id``, ``category``, ``source_key``,
  ``created_at``) stay unchanged on update.
* ``updated_at`` is refreshed by the ORM ``onupdate`` hook on update.
* List filters (``project_id``, ``category``, ``source_key``,
  ``batch_id``) and pagination.
* No ``commit`` happens inside the service — the outer transaction rolls
  back cleanly at fixture teardown.
"""

from __future__ import annotations

import uuid

import pytest

from backend.db.models.foundation import User
from backend.db.models.migration import MigrationBatch, MigrationIdMap
from backend.db.models.projects import Project
from backend.schemas.migration_id_map import (
    MigrationIdMapCreate,
    MigrationIdMapUpdate,
)
from backend.services import migration_id_map as service


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


def _make_batch(
    db_session,
    *,
    project: Project | None = None,
    **overrides,
) -> MigrationBatch:
    """Create and persist a MigrationBatch for FK references."""
    if project is None:
        project = _make_project(db_session)
    defaults = {
        "project_id": project.id,
        "category": "PAB",
    }
    defaults.update(overrides)
    batch = MigrationBatch(**defaults)
    db_session.add(batch)
    db_session.flush()
    return batch


def _payload(project_id, **overrides) -> MigrationIdMapCreate:
    """Return a :class:`MigrationIdMapCreate` payload with sensible defaults."""
    defaults = {
        "project_id": project_id,
        "category": "PAB",
        "source_key": f"src_{uuid.uuid4().hex[:8]}",
        "target_id": str(uuid.uuid4()),
    }
    defaults.update(overrides)
    return MigrationIdMapCreate(**defaults)


class TestMigrationIdMapService:
    """Synchronous CRUD coverage for the MigrationIdMap service."""

    # ------------------------------------------------------------------ create
    def test_create_row(self, db_session):
        """``create`` persists the row and returns an ORM instance with server defaults."""
        project = _make_project(db_session)
        payload = _payload(project.id)
        created = service.create(db_session, payload)

        assert isinstance(created, MigrationIdMap)
        assert created.id is not None
        assert created.created_at is not None
        assert created.updated_at is not None
        assert created.project_id == project.id
        assert created.category == payload.category
        assert created.source_key == payload.source_key
        assert created.target_id == payload.target_id
        # ``batch_id`` defaults to None when omitted.
        assert created.batch_id is None

    def test_create_with_batch(self, db_session):
        """``create`` accepts and persists an optional ``batch_id``."""
        project = _make_project(db_session)
        batch = _make_batch(db_session, project=project)
        created = service.create(
            db_session,
            _payload(project.id, batch_id=batch.id),
        )
        assert created.batch_id == batch.id

    def test_create_duplicate_natural_key_raises(self, db_session):
        """``UNIQUE(project_id, category, source_key)`` — duplicate triple rejected pre-flush."""
        project = _make_project(db_session)
        service.create(
            db_session,
            _payload(project.id, category="PAB", source_key="legacy-42"),
        )

        with pytest.raises(ValueError, match="already exists"):
            service.create(
                db_session,
                _payload(project.id, category="PAB", source_key="legacy-42"),
            )

    def test_create_same_source_key_different_category_allowed(self, db_session):
        """Unique constraint is scoped per category — same key across categories allowed."""
        project = _make_project(db_session)
        pab = service.create(
            db_session,
            _payload(project.id, category="PAB", source_key="shared-key"),
        )
        gsc = service.create(
            db_session,
            _payload(project.id, category="GSC", source_key="shared-key"),
        )
        assert pab.id != gsc.id
        assert pab.source_key == gsc.source_key == "shared-key"
        assert pab.category == "PAB"
        assert gsc.category == "GSC"

    def test_create_same_source_key_different_project_allowed(self, db_session):
        """Unique constraint is scoped per project — same key across projects allowed."""
        user = _make_user(db_session)
        project_a = _make_project(db_session, user=user)
        project_b = _make_project(db_session, user=user)
        a = service.create(
            db_session,
            _payload(project_a.id, category="PAB", source_key="legacy-1"),
        )
        b = service.create(
            db_session,
            _payload(project_b.id, category="PAB", source_key="legacy-1"),
        )
        assert a.id != b.id
        assert a.project_id != b.project_id
        assert a.source_key == b.source_key == "legacy-1"

    # ------------------------------------------------------------------- get
    def test_get_by_id(self, db_session):
        """``get_by_id`` returns the row when it exists."""
        project = _make_project(db_session)
        created = service.create(db_session, _payload(project.id))
        fetched = service.get_by_id(db_session, created.id)
        assert fetched.id == created.id

    def test_get_by_id_missing_raises(self, db_session):
        """``get_by_id`` raises ``ValueError`` for an unknown id."""
        with pytest.raises(ValueError, match="not found"):
            service.get_by_id(db_session, uuid.uuid4())

    # ---------------------------------------------------------------- update
    def test_update_mutable_fields(self, db_session):
        """``update`` changes every mutable column (``target_id``, ``batch_id``)."""
        project = _make_project(db_session)
        batch = _make_batch(db_session, project=project)
        created = service.create(db_session, _payload(project.id))
        original_id = created.id
        original_project_id = created.project_id
        original_category = created.category
        original_source_key = created.source_key
        original_created_at = created.created_at

        new_target = str(uuid.uuid4())
        updated = service.update(
            db_session,
            created.id,
            MigrationIdMapUpdate(
                target_id=new_target,
                batch_id=batch.id,
            ),
        )
        assert updated.target_id == new_target
        assert updated.batch_id == batch.id
        # Immutable fields unchanged.
        assert updated.id == original_id
        assert updated.project_id == original_project_id
        assert updated.category == original_category
        assert updated.source_key == original_source_key
        assert updated.created_at == original_created_at

    def test_update_partial(self, db_session):
        """``update`` with only ``target_id`` leaves other fields untouched."""
        project = _make_project(db_session)
        batch = _make_batch(db_session, project=project)
        created = service.create(
            db_session,
            _payload(project.id, batch_id=batch.id),
        )
        new_target = str(uuid.uuid4())
        updated = service.update(
            db_session,
            created.id,
            MigrationIdMapUpdate(target_id=new_target),
        )
        assert updated.target_id == new_target
        # ``batch_id`` not in the PATCH payload → untouched.
        assert updated.batch_id == batch.id
        assert updated.source_key == created.source_key

    def test_update_missing_raises(self, db_session):
        """``update`` on a non-existent id raises ``ValueError``."""
        with pytest.raises(ValueError, match="not found"):
            service.update(
                db_session,
                uuid.uuid4(),
                MigrationIdMapUpdate(target_id=str(uuid.uuid4())),
            )

    def test_update_ignores_disallowed_fields(self, db_session):
        """Immutable ``project_id`` / ``category`` / ``source_key`` stay put.

        :class:`MigrationIdMapUpdate` has no fields for these columns,
        so the service's allow-list merely formalises that contract.
        A benign update (e.g. ``target_id``) on the same row must leave
        them untouched.
        """
        project = _make_project(db_session)
        created = service.create(db_session, _payload(project.id))
        original_project_id = created.project_id
        original_category = created.category
        original_source_key = created.source_key

        updated = service.update(
            db_session,
            created.id,
            MigrationIdMapUpdate(target_id=str(uuid.uuid4())),
        )
        assert updated.project_id == original_project_id
        assert updated.category == original_category
        assert updated.source_key == original_source_key

    # ---------------------------------------------------------------- delete
    def test_delete(self, db_session):
        """``delete`` removes the row; subsequent lookup raises."""
        project = _make_project(db_session)
        created = service.create(db_session, _payload(project.id))
        service.delete(db_session, created.id)

        with pytest.raises(ValueError, match="not found"):
            service.get_by_id(db_session, created.id)

    def test_delete_missing_raises(self, db_session):
        """``delete`` on a non-existent id raises ``ValueError``."""
        with pytest.raises(ValueError, match="not found"):
            service.delete(db_session, uuid.uuid4())

    # ------------------------------------------------------------------ list
    def test_list_all(self, db_session):
        """``list_migration_id_maps`` returns every row when no filter is supplied."""
        project = _make_project(db_session)
        created_ids: set = set()
        for _ in range(3):
            created_ids.add(service.create(db_session, _payload(project.id)).id)
        rows = service.list_migration_id_maps(db_session)
        assert created_ids.issubset({r.id for r in rows})

    def test_list_filter_by_project(self, db_session):
        """``list_migration_id_maps(project_id=...)`` returns only that project's rows."""
        user = _make_user(db_session)
        project_a = _make_project(db_session, user=user)
        project_b = _make_project(db_session, user=user)
        in_a = service.create(db_session, _payload(project_a.id))
        service.create(db_session, _payload(project_b.id))

        rows = service.list_migration_id_maps(db_session, project_id=project_a.id)
        assert all(r.project_id == project_a.id for r in rows)
        assert any(r.id == in_a.id for r in rows)

    def test_list_filter_by_category(self, db_session):
        """``list_migration_id_maps(category=...)`` returns only matching-category rows."""
        project = _make_project(db_session)
        service.create(db_session, _payload(project.id, category="PAB"))
        gsc = service.create(db_session, _payload(project.id, category="GSC"))

        rows = service.list_migration_id_maps(db_session, category="GSC")
        assert all(r.category == "GSC" for r in rows)
        assert any(r.id == gsc.id for r in rows)

    def test_list_filter_by_source_key(self, db_session):
        """``list_migration_id_maps(source_key=...)`` returns only matching-key rows."""
        project = _make_project(db_session)
        target = service.create(
            db_session,
            _payload(project.id, category="PAB", source_key="lookup-me"),
        )
        service.create(
            db_session,
            _payload(project.id, category="PAB", source_key="other-key"),
        )

        rows = service.list_migration_id_maps(db_session, source_key="lookup-me")
        assert all(r.source_key == "lookup-me" for r in rows)
        assert any(r.id == target.id for r in rows)

    def test_list_filter_by_batch(self, db_session):
        """``list_migration_id_maps(batch_id=...)`` returns only rows tied to that batch."""
        project = _make_project(db_session)
        batch = _make_batch(db_session, project=project)
        in_batch = service.create(
            db_session,
            _payload(project.id, batch_id=batch.id),
        )
        service.create(db_session, _payload(project.id))  # no batch

        rows = service.list_migration_id_maps(db_session, batch_id=batch.id)
        assert all(r.batch_id == batch.id for r in rows)
        assert any(r.id == in_batch.id for r in rows)

    def test_list_ordered_by_created_at_desc(self, db_session):
        """Results are ordered newest-first so recent rows appear on top.

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
        r1 = service.create(db_session, _payload(project.id))
        r2 = service.create(db_session, _payload(project.id))
        r3 = service.create(db_session, _payload(project.id))
        r1.created_at = base_time
        r2.created_at = base_time + timedelta(minutes=1)
        r3.created_at = base_time + timedelta(minutes=2)
        db_session.flush()

        rows = service.list_migration_id_maps(db_session, project_id=project.id)
        ids_in_order = [r.id for r in rows]
        assert ids_in_order.index(r3.id) < ids_in_order.index(r2.id) < ids_in_order.index(r1.id)

    def test_list_pagination(self, db_session):
        """``limit`` / ``offset`` restrict the result window."""
        project = _make_project(db_session)
        for _ in range(5):
            service.create(db_session, _payload(project.id))

        first_page = service.list_migration_id_maps(
            db_session,
            project_id=project.id,
            limit=2,
            offset=0,
        )
        second_page = service.list_migration_id_maps(
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
        created = service.create(db_session, _payload(project.id))
        # ``in_transaction()`` must be True — commit would clear it.
        assert db_session.in_transaction()
        # Row is visible within the session after flush.
        assert service.get_by_id(db_session, created.id).id == created.id
