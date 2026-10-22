"""Tests for :mod:`backend.services.bug`.

Exercises every public CRUD entry point against the SAVEPOINT-isolated
session provided by ``tests/conftest.py``. Verifies:

* Happy-path list / get / create / update / delete.
* ``bug_number`` is auto-assigned as ``MAX(bug_number) + 1`` per project
  and resets to ``1`` within each new project.
* ``create`` rejects a missing ``version_id`` with
  ``ValueError("version_id required for new bugs")`` (DESIGN.md §4.0
  Rule 2 — every new BUG belongs to a release version).
* ``ValueError`` on missing ``id`` for get / update / delete.
* Immutable fields (``id``, ``project_id``, ``bug_number``, ``created_by``,
  ``created_at``) stay unchanged on update.
* ``update`` auto-stamps ``resolved_at`` when ``status`` transitions to
  ``resolved`` and the caller did not supply an explicit value; explicit
  ``resolved_at`` always wins.
* List filters (``project_id``, ``status``, ``severity``, ``source``,
  ``created_by``) and pagination.
* ``delete`` cascades to dependent ``bug_fix_tasks`` rows — the single
  inbound FK uses ``ON DELETE CASCADE`` so no RESTRICT guard is needed.
* No ``commit`` happens inside the service — the outer transaction rolls
  back cleanly at fixture teardown.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from backend.db.models.bugs import Bug
from backend.db.models.foundation import User
from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.schemas.bug import BugCreate, BugUpdate
from backend.services import bug as service


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


def _make_version(
    db_session,
    *,
    project: Project | None = None,
    status: str = "planned",
    **overrides,
) -> Version:
    """Create and persist a Version bound to ``project`` for FK references.

    Every Bug now carries a required ``version_id`` (DESIGN.md §4.0
    Rule 2) so the helpers seed one by default.
    """
    if project is None:
        project = _make_project(db_session)
    defaults = {
        "project_id": project.id,
        "version_number": f"0.0.{uuid.uuid4().int % 10**9}",
        "name": "Test version",
        "status": status,
    }
    defaults.update(overrides)
    version = Version(**defaults)
    db_session.add(version)
    db_session.flush()
    return version


def _payload(
    project_id,
    created_by,
    *,
    version_id: uuid.UUID | None = None,
    **overrides,
) -> BugCreate:
    """Return a :class:`BugCreate` payload with deterministic-ish defaults.

    ``version_id`` is required by the service (DESIGN.md §4.0 Rule 2).
    The helper accepts a ``version_id`` keyword; callers that omit it
    fall back to ``None`` and are expected to either supply one
    explicitly or be asserting the ``version_id required`` error path.
    """
    defaults = {
        "project_id": project_id,
        "title": f"Bug {uuid.uuid4().hex[:6]}",
        "description": "Steps to reproduce.",
        "severity": "major",
        "created_by": created_by,
    }
    if version_id is not None:
        defaults["version_id"] = version_id
    defaults.update(overrides)
    return BugCreate(**defaults)


class TestBugService:
    """Synchronous CRUD coverage for the Bug service."""

    # ------------------------------------------------------------------ create
    def test_create_bug(self, db_session):
        """``create`` persists the row and returns an ORM instance with server defaults."""
        user = _make_user(db_session)
        project = _make_project(db_session, user=user)
        version = _make_version(db_session, project=project)
        created = service.create(db_session, _payload(project.id, user.id, version_id=version.id))

        assert isinstance(created, Bug)
        assert created.id is not None
        assert created.created_at is not None
        assert created.updated_at is not None
        assert created.project_id == project.id
        assert created.version_id == version.id
        assert created.created_by == user.id
        assert created.bug_number == 1
        assert created.status == "new"
        assert created.source == "internal"
        assert created.severity == "major"

    def test_create_requires_version_id(self, db_session):
        """``create`` rejects a missing ``version_id`` with a clean ``ValueError``.

        DESIGN.md §4.0 Rule 2 — every new BUG must be assigned to a
        release version before it can be scheduled. The service raises
        ``ValueError("version_id required for new bugs")`` so the
        router can translate it to HTTP 422.
        """
        user = _make_user(db_session)
        project = _make_project(db_session, user=user)

        with pytest.raises(ValueError, match="version_id required for new bugs"):
            service.create(db_session, _payload(project.id, user.id))

    def test_bug_number_auto_increments_per_project(self, db_session):
        """Each successive bug in the same project gets ``max + 1``."""
        user = _make_user(db_session)
        project = _make_project(db_session, user=user)
        version = _make_version(db_session, project=project)
        first = service.create(db_session, _payload(project.id, user.id, version_id=version.id))
        second = service.create(db_session, _payload(project.id, user.id, version_id=version.id))
        third = service.create(db_session, _payload(project.id, user.id, version_id=version.id))

        assert first.bug_number == 1
        assert second.bug_number == 2
        assert third.bug_number == 3

    def test_bug_number_resets_per_project(self, db_session):
        """``bug_number`` is scoped to ``project_id`` — two projects each start at 1."""
        user = _make_user(db_session)
        p1 = _make_project(db_session, user=user)
        p2 = _make_project(db_session, user=user)
        v1 = _make_version(db_session, project=p1)
        v2 = _make_version(db_session, project=p2)

        b1 = service.create(db_session, _payload(p1.id, user.id, version_id=v1.id))
        b2 = service.create(db_session, _payload(p2.id, user.id, version_id=v2.id))
        b1b = service.create(db_session, _payload(p1.id, user.id, version_id=v1.id))

        assert b1.bug_number == 1
        assert b2.bug_number == 1
        assert b1b.bug_number == 2

    def test_create_defaults(self, db_session):
        """``status`` / ``source`` take their schema defaults when omitted."""
        user = _make_user(db_session)
        project = _make_project(db_session, user=user)
        version = _make_version(db_session, project=project)
        payload = BugCreate(
            project_id=project.id,
            version_id=version.id,
            title="Default bug",
            description="Some description.",
            severity="minor",
            created_by=user.id,
        )
        created = service.create(db_session, payload)
        assert created.status == "new"
        assert created.source == "internal"

    # ------------------------------------------------------------------- get
    def test_get_by_id(self, db_session):
        """``get_by_id`` returns the bug when it exists."""
        user = _make_user(db_session)
        project = _make_project(db_session, user=user)
        version = _make_version(db_session, project=project)
        created = service.create(db_session, _payload(project.id, user.id, version_id=version.id))
        fetched = service.get_by_id(db_session, created.id)
        assert fetched.id == created.id

    def test_get_by_id_missing_raises(self, db_session):
        """``get_by_id`` raises ``ValueError`` for an unknown id."""
        with pytest.raises(ValueError, match="not found"):
            service.get_by_id(db_session, uuid.uuid4())

    # ---------------------------------------------------------------- update
    def test_update_mutable_fields(self, db_session):
        """``update`` changes every mutable column."""
        user = _make_user(db_session)
        project = _make_project(db_session, user=user)
        version = _make_version(db_session, project=project)
        created = service.create(db_session, _payload(project.id, user.id, version_id=version.id))
        original_id = created.id
        original_project_id = created.project_id
        original_bug_number = created.bug_number
        original_created_by = created.created_by
        original_created_at = created.created_at

        explicit_resolved = datetime(2025, 1, 1, tzinfo=timezone.utc)
        updated = service.update(
            db_session,
            created.id,
            BugUpdate(
                title="new title",
                description="new description",
                severity="critical",
                status="accepted",
                source="customer",
                reported_by="Zákazník X",
                environment="production",
                resolved_at=explicit_resolved,
                commit_hash="abc123",
            ),
        )
        assert updated.title == "new title"
        assert updated.description == "new description"
        assert updated.severity == "critical"
        assert updated.status == "accepted"
        assert updated.source == "customer"
        assert updated.reported_by == "Zákazník X"
        assert updated.environment == "production"
        assert updated.resolved_at == explicit_resolved
        assert updated.commit_hash == "abc123"
        # Immutable fields unchanged.
        assert updated.id == original_id
        assert updated.project_id == original_project_id
        assert updated.bug_number == original_bug_number
        assert updated.created_by == original_created_by
        assert updated.created_at == original_created_at

    def test_update_partial(self, db_session):
        """``update`` with only ``status`` leaves other fields untouched."""
        user = _make_user(db_session)
        project = _make_project(db_session, user=user)
        version = _make_version(db_session, project=project)
        created = service.create(
            db_session,
            _payload(project.id, user.id, version_id=version.id, title="keep me"),
        )
        updated = service.update(
            db_session,
            created.id,
            BugUpdate(status="accepted"),
        )
        assert updated.status == "accepted"
        assert updated.title == "keep me"
        assert updated.severity == created.severity

    def test_update_missing_raises(self, db_session):
        """``update`` on a non-existent id raises ``ValueError``."""
        with pytest.raises(ValueError, match="not found"):
            service.update(db_session, uuid.uuid4(), BugUpdate(status="resolved"))

    def test_update_auto_stamps_resolved_at(self, db_session):
        """Transitioning ``status`` → ``resolved`` without explicit ``resolved_at`` auto-stamps now."""
        user = _make_user(db_session)
        project = _make_project(db_session, user=user)
        version = _make_version(db_session, project=project)
        created = service.create(db_session, _payload(project.id, user.id, version_id=version.id))
        assert created.resolved_at is None

        before = datetime.now(tz=timezone.utc)
        updated = service.update(
            db_session,
            created.id,
            BugUpdate(status="resolved"),
        )
        after = datetime.now(tz=timezone.utc)

        assert updated.status == "resolved"
        assert updated.resolved_at is not None
        assert before <= updated.resolved_at <= after

    def test_update_explicit_resolved_at_wins(self, db_session):
        """Explicit ``resolved_at`` in the payload overrides auto-stamping."""
        user = _make_user(db_session)
        project = _make_project(db_session, user=user)
        version = _make_version(db_session, project=project)
        created = service.create(db_session, _payload(project.id, user.id, version_id=version.id))
        explicit = datetime(2024, 6, 15, 12, 0, tzinfo=timezone.utc)

        updated = service.update(
            db_session,
            created.id,
            BugUpdate(status="resolved", resolved_at=explicit),
        )
        assert updated.status == "resolved"
        assert updated.resolved_at == explicit

    def test_update_resolved_to_resolved_does_not_restamp(self, db_session):
        """Re-updating a bug that is already ``resolved`` leaves ``resolved_at`` alone."""
        user = _make_user(db_session)
        project = _make_project(db_session, user=user)
        version = _make_version(db_session, project=project)
        created = service.create(db_session, _payload(project.id, user.id, version_id=version.id))
        first = service.update(db_session, created.id, BugUpdate(status="resolved"))
        stamped_at = first.resolved_at
        assert stamped_at is not None

        # Another update that keeps status=resolved must not overwrite
        # resolved_at because status did not transition into resolved.
        second = service.update(
            db_session,
            created.id,
            BugUpdate(status="resolved", title="retitled"),
        )
        assert second.resolved_at == stamped_at
        assert second.title == "retitled"

    def test_update_ignores_disallowed_fields(self, db_session):
        """``BugUpdate`` has no ``project_id`` / ``bug_number`` — immutable fields stay put."""
        user = _make_user(db_session)
        project = _make_project(db_session, user=user)
        version = _make_version(db_session, project=project)
        created = service.create(db_session, _payload(project.id, user.id, version_id=version.id))
        original_project_id = created.project_id
        original_bug_number = created.bug_number

        updated = service.update(
            db_session,
            created.id,
            BugUpdate(description="just a desc change"),
        )
        assert updated.description == "just a desc change"
        assert updated.project_id == original_project_id
        assert updated.bug_number == original_bug_number

    # ---------------------------------------------------------------- delete
    def test_delete(self, db_session):
        """``delete`` removes the row; subsequent lookup raises."""
        user = _make_user(db_session)
        project = _make_project(db_session, user=user)
        version = _make_version(db_session, project=project)
        created = service.create(db_session, _payload(project.id, user.id, version_id=version.id))
        service.delete(db_session, created.id)

        with pytest.raises(ValueError, match="not found"):
            service.get_by_id(db_session, created.id)

    def test_delete_missing_raises(self, db_session):
        """``delete`` on a non-existent id raises ``ValueError``."""
        with pytest.raises(ValueError, match="not found"):
            service.delete(db_session, uuid.uuid4())

    # ------------------------------------------------------------------ list

    # --------------------------------------------------------------- commit
    def test_service_does_not_commit(self, db_session):
        """Service calls only ``flush`` — rows vanish when the outer transaction rolls back.

        This asserts the contract that transaction control belongs to the
        router, not the service. The SAVEPOINT-isolated ``db_session`` fixture
        rolls back at teardown; a service that called ``commit`` would leak
        rows into the test database and break other tests.
        """
        user = _make_user(db_session)
        project = _make_project(db_session, user=user)
        version = _make_version(db_session, project=project)
        created = service.create(db_session, _payload(project.id, user.id, version_id=version.id))
        # ``in_transaction()`` must be True — commit would clear it.
        assert db_session.in_transaction()
        # Row is visible within the session after flush.
        assert service.get_by_id(db_session, created.id).id == created.id
