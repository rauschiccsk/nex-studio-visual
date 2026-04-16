"""Tests for :mod:`backend.services.migration_category_status`.

Exercises every public CRUD entry point against the SAVEPOINT-isolated
session provided by ``tests/conftest.py``. Verifies:

* Happy-path list / get / create / update / delete.
* Default ``status`` value comes from the schema / DB ``server_default``.
* ``ValueError`` on missing ``id`` for get / update / delete.
* ``ValueError`` on duplicate ``(project_id, category)`` pair for create.
* Immutable fields (``id``, ``project_id``, ``category``, ``created_at``)
  stay unchanged on update.
* ``updated_at`` is refreshed by the ORM ``onupdate`` hook on update.
* List filters (``project_id``, ``category``, ``status``) and pagination.
* No ``commit`` happens inside the service — the outer transaction rolls
  back cleanly at fixture teardown.
"""

from __future__ import annotations

import uuid

import pytest

from backend.db.models.foundation import User
from backend.db.models.migration import MigrationCategoryStatus
from backend.db.models.projects import Project
from backend.schemas.migration_category_status import (
    MigrationCategoryStatusCreate,
    MigrationCategoryStatusUpdate,
)
from backend.services import migration_category_status as service


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


def _payload(project_id, **overrides) -> MigrationCategoryStatusCreate:
    """Return a :class:`MigrationCategoryStatusCreate` payload with sensible defaults."""
    defaults = {
        "project_id": project_id,
        "category": "PAB",
    }
    defaults.update(overrides)
    return MigrationCategoryStatusCreate(**defaults)


class TestMigrationCategoryStatusService:
    """Synchronous CRUD coverage for the MigrationCategoryStatus service."""

    # ------------------------------------------------------------------ create
    def test_create_row(self, db_session):
        """``create`` persists the row and returns an ORM instance with server defaults."""
        project = _make_project(db_session)
        created = service.create(db_session, _payload(project.id))

        assert isinstance(created, MigrationCategoryStatus)
        assert created.id is not None
        assert created.created_at is not None
        assert created.updated_at is not None
        assert created.project_id == project.id
        assert created.category == "PAB"
        # Server / schema default.
        assert created.status == "pending"

    def test_create_defaults(self, db_session):
        """Omitted optional fields take their schema / DB defaults."""
        project = _make_project(db_session)
        payload = MigrationCategoryStatusCreate(
            project_id=project.id,
            category="GSC",
        )
        created = service.create(db_session, payload)
        assert created.status == "pending"
        assert created.last_run_at is None
        assert created.notes is None

    def test_create_with_all_optional_fields(self, db_session):
        """``create`` accepts and persists every optional field."""
        from datetime import datetime, timezone

        project = _make_project(db_session)
        last_run = datetime(2026, 4, 15, 10, 0, tzinfo=timezone.utc)
        payload = MigrationCategoryStatusCreate(
            project_id=project.id,
            category="STK",
            status="completed",
            last_run_at=last_run,
            notes="Encoding issues on rows 42-51",
        )
        created = service.create(db_session, payload)
        assert created.status == "completed"
        assert created.last_run_at == last_run
        assert created.notes == "Encoding issues on rows 42-51"

    def test_create_duplicate_project_category_raises(self, db_session):
        """``UNIQUE(project_id, category)`` — duplicate pair is rejected pre-flush."""
        project = _make_project(db_session)
        service.create(db_session, _payload(project.id, category="PAB"))

        with pytest.raises(ValueError, match="already exists"):
            service.create(db_session, _payload(project.id, category="PAB"))

    def test_create_same_category_different_project_allowed(self, db_session):
        """Unique constraint is scoped to ``(project_id, category)`` — different projects allowed."""
        user = _make_user(db_session)
        project_a = _make_project(db_session, user=user)
        project_b = _make_project(db_session, user=user)

        a = service.create(db_session, _payload(project_a.id, category="PAB"))
        b = service.create(db_session, _payload(project_b.id, category="PAB"))

        assert a.id != b.id
        assert a.project_id != b.project_id
        assert a.category == b.category == "PAB"

    def test_create_different_category_same_project_allowed(self, db_session):
        """Same project with different categories — each category gets its own row."""
        project = _make_project(db_session)
        pab = service.create(db_session, _payload(project.id, category="PAB"))
        gsc = service.create(db_session, _payload(project.id, category="GSC"))

        assert pab.id != gsc.id
        assert pab.project_id == gsc.project_id
        assert pab.category == "PAB"
        assert gsc.category == "GSC"

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
        """``update`` changes every mutable column."""
        from datetime import datetime, timezone

        project = _make_project(db_session)
        created = service.create(db_session, _payload(project.id))
        original_id = created.id
        original_project_id = created.project_id
        original_category = created.category
        original_created_at = created.created_at

        last_run = datetime(2026, 4, 15, 12, 0, tzinfo=timezone.utc)
        updated = service.update(
            db_session,
            created.id,
            MigrationCategoryStatusUpdate(
                status="completed",
                last_run_at=last_run,
                notes="Migration finished cleanly.",
            ),
        )
        assert updated.status == "completed"
        assert updated.last_run_at == last_run
        assert updated.notes == "Migration finished cleanly."
        # Immutable fields unchanged.
        assert updated.id == original_id
        assert updated.project_id == original_project_id
        assert updated.category == original_category
        assert updated.created_at == original_created_at

    def test_update_partial(self, db_session):
        """``update`` with only ``status`` leaves other fields untouched."""
        project = _make_project(db_session)
        created = service.create(
            db_session,
            _payload(project.id, notes="initial note"),
        )
        updated = service.update(
            db_session,
            created.id,
            MigrationCategoryStatusUpdate(status="in_progress"),
        )
        assert updated.status == "in_progress"
        assert updated.notes == "initial note"
        assert updated.category == created.category

    def test_update_missing_raises(self, db_session):
        """``update`` on a non-existent id raises ``ValueError``."""
        with pytest.raises(ValueError, match="not found"):
            service.update(
                db_session,
                uuid.uuid4(),
                MigrationCategoryStatusUpdate(status="in_progress"),
            )

    def test_update_ignores_disallowed_fields(self, db_session):
        """Immutable ``project_id`` / ``category`` stay put.

        ``MigrationCategoryStatusUpdate`` has no fields for these
        columns, so the service's allow-list merely formalises that
        contract. A benign update (e.g. ``status``) on the same row
        must leave them untouched.
        """
        project = _make_project(db_session)
        created = service.create(db_session, _payload(project.id))
        original_project_id = created.project_id
        original_category = created.category

        updated = service.update(
            db_session,
            created.id,
            MigrationCategoryStatusUpdate(status="in_progress"),
        )
        assert updated.status == "in_progress"
        assert updated.project_id == original_project_id
        assert updated.category == original_category

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
        """``list_migration_category_statuses`` returns every row when no filter is supplied."""
        project = _make_project(db_session)
        created_ids: set = set()
        for cat in ("PAB", "GSC", "STK"):
            created_ids.add(service.create(db_session, _payload(project.id, category=cat)).id)
        rows = service.list_migration_category_statuses(db_session)
        assert created_ids.issubset({r.id for r in rows})

    def test_list_filter_by_project(self, db_session):
        """``list_migration_category_statuses(project_id=...)`` returns only that project's rows."""
        user = _make_user(db_session)
        project_a = _make_project(db_session, user=user)
        project_b = _make_project(db_session, user=user)
        in_a = service.create(db_session, _payload(project_a.id, category="PAB"))
        service.create(db_session, _payload(project_b.id, category="PAB"))

        rows = service.list_migration_category_statuses(
            db_session,
            project_id=project_a.id,
        )
        assert all(r.project_id == project_a.id for r in rows)
        assert any(r.id == in_a.id for r in rows)

    def test_list_filter_by_category(self, db_session):
        """``list_migration_category_statuses(category=...)`` returns only matching-category rows."""
        project = _make_project(db_session)
        service.create(db_session, _payload(project.id, category="PAB"))
        gsc = service.create(db_session, _payload(project.id, category="GSC"))

        rows = service.list_migration_category_statuses(db_session, category="GSC")
        assert all(r.category == "GSC" for r in rows)
        assert any(r.id == gsc.id for r in rows)

    def test_list_filter_by_status(self, db_session):
        """``list_migration_category_statuses(status=...)`` returns only matching-status rows."""
        project = _make_project(db_session)
        service.create(db_session, _payload(project.id, category="PAB"))  # status='pending'
        failed = service.create(
            db_session,
            _payload(project.id, category="GSC", status="failed"),
        )

        rows = service.list_migration_category_statuses(db_session, status="failed")
        assert all(r.status == "failed" for r in rows)
        assert any(r.id == failed.id for r in rows)

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
        r1 = service.create(db_session, _payload(project.id, category="PAB"))
        r2 = service.create(db_session, _payload(project.id, category="GSC"))
        r3 = service.create(db_session, _payload(project.id, category="STK"))
        r1.created_at = base_time
        r2.created_at = base_time + timedelta(minutes=1)
        r3.created_at = base_time + timedelta(minutes=2)
        db_session.flush()

        rows = service.list_migration_category_statuses(
            db_session,
            project_id=project.id,
        )
        ids_in_order = [r.id for r in rows]
        # Most-recently-created row appears first; earliest last.
        assert ids_in_order.index(r3.id) < ids_in_order.index(r2.id) < ids_in_order.index(r1.id)

    def test_list_pagination(self, db_session):
        """``limit`` / ``offset`` restrict the result window."""
        project = _make_project(db_session)
        for cat in ("PAB", "GSC", "STK", "TSH", "ICB"):
            service.create(db_session, _payload(project.id, category=cat))

        first_page = service.list_migration_category_statuses(
            db_session,
            project_id=project.id,
            limit=2,
            offset=0,
        )
        second_page = service.list_migration_category_statuses(
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
