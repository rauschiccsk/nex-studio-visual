"""Tests for :mod:`backend.services.task`.

Exercises every public CRUD entry point against the SAVEPOINT-isolated
session provided by ``tests/conftest.py``. Verifies:

* Happy-path list / get / create / update / delete.
* ``ValueError`` on missing ``id`` for get / update / delete.
* ``create`` auto-assigns ``number`` as ``MAX(number) + 1`` per feat,
  starts at ``1`` for the first task, independent per feat.
* Update allow-list — only ``title``, ``description``, ``task_type``,
  ``status``, ``estimated_minutes``, ``actual_minutes`` and
  ``checklist_type`` are applied; ``id``, ``feat_id``, ``number`` and
  ``created_at`` are preserved.
* PATCH semantics — omitted fields stay untouched.
* List filters (``feat_id``, ``status``, ``task_type``) and pagination.
* List ordering is ``number ASC``.
* ``delete`` removes the row; inbound FKs — ``delegations.task_id``
  (``ON DELETE SET NULL``) and ``execution_logs.task_id``
  (``ON DELETE SET NULL``) — are handled at the DB level so no
  RESTRICT guard is required.
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
from backend.schemas.task import TaskCreate, TaskUpdate
from backend.services import task as service


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


def _make_feat(db_session, *, epic: Epic | None = None, **overrides) -> Feat:
    """Create and persist a Feat for FK references."""
    if epic is None:
        epic = _make_epic(db_session)
    next_number = (
        db_session.execute(
            sa_select(Feat.number).where(Feat.epic_id == epic.id).order_by(Feat.number.desc()).limit(1)
        ).scalar()
        or 0
    ) + 1
    defaults = {
        "epic_id": epic.id,
        "number": next_number,
        "title": f"Feat {uuid.uuid4().hex[:6]}",
    }
    defaults.update(overrides)
    feat = Feat(**defaults)
    db_session.add(feat)
    db_session.flush()
    return feat


def _payload(feat_id, **overrides) -> TaskCreate:
    """Return a :class:`TaskCreate` payload with sensible defaults."""
    defaults = {
        "feat_id": feat_id,
        "title": f"Task {uuid.uuid4().hex[:6]}",
        "task_type": "backend",
    }
    defaults.update(overrides)
    return TaskCreate(**defaults)


class TestTaskService:
    """Synchronous CRUD coverage for the Task service."""

    # ------------------------------------------------------------------ create
    def test_create_task(self, db_session):
        """``create`` persists the row and returns an ORM instance with server defaults."""
        feat = _make_feat(db_session)

        created = service.create(
            db_session,
            _payload(feat.id, title="First task"),
        )

        assert isinstance(created, Task)
        assert created.id is not None
        assert created.created_at is not None
        assert created.updated_at is not None
        assert created.feat_id == feat.id
        assert created.title == "First task"
        assert created.task_type == "backend"
        # Schema / DB defaults.
        assert created.description == ""
        assert created.status == "todo"
        assert created.estimated_minutes is None
        assert created.actual_minutes is None
        assert created.checklist_type is None
        # Auto-assigned number.
        assert created.number == 1

    def test_create_with_description_and_estimate(self, db_session):
        """``create`` applies explicit ``description`` and ``estimated_minutes``."""
        feat = _make_feat(db_session)

        created = service.create(
            db_session,
            _payload(
                feat.id,
                description="Detailed task body.",
                estimated_minutes=45,
            ),
        )

        assert created.description == "Detailed task body."
        assert created.estimated_minutes == 45

    def test_create_with_custom_status(self, db_session):
        """``create`` applies a non-default ``status`` when supplied."""
        feat = _make_feat(db_session)

        created = service.create(
            db_session,
            _payload(feat.id, status="in_progress"),
        )

        assert created.status == "in_progress"

    def test_create_with_checklist_type(self, db_session):
        """``create`` applies explicit ``checklist_type``."""
        feat = _make_feat(db_session)

        created = service.create(
            db_session,
            _payload(feat.id, checklist_type="service"),
        )

        assert created.checklist_type == "service"

    def test_create_with_actual_minutes(self, db_session):
        """``create`` accepts ``actual_minutes`` for backfill flows."""
        feat = _make_feat(db_session)

        created = service.create(
            db_session,
            _payload(feat.id, actual_minutes=33),
        )

        assert created.actual_minutes == 33

    def test_create_with_all_task_types(self, db_session):
        """``create`` accepts every value permitted by the CHECK constraint."""
        feat = _make_feat(db_session)
        for task_type in ("backend", "frontend", "migration", "test", "docs"):
            created = service.create(
                db_session,
                _payload(feat.id, task_type=task_type),
            )
            assert created.task_type == task_type

    def test_create_auto_numbers_sequentially(self, db_session):
        """``create`` auto-assigns ``number`` as MAX(number) + 1 per feat."""
        feat = _make_feat(db_session)

        t1 = service.create(db_session, _payload(feat.id))
        t2 = service.create(db_session, _payload(feat.id))
        t3 = service.create(db_session, _payload(feat.id))

        assert (t1.number, t2.number, t3.number) == (1, 2, 3)

    def test_create_numbering_is_per_feat(self, db_session):
        """Two feats each start their task numbering at 1 independently."""
        epic = _make_epic(db_session)
        f1 = _make_feat(db_session, epic=epic)
        f2 = _make_feat(db_session, epic=epic)

        t1_f1 = service.create(db_session, _payload(f1.id))
        t2_f1 = service.create(db_session, _payload(f1.id))
        t1_f2 = service.create(db_session, _payload(f2.id))

        assert t1_f1.number == 1
        assert t2_f1.number == 2
        assert t1_f2.number == 1

    # ------------------------------------------------------------------- get
    def test_get_by_id(self, db_session):
        """``get_by_id`` returns the row when it exists."""
        feat = _make_feat(db_session)
        created = service.create(db_session, _payload(feat.id))

        fetched = service.get_by_id(db_session, created.id)
        assert fetched.id == created.id
        assert fetched.feat_id == feat.id

    def test_get_by_id_missing_raises(self, db_session):
        """``get_by_id`` raises ``ValueError`` for an unknown id."""
        with pytest.raises(ValueError, match="not found"):
            service.get_by_id(db_session, uuid.uuid4())

    # ---------------------------------------------------------------- update
    def test_update_title_and_status(self, db_session):
        """``update`` patches mutable fields."""
        feat = _make_feat(db_session)
        created = service.create(
            db_session,
            _payload(feat.id, title="Old title"),
        )

        updated = service.update(
            db_session,
            created.id,
            TaskUpdate(title="New title", status="in_progress"),
        )

        assert updated.id == created.id
        assert updated.title == "New title"
        assert updated.status == "in_progress"

    def test_update_description_and_checklist_type(self, db_session):
        """``description`` and ``checklist_type`` are mutable."""
        feat = _make_feat(db_session)
        created = service.create(
            db_session,
            _payload(feat.id, description="Initial description."),
        )

        updated = service.update(
            db_session,
            created.id,
            TaskUpdate(
                description="Revised description.",
                checklist_type="router",
            ),
        )

        assert updated.description == "Revised description."
        assert updated.checklist_type == "router"

    def test_update_task_type(self, db_session):
        """``task_type`` is mutable."""
        feat = _make_feat(db_session)
        created = service.create(
            db_session,
            _payload(feat.id, task_type="backend"),
        )

        updated = service.update(
            db_session,
            created.id,
            TaskUpdate(task_type="frontend"),
        )

        assert updated.task_type == "frontend"

    def test_update_estimated_and_actual_minutes(self, db_session):
        """``estimated_minutes`` and ``actual_minutes`` are updatable (backfill flow)."""
        feat = _make_feat(db_session)
        created = service.create(
            db_session,
            _payload(feat.id, estimated_minutes=30),
        )
        assert created.actual_minutes is None

        updated = service.update(
            db_session,
            created.id,
            TaskUpdate(estimated_minutes=60, actual_minutes=55),
        )

        assert updated.estimated_minutes == 60
        assert updated.actual_minutes == 55

    def test_update_partial_only_status(self, db_session):
        """``update`` leaves omitted fields untouched (PATCH semantics)."""
        feat = _make_feat(db_session)
        created = service.create(
            db_session,
            _payload(
                feat.id,
                title="Original title",
                description="Original description",
                estimated_minutes=30,
                checklist_type="model",
            ),
        )

        updated = service.update(
            db_session,
            created.id,
            TaskUpdate(status="done"),
        )

        assert updated.status == "done"
        # Unchanged fields preserved.
        assert updated.title == "Original title"
        assert updated.description == "Original description"
        assert updated.estimated_minutes == 30
        assert updated.checklist_type == "model"

    def test_update_preserves_immutable_fields(self, db_session):
        """``id``, ``feat_id``, ``number`` and ``created_at`` must not change across ``update``."""
        feat = _make_feat(db_session)
        created = service.create(db_session, _payload(feat.id))

        original_id = created.id
        original_feat_id = created.feat_id
        original_number = created.number
        original_created_at = created.created_at

        updated = service.update(
            db_session,
            created.id,
            TaskUpdate(title="Renamed", status="in_progress"),
        )

        assert updated.id == original_id
        assert updated.feat_id == original_feat_id
        assert updated.number == original_number
        assert updated.created_at == original_created_at

    def test_update_empty_payload_is_noop(self, db_session):
        """A :class:`TaskUpdate` with no fields set leaves the row intact."""
        feat = _make_feat(db_session)
        created = service.create(
            db_session,
            _payload(
                feat.id,
                title="Keep me",
                description="Keep this too",
                status="in_progress",
                estimated_minutes=45,
                checklist_type="schema",
            ),
        )

        updated = service.update(db_session, created.id, TaskUpdate())

        assert updated.title == "Keep me"
        assert updated.description == "Keep this too"
        assert updated.status == "in_progress"
        assert updated.estimated_minutes == 45
        assert updated.checklist_type == "schema"

    def test_update_missing_raises(self, db_session):
        """``update`` on a non-existent id raises ``ValueError``."""
        with pytest.raises(ValueError, match="not found"):
            service.update(
                db_session,
                uuid.uuid4(),
                TaskUpdate(title="nope"),
            )

    # ---------------------------------------------------------------- delete
    def test_delete(self, db_session):
        """``delete`` removes the row; subsequent lookup raises."""
        feat = _make_feat(db_session)
        created = service.create(db_session, _payload(feat.id))

        service.delete(db_session, created.id)

        with pytest.raises(ValueError, match="not found"):
            service.get_by_id(db_session, created.id)

    def test_delete_missing_raises(self, db_session):
        """``delete`` on a non-existent id raises ``ValueError``."""
        with pytest.raises(ValueError, match="not found"):
            service.delete(db_session, uuid.uuid4())

    # ------------------------------------------------------------------ list
    def test_list_all(self, db_session):
        """``list_tasks`` returns every row when no filter is supplied."""
        feat = _make_feat(db_session)
        created_ids: set = set()
        for _ in range(3):
            created_ids.add(service.create(db_session, _payload(feat.id)).id)

        rows = service.list_tasks(db_session)
        assert created_ids.issubset({r.id for r in rows})

    def test_list_filter_by_feat(self, db_session):
        """``list_tasks(feat_id=...)`` returns only that feat's tasks."""
        epic = _make_epic(db_session)
        f1 = _make_feat(db_session, epic=epic)
        f2 = _make_feat(db_session, epic=epic)
        mine = service.create(db_session, _payload(f1.id))
        service.create(db_session, _payload(f2.id))

        rows = service.list_tasks(db_session, feat_id=f1.id)
        assert all(r.feat_id == f1.id for r in rows)
        assert any(r.id == mine.id for r in rows)

    def test_list_filter_by_status(self, db_session):
        """``status`` filter returns only matching tasks."""
        feat = _make_feat(db_session)
        todo = service.create(db_session, _payload(feat.id))
        in_progress = service.create(
            db_session,
            _payload(feat.id, status="in_progress"),
        )

        rows = service.list_tasks(
            db_session,
            feat_id=feat.id,
            status="in_progress",
        )
        ids = {r.id for r in rows}
        assert in_progress.id in ids
        assert todo.id not in ids

    def test_list_filter_by_task_type(self, db_session):
        """``task_type`` filter returns only matching tasks."""
        feat = _make_feat(db_session)
        backend_task = service.create(
            db_session,
            _payload(feat.id, task_type="backend"),
        )
        frontend_task = service.create(
            db_session,
            _payload(feat.id, task_type="frontend"),
        )

        rows = service.list_tasks(
            db_session,
            feat_id=feat.id,
            task_type="frontend",
        )
        ids = {r.id for r in rows}
        assert frontend_task.id in ids
        assert backend_task.id not in ids

    def test_list_combined_filters(self, db_session):
        """Multiple filters AND together."""
        epic = _make_epic(db_session)
        f1 = _make_feat(db_session, epic=epic)
        f2 = _make_feat(db_session, epic=epic)

        match = service.create(
            db_session,
            _payload(f1.id, status="in_progress", task_type="backend"),
        )
        # Different feat.
        service.create(
            db_session,
            _payload(f2.id, status="in_progress", task_type="backend"),
        )
        # Different status.
        service.create(
            db_session,
            _payload(f1.id, status="todo", task_type="backend"),
        )
        # Different task_type.
        service.create(
            db_session,
            _payload(f1.id, status="in_progress", task_type="frontend"),
        )

        rows = service.list_tasks(
            db_session,
            feat_id=f1.id,
            status="in_progress",
            task_type="backend",
        )
        assert len(rows) == 1
        assert rows[0].id == match.id

    def test_list_ordered_by_number_asc(self, db_session):
        """Results are ordered by ``number ASC`` (task 1, task 2, …)."""
        feat = _make_feat(db_session)
        t1 = service.create(db_session, _payload(feat.id))
        t2 = service.create(db_session, _payload(feat.id))
        t3 = service.create(db_session, _payload(feat.id))

        rows = service.list_tasks(db_session, feat_id=feat.id)
        ids_in_order = [r.id for r in rows]
        assert ids_in_order.index(t1.id) < ids_in_order.index(t2.id) < ids_in_order.index(t3.id)

    def test_list_pagination(self, db_session):
        """``limit`` / ``offset`` restrict the result window."""
        feat = _make_feat(db_session)
        for _ in range(5):
            service.create(db_session, _payload(feat.id))

        first_page = service.list_tasks(
            db_session,
            feat_id=feat.id,
            limit=2,
            offset=0,
        )
        second_page = service.list_tasks(
            db_session,
            feat_id=feat.id,
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
        feat = _make_feat(db_session)
        created = service.create(db_session, _payload(feat.id))
        # ``in_transaction()`` must be True — commit would clear it.
        assert db_session.in_transaction()
        # Row is visible within the session after flush.
        assert service.get_by_id(db_session, created.id).id == created.id
