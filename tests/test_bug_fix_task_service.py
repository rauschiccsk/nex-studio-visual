"""Tests for :mod:`backend.services.bug_fix_task`.

Exercises every public CRUD entry point against the SAVEPOINT-isolated
session provided by ``tests/conftest.py``. Verifies:

* Happy-path list / get / create / update / delete.
* ``number`` is auto-assigned as ``MAX(number) + 1`` per bug and resets
  to ``1`` within each new bug.
* ``ValueError`` on missing ``id`` for get / update / delete.
* Immutable fields (``id``, ``bug_id``, ``number``, ``created_at``) stay
  unchanged on update.
* List filters (``bug_id``, ``status``, ``task_type``) and pagination.
* ``delete`` nulls out dependent ``delegations.bug_fix_task_id`` via
  the inbound ``ON DELETE SET NULL`` FK — delegations are kept for the
  audit trail.
* No ``commit`` happens inside the service — the outer transaction
  rolls back cleanly at fixture teardown.
"""

from __future__ import annotations

import uuid

import pytest

from backend.db.models.bugs import Bug, BugFixTask
from backend.db.models.delegations import Delegation
from backend.db.models.foundation import User
from backend.db.models.projects import Project
from backend.schemas.bug_fix_task import BugFixTaskCreate, BugFixTaskUpdate
from backend.services import bug_fix_task as service


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


def _make_bug(
    db_session,
    *,
    project: Project | None = None,
    user: User | None = None,
    **overrides,
) -> Bug:
    """Create and persist a Bug for FK references."""
    if user is None:
        user = _make_user(db_session)
    if project is None:
        project = _make_project(db_session, user=user)
    defaults = {
        "project_id": project.id,
        "bug_number": 1,
        "title": f"Bug {uuid.uuid4().hex[:6]}",
        "description": "Steps to reproduce.",
        "severity": "major",
        "created_by": user.id,
    }
    defaults.update(overrides)
    bug = Bug(**defaults)
    db_session.add(bug)
    db_session.flush()
    return bug


def _payload(bug_id, **overrides) -> BugFixTaskCreate:
    """Return a :class:`BugFixTaskCreate` payload with deterministic-ish defaults."""
    defaults = {
        "bug_id": bug_id,
        "title": f"Fix {uuid.uuid4().hex[:6]}",
        "task_type": "backend",
    }
    defaults.update(overrides)
    return BugFixTaskCreate(**defaults)


class TestBugFixTaskService:
    """Synchronous CRUD coverage for the BugFixTask service."""

    # ------------------------------------------------------------------ create
    def test_create_bug_fix_task(self, db_session):
        """``create`` persists the row and returns an ORM instance with server defaults."""
        bug = _make_bug(db_session)
        created = service.create(db_session, _payload(bug.id))

        assert isinstance(created, BugFixTask)
        assert created.id is not None
        assert created.created_at is not None
        assert created.updated_at is not None
        assert created.bug_id == bug.id
        assert created.number == 1
        assert created.status == "todo"
        assert created.description == ""
        assert created.task_type == "backend"

    def test_number_auto_increments_per_bug(self, db_session):
        """Each successive fix task for the same bug gets ``max + 1``."""
        bug = _make_bug(db_session)
        first = service.create(db_session, _payload(bug.id))
        second = service.create(db_session, _payload(bug.id))
        third = service.create(db_session, _payload(bug.id))

        assert first.number == 1
        assert second.number == 2
        assert third.number == 3

    def test_number_resets_per_bug(self, db_session):
        """``number`` is scoped to ``bug_id`` — two bugs each start at 1."""
        user = _make_user(db_session)
        project = _make_project(db_session, user=user)
        bug_a = _make_bug(db_session, project=project, user=user, bug_number=1)
        bug_b = _make_bug(db_session, project=project, user=user, bug_number=2)

        t_a1 = service.create(db_session, _payload(bug_a.id))
        t_b1 = service.create(db_session, _payload(bug_b.id))
        t_a2 = service.create(db_session, _payload(bug_a.id))

        assert t_a1.number == 1
        assert t_b1.number == 1
        assert t_a2.number == 2

    def test_create_defaults(self, db_session):
        """``status`` / ``description`` take their schema defaults when omitted."""
        bug = _make_bug(db_session)
        payload = BugFixTaskCreate(
            bug_id=bug.id,
            title="Default fix task",
            task_type="frontend",
        )
        created = service.create(db_session, payload)
        assert created.status == "todo"
        assert created.description == ""
        assert created.task_type == "frontend"

    def test_create_with_all_optional_fields(self, db_session):
        """``create`` accepts and persists every optional field."""
        bug = _make_bug(db_session)
        payload = BugFixTaskCreate(
            bug_id=bug.id,
            title="Full payload",
            description="Detailed steps.",
            task_type="migration",
            status="in_progress",
            estimated_minutes=30,
            actual_minutes=25,
            checklist_type="backend_fastapi",
        )
        created = service.create(db_session, payload)
        assert created.description == "Detailed steps."
        assert created.task_type == "migration"
        assert created.status == "in_progress"
        assert created.estimated_minutes == 30
        assert created.actual_minutes == 25
        assert created.checklist_type == "backend_fastapi"

    # ------------------------------------------------------------------- get
    def test_get_by_id(self, db_session):
        """``get_by_id`` returns the fix task when it exists."""
        bug = _make_bug(db_session)
        created = service.create(db_session, _payload(bug.id))
        fetched = service.get_by_id(db_session, created.id)
        assert fetched.id == created.id

    def test_get_by_id_missing_raises(self, db_session):
        """``get_by_id`` raises ``ValueError`` for an unknown id."""
        with pytest.raises(ValueError, match="not found"):
            service.get_by_id(db_session, uuid.uuid4())

    # ---------------------------------------------------------------- update
    def test_update_mutable_fields(self, db_session):
        """``update`` changes every mutable column."""
        bug = _make_bug(db_session)
        created = service.create(db_session, _payload(bug.id))
        original_id = created.id
        original_bug_id = created.bug_id
        original_number = created.number
        original_created_at = created.created_at

        updated = service.update(
            db_session,
            created.id,
            BugFixTaskUpdate(
                title="new title",
                description="new description",
                task_type="docs",
                status="in_progress",
                estimated_minutes=45,
                actual_minutes=60,
                checklist_type="docs_markdown",
            ),
        )
        assert updated.title == "new title"
        assert updated.description == "new description"
        assert updated.task_type == "docs"
        assert updated.status == "in_progress"
        assert updated.estimated_minutes == 45
        assert updated.actual_minutes == 60
        assert updated.checklist_type == "docs_markdown"
        # Immutable fields unchanged.
        assert updated.id == original_id
        assert updated.bug_id == original_bug_id
        assert updated.number == original_number
        assert updated.created_at == original_created_at

    def test_update_partial(self, db_session):
        """``update`` with only ``status`` leaves other fields untouched."""
        bug = _make_bug(db_session)
        created = service.create(
            db_session,
            _payload(bug.id, title="keep me"),
        )
        updated = service.update(
            db_session,
            created.id,
            BugFixTaskUpdate(status="done"),
        )
        assert updated.status == "done"
        assert updated.title == "keep me"
        assert updated.task_type == created.task_type

    def test_update_missing_raises(self, db_session):
        """``update`` on a non-existent id raises ``ValueError``."""
        with pytest.raises(ValueError, match="not found"):
            service.update(db_session, uuid.uuid4(), BugFixTaskUpdate(status="done"))

    def test_update_ignores_disallowed_fields(self, db_session):
        """``BugFixTaskUpdate`` has no ``bug_id`` / ``number`` — immutable fields stay put."""
        bug = _make_bug(db_session)
        created = service.create(db_session, _payload(bug.id))
        original_bug_id = created.bug_id
        original_number = created.number

        updated = service.update(
            db_session,
            created.id,
            BugFixTaskUpdate(description="just a desc change"),
        )
        assert updated.description == "just a desc change"
        assert updated.bug_id == original_bug_id
        assert updated.number == original_number

    # ---------------------------------------------------------------- delete
    def test_delete(self, db_session):
        """``delete`` removes the row; subsequent lookup raises."""
        bug = _make_bug(db_session)
        created = service.create(db_session, _payload(bug.id))
        service.delete(db_session, created.id)

        with pytest.raises(ValueError, match="not found"):
            service.get_by_id(db_session, created.id)

    def test_delete_missing_raises(self, db_session):
        """``delete`` on a non-existent id raises ``ValueError``."""
        with pytest.raises(ValueError, match="not found"):
            service.delete(db_session, uuid.uuid4())

    def test_delete_sets_null_on_delegations(self, db_session):
        """``delete`` relies on DB-level ``SET NULL`` for dependent ``delegations``.

        ``delegations.bug_fix_task_id`` has ``ON DELETE SET NULL``
        (DESIGN.md §1.7), so the delegation row is retained for the
        audit trail but its reference is cleared.
        """
        from sqlalchemy import select as sa_select

        bug = _make_bug(db_session)
        created = service.create(db_session, _payload(bug.id))

        delegation = Delegation(
            bug_fix_task_id=created.id,
            bug_id=bug.id,
            prompt="Fix it now.",
        )
        db_session.add(delegation)
        db_session.flush()
        delegation_id = delegation.id

        service.delete(db_session, created.id)

        # Expire the session cache so the next query hits the DB and
        # sees the SET NULL effect rather than the stale in-memory ORM
        # state.
        db_session.expire_all()

        refreshed = db_session.execute(sa_select(Delegation).where(Delegation.id == delegation_id)).scalar_one_or_none()
        assert refreshed is not None
        assert refreshed.bug_fix_task_id is None

    # ------------------------------------------------------------------ list
    def test_list_all(self, db_session):
        """``list_bug_fix_tasks`` returns every fix task when no filter is supplied."""
        bug = _make_bug(db_session)
        created_ids: set = set()
        for _ in range(3):
            created_ids.add(service.create(db_session, _payload(bug.id)).id)
        rows = service.list_bug_fix_tasks(db_session)
        assert created_ids.issubset({t.id for t in rows})

    def test_list_filter_by_bug(self, db_session):
        """``list_bug_fix_tasks(bug_id=...)`` returns only tasks for that bug."""
        user = _make_user(db_session)
        project = _make_project(db_session, user=user)
        bug_a = _make_bug(db_session, project=project, user=user, bug_number=1)
        bug_b = _make_bug(db_session, project=project, user=user, bug_number=2)
        in_a = service.create(db_session, _payload(bug_a.id))
        service.create(db_session, _payload(bug_b.id))

        rows = service.list_bug_fix_tasks(db_session, bug_id=bug_a.id)
        assert all(t.bug_id == bug_a.id for t in rows)
        assert any(t.id == in_a.id for t in rows)

    def test_list_filter_by_status(self, db_session):
        """``list_bug_fix_tasks(status=...)`` returns only matching-status tasks."""
        bug = _make_bug(db_session)
        service.create(db_session, _payload(bug.id))  # status='todo'
        done = service.create(db_session, _payload(bug.id, status="done"))

        rows = service.list_bug_fix_tasks(db_session, status="done")
        assert all(t.status == "done" for t in rows)
        assert any(t.id == done.id for t in rows)

    def test_list_filter_by_task_type(self, db_session):
        """``list_bug_fix_tasks(task_type=...)`` returns only matching-type tasks."""
        bug = _make_bug(db_session)
        service.create(db_session, _payload(bug.id, task_type="backend"))
        frontend = service.create(db_session, _payload(bug.id, task_type="frontend"))

        rows = service.list_bug_fix_tasks(db_session, task_type="frontend")
        assert all(t.task_type == "frontend" for t in rows)
        assert any(t.id == frontend.id for t in rows)

    def test_list_ordered_by_bug_then_number(self, db_session):
        """Results are ordered by ``(bug_id, number)`` for natural display order."""
        bug = _make_bug(db_session)
        t1 = service.create(db_session, _payload(bug.id))
        t2 = service.create(db_session, _payload(bug.id))
        t3 = service.create(db_session, _payload(bug.id))

        rows = service.list_bug_fix_tasks(db_session, bug_id=bug.id)
        ids_in_order = [t.id for t in rows]
        numbers_in_order = [t.number for t in rows]
        # Sorted ascending by number (1, 2, 3).
        assert numbers_in_order == sorted(numbers_in_order)
        assert ids_in_order.index(t1.id) < ids_in_order.index(t2.id) < ids_in_order.index(t3.id)

    def test_list_pagination(self, db_session):
        """``limit`` / ``offset`` restrict the result window."""
        bug = _make_bug(db_session)
        for _ in range(5):
            service.create(db_session, _payload(bug.id))

        first_page = service.list_bug_fix_tasks(db_session, bug_id=bug.id, limit=2, offset=0)
        second_page = service.list_bug_fix_tasks(db_session, bug_id=bug.id, limit=2, offset=2)
        assert len(first_page) == 2
        assert len(second_page) == 2
        first_ids = {t.id for t in first_page}
        second_ids = {t.id for t in second_page}
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
        bug = _make_bug(db_session)
        created = service.create(db_session, _payload(bug.id))
        # ``in_transaction()`` must be True — commit would clear it.
        assert db_session.in_transaction()
        # Row is visible within the session after flush.
        assert service.get_by_id(db_session, created.id).id == created.id
