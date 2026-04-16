"""Tests for :mod:`backend.services.module_dependency`.

Exercises every public CRUD entry point against the SAVEPOINT-isolated
session provided by ``tests/conftest.py``. Verifies:

* Happy-path list / get / create / update / delete.
* ``ValueError`` on duplicate ``(module_id, depends_on_module_id)``
  natural key.
* ``ValueError`` on self-loop (``module_id == depends_on_module_id``).
* ``ValueError`` on missing ``id`` for get / update / delete.
* ``update`` is a no-op — :class:`ModuleDependency` has no mutable
  columns — but still raises :class:`ValueError` on an unknown id and
  returns the unmodified row otherwise.
* Immutable fields (``id``, ``module_id``, ``depends_on_module_id``,
  ``created_at``) stay unchanged across :func:`update`.
* List filters (``module_id``, ``depends_on_module_id``) and
  pagination.
* ``delete`` removes the row — ``module_dependencies`` has no inbound
  FKs so no RESTRICT guard is needed.
* No ``commit`` happens inside the service — the outer transaction
  rolls back cleanly at fixture teardown.
"""

from __future__ import annotations

import uuid

import pytest

from backend.db.models.foundation import User
from backend.db.models.projects import ModuleDependency, Project, ProjectModule
from backend.schemas.module_dependency import (
    ModuleDependencyCreate,
    ModuleDependencyUpdate,
)
from backend.services import module_dependency as service


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
    """Create and persist a multimodule Project for FK references."""
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
    """Create and persist a ProjectModule for FK references."""
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


def _payload(module_id, depends_on_module_id) -> ModuleDependencyCreate:
    """Return a :class:`ModuleDependencyCreate` payload."""
    return ModuleDependencyCreate(
        module_id=module_id,
        depends_on_module_id=depends_on_module_id,
    )


class TestModuleDependencyService:
    """Synchronous CRUD coverage for the ModuleDependency service."""

    # ------------------------------------------------------------------ create
    def test_create_dependency(self, db_session):
        """``create`` persists the row and returns an ORM instance with server defaults."""
        project = _make_project(db_session)
        a = _make_module(db_session, project=project)
        b = _make_module(db_session, project=project)

        created = service.create(db_session, _payload(a.id, b.id))

        assert isinstance(created, ModuleDependency)
        assert created.id is not None
        assert created.created_at is not None
        assert created.updated_at is not None
        assert created.module_id == a.id
        assert created.depends_on_module_id == b.id

    def test_create_duplicate_natural_key_raises(self, db_session):
        """``UNIQUE(module_id, depends_on_module_id)`` — duplicate edge rejected pre-flush."""
        project = _make_project(db_session)
        a = _make_module(db_session, project=project)
        b = _make_module(db_session, project=project)
        service.create(db_session, _payload(a.id, b.id))

        with pytest.raises(ValueError, match="already exists"):
            service.create(db_session, _payload(a.id, b.id))

    def test_create_self_loop_raises(self, db_session):
        """A module cannot depend on itself — service rejects the self-loop."""
        project = _make_project(db_session)
        a = _make_module(db_session, project=project)

        with pytest.raises(ValueError, match="self-loop"):
            service.create(db_session, _payload(a.id, a.id))

    def test_create_reverse_edge_allowed_by_service_but_blocked_upstream(self, db_session):
        """Both (A→B) and (B→A) edges pass the service's local checks.

        The service enforces only self-loop and uniqueness. A two-node
        cycle (A depends on B AND B depends on A) is a semantic cycle
        that must be caught by the caller (Architect /
        ``ModuleService``) per DESIGN.md §1.2 "Application-level cycle
        detection". This test documents that contract — the service
        does NOT perform multi-hop cycle detection.
        """
        project = _make_project(db_session)
        a = _make_module(db_session, project=project)
        b = _make_module(db_session, project=project)

        ab = service.create(db_session, _payload(a.id, b.id))
        ba = service.create(db_session, _payload(b.id, a.id))

        assert ab.id != ba.id
        assert ab.module_id == a.id and ab.depends_on_module_id == b.id
        assert ba.module_id == b.id and ba.depends_on_module_id == a.id

    def test_create_different_pairs_allowed(self, db_session):
        """Distinct ``(module_id, depends_on_module_id)`` pairs coexist freely."""
        project = _make_project(db_session)
        a = _make_module(db_session, project=project)
        b = _make_module(db_session, project=project)
        c = _make_module(db_session, project=project)

        ab = service.create(db_session, _payload(a.id, b.id))
        ac = service.create(db_session, _payload(a.id, c.id))

        assert ab.id != ac.id
        assert ab.module_id == ac.module_id == a.id
        assert ab.depends_on_module_id != ac.depends_on_module_id

    # ------------------------------------------------------------------- get
    def test_get_by_id(self, db_session):
        """``get_by_id`` returns the row when it exists."""
        project = _make_project(db_session)
        a = _make_module(db_session, project=project)
        b = _make_module(db_session, project=project)
        created = service.create(db_session, _payload(a.id, b.id))

        fetched = service.get_by_id(db_session, created.id)
        assert fetched.id == created.id
        assert fetched.module_id == a.id
        assert fetched.depends_on_module_id == b.id

    def test_get_by_id_missing_raises(self, db_session):
        """``get_by_id`` raises ``ValueError`` for an unknown id."""
        with pytest.raises(ValueError, match="not found"):
            service.get_by_id(db_session, uuid.uuid4())

    # ---------------------------------------------------------------- update
    def test_update_is_noop(self, db_session):
        """``update`` is a no-op — ModuleDependency has no mutable fields.

        The natural key ``(module_id, depends_on_module_id)`` is
        immutable and the :class:`ModuleDependencyUpdate` schema
        exposes no fields; the service simply confirms the row exists
        and returns it unchanged.
        """
        project = _make_project(db_session)
        a = _make_module(db_session, project=project)
        b = _make_module(db_session, project=project)
        created = service.create(db_session, _payload(a.id, b.id))

        original_id = created.id
        original_module_id = created.module_id
        original_depends_on = created.depends_on_module_id
        original_created_at = created.created_at

        updated = service.update(
            db_session,
            created.id,
            ModuleDependencyUpdate(),
        )

        assert updated.id == original_id
        assert updated.module_id == original_module_id
        assert updated.depends_on_module_id == original_depends_on
        assert updated.created_at == original_created_at

    def test_update_missing_raises(self, db_session):
        """``update`` on a non-existent id raises ``ValueError``."""
        with pytest.raises(ValueError, match="not found"):
            service.update(
                db_session,
                uuid.uuid4(),
                ModuleDependencyUpdate(),
            )

    # ---------------------------------------------------------------- delete
    def test_delete(self, db_session):
        """``delete`` removes the row; subsequent lookup raises."""
        project = _make_project(db_session)
        a = _make_module(db_session, project=project)
        b = _make_module(db_session, project=project)
        created = service.create(db_session, _payload(a.id, b.id))

        service.delete(db_session, created.id)

        with pytest.raises(ValueError, match="not found"):
            service.get_by_id(db_session, created.id)

    def test_delete_missing_raises(self, db_session):
        """``delete`` on a non-existent id raises ``ValueError``."""
        with pytest.raises(ValueError, match="not found"):
            service.delete(db_session, uuid.uuid4())

    def test_delete_one_edge_leaves_others_intact(self, db_session):
        """Deleting one edge does not affect sibling edges in the graph."""
        project = _make_project(db_session)
        a = _make_module(db_session, project=project)
        b = _make_module(db_session, project=project)
        c = _make_module(db_session, project=project)
        ab = service.create(db_session, _payload(a.id, b.id))
        ac = service.create(db_session, _payload(a.id, c.id))

        service.delete(db_session, ab.id)

        assert service.get_by_id(db_session, ac.id).id == ac.id

    # ------------------------------------------------------------------ list
    def test_list_all(self, db_session):
        """``list_module_dependencies`` returns every row when no filter is supplied."""
        project = _make_project(db_session)
        a = _make_module(db_session, project=project)
        created_ids: set = set()
        for _ in range(3):
            other = _make_module(db_session, project=project)
            created_ids.add(service.create(db_session, _payload(a.id, other.id)).id)

        rows = service.list_module_dependencies(db_session)
        assert created_ids.issubset({r.id for r in rows})

    def test_list_filter_by_module(self, db_session):
        """``list_module_dependencies(module_id=...)`` returns only that module's outgoing edges."""
        project = _make_project(db_session)
        a = _make_module(db_session, project=project)
        b = _make_module(db_session, project=project)
        c = _make_module(db_session, project=project)
        d = _make_module(db_session, project=project)
        outgoing_a = service.create(db_session, _payload(a.id, b.id))
        service.create(db_session, _payload(c.id, d.id))

        rows = service.list_module_dependencies(db_session, module_id=a.id)
        assert all(r.module_id == a.id for r in rows)
        assert any(r.id == outgoing_a.id for r in rows)

    def test_list_filter_by_depends_on_module(self, db_session):
        """``list_module_dependencies(depends_on_module_id=...)`` returns only incoming edges.

        This is the "which modules depend on this one" query behind
        the dependency-graph visualisation (DESIGN.md §3.2
        ``ModuleGraph``).
        """
        project = _make_project(db_session)
        a = _make_module(db_session, project=project)
        b = _make_module(db_session, project=project)
        c = _make_module(db_session, project=project)
        incoming_a = service.create(db_session, _payload(b.id, a.id))
        incoming_b = service.create(db_session, _payload(c.id, a.id))
        # Unrelated edge — should be excluded.
        service.create(db_session, _payload(b.id, c.id))

        rows = service.list_module_dependencies(db_session, depends_on_module_id=a.id)
        assert all(r.depends_on_module_id == a.id for r in rows)
        result_ids = {r.id for r in rows}
        assert incoming_a.id in result_ids
        assert incoming_b.id in result_ids

    def test_list_filter_by_both_endpoints(self, db_session):
        """Combined filters converge on the natural key — at most one row."""
        project = _make_project(db_session)
        a = _make_module(db_session, project=project)
        b = _make_module(db_session, project=project)
        created = service.create(db_session, _payload(a.id, b.id))

        rows = service.list_module_dependencies(
            db_session,
            module_id=a.id,
            depends_on_module_id=b.id,
        )
        assert len(rows) == 1
        assert rows[0].id == created.id

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
        a = _make_module(db_session, project=project)
        b = _make_module(db_session, project=project)
        c = _make_module(db_session, project=project)
        d = _make_module(db_session, project=project)
        base_time = datetime(2026, 4, 15, 10, 0, tzinfo=timezone.utc)
        e1 = service.create(db_session, _payload(a.id, b.id))
        e2 = service.create(db_session, _payload(a.id, c.id))
        e3 = service.create(db_session, _payload(a.id, d.id))
        e1.created_at = base_time
        e2.created_at = base_time + timedelta(minutes=1)
        e3.created_at = base_time + timedelta(minutes=2)
        db_session.flush()

        rows = service.list_module_dependencies(db_session, module_id=a.id)
        ids_in_order = [r.id for r in rows]
        assert ids_in_order.index(e3.id) < ids_in_order.index(e2.id) < ids_in_order.index(e1.id)

    def test_list_pagination(self, db_session):
        """``limit`` / ``offset`` restrict the result window."""
        project = _make_project(db_session)
        a = _make_module(db_session, project=project)
        for _ in range(5):
            other = _make_module(db_session, project=project)
            service.create(db_session, _payload(a.id, other.id))

        first_page = service.list_module_dependencies(
            db_session,
            module_id=a.id,
            limit=2,
            offset=0,
        )
        second_page = service.list_module_dependencies(
            db_session,
            module_id=a.id,
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
        a = _make_module(db_session, project=project)
        b = _make_module(db_session, project=project)
        created = service.create(db_session, _payload(a.id, b.id))
        # ``in_transaction()`` must be True — commit would clear it.
        assert db_session.in_transaction()
        # Row is visible within the session after flush.
        assert service.get_by_id(db_session, created.id).id == created.id
