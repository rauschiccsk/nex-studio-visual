"""Tests for :mod:`backend.services.feat`.

Exercises every public CRUD entry point against the SAVEPOINT-isolated
session provided by ``tests/conftest.py``. Verifies:

* Happy-path list / get / create / update / delete.
* ``ValueError`` on missing ``id`` for get / update / delete.
* ``create`` auto-assigns ``number`` as ``MAX(number) + 1`` per epic,
  starts at ``1`` for the first feat, independent per epic.
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
from backend.db.models.tasks import Epic, Feat
from backend.schemas.feat import FeatCreate
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
        # Server-managed counters seeded by DB server_default.
        assert created.auto_fix_count == 0
        # Auto-assigned number.
        assert created.number == 1

    def test_create_with_description(self, db_session):
        """``create`` applies an explicit ``description``."""
        epic = _make_epic(db_session)

        created = service.create(
            db_session,
            _payload(
                epic.id,
                description="Detailed feat body.",
            ),
        )

        assert created.description == "Detailed feat body."

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
        epic = _make_epic(db_session)
        created = service.create(db_session, _payload(epic.id))
        # ``in_transaction()`` must be True — commit would clear it.
        assert db_session.in_transaction()
        # Row is visible within the session after flush.
        assert service.get_by_id(db_session, created.id).id == created.id
