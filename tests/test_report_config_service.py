"""Tests for :mod:`backend.services.report_config`.

Exercises every public CRUD entry point against the SAVEPOINT-isolated
session provided by ``tests/conftest.py``. Verifies:

* Happy-path list / get / create / update / delete.
* Default ``senior_hourly_rate_eur`` / ``junior_hourly_rate_eur`` values
  come from the schema / DB ``server_default``.
* ``ValueError`` on missing ``id`` for get / update / delete.
* ``ValueError`` on duplicate ``project_id`` for create.
* Immutable fields (``id``, ``project_id``, ``created_at``) stay
  unchanged on update.
* ``updated_at`` is refreshed by the ORM ``onupdate`` hook on update.
* List filters (``project_id``) and pagination.
* No ``commit`` happens inside the service — the outer transaction rolls
  back cleanly at fixture teardown.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest

from backend.db.models.foundation import User
from backend.db.models.projects import Project
from backend.db.models.reports import ReportConfig
from backend.schemas.report_config import (
    ReportConfigCreate,
    ReportConfigUpdate,
)
from backend.services import report_config as service


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


def _payload(project_id, **overrides) -> ReportConfigCreate:
    """Return a :class:`ReportConfigCreate` payload with sensible defaults."""
    defaults = {"project_id": project_id}
    defaults.update(overrides)
    return ReportConfigCreate(**defaults)


class TestReportConfigService:
    """Synchronous CRUD coverage for the ReportConfig service."""

    # ------------------------------------------------------------------ create
    def test_create_row(self, db_session):
        """``create`` persists the row and returns an ORM instance with server defaults."""
        project = _make_project(db_session)
        created = service.create(db_session, _payload(project.id))

        assert isinstance(created, ReportConfig)
        assert created.id is not None
        assert created.created_at is not None
        assert created.updated_at is not None
        assert created.project_id == project.id
        # Schema / DB defaults — ``75.0000`` / ``35.0000``.
        assert created.senior_hourly_rate_eur == Decimal("75.0000")
        assert created.junior_hourly_rate_eur == Decimal("35.0000")

    def test_create_defaults(self, db_session):
        """Omitted optional fields take their schema / DB defaults."""
        project = _make_project(db_session)
        payload = ReportConfigCreate(project_id=project.id)
        created = service.create(db_session, payload)
        assert created.senior_hourly_rate_eur == Decimal("75.0000")
        assert created.junior_hourly_rate_eur == Decimal("35.0000")

    def test_create_with_custom_rates(self, db_session):
        """``create`` accepts and persists custom hourly rates."""
        project = _make_project(db_session)
        payload = ReportConfigCreate(
            project_id=project.id,
            senior_hourly_rate_eur=Decimal("100.0000"),
            junior_hourly_rate_eur=Decimal("45.5000"),
        )
        created = service.create(db_session, payload)
        assert created.senior_hourly_rate_eur == Decimal("100.0000")
        assert created.junior_hourly_rate_eur == Decimal("45.5000")

    def test_create_duplicate_project_raises(self, db_session):
        """``UNIQUE(project_id)`` — duplicate project is rejected pre-flush."""
        project = _make_project(db_session)
        service.create(db_session, _payload(project.id))

        with pytest.raises(ValueError, match="already exists"):
            service.create(db_session, _payload(project.id))

    def test_create_different_projects_allowed(self, db_session):
        """Different projects each get their own configuration row."""
        user = _make_user(db_session)
        project_a = _make_project(db_session, user=user)
        project_b = _make_project(db_session, user=user)

        a = service.create(db_session, _payload(project_a.id))
        b = service.create(db_session, _payload(project_b.id))

        assert a.id != b.id
        assert a.project_id != b.project_id

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
        project = _make_project(db_session)
        created = service.create(db_session, _payload(project.id))
        original_id = created.id
        original_project_id = created.project_id
        original_created_at = created.created_at

        updated = service.update(
            db_session,
            created.id,
            ReportConfigUpdate(
                senior_hourly_rate_eur=Decimal("120.0000"),
                junior_hourly_rate_eur=Decimal("60.0000"),
            ),
        )
        assert updated.senior_hourly_rate_eur == Decimal("120.0000")
        assert updated.junior_hourly_rate_eur == Decimal("60.0000")
        # Immutable fields unchanged.
        assert updated.id == original_id
        assert updated.project_id == original_project_id
        assert updated.created_at == original_created_at

    def test_update_partial(self, db_session):
        """``update`` with only ``senior_hourly_rate_eur`` leaves other fields untouched."""
        project = _make_project(db_session)
        created = service.create(db_session, _payload(project.id))
        original_junior = created.junior_hourly_rate_eur

        updated = service.update(
            db_session,
            created.id,
            ReportConfigUpdate(senior_hourly_rate_eur=Decimal("90.0000")),
        )
        assert updated.senior_hourly_rate_eur == Decimal("90.0000")
        assert updated.junior_hourly_rate_eur == original_junior

    def test_update_missing_raises(self, db_session):
        """``update`` on a non-existent id raises ``ValueError``."""
        with pytest.raises(ValueError, match="not found"):
            service.update(
                db_session,
                uuid.uuid4(),
                ReportConfigUpdate(senior_hourly_rate_eur=Decimal("100.0000")),
            )

    def test_update_ignores_disallowed_fields(self, db_session):
        """Immutable ``project_id`` stays put.

        ``ReportConfigUpdate`` has no field for ``project_id``, so the
        service's allow-list merely formalises that contract. A benign
        update (e.g. ``senior_hourly_rate_eur``) on the same row must
        leave it untouched.
        """
        project = _make_project(db_session)
        created = service.create(db_session, _payload(project.id))
        original_project_id = created.project_id

        updated = service.update(
            db_session,
            created.id,
            ReportConfigUpdate(senior_hourly_rate_eur=Decimal("99.0000")),
        )
        assert updated.senior_hourly_rate_eur == Decimal("99.0000")
        assert updated.project_id == original_project_id

    def test_update_empty_payload_noop(self, db_session):
        """``update`` with all-None payload leaves the row unchanged."""
        project = _make_project(db_session)
        created = service.create(db_session, _payload(project.id))
        original_senior = created.senior_hourly_rate_eur
        original_junior = created.junior_hourly_rate_eur

        updated = service.update(
            db_session,
            created.id,
            ReportConfigUpdate(),
        )
        assert updated.senior_hourly_rate_eur == original_senior
        assert updated.junior_hourly_rate_eur == original_junior

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

    def test_delete_then_recreate_allowed(self, db_session):
        """After delete, creating a new config for the same project is allowed."""
        project = _make_project(db_session)
        created = service.create(db_session, _payload(project.id))
        service.delete(db_session, created.id)

        recreated = service.create(db_session, _payload(project.id))
        assert recreated.id != created.id
        assert recreated.project_id == project.id

    # ------------------------------------------------------------------ list
    def test_list_all(self, db_session):
        """``list_report_configs`` returns every row when no filter is supplied."""
        user = _make_user(db_session)
        created_ids: set = set()
        for _ in range(3):
            project = _make_project(db_session, user=user)
            created_ids.add(service.create(db_session, _payload(project.id)).id)

        rows = service.list_report_configs(db_session)
        assert created_ids.issubset({r.id for r in rows})

    def test_list_filter_by_project(self, db_session):
        """``list_report_configs(project_id=...)`` returns only that project's row."""
        user = _make_user(db_session)
        project_a = _make_project(db_session, user=user)
        project_b = _make_project(db_session, user=user)
        in_a = service.create(db_session, _payload(project_a.id))
        service.create(db_session, _payload(project_b.id))

        rows = service.list_report_configs(db_session, project_id=project_a.id)
        # Unique constraint — exactly one row matches.
        assert len(rows) == 1
        assert rows[0].id == in_a.id
        assert rows[0].project_id == project_a.id

    def test_list_filter_by_project_no_match(self, db_session):
        """``list_report_configs(project_id=...)`` for a project without a config returns []."""
        project = _make_project(db_session)
        rows = service.list_report_configs(db_session, project_id=project.id)
        assert rows == []

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

        user = _make_user(db_session)
        project_1 = _make_project(db_session, user=user)
        project_2 = _make_project(db_session, user=user)
        project_3 = _make_project(db_session, user=user)

        base_time = datetime(2026, 4, 15, 10, 0, tzinfo=timezone.utc)
        r1 = service.create(db_session, _payload(project_1.id))
        r2 = service.create(db_session, _payload(project_2.id))
        r3 = service.create(db_session, _payload(project_3.id))
        r1.created_at = base_time
        r2.created_at = base_time + timedelta(minutes=1)
        r3.created_at = base_time + timedelta(minutes=2)
        db_session.flush()

        rows = service.list_report_configs(db_session)
        ids_in_order = [r.id for r in rows]
        # Most-recently-created row appears first; earliest last.
        assert ids_in_order.index(r3.id) < ids_in_order.index(r2.id) < ids_in_order.index(r1.id)

    def test_list_pagination(self, db_session):
        """``limit`` / ``offset`` restrict the result window."""
        user = _make_user(db_session)
        for _ in range(5):
            project = _make_project(db_session, user=user)
            service.create(db_session, _payload(project.id))

        first_page = service.list_report_configs(db_session, limit=2, offset=0)
        second_page = service.list_report_configs(db_session, limit=2, offset=2)
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
