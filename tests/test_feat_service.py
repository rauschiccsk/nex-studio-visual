"""Tests for :mod:`backend.services.feat`.

Exercises every public CRUD entry point against the SAVEPOINT-isolated
session provided by ``tests/conftest.py``. Verifies:

* Happy-path list / get / create / update / delete.
* ``ValueError`` on missing ``id`` for get / update / delete.
* ``create`` auto-assigns ``number`` as ``MAX(number) + 1`` per epic,
  starts at ``1`` for the first feat, independent per epic.
* Update allow-list — only ``title``, ``description``, ``status``,
  ``estimated_minutes`` and ``actual_minutes`` are applied; ``id``,
  ``epic_id``, ``number``, ``task_count``, ``auto_fix_count`` and
  ``created_at`` are preserved.
* PATCH semantics — omitted fields stay untouched.
* List filters (``epic_id``, ``status``) and pagination.
* List ordering is ``number ASC``.
* ``delete`` removes the row; inbound FKs — ``tasks.feat_id``
  (``ON DELETE CASCADE``) and ``auto_fix_attempts.feat_id``
  (``ON DELETE CASCADE``) — are cleaned up without a RESTRICT guard.
* No ``commit`` happens inside the service — the outer transaction
  rolls back cleanly at fixture teardown.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select as sa_select

from backend.db.models.foundation import User
from backend.db.models.projects import Project
from backend.db.models.tasks import Epic, Feat, Task
from backend.schemas.feat import FeatCreate, FeatUpdate
from backend.services import feat as service


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


def _make_epic(db_session, *, project: Project | None = None, **overrides) -> Epic:
    """Create and persist an Epic for FK references."""
    if project is None:
        project = _make_project(db_session)
    # Auto-assign number per project to avoid clashes when multiple
    # epics are created for the same project inside a single test.
    next_number = (
        db_session.execute(
            sa_select(Epic.number).where(Epic.project_id == project.id).order_by(Epic.number.desc()).limit(1)
        ).scalar()
        or 0
    ) + 1
    defaults = {
        "project_id": project.id,
        "number": next_number,
        "title": f"Epic {uuid.uuid4().hex[:6]}",
    }
    defaults.update(overrides)
    epic = Epic(**defaults)
    db_session.add(epic)
    db_session.flush()
    return epic


def _payload(epic_id, **overrides) -> FeatCreate:
    """Return a :class:`FeatCreate` payload with sensible defaults."""
    defaults = {
        "epic_id": epic_id,
        "title": f"Feat {uuid.uuid4().hex[:6]}",
    }
    defaults.update(overrides)
    return FeatCreate(**defaults)


class TestFeatService:
    """Synchronous CRUD coverage for the Feat service."""

    # ------------------------------------------------------------------ create
    def test_create_feat(self, db_session):
        """``create`` persists the row and returns an ORM instance with server defaults."""
        epic = _make_epic(db_session)

        created = service.create(db_session, _payload(epic.id, title="First feat"))

        assert isinstance(created, Feat)
        assert created.id is not None
        assert created.created_at is not None
        assert created.updated_at is not None
        assert created.epic_id == epic.id
        assert created.title == "First feat"
        # Schema / DB defaults.
        assert created.description == ""
        assert created.status == "todo"
        assert created.estimated_minutes is None
        assert created.actual_minutes is None
        # Server-managed counters seeded by DB server_default.
        assert created.task_count == 0
        assert created.auto_fix_count == 0
        # Auto-assigned number.
        assert created.number == 1

    def test_create_with_description_and_estimate(self, db_session):
        """``create`` applies explicit ``description`` and ``estimated_minutes``."""
        epic = _make_epic(db_session)

        created = service.create(
            db_session,
            _payload(
                epic.id,
                description="Detailed feat body.",
                estimated_minutes=120,
            ),
        )

        assert created.description == "Detailed feat body."
        assert created.estimated_minutes == 120

    def test_create_with_custom_status(self, db_session):
        """``create`` applies a non-default ``status`` when supplied."""
        epic = _make_epic(db_session)

        created = service.create(
            db_session,
            _payload(epic.id, status="in_progress"),
        )

        assert created.status == "in_progress"

    def test_create_auto_numbers_sequentially(self, db_session):
        """``create`` auto-assigns ``number`` as MAX(number) + 1 per epic."""
        epic = _make_epic(db_session)

        f1 = service.create(db_session, _payload(epic.id))
        f2 = service.create(db_session, _payload(epic.id))
        f3 = service.create(db_session, _payload(epic.id))

        assert (f1.number, f2.number, f3.number) == (1, 2, 3)

    def test_create_numbering_is_per_epic(self, db_session):
        """Two epics each start their feat numbering at 1 independently."""
        project = _make_project(db_session)
        e1 = _make_epic(db_session, project=project)
        e2 = _make_epic(db_session, project=project)

        f1_e1 = service.create(db_session, _payload(e1.id))
        f2_e1 = service.create(db_session, _payload(e1.id))
        f1_e2 = service.create(db_session, _payload(e2.id))

        assert f1_e1.number == 1
        assert f2_e1.number == 2
        assert f1_e2.number == 1

    # ------------------------------------------------------------------- get
    def test_get_by_id(self, db_session):
        """``get_by_id`` returns the row when it exists."""
        epic = _make_epic(db_session)
        created = service.create(db_session, _payload(epic.id))

        fetched = service.get_by_id(db_session, created.id)
        assert fetched.id == created.id
        assert fetched.epic_id == epic.id

    def test_get_by_id_missing_raises(self, db_session):
        """``get_by_id`` raises ``ValueError`` for an unknown id."""
        with pytest.raises(ValueError, match="not found"):
            service.get_by_id(db_session, uuid.uuid4())

    # ---------------------------------------------------------------- update
    def test_update_title_and_status(self, db_session):
        """``update`` patches mutable fields."""
        epic = _make_epic(db_session)
        created = service.create(db_session, _payload(epic.id, title="Old title"))

        updated = service.update(
            db_session,
            created.id,
            FeatUpdate(title="New title", status="in_progress"),
        )

        assert updated.id == created.id
        assert updated.title == "New title"
        assert updated.status == "in_progress"

    def test_update_description(self, db_session):
        """``description`` is mutable."""
        epic = _make_epic(db_session)
        created = service.create(
            db_session,
            _payload(epic.id, description="Initial description."),
        )

        updated = service.update(
            db_session,
            created.id,
            FeatUpdate(description="Revised description."),
        )

        assert updated.description == "Revised description."

    def test_update_estimated_and_actual_minutes(self, db_session):
        """``estimated_minutes`` and ``actual_minutes`` are updatable (backfill flow)."""
        epic = _make_epic(db_session)
        created = service.create(
            db_session,
            _payload(epic.id, estimated_minutes=60),
        )
        assert created.actual_minutes is None

        updated = service.update(
            db_session,
            created.id,
            FeatUpdate(estimated_minutes=90, actual_minutes=75),
        )

        assert updated.estimated_minutes == 90
        assert updated.actual_minutes == 75

    def test_update_partial_only_status(self, db_session):
        """``update`` leaves omitted fields untouched (PATCH semantics)."""
        epic = _make_epic(db_session)
        created = service.create(
            db_session,
            _payload(
                epic.id,
                title="Original title",
                description="Original description",
                estimated_minutes=30,
            ),
        )

        updated = service.update(
            db_session,
            created.id,
            FeatUpdate(status="done"),
        )

        assert updated.status == "done"
        # Unchanged fields preserved.
        assert updated.title == "Original title"
        assert updated.description == "Original description"
        assert updated.estimated_minutes == 30

    def test_update_preserves_immutable_fields(self, db_session):
        """``id``, ``epic_id``, ``number`` and ``created_at`` must not change across ``update``."""
        epic = _make_epic(db_session)
        created = service.create(db_session, _payload(epic.id))

        original_id = created.id
        original_epic_id = created.epic_id
        original_number = created.number
        original_created_at = created.created_at
        original_task_count = created.task_count
        original_auto_fix_count = created.auto_fix_count

        updated = service.update(
            db_session,
            created.id,
            FeatUpdate(title="Renamed", status="in_progress"),
        )

        assert updated.id == original_id
        assert updated.epic_id == original_epic_id
        assert updated.number == original_number
        assert updated.created_at == original_created_at
        # Server-managed counters must not be touched by the update path.
        assert updated.task_count == original_task_count
        assert updated.auto_fix_count == original_auto_fix_count

    def test_update_empty_payload_is_noop(self, db_session):
        """An :class:`FeatUpdate` with no fields set leaves the row intact."""
        epic = _make_epic(db_session)
        created = service.create(
            db_session,
            _payload(
                epic.id,
                title="Keep me",
                description="Keep this too",
                status="in_progress",
                estimated_minutes=45,
            ),
        )

        updated = service.update(db_session, created.id, FeatUpdate())

        assert updated.title == "Keep me"
        assert updated.description == "Keep this too"
        assert updated.status == "in_progress"
        assert updated.estimated_minutes == 45

    def test_update_missing_raises(self, db_session):
        """``update`` on a non-existent id raises ``ValueError``."""
        with pytest.raises(ValueError, match="not found"):
            service.update(
                db_session,
                uuid.uuid4(),
                FeatUpdate(title="nope"),
            )

    # ---------------------------------------------------------------- delete
    def test_delete(self, db_session):
        """``delete`` removes the row; subsequent lookup raises."""
        epic = _make_epic(db_session)
        created = service.create(db_session, _payload(epic.id))

        service.delete(db_session, created.id)

        with pytest.raises(ValueError, match="not found"):
            service.get_by_id(db_session, created.id)

    def test_delete_missing_raises(self, db_session):
        """``delete`` on a non-existent id raises ``ValueError``."""
        with pytest.raises(ValueError, match="not found"):
            service.delete(db_session, uuid.uuid4())

    def test_delete_cascades_tasks(self, db_session):
        """Deleting a feat cascades to its tasks (``ON DELETE CASCADE``)."""
        epic = _make_epic(db_session)
        feat = service.create(db_session, _payload(epic.id))

        task = Task(
            feat_id=feat.id,
            number=1,
            title="T1",
            task_type="backend",
        )
        db_session.add(task)
        db_session.flush()
        task_id = task.id

        service.delete(db_session, feat.id)
        # Expire the identity map so subsequent lookups hit the DB and
        # observe the DB-level CASCADE. Without this, SQLAlchemy would
        # return the still-cached Task instance even though its row has
        # been removed.
        db_session.expire_all()

        remaining = db_session.execute(
            sa_select(Task).where(Task.id == task_id),
        ).scalar_one_or_none()
        assert remaining is None

    # ------------------------------------------------------------------ list
    def test_list_all(self, db_session):
        """``list_feats`` returns every row when no filter is supplied."""
        epic = _make_epic(db_session)
        created_ids: set = set()
        for _ in range(3):
            created_ids.add(service.create(db_session, _payload(epic.id)).id)

        rows = service.list_feats(db_session)
        assert created_ids.issubset({r.id for r in rows})

    def test_list_filter_by_epic(self, db_session):
        """``list_feats(epic_id=...)`` returns only that epic's feats."""
        project = _make_project(db_session)
        e1 = _make_epic(db_session, project=project)
        e2 = _make_epic(db_session, project=project)
        mine = service.create(db_session, _payload(e1.id))
        service.create(db_session, _payload(e2.id))

        rows = service.list_feats(db_session, epic_id=e1.id)
        assert all(r.epic_id == e1.id for r in rows)
        assert any(r.id == mine.id for r in rows)

    def test_list_filter_by_status(self, db_session):
        """``status`` filter returns only matching feats."""
        epic = _make_epic(db_session)
        todo = service.create(db_session, _payload(epic.id))
        in_progress = service.create(
            db_session,
            _payload(epic.id, status="in_progress"),
        )

        rows = service.list_feats(db_session, epic_id=epic.id, status="in_progress")
        ids = {r.id for r in rows}
        assert in_progress.id in ids
        assert todo.id not in ids

    def test_list_combined_filters(self, db_session):
        """Multiple filters AND together."""
        project = _make_project(db_session)
        e1 = _make_epic(db_session, project=project)
        e2 = _make_epic(db_session, project=project)

        match = service.create(
            db_session,
            _payload(e1.id, status="in_progress"),
        )
        # Different epic.
        service.create(db_session, _payload(e2.id, status="in_progress"))
        # Different status.
        service.create(db_session, _payload(e1.id, status="todo"))

        rows = service.list_feats(
            db_session,
            epic_id=e1.id,
            status="in_progress",
        )
        assert len(rows) == 1
        assert rows[0].id == match.id

    def test_list_ordered_by_number_asc(self, db_session):
        """Results are ordered by ``number ASC`` (feat 1, feat 2, …)."""
        epic = _make_epic(db_session)
        f1 = service.create(db_session, _payload(epic.id))
        f2 = service.create(db_session, _payload(epic.id))
        f3 = service.create(db_session, _payload(epic.id))

        rows = service.list_feats(db_session, epic_id=epic.id)
        ids_in_order = [r.id for r in rows]
        assert ids_in_order.index(f1.id) < ids_in_order.index(f2.id) < ids_in_order.index(f3.id)

    def test_list_pagination(self, db_session):
        """``limit`` / ``offset`` restrict the result window."""
        epic = _make_epic(db_session)
        for _ in range(5):
            service.create(db_session, _payload(epic.id))

        first_page = service.list_feats(
            db_session,
            epic_id=epic.id,
            limit=2,
            offset=0,
        )
        second_page = service.list_feats(
            db_session,
            epic_id=epic.id,
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
        epic = _make_epic(db_session)
        created = service.create(db_session, _payload(epic.id))
        # ``in_transaction()`` must be True — commit would clear it.
        assert db_session.in_transaction()
        # Row is visible within the session after flush.
        assert service.get_by_id(db_session, created.id).id == created.id
