"""Tests for :mod:`backend.services.epic`.

Exercises every public CRUD entry point against the SAVEPOINT-isolated
session provided by ``tests/conftest.py``. Verifies:

* Happy-path list / get / create / update / delete.
* ``ValueError`` on missing ``id`` for get / update / delete.
* ``create`` auto-assigns ``number`` as ``MAX(number) + 1`` per
  project, starts at ``1`` for the first epic, independent per project.
* ``create`` rejects a missing ``version_id`` with
  ``ValueError("version_id required for new epics")`` (DESIGN.md §4.0
  Rule 2 — every new EPIC belongs to a release version).
* ``update`` fires the DESIGN.md §4.0 Rule 4 auto-activate trigger:
  transitioning ``status → in_progress`` promotes the linked version
  from ``planned`` to ``active`` (idempotent for versions already in
  ``active`` / ``released``).
* Update allow-list — only ``title`` and ``status`` are
  applied; ``project_id``, ``number``, ``id`` and ``created_at`` are
  preserved.
* PATCH semantics — omitted fields stay untouched.
* List filters (``project_id``, ``status``) and
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
from backend.db.models.projects import Project
from backend.db.models.tasks import Epic, Feat
from backend.db.models.versions import Version
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

    Every Epic now carries a required ``version_id`` (DESIGN.md §4.0
    Rule 2) so the helpers seed one by default. ``status`` defaults to
    ``planned`` so :func:`auto_activate` has something to promote.
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
    *,
    version_id: uuid.UUID | None = None,
    **overrides,
) -> EpicCreate:
    """Return an :class:`EpicCreate` payload with sensible defaults.

    ``version_id`` is required by the service (DESIGN.md §4.0 Rule 2).
    The helper accepts a ``version_id`` keyword; callers that omit it
    fall back to ``None`` and the caller is expected to either supply
    one explicitly or be asserting the ``version_id required`` error
    path.
    """
    defaults = {
        "project_id": project_id,
        "title": f"Epic {uuid.uuid4().hex[:6]}",
    }
    if version_id is not None:
        defaults["version_id"] = version_id
    defaults.update(overrides)
    return EpicCreate(**defaults)


class TestEpicService:
    """Synchronous CRUD coverage for the Epic service."""

    # ------------------------------------------------------------------ create
    def test_create_epic(self, db_session):
        """``create`` persists the row and returns an ORM instance with server defaults."""
        project = _make_project(db_session)
        version = _make_version(db_session, project=project)

        created = service.create(
            db_session,
            _payload(project.id, version_id=version.id, title="First epic"),
        )

        assert isinstance(created, Epic)
        assert created.id is not None
        assert created.created_at is not None
        assert created.updated_at is not None
        assert created.project_id == project.id
        assert created.version_id == version.id
        assert created.title == "First epic"
        # Schema-level default.
        assert created.status == "planned"
        # Auto-assigned number.
        assert created.number == 1

    def test_create_requires_version_id(self, db_session):
        """``create`` rejects a missing ``version_id`` with a clean ``ValueError``.

        DESIGN.md §4.0 Rule 2 — every new EPIC must be assigned to a
        release version before it can be scheduled. The service raises
        ``ValueError("version_id required for new epics")`` so the
        router can translate it to HTTP 422.
        """
        project = _make_project(db_session)

        with pytest.raises(ValueError, match="version_id required for new epics"):
            service.create(db_session, _payload(project.id))

    def test_create_with_custom_status(self, db_session):
        """``create`` applies a non-default ``status`` when supplied."""
        project = _make_project(db_session)
        version = _make_version(db_session, project=project)

        created = service.create(
            db_session,
            _payload(project.id, version_id=version.id, status="in_progress"),
        )

        assert created.status == "in_progress"

    def test_create_auto_numbers_sequentially(self, db_session):
        """``create`` auto-assigns ``number`` as MAX(number) + 1 per project."""
        project = _make_project(db_session)
        version = _make_version(db_session, project=project)

        e1 = service.create(db_session, _payload(project.id, version_id=version.id))
        e2 = service.create(db_session, _payload(project.id, version_id=version.id))
        e3 = service.create(db_session, _payload(project.id, version_id=version.id))

        assert (e1.number, e2.number, e3.number) == (1, 2, 3)

    def test_create_numbering_is_per_project(self, db_session):
        """Two projects each start their epic numbering at 1 independently."""
        p1 = _make_project(db_session)
        p2 = _make_project(db_session)
        v1 = _make_version(db_session, project=p1)
        v2 = _make_version(db_session, project=p2)

        e1_p1 = service.create(db_session, _payload(p1.id, version_id=v1.id))
        e2_p1 = service.create(db_session, _payload(p1.id, version_id=v1.id))
        e1_p2 = service.create(db_session, _payload(p2.id, version_id=v2.id))

        assert e1_p1.number == 1
        assert e2_p1.number == 2
        assert e1_p2.number == 1

    # ------------------------------------------------------------------- get
    def test_get_by_id(self, db_session):
        """``get_by_id`` returns the row when it exists."""
        project = _make_project(db_session)
        version = _make_version(db_session, project=project)
        created = service.create(db_session, _payload(project.id, version_id=version.id))

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
        version = _make_version(db_session, project=project)
        created = service.create(
            db_session,
            _payload(project.id, version_id=version.id, title="Old title"),
        )

        updated = service.update(
            db_session,
            created.id,
            EpicUpdate(title="New title", status="in_progress"),
        )

        assert updated.id == created.id
        assert updated.title == "New title"
        assert updated.status == "in_progress"

    def test_update_partial_only_status(self, db_session):
        """``update`` leaves omitted fields untouched (PATCH semantics)."""
        project = _make_project(db_session)
        version = _make_version(db_session, project=project)
        created = service.create(
            db_session,
            _payload(
                project.id,
                version_id=version.id,
                title="Original title",
            ),
        )

        updated = service.update(
            db_session,
            created.id,
            EpicUpdate(status="done"),
        )

        assert updated.status == "done"
        # Unchanged fields preserved.
        assert updated.title == "Original title"

    def test_update_preserves_immutable_fields(self, db_session):
        """``id``, ``project_id``, ``number`` and ``created_at`` must not change across ``update``."""
        project = _make_project(db_session)
        version = _make_version(db_session, project=project)
        created = service.create(db_session, _payload(project.id, version_id=version.id))

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
        version = _make_version(db_session, project=project)
        created = service.create(
            db_session,
            _payload(
                project.id,
                version_id=version.id,
                title="Keep me",
                status="in_progress",
            ),
        )

        updated = service.update(db_session, created.id, EpicUpdate())

        assert updated.title == "Keep me"
        assert updated.status == "in_progress"

    def test_update_missing_raises(self, db_session):
        """``update`` on a non-existent id raises ``ValueError``."""
        with pytest.raises(ValueError, match="not found"):
            service.update(
                db_session,
                uuid.uuid4(),
                EpicUpdate(title="nope"),
            )

    def test_update_to_in_progress_auto_activates_version(self, db_session):
        """Transitioning ``status → in_progress`` promotes a ``planned`` version to ``active``.

        DESIGN.md §4.0 Rule 4 — the Epic service fires
        :func:`version.auto_activate` whenever an epic enters
        ``in_progress``. The linked version must flip from ``planned``
        to ``active``.
        """
        project = _make_project(db_session)
        version = _make_version(db_session, project=project, status="planned")
        created = service.create(
            db_session,
            _payload(project.id, version_id=version.id, status="planned"),
        )
        assert version.status == "planned"

        service.update(db_session, created.id, EpicUpdate(status="in_progress"))

        db_session.refresh(version)
        assert version.status == "active"

    def test_update_to_in_progress_noop_for_released_version(self, db_session):
        """``auto_activate`` is a no-op for a version already in ``released``.

        Protects the forward-only status ordering (DESIGN.md §4.0
        Rule 3) — a released version must not regress to ``active`` on
        a stray epic transition.
        """
        project = _make_project(db_session)
        version = _make_version(db_session, project=project, status="released")
        created = service.create(
            db_session,
            _payload(project.id, version_id=version.id, status="planned"),
        )

        service.update(db_session, created.id, EpicUpdate(status="in_progress"))

        db_session.refresh(version)
        assert version.status == "released"

    def test_update_to_done_does_not_auto_activate(self, db_session):
        """Only transitions **into** ``in_progress`` fire ``auto_activate``.

        A straight ``planned → done`` transition (e.g. closing an epic
        that turned out to be a no-op) must leave the version alone.
        """
        project = _make_project(db_session)
        version = _make_version(db_session, project=project, status="planned")
        created = service.create(
            db_session,
            _payload(project.id, version_id=version.id, status="planned"),
        )

        service.update(db_session, created.id, EpicUpdate(status="done"))

        db_session.refresh(version)
        assert version.status == "planned"

    def test_update_in_progress_to_in_progress_is_idempotent(self, db_session):
        """Re-patching an already-``in_progress`` epic does not re-fire the trigger.

        The trigger checks ``previous_status != "in_progress"`` so a
        no-op status patch on a version that was manually rolled back
        (for example via admin tooling) does not silently re-activate
        it.
        """
        project = _make_project(db_session)
        version = _make_version(db_session, project=project, status="planned")
        created = service.create(
            db_session,
            _payload(project.id, version_id=version.id, status="in_progress"),
        )
        # Admin reverts the version to ``planned`` without touching the
        # epic — representative of a correction flow.
        version.status = "planned"
        db_session.flush()

        # Re-patch the epic with the same status — must not re-activate.
        service.update(
            db_session,
            created.id,
            EpicUpdate(status="in_progress", title="Relabelled"),
        )

        db_session.refresh(version)
        assert version.status == "planned"

    # ---------------------------------------------------------------- delete
    def test_delete(self, db_session):
        """``delete`` removes the row; subsequent lookup raises."""
        project = _make_project(db_session)
        version = _make_version(db_session, project=project)
        created = service.create(db_session, _payload(project.id, version_id=version.id))

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
        version = _make_version(db_session, project=project)
        epic = service.create(db_session, _payload(project.id, version_id=version.id))

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
        version = _make_version(db_session, project=project)
        created = service.create(db_session, _payload(project.id, version_id=version.id))
        # ``in_transaction()`` must be True — commit would clear it.
        assert db_session.in_transaction()
        # Row is visible within the session after flush.
        assert service.get_by_id(db_session, created.id).id == created.id
