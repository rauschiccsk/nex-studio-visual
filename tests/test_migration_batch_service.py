"""Tests for :mod:`backend.services.migration_batch`.

Exercises every public CRUD entry point against the SAVEPOINT-isolated
session provided by ``tests/conftest.py``. Verifies:

* Happy-path list / get / create / update / delete.
* Default ``direction`` / ``status`` / ``error_count`` values come from
  the schema / DB ``server_default``.
* ``ValueError`` on missing ``id`` for get / update / delete.
* Immutable fields (``id``, ``project_id``, ``category``, ``direction``,
  ``created_at``) stay unchanged on update.
* List filters (``project_id``, ``category``, ``direction``, ``status``)
  and pagination.
* ``delete`` nulls out dependent ``migration_id_map.batch_id`` via the
  inbound ``ON DELETE SET NULL`` FK — id-map rows survive the run
  record being removed.
* No ``commit`` happens inside the service — the outer transaction
  rolls back cleanly at fixture teardown.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select as sa_select

from backend.db.models.foundation import User
from backend.db.models.migration import MigrationBatch, MigrationIdMap
from backend.db.models.projects import Project
from backend.schemas.migration_batch import (
    MigrationBatchCreate,
    MigrationBatchUpdate,
)
from backend.services import migration_batch as service


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


def _payload(project_id, **overrides) -> MigrationBatchCreate:
    """Return a :class:`MigrationBatchCreate` payload with sensible defaults."""
    defaults = {
        "project_id": project_id,
        "category": "PAB",
    }
    defaults.update(overrides)
    return MigrationBatchCreate(**defaults)


class TestMigrationBatchService:
    """Synchronous CRUD coverage for the MigrationBatch service."""

    # ------------------------------------------------------------------ create
    def test_create_batch(self, db_session):
        """``create`` persists the row and returns an ORM instance with server defaults."""
        project = _make_project(db_session)
        created = service.create(db_session, _payload(project.id))

        assert isinstance(created, MigrationBatch)
        assert created.id is not None
        assert created.created_at is not None
        assert created.project_id == project.id
        assert created.category == "PAB"
        # Server / schema defaults.
        assert created.direction == "extract"
        assert created.status == "pending"
        assert created.error_count == 0

    def test_create_defaults(self, db_session):
        """Omitted optional fields take their schema / DB defaults."""
        project = _make_project(db_session)
        payload = MigrationBatchCreate(
            project_id=project.id,
            category="GSC",
        )
        created = service.create(db_session, payload)
        assert created.direction == "extract"
        assert created.status == "pending"
        assert created.error_count == 0
        assert created.source_count is None
        assert created.target_count is None
        assert created.error_log is None
        assert created.started_at is None
        assert created.completed_at is None

    def test_create_with_all_optional_fields(self, db_session):
        """``create`` accepts and persists every optional field."""
        from datetime import datetime, timezone

        project = _make_project(db_session)
        started = datetime(2026, 4, 15, 10, 0, tzinfo=timezone.utc)
        completed = datetime(2026, 4, 15, 10, 30, tzinfo=timezone.utc)
        payload = MigrationBatchCreate(
            project_id=project.id,
            category="STK",
            direction="load",
            status="completed",
            source_count=1500,
            target_count=1480,
            error_count=20,
            error_log="Row 42: encoding error",
            started_at=started,
            completed_at=completed,
        )
        created = service.create(db_session, payload)
        assert created.direction == "load"
        assert created.status == "completed"
        assert created.source_count == 1500
        assert created.target_count == 1480
        assert created.error_count == 20
        assert created.error_log == "Row 42: encoding error"
        assert created.started_at == started
        assert created.completed_at == completed

    def test_create_multiple_batches_same_project_and_category(self, db_session):
        """``migration_batches`` has no unique constraint — retries are allowed."""
        project = _make_project(db_session)
        first = service.create(db_session, _payload(project.id, category="PAB"))
        second = service.create(db_session, _payload(project.id, category="PAB"))
        third = service.create(
            db_session,
            _payload(project.id, category="PAB", direction="load"),
        )

        assert first.id != second.id != third.id
        assert first.category == second.category == third.category == "PAB"

    # ------------------------------------------------------------------- get
    def test_get_by_id(self, db_session):
        """``get_by_id`` returns the batch when it exists."""
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
        """``update`` changes every mutable column."""
        from datetime import datetime, timezone

        project = _make_project(db_session)
        created = service.create(db_session, _payload(project.id))
        original_id = created.id
        original_project_id = created.project_id
        original_category = created.category
        original_direction = created.direction
        original_created_at = created.created_at

        started = datetime(2026, 4, 15, 12, 0, tzinfo=timezone.utc)
        completed = datetime(2026, 4, 15, 12, 30, tzinfo=timezone.utc)
        updated = service.update(
            db_session,
            created.id,
            MigrationBatchUpdate(
                status="completed",
                source_count=100,
                target_count=95,
                error_count=5,
                error_log="Some errors",
                started_at=started,
                completed_at=completed,
            ),
        )
        assert updated.status == "completed"
        assert updated.source_count == 100
        assert updated.target_count == 95
        assert updated.error_count == 5
        assert updated.error_log == "Some errors"
        assert updated.started_at == started
        assert updated.completed_at == completed
        # Immutable fields unchanged.
        assert updated.id == original_id
        assert updated.project_id == original_project_id
        assert updated.category == original_category
        assert updated.direction == original_direction
        assert updated.created_at == original_created_at

    def test_update_partial(self, db_session):
        """``update`` with only ``status`` leaves other fields untouched."""
        project = _make_project(db_session)
        created = service.create(
            db_session,
            _payload(project.id, source_count=42),
        )
        updated = service.update(
            db_session,
            created.id,
            MigrationBatchUpdate(status="running"),
        )
        assert updated.status == "running"
        assert updated.source_count == 42
        assert updated.category == created.category

    def test_update_missing_raises(self, db_session):
        """``update`` on a non-existent id raises ``ValueError``."""
        with pytest.raises(ValueError, match="not found"):
            service.update(
                db_session,
                uuid.uuid4(),
                MigrationBatchUpdate(status="running"),
            )

    def test_update_ignores_disallowed_fields(self, db_session):
        """Immutable ``project_id`` / ``category`` / ``direction`` stay put.

        ``MigrationBatchUpdate`` has no fields for these columns, so
        the service's allow-list merely formalises that contract. A
        benign update (e.g. ``error_count``) on the same batch must
        leave them untouched.
        """
        project = _make_project(db_session)
        created = service.create(db_session, _payload(project.id))
        original_project_id = created.project_id
        original_category = created.category
        original_direction = created.direction

        updated = service.update(
            db_session,
            created.id,
            MigrationBatchUpdate(error_count=7),
        )
        assert updated.error_count == 7
        assert updated.project_id == original_project_id
        assert updated.category == original_category
        assert updated.direction == original_direction

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

    def test_delete_sets_null_on_id_map(self, db_session):
        """``delete`` relies on DB-level ``SET NULL`` for dependent ``migration_id_map``.

        ``migration_id_map.batch_id`` has ``ON DELETE SET NULL``
        (DESIGN.md §1.10), so the id-map row is retained with its
        ``batch_id`` cleared — cross-reference integrity of the
        migrated data survives a deleted run record.
        """
        project = _make_project(db_session)
        created = service.create(db_session, _payload(project.id))

        id_map = MigrationIdMap(
            project_id=project.id,
            category="PAB",
            source_key="legacy-001",
            target_id=str(uuid.uuid4()),
            batch_id=created.id,
        )
        db_session.add(id_map)
        db_session.flush()
        id_map_pk = id_map.id

        service.delete(db_session, created.id)

        # Expire so the next query hits the DB and sees the SET NULL
        # effect rather than stale in-memory ORM state.
        db_session.expire_all()

        refreshed = db_session.execute(
            sa_select(MigrationIdMap).where(MigrationIdMap.id == id_map_pk)
        ).scalar_one_or_none()
        assert refreshed is not None
        assert refreshed.batch_id is None

    # ------------------------------------------------------------------ list
    def test_list_all(self, db_session):
        """``list_migration_batches`` returns every batch when no filter is supplied."""
        project = _make_project(db_session)
        created_ids: set = set()
        for _ in range(3):
            created_ids.add(service.create(db_session, _payload(project.id)).id)
        rows = service.list_migration_batches(db_session)
        assert created_ids.issubset({b.id for b in rows})

    def test_list_filter_by_project(self, db_session):
        """``list_migration_batches(project_id=...)`` returns only that project's batches."""
        user = _make_user(db_session)
        project_a = _make_project(db_session, user=user)
        project_b = _make_project(db_session, user=user)
        in_a = service.create(db_session, _payload(project_a.id))
        service.create(db_session, _payload(project_b.id))

        rows = service.list_migration_batches(db_session, project_id=project_a.id)
        assert all(b.project_id == project_a.id for b in rows)
        assert any(b.id == in_a.id for b in rows)

    def test_list_filter_by_category(self, db_session):
        """``list_migration_batches(category=...)`` returns only matching-category batches."""
        project = _make_project(db_session)
        service.create(db_session, _payload(project.id, category="PAB"))
        gsc = service.create(db_session, _payload(project.id, category="GSC"))

        rows = service.list_migration_batches(db_session, category="GSC")
        assert all(b.category == "GSC" for b in rows)
        assert any(b.id == gsc.id for b in rows)

    def test_list_filter_by_direction(self, db_session):
        """``list_migration_batches(direction=...)`` returns only matching-direction batches."""
        project = _make_project(db_session)
        service.create(db_session, _payload(project.id, direction="extract"))
        load = service.create(db_session, _payload(project.id, direction="load"))

        rows = service.list_migration_batches(db_session, direction="load")
        assert all(b.direction == "load" for b in rows)
        assert any(b.id == load.id for b in rows)

    def test_list_filter_by_status(self, db_session):
        """``list_migration_batches(status=...)`` returns only matching-status batches."""
        project = _make_project(db_session)
        service.create(db_session, _payload(project.id))  # status='pending'
        failed = service.create(db_session, _payload(project.id, status="failed"))

        rows = service.list_migration_batches(db_session, status="failed")
        assert all(b.status == "failed" for b in rows)
        assert any(b.id == failed.id for b in rows)

    def test_list_ordered_by_created_at_desc(self, db_session):
        """Results are ordered newest-first so recent runs appear on top.

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
        b1 = service.create(db_session, _payload(project.id))
        b2 = service.create(db_session, _payload(project.id))
        b3 = service.create(db_session, _payload(project.id))
        b1.created_at = base_time
        b2.created_at = base_time + timedelta(minutes=1)
        b3.created_at = base_time + timedelta(minutes=2)
        db_session.flush()

        rows = service.list_migration_batches(db_session, project_id=project.id)
        ids_in_order = [b.id for b in rows]
        # Most-recently-created batch appears first; earliest last.
        assert ids_in_order.index(b3.id) < ids_in_order.index(b2.id) < ids_in_order.index(b1.id)

    def test_list_pagination(self, db_session):
        """``limit`` / ``offset`` restrict the result window."""
        project = _make_project(db_session)
        for _ in range(5):
            service.create(db_session, _payload(project.id))

        first_page = service.list_migration_batches(db_session, project_id=project.id, limit=2, offset=0)
        second_page = service.list_migration_batches(db_session, project_id=project.id, limit=2, offset=2)
        assert len(first_page) == 2
        assert len(second_page) == 2
        first_ids = {b.id for b in first_page}
        second_ids = {b.id for b in second_page}
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
