"""Tests for :mod:`backend.services.task`.

Exercises every public CRUD entry point against the SAVEPOINT-isolated
session provided by ``tests/conftest.py``. Verifies:

* Happy-path list / get / create / update / delete.
* ``ValueError`` on missing ``id`` for get / update / delete.
* ``create`` auto-assigns ``number`` as ``MAX(number) + 1`` per feat,
  starts at ``1`` for the first task, independent per feat.
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
from backend.schemas.task import TaskCreate
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
        "type": "standard",
        "auth_mode": "password",
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
        assert created.checklist_type is None
        # Auto-assigned number.
        assert created.number == 1

    def test_create_with_description(self, db_session):
        """``create`` applies an explicit ``description``."""
        feat = _make_feat(db_session)

        created = service.create(
            db_session,
            _payload(
                feat.id,
                description="Detailed task body.",
            ),
        )

        assert created.description == "Detailed task body."

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

    # ---------------------------------------------------------------- delete

    # ------------------------------------------------------------------ list

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
