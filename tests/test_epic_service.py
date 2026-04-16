"""Tests for :mod:`backend.services.epic`.

Exercises every public CRUD entry point against the SAVEPOINT-isolated
session provided by ``tests/conftest.py``. Verifies:

* Happy-path list / get / create / update / delete.
* ``ValueError`` on missing ``id`` for get / update / delete.
* ``create`` auto-assigns ``number`` as ``MAX(number) + 1`` per
  project, starts at ``1`` for the first epic, independent per project.
* Update allow-list — only ``module_id``, ``title`` and ``status`` are
  applied; ``project_id``, ``number``, ``id`` and ``created_at`` are
  preserved.
* PATCH semantics — omitted fields stay untouched.
* ``module_id`` is nullable at create (project-level epic) and
  re-scopable at update.
* List filters (``project_id``, ``module_id``, ``status``) and
  pagination.
* List ordering is ``number ASC``.
* ``delete`` removes the row; the inbound FK
  (``feats.epic_id``) is ``ON DELETE CASCADE`` so dependent feats are
  cleaned up without a RESTRICT guard.
* No ``commit`` happens inside the service — the outer transaction
  rolls back cleanly at fixture teardown.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select as sa_select

from backend.db.models.foundation import User
from backend.db.models.projects import Project, ProjectModule
from backend.db.models.tasks import Epic, Feat
from backend.schemas.epic import EpicCreate, EpicUpdate
from backend.services import epic as service


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


def _payload(project_id, **overrides) -> EpicCreate:
    """Return an :class:`EpicCreate` payload with sensible defaults."""
    defaults = {
        "project_id": project_id,
        "title": f"Epic {uuid.uuid4().hex[:6]}",
    }
    defaults.update(overrides)
    return EpicCreate(**defaults)


class TestEpicService:
    """Synchronous CRUD coverage for the Epic service."""

    # ------------------------------------------------------------------ create
    def test_create_epic(self, db_session):
        """``create`` persists the row and returns an ORM instance with server defaults."""
        project = _make_project(db_session)

        created = service.create(db_session, _payload(project.id, title="First epic"))

        assert isinstance(created, Epic)
        assert created.id is not None
        assert created.created_at is not None
        assert created.updated_at is not None
        assert created.project_id == project.id
        assert created.module_id is None
        assert created.title == "First epic"
        # Schema-level default.
        assert created.status == "planned"
        # Auto-assigned number.
        assert created.number == 1

    def test_create_with_module(self, db_session):
        """``create`` accepts a ``module_id`` to scope the epic to a module."""
        project = _make_project(db_session)
        module = _make_module(db_session, project=project)

        created = service.create(
            db_session,
            _payload(project.id, module_id=module.id),
        )

        assert created.module_id == module.id

    def test_create_with_custom_status(self, db_session):
        """``create`` applies a non-default ``status`` when supplied."""
        project = _make_project(db_session)

        created = service.create(
            db_session,
            _payload(project.id, status="in_progress"),
        )

        assert created.status == "in_progress"

    def test_create_auto_numbers_sequentially(self, db_session):
        """``create`` auto-assigns ``number`` as MAX(number) + 1 per project."""
        project = _make_project(db_session)

        e1 = service.create(db_session, _payload(project.id))
        e2 = service.create(db_session, _payload(project.id))
        e3 = service.create(db_session, _payload(project.id))

        assert (e1.number, e2.number, e3.number) == (1, 2, 3)

    def test_create_numbering_is_per_project(self, db_session):
        """Two projects each start their epic numbering at 1 independently."""
        p1 = _make_project(db_session)
        p2 = _make_project(db_session)

        e1_p1 = service.create(db_session, _payload(p1.id))
        e2_p1 = service.create(db_session, _payload(p1.id))
        e1_p2 = service.create(db_session, _payload(p2.id))

        assert e1_p1.number == 1
        assert e2_p1.number == 2
        assert e1_p2.number == 1

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
    def test_update_title_and_status(self, db_session):
        """``update`` patches mutable fields."""
        project = _make_project(db_session)
        created = service.create(db_session, _payload(project.id, title="Old title"))

        updated = service.update(
            db_session,
            created.id,
            EpicUpdate(title="New title", status="in_progress"),
        )

        assert updated.id == created.id
        assert updated.title == "New title"
        assert updated.status == "in_progress"

    def test_update_module_id(self, db_session):
        """``module_id`` is mutable — an epic can be re-scoped to a module."""
        project = _make_project(db_session)
        module = _make_module(db_session, project=project)
        created = service.create(db_session, _payload(project.id))
        assert created.module_id is None

        updated = service.update(
            db_session,
            created.id,
            EpicUpdate(module_id=module.id),
        )

        assert updated.module_id == module.id

    def test_update_partial_only_status(self, db_session):
        """``update`` leaves omitted fields untouched (PATCH semantics)."""
        project = _make_project(db_session)
        module = _make_module(db_session, project=project)
        created = service.create(
            db_session,
            _payload(project.id, module_id=module.id, title="Original title"),
        )

        updated = service.update(
            db_session,
            created.id,
            EpicUpdate(status="done"),
        )

        assert updated.status == "done"
        # Unchanged fields preserved.
        assert updated.title == "Original title"
        assert updated.module_id == module.id

    def test_update_preserves_immutable_fields(self, db_session):
        """``id``, ``project_id``, ``number`` and ``created_at`` must not change across ``update``."""
        project = _make_project(db_session)
        created = service.create(db_session, _payload(project.id))

        original_id = created.id
        original_project_id = created.project_id
        original_number = created.number
        original_created_at = created.created_at

        updated = service.update(
            db_session,
            created.id,
            EpicUpdate(title="Renamed", status="in_progress"),
        )

        assert updated.id == original_id
        assert updated.project_id == original_project_id
        assert updated.number == original_number
        assert updated.created_at == original_created_at

    def test_update_empty_payload_is_noop(self, db_session):
        """An :class:`EpicUpdate` with no fields set leaves the row intact."""
        project = _make_project(db_session)
        created = service.create(
            db_session,
            _payload(project.id, title="Keep me", status="in_progress"),
        )

        updated = service.update(db_session, created.id, EpicUpdate())

        assert updated.title == "Keep me"
        assert updated.status == "in_progress"
        assert updated.module_id is None

    def test_update_missing_raises(self, db_session):
        """``update`` on a non-existent id raises ``ValueError``."""
        with pytest.raises(ValueError, match="not found"):
            service.update(
                db_session,
                uuid.uuid4(),
                EpicUpdate(title="nope"),
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

    def test_delete_cascades_feats(self, db_session):
        """Deleting an epic cascades to its feats (``ON DELETE CASCADE``)."""
        project = _make_project(db_session)
        epic = service.create(db_session, _payload(project.id))

        feat = Feat(epic_id=epic.id, number=1, title="F1")
        db_session.add(feat)
        db_session.flush()
        feat_id = feat.id

        service.delete(db_session, epic.id)
        # Expire the identity map so subsequent lookups hit the DB and
        # observe the DB-level CASCADE. Without this, SQLAlchemy would
        # return the still-cached Feat instance even though its row has
        # been removed.
        db_session.expire_all()

        remaining = db_session.execute(
            sa_select(Feat).where(Feat.id == feat_id),
        ).scalar_one_or_none()
        assert remaining is None

    # ------------------------------------------------------------------ list
    def test_list_all(self, db_session):
        """``list_epics`` returns every row when no filter is supplied."""
        project = _make_project(db_session)
        created_ids: set = set()
        for _ in range(3):
            created_ids.add(service.create(db_session, _payload(project.id)).id)

        rows = service.list_epics(db_session)
        assert created_ids.issubset({r.id for r in rows})

    def test_list_filter_by_project(self, db_session):
        """``list_epics(project_id=...)`` returns only that project's epics."""
        p1 = _make_project(db_session)
        p2 = _make_project(db_session)
        mine = service.create(db_session, _payload(p1.id))
        service.create(db_session, _payload(p2.id))

        rows = service.list_epics(db_session, project_id=p1.id)
        assert all(r.project_id == p1.id for r in rows)
        assert any(r.id == mine.id for r in rows)

    def test_list_filter_by_module(self, db_session):
        """``module_id`` filter returns only epics scoped to that module."""
        project = _make_project(db_session)
        module = _make_module(db_session, project=project)
        module_epic = service.create(db_session, _payload(project.id, module_id=module.id))
        project_level_epic = service.create(db_session, _payload(project.id))

        rows = service.list_epics(db_session, module_id=module.id)
        row_ids = {r.id for r in rows}
        assert module_epic.id in row_ids
        # Project-level epic has module_id IS NULL — excluded by the filter.
        assert project_level_epic.id not in row_ids

    def test_list_filter_by_status(self, db_session):
        """``status`` filter returns only matching epics."""
        project = _make_project(db_session)
        planned = service.create(db_session, _payload(project.id))
        in_progress = service.create(
            db_session,
            _payload(project.id, status="in_progress"),
        )

        rows = service.list_epics(db_session, project_id=project.id, status="in_progress")
        ids = {r.id for r in rows}
        assert in_progress.id in ids
        assert planned.id not in ids

    def test_list_combined_filters(self, db_session):
        """Multiple filters AND together."""
        p1 = _make_project(db_session)
        p2 = _make_project(db_session)
        module = _make_module(db_session, project=p1)

        match = service.create(
            db_session,
            _payload(p1.id, module_id=module.id, status="in_progress"),
        )
        # Different project.
        service.create(db_session, _payload(p2.id, status="in_progress"))
        # Different status.
        service.create(db_session, _payload(p1.id, module_id=module.id, status="planned"))
        # Project-level (module_id IS NULL).
        service.create(db_session, _payload(p1.id, status="in_progress"))

        rows = service.list_epics(
            db_session,
            project_id=p1.id,
            module_id=module.id,
            status="in_progress",
        )
        assert len(rows) == 1
        assert rows[0].id == match.id

    def test_list_ordered_by_number_asc(self, db_session):
        """Results are ordered by ``number ASC`` (epic 1, epic 2, …)."""
        project = _make_project(db_session)
        e1 = service.create(db_session, _payload(project.id))
        e2 = service.create(db_session, _payload(project.id))
        e3 = service.create(db_session, _payload(project.id))

        rows = service.list_epics(db_session, project_id=project.id)
        ids_in_order = [r.id for r in rows]
        assert ids_in_order.index(e1.id) < ids_in_order.index(e2.id) < ids_in_order.index(e3.id)

    def test_list_pagination(self, db_session):
        """``limit`` / ``offset`` restrict the result window."""
        project = _make_project(db_session)
        for _ in range(5):
            service.create(db_session, _payload(project.id))

        first_page = service.list_epics(
            db_session,
            project_id=project.id,
            limit=2,
            offset=0,
        )
        second_page = service.list_epics(
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
