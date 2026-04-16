"""Tests for :mod:`backend.services.project_module`.

Exercises every public CRUD entry point against the SAVEPOINT-isolated
session provided by ``tests/conftest.py``. Verifies:

* Happy-path list / get / create / update / delete.
* ``ValueError`` on duplicate ``(project_id, code)`` natural key.
* ``ValueError`` on missing ``id`` for get / update / delete.
* Same ``code`` allowed across different projects (per
  ``UNIQUE(project_id, code)``).
* Update allow-list — only ``code``, ``name``, ``category``,
  ``status`` and ``design_doc_path`` are applied; ``project_id``,
  ``id``, ``created_at`` are preserved.
* Update of ``code`` re-validates uniqueness (within the same
  project) and raises :class:`ValueError` on collision.
* List filters (``project_id``, ``status``, ``category``) and
  pagination.
* ``delete`` removes the row — no inbound FK uses ``RESTRICT`` so no
  dependency guard is needed.
* No ``commit`` happens inside the service — the outer transaction
  rolls back cleanly at fixture teardown.
"""

from __future__ import annotations

import uuid

import pytest

from backend.db.models.foundation import User
from backend.db.models.projects import Project, ProjectModule
from backend.schemas.project_module import (
    ProjectModuleCreate,
    ProjectModuleUpdate,
)
from backend.services import project_module as service


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


def _payload(project_id, **overrides) -> ProjectModuleCreate:
    """Return a :class:`ProjectModuleCreate` payload with sensible defaults."""
    suffix = uuid.uuid4().hex[:4].upper()
    defaults = {
        "project_id": project_id,
        "code": f"M{suffix}",
        "name": f"Module {suffix}",
        "category": "General",
    }
    defaults.update(overrides)
    return ProjectModuleCreate(**defaults)


class TestProjectModuleService:
    """Synchronous CRUD coverage for the ProjectModule service."""

    # ------------------------------------------------------------------ create
    def test_create_module(self, db_session):
        """``create`` persists the row and returns an ORM instance with server defaults."""
        project = _make_project(db_session)

        created = service.create(
            db_session,
            _payload(project.id, code="PAB", name="Katalóg partnerov", category="Katalógy"),
        )

        assert isinstance(created, ProjectModule)
        assert created.id is not None
        assert created.created_at is not None
        assert created.updated_at is not None
        assert created.project_id == project.id
        assert created.code == "PAB"
        assert created.name == "Katalóg partnerov"
        assert created.category == "Katalógy"
        # server_default 'planned'
        assert created.status == "planned"
        assert created.design_doc_path is None

    def test_create_with_all_fields(self, db_session):
        """``create`` applies every supplied field."""
        project = _make_project(db_session)

        created = service.create(
            db_session,
            _payload(
                project.id,
                code="GSC",
                name="General Stock Control",
                category="Sklad",
                status="in_design",
                design_doc_path="/home/icc/kb/gsc/DESIGN.md",
            ),
        )

        assert created.status == "in_design"
        assert created.design_doc_path == "/home/icc/kb/gsc/DESIGN.md"

    def test_create_duplicate_natural_key_raises(self, db_session):
        """``UNIQUE(project_id, code)`` — duplicate pair rejected pre-flush."""
        project = _make_project(db_session)
        service.create(db_session, _payload(project.id, code="PAB"))

        with pytest.raises(ValueError, match="already exists"):
            service.create(db_session, _payload(project.id, code="PAB"))

    def test_create_same_code_different_projects_allowed(self, db_session):
        """The same code may exist across multiple projects."""
        p1 = _make_project(db_session)
        p2 = _make_project(db_session)

        a = service.create(db_session, _payload(p1.id, code="PAB"))
        b = service.create(db_session, _payload(p2.id, code="PAB"))

        assert a.id != b.id
        assert a.code == b.code == "PAB"
        assert a.project_id != b.project_id

    def test_create_same_project_different_codes_allowed(self, db_session):
        """A single project may host many modules with distinct codes."""
        project = _make_project(db_session)

        a = service.create(db_session, _payload(project.id, code="PAB"))
        b = service.create(db_session, _payload(project.id, code="GSC"))

        assert a.id != b.id
        assert a.project_id == b.project_id == project.id
        assert a.code != b.code

    # ------------------------------------------------------------------- get
    def test_get_by_id(self, db_session):
        """``get_by_id`` returns the row when it exists."""
        project = _make_project(db_session)
        created = service.create(db_session, _payload(project.id))

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
        project = _make_project(db_session)
        created = service.create(
            db_session,
            _payload(project.id, code="PAB", name="Original", category="Katalógy"),
        )

        updated = service.update(
            db_session,
            created.id,
            ProjectModuleUpdate(
                code="PAB2",
                name="Updated Name",
                category="Administrácia",
                status="in_development",
                design_doc_path="/home/icc/kb/pab/DESIGN.md",
            ),
        )

        assert updated.id == created.id
        assert updated.code == "PAB2"
        assert updated.name == "Updated Name"
        assert updated.category == "Administrácia"
        assert updated.status == "in_development"
        assert updated.design_doc_path == "/home/icc/kb/pab/DESIGN.md"

    def test_update_partial(self, db_session):
        """``update`` leaves omitted fields untouched (PATCH semantics)."""
        project = _make_project(db_session)
        created = service.create(
            db_session,
            _payload(project.id, code="PAB", name="Original", category="Katalógy"),
        )

        updated = service.update(
            db_session,
            created.id,
            ProjectModuleUpdate(status="done"),
        )

        assert updated.status == "done"
        assert updated.code == "PAB"
        assert updated.name == "Original"
        assert updated.category == "Katalógy"

    def test_update_preserves_immutable_fields(self, db_session):
        """``id``, ``project_id``, ``created_at`` must not change across ``update``."""
        project = _make_project(db_session)
        created = service.create(db_session, _payload(project.id))

        original_id = created.id
        original_project_id = created.project_id
        original_created_at = created.created_at

        updated = service.update(
            db_session,
            created.id,
            ProjectModuleUpdate(name="New Name"),
        )

        assert updated.id == original_id
        assert updated.project_id == original_project_id
        assert updated.created_at == original_created_at

    def test_update_code_collision_raises(self, db_session):
        """Renaming ``code`` to one already in use within the same project raises."""
        project = _make_project(db_session)
        service.create(db_session, _payload(project.id, code="PAB"))
        m2 = service.create(db_session, _payload(project.id, code="GSC"))

        with pytest.raises(ValueError, match="already exists"):
            service.update(db_session, m2.id, ProjectModuleUpdate(code="PAB"))

    def test_update_code_same_value_noop(self, db_session):
        """Setting ``code`` to its current value does not trip the uniqueness check."""
        project = _make_project(db_session)
        created = service.create(db_session, _payload(project.id, code="PAB"))

        updated = service.update(
            db_session,
            created.id,
            ProjectModuleUpdate(code="PAB"),
        )
        assert updated.code == "PAB"

    def test_update_code_same_across_projects(self, db_session):
        """Renaming to a code used in *another* project is allowed."""
        p1 = _make_project(db_session)
        p2 = _make_project(db_session)
        service.create(db_session, _payload(p1.id, code="PAB"))
        m2 = service.create(db_session, _payload(p2.id, code="GSC"))

        updated = service.update(
            db_session,
            m2.id,
            ProjectModuleUpdate(code="PAB"),
        )
        assert updated.code == "PAB"
        assert updated.project_id == p2.id

    def test_update_missing_raises(self, db_session):
        """``update`` on a non-existent id raises ``ValueError``."""
        with pytest.raises(ValueError, match="not found"):
            service.update(
                db_session,
                uuid.uuid4(),
                ProjectModuleUpdate(name="x"),
            )

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

    def test_delete_one_module_leaves_others_intact(self, db_session):
        """Deleting one module does not affect siblings in the same project."""
        project = _make_project(db_session)
        a = service.create(db_session, _payload(project.id, code="PAB"))
        b = service.create(db_session, _payload(project.id, code="GSC"))

        service.delete(db_session, a.id)

        assert service.get_by_id(db_session, b.id).id == b.id

    # ------------------------------------------------------------------ list
    def test_list_all(self, db_session):
        """``list_project_modules`` returns every row when no filter is supplied."""
        project = _make_project(db_session)
        created_ids: set = set()
        for i in range(3):
            created_ids.add(service.create(db_session, _payload(project.id, code=f"M{i:02d}")).id)

        rows = service.list_project_modules(db_session)
        assert created_ids.issubset({r.id for r in rows})

    def test_list_filter_by_project(self, db_session):
        """``list_project_modules(project_id=...)`` returns only that project's modules."""
        p1 = _make_project(db_session)
        p2 = _make_project(db_session)
        in_a = service.create(db_session, _payload(p1.id))
        service.create(db_session, _payload(p2.id))

        rows = service.list_project_modules(db_session, project_id=p1.id)
        assert all(r.project_id == p1.id for r in rows)
        assert any(r.id == in_a.id for r in rows)

    def test_list_filter_by_status(self, db_session):
        """``status`` filter returns only rows with the given status."""
        project = _make_project(db_session)
        planned = service.create(db_session, _payload(project.id, code="PL", status="planned"))
        in_dev = service.create(db_session, _payload(project.id, code="DV", status="in_development"))

        planned_rows = service.list_project_modules(db_session, status="planned")
        planned_ids = {r.id for r in planned_rows}
        assert planned.id in planned_ids
        assert in_dev.id not in planned_ids

        dev_rows = service.list_project_modules(db_session, status="in_development")
        dev_ids = {r.id for r in dev_rows}
        assert in_dev.id in dev_ids
        assert planned.id not in dev_ids

    def test_list_filter_by_category(self, db_session):
        """``category`` filter returns only rows in that category."""
        project = _make_project(db_session)
        a = service.create(db_session, _payload(project.id, code="PAB", category="Katalógy"))
        b = service.create(db_session, _payload(project.id, code="GSC", category="Sklad"))

        rows = service.list_project_modules(db_session, category="Katalógy")
        ids = {r.id for r in rows}
        assert a.id in ids
        assert b.id not in ids

    def test_list_combined_filters(self, db_session):
        """Multiple filters AND together."""
        project = _make_project(db_session)
        match = service.create(
            db_session,
            _payload(project.id, code="M1", category="Katalógy", status="in_development"),
        )
        # Different category
        service.create(
            db_session,
            _payload(project.id, code="M2", category="Sklad", status="in_development"),
        )
        # Different status
        service.create(
            db_session,
            _payload(project.id, code="M3", category="Katalógy", status="planned"),
        )

        rows = service.list_project_modules(
            db_session,
            project_id=project.id,
            category="Katalógy",
            status="in_development",
        )
        assert len(rows) == 1
        assert rows[0].id == match.id

    def test_list_ordered_by_created_at_desc(self, db_session):
        """Results are ordered newest-first.

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
        m1 = service.create(db_session, _payload(project.id, code="M01"))
        m2 = service.create(db_session, _payload(project.id, code="M02"))
        m3 = service.create(db_session, _payload(project.id, code="M03"))
        m1.created_at = base_time
        m2.created_at = base_time + timedelta(minutes=1)
        m3.created_at = base_time + timedelta(minutes=2)
        db_session.flush()

        rows = service.list_project_modules(db_session, project_id=project.id)
        ids_in_order = [r.id for r in rows]
        assert ids_in_order.index(m3.id) < ids_in_order.index(m2.id) < ids_in_order.index(m1.id)

    def test_list_pagination(self, db_session):
        """``limit`` / ``offset`` restrict the result window."""
        project = _make_project(db_session)
        for i in range(5):
            service.create(db_session, _payload(project.id, code=f"M{i:02d}"))

        first_page = service.list_project_modules(
            db_session,
            project_id=project.id,
            limit=2,
            offset=0,
        )
        second_page = service.list_project_modules(
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
