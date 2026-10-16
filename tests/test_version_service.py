"""Tests for :mod:`backend.services.version`.

Exercises every public entry point against the SAVEPOINT-isolated
session provided by ``tests/conftest.py``. Covers CRUD plus the two
lifecycle helpers specified in DESIGN.md §4.0 Version Lifecycle Rules:

* Happy-path list / get / create / update — no explicit ``delete`` here
  because DESIGN.md §4.0 Rule 6 forbids deleting a version that still
  has EPICs or BUGs; the router handles the 409 path in a follow-up
  task (9.3).
* ``create`` defaults ``status`` to ``planned`` and rejects duplicate
  ``(project_id, version_number)`` pairs with :class:`ValueError`.
* ``get_by_id`` eager-loads ``epics`` and ``bugs`` for the detail view.
* ``list_versions`` orders by ``version_number DESC`` and attaches the
  three aggregate counts (``epic_count``, ``epics_done``,
  ``bug_count``) consumed by :class:`VersionRead`.
* ``update`` allow-list: only mutable fields are applied; immutable
  ``project_id`` / ``id`` / ``created_at`` stay intact across updates.
  ``version_number`` rename rejects collisions within the same project.
* ``release`` is blocked when any EPIC has ``status != 'done'``; the
  :class:`ValueError` carries the blocking EPIC IDs so the router can
  surface them in the 409 response. Success sets ``status = 'released'``
  and ``release_date = today``.
* ``auto_activate`` promotes a ``planned`` version to ``active`` and is
  a no-op for versions already in ``active`` / ``released``.
* No ``commit`` happens inside the service — the outer transaction
  rolls back cleanly at fixture teardown.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest

from backend.db.models.bugs import Bug
from backend.db.models.foundation import User
from backend.db.models.projects import Project
from backend.db.models.tasks import Epic
from backend.db.models.versions import Version
from backend.schemas.version import VersionCreate, VersionUpdate
from backend.services import version as service

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


def _make_epic(
    db_session,
    *,
    project: Project,
    version: Version,
    number: int,
    status: str = "planned",
) -> Epic:
    """Create and persist an Epic attached to a version."""
    epic = Epic(
        project_id=project.id,
        version_id=version.id,
        number=number,
        title=f"Epic {number}",
        status=status,
    )
    db_session.add(epic)
    db_session.flush()
    return epic


def _make_bug(
    db_session,
    *,
    project: Project,
    version: Version,
    user: User,
    bug_number: int,
) -> Bug:
    """Create and persist a Bug attached to a version."""
    bug = Bug(
        project_id=project.id,
        version_id=version.id,
        bug_number=bug_number,
        title=f"Bug {bug_number}",
        description="Steps to reproduce.",
        severity="minor",
        created_by=user.id,
    )
    db_session.add(bug)
    db_session.flush()
    return bug


def _payload(version_number: str = "1.0.0", **overrides) -> VersionCreate:
    """Return a :class:`VersionCreate` payload with sensible defaults."""
    defaults = {
        "version_number": version_number,
        "name": f"Release {version_number}",
        "description": "Release notes.",
        "target_date": date.today() + timedelta(days=30),
    }
    defaults.update(overrides)
    return VersionCreate(**defaults)


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------


class TestCreate:
    """``create`` persistence + uniqueness guards."""

    def test_create_defaults(self, db_session):
        """``create`` persists the row with ``status='planned'`` default."""
        user = _make_user(db_session)
        project = _make_project(db_session, user=user)

        created = service.create(
            db_session,
            project.id,
            _payload("1.0.0"),
            user.id,
        )

        assert isinstance(created, Version)
        assert created.id is not None
        assert created.created_at is not None
        assert created.updated_at is not None
        assert created.project_id == project.id
        assert created.version_number == "1.0.0"
        # DB server_default fires on flush.
        assert created.status == "planned"
        assert created.release_date is None

    def test_create_with_all_fields(self, db_session):
        """``create`` stores every optional field from :class:`VersionCreate`."""
        user = _make_user(db_session)
        project = _make_project(db_session, user=user)
        target = date(2026, 6, 1)

        created = service.create(
            db_session,
            project.id,
            VersionCreate(
                version_number="1.1.0",
                name="Bug fix release",
                description="Minor fixes.",
                target_date=target,
            ),
            user.id,
        )

        assert created.name == "Bug fix release"
        assert created.description == "Minor fixes."
        assert created.target_date == target

    def test_create_duplicate_version_number_raises(self, db_session):
        """Duplicate ``(project_id, version_number)`` pairs raise ``ValueError``."""
        user = _make_user(db_session)
        project = _make_project(db_session, user=user)
        service.create(db_session, project.id, _payload("1.0.0"), user.id)

        with pytest.raises(ValueError, match="already exists"):
            service.create(db_session, project.id, _payload("1.0.0"), user.id)

    def test_create_same_version_number_different_projects_ok(self, db_session):
        """``version_number`` uniqueness is scoped to ``project_id``."""
        user = _make_user(db_session)
        p1 = _make_project(db_session, user=user)
        p2 = _make_project(db_session, user=user)

        v1 = service.create(db_session, p1.id, _payload("1.0.0"), user.id)
        v2 = service.create(db_session, p2.id, _payload("1.0.0"), user.id)

        assert v1.id != v2.id
        assert v1.version_number == v2.version_number == "1.0.0"


# ---------------------------------------------------------------------------
# get_by_id
# ---------------------------------------------------------------------------


class TestGetById:
    """``get_by_id`` happy / missing / eager-load paths."""

    def test_get_by_id(self, db_session):
        """Returns the row when it exists."""
        user = _make_user(db_session)
        project = _make_project(db_session, user=user)
        created = service.create(db_session, project.id, _payload("1.0.0"), user.id)

        fetched = service.get_by_id(db_session, created.id)
        assert fetched.id == created.id
        assert fetched.project_id == project.id

    def test_get_by_id_missing_raises(self, db_session):
        """Unknown id → ``ValueError``."""
        with pytest.raises(ValueError, match="not found"):
            service.get_by_id(db_session, uuid.uuid4())

    def test_get_by_id_eager_loads_epics_and_bugs(self, db_session):
        """EPIC and BUG collections are populated in one round-trip."""
        user = _make_user(db_session)
        project = _make_project(db_session, user=user)
        version = service.create(db_session, project.id, _payload("1.0.0"), user.id)

        epic_a = _make_epic(db_session, project=project, version=version, number=1)
        epic_b = _make_epic(db_session, project=project, version=version, number=2)
        bug_a = _make_bug(db_session, project=project, version=version, user=user, bug_number=1)

        # Expire so we hit the DB rather than the identity map.
        db_session.expire_all()

        fetched = service.get_by_id(db_session, version.id)
        epic_ids = {e.id for e in fetched.epics}
        bug_ids = {b.id for b in fetched.bugs}
        assert epic_ids == {epic_a.id, epic_b.id}
        assert bug_ids == {bug_a.id}


# ---------------------------------------------------------------------------
# update
# ---------------------------------------------------------------------------


class TestUpdate:
    """``update`` mutable-field + immutability + rename guards."""

    def test_update_fields(self, db_session):
        """Mutable fields are patched in place."""
        user = _make_user(db_session)
        project = _make_project(db_session, user=user)
        created = service.create(db_session, project.id, _payload("1.0.0"), user.id)

        new_target = date(2027, 1, 1)
        updated = service.update(
            db_session,
            created.id,
            VersionUpdate(
                name="Renamed",
                description="Updated notes.",
                target_date=new_target,
            ),
        )

        assert updated.id == created.id
        assert updated.name == "Renamed"
        assert updated.description == "Updated notes."
        assert updated.target_date == new_target

    def test_update_partial_leaves_other_fields(self, db_session):
        """PATCH semantics — omitted fields stay untouched."""
        user = _make_user(db_session)
        project = _make_project(db_session, user=user)
        created = service.create(
            db_session,
            project.id,
            VersionCreate(version_number="1.0.0", name="Keep me"),
            user.id,
        )

        updated = service.update(
            db_session,
            created.id,
            VersionUpdate(description="only description changes"),
        )

        assert updated.description == "only description changes"
        assert updated.name == "Keep me"
        assert updated.version_number == "1.0.0"

    def test_update_preserves_immutable_fields(self, db_session):
        """``id``, ``project_id`` and ``created_at`` must not change."""
        user = _make_user(db_session)
        project = _make_project(db_session, user=user)
        created = service.create(db_session, project.id, _payload("1.0.0"), user.id)

        original_id = created.id
        original_project_id = created.project_id
        original_created_at = created.created_at

        updated = service.update(
            db_session,
            created.id,
            VersionUpdate(name="Renamed"),
        )

        assert updated.id == original_id
        assert updated.project_id == original_project_id
        assert updated.created_at == original_created_at

    def test_update_empty_payload_is_noop(self, db_session):
        """A ``VersionUpdate`` with no fields set leaves the row intact."""
        user = _make_user(db_session)
        project = _make_project(db_session, user=user)
        created = service.create(db_session, project.id, _payload("1.0.0"), user.id)

        updated = service.update(db_session, created.id, VersionUpdate())

        assert updated.version_number == "1.0.0"
        assert updated.status == "planned"

    def test_update_rename_collides_raises(self, db_session):
        """Renaming ``version_number`` to an existing sibling's value raises."""
        user = _make_user(db_session)
        project = _make_project(db_session, user=user)
        service.create(db_session, project.id, _payload("1.0.0"), user.id)
        other = service.create(db_session, project.id, _payload("1.1.0"), user.id)

        with pytest.raises(ValueError, match="already exists"):
            service.update(
                db_session,
                other.id,
                VersionUpdate(version_number="1.0.0"),
            )

    def test_update_missing_raises(self, db_session):
        """``update`` on a non-existent id raises ``ValueError``."""
        with pytest.raises(ValueError, match="not found"):
            service.update(
                db_session,
                uuid.uuid4(),
                VersionUpdate(name="nope"),
            )


# ---------------------------------------------------------------------------
# list_versions
# ---------------------------------------------------------------------------


class TestListVersions:
    """``list_versions`` ordering + aggregate-count attachment."""

    def test_list_filters_by_project(self, db_session):
        """Only the requested project's versions are returned."""
        user = _make_user(db_session)
        p1 = _make_project(db_session, user=user)
        p2 = _make_project(db_session, user=user)

        mine = service.create(db_session, p1.id, _payload("1.0.0"), user.id)
        service.create(db_session, p2.id, _payload("1.0.0"), user.id)

        rows = service.list_versions(db_session, p1.id)
        assert [r.id for r in rows] == [mine.id]

    def test_list_ordered_by_version_number_desc(self, db_session):
        """Results are ordered by ``version_number DESC``."""
        user = _make_user(db_session)
        project = _make_project(db_session, user=user)
        service.create(db_session, project.id, _payload("1.0.0"), user.id)
        service.create(db_session, project.id, _payload("1.1.0"), user.id)
        service.create(db_session, project.id, _payload("2.0.0"), user.id)

        rows = service.list_versions(db_session, project.id)
        numbers = [r.version_number for r in rows]
        assert numbers == sorted(numbers, reverse=True)
        assert numbers[0] == "2.0.0"
        assert numbers[-1] == "1.0.0"

    def test_list_attaches_aggregate_counts(self, db_session):
        """Each returned Version carries ``epic_count`` / ``epics_done`` / ``bug_count``."""
        user = _make_user(db_session)
        project = _make_project(db_session, user=user)
        version = service.create(db_session, project.id, _payload("1.0.0"), user.id)

        _make_epic(db_session, project=project, version=version, number=1, status="done")
        _make_epic(db_session, project=project, version=version, number=2, status="in_progress")
        _make_epic(db_session, project=project, version=version, number=3, status="planned")
        _make_bug(db_session, project=project, version=version, user=user, bug_number=1)
        _make_bug(db_session, project=project, version=version, user=user, bug_number=2)

        rows = service.list_versions(db_session, project.id)
        assert len(rows) == 1
        row = rows[0]
        assert row.epic_count == 3
        assert row.epics_done == 1
        assert row.bug_count == 2

    def test_list_empty_project_returns_zero_counts(self, db_session):
        """Versions with no EPIC/BUG rows report zero counts, not ``None``."""
        user = _make_user(db_session)
        project = _make_project(db_session, user=user)
        service.create(db_session, project.id, _payload("1.0.0"), user.id)

        rows = service.list_versions(db_session, project.id)
        assert len(rows) == 1
        row = rows[0]
        assert row.epic_count == 0
        assert row.epics_done == 0
        assert row.bug_count == 0

    def test_list_empty_when_no_versions(self, db_session):
        """A project with no versions returns an empty list."""
        user = _make_user(db_session)
        project = _make_project(db_session, user=user)

        assert service.list_versions(db_session, project.id) == []


# ---------------------------------------------------------------------------
# release — the DESIGN.md §4.0 Rule 5 release gate
# ---------------------------------------------------------------------------


class TestRelease:
    """``release`` gate: reject if any EPIC not ``done``, else stamp release."""

    def test_release_no_epics_succeeds(self, db_session):
        """A version with zero EPICs passes the gate (empty AND is true)."""
        user = _make_user(db_session)
        project = _make_project(db_session, user=user)
        version = service.create(db_session, project.id, _payload("1.0.0"), user.id)

        released = service.release(db_session, version.id)

        assert released.status == "released"
        assert released.release_date == date.today()

    def test_release_all_epics_done_succeeds(self, db_session):
        """Every EPIC at ``done`` → release succeeds."""
        user = _make_user(db_session)
        project = _make_project(db_session, user=user)
        version = service.create(db_session, project.id, _payload("1.0.0"), user.id)
        _make_epic(db_session, project=project, version=version, number=1, status="done")
        _make_epic(db_session, project=project, version=version, number=2, status="done")

        released = service.release(db_session, version.id)

        assert released.status == "released"
        assert released.release_date == date.today()

    def test_release_blocked_lists_blocking_epics(self, db_session):
        """Non-``done`` EPICs are reported in the ``ValueError`` message."""
        user = _make_user(db_session)
        project = _make_project(db_session, user=user)
        version = service.create(db_session, project.id, _payload("1.0.0"), user.id)

        done_epic = _make_epic(db_session, project=project, version=version, number=1, status="done")
        in_progress_epic = _make_epic(
            db_session,
            project=project,
            version=version,
            number=2,
            status="in_progress",
        )
        planned_epic = _make_epic(db_session, project=project, version=version, number=3, status="planned")

        with pytest.raises(ValueError) as excinfo:
            service.release(db_session, version.id)

        message = str(excinfo.value)
        assert "blocking EPICs" in message
        # Blocking EPICs appear, the done EPIC does not.
        assert str(in_progress_epic.id) in message
        assert str(planned_epic.id) in message
        assert str(done_epic.id) not in message

        # Version state was NOT mutated — still ``planned``, no release_date.
        db_session.expire(version)
        assert version.status == "planned"
        assert version.release_date is None

    def test_release_already_released_raises(self, db_session):
        """Re-releasing a released version is rejected."""
        user = _make_user(db_session)
        project = _make_project(db_session, user=user)
        version = service.create(db_session, project.id, _payload("1.0.0"), user.id)
        service.release(db_session, version.id)

        with pytest.raises(ValueError, match="already released"):
            service.release(db_session, version.id)

    def test_release_missing_raises(self, db_session):
        """``release`` on a non-existent id raises ``ValueError``."""
        with pytest.raises(ValueError, match="not found"):
            service.release(db_session, uuid.uuid4())


# ---------------------------------------------------------------------------
# auto_activate — the DESIGN.md §4.0 Rule 4 lifecycle helper
# ---------------------------------------------------------------------------


class TestAutoActivate:
    """``auto_activate`` promotes ``planned → active``; no-op otherwise."""

    def test_auto_activate_from_planned_to_active(self, db_session):
        """A ``planned`` version becomes ``active``."""
        user = _make_user(db_session)
        project = _make_project(db_session, user=user)
        version = service.create(db_session, project.id, _payload("1.0.0"), user.id)
        assert version.status == "planned"

        result = service.auto_activate(db_session, version.id)

        assert result.id == version.id
        assert result.status == "active"

    def test_auto_activate_noop_when_active(self, db_session):
        """Already ``active`` → no change."""
        user = _make_user(db_session)
        project = _make_project(db_session, user=user)
        version = service.create(db_session, project.id, _payload("1.0.0"), user.id)
        # Promote once.
        service.auto_activate(db_session, version.id)

        result = service.auto_activate(db_session, version.id)

        assert result.status == "active"

    def test_auto_activate_noop_when_released(self, db_session):
        """Released versions must not regress."""
        user = _make_user(db_session)
        project = _make_project(db_session, user=user)
        version = service.create(db_session, project.id, _payload("1.0.0"), user.id)
        service.release(db_session, version.id)

        result = service.auto_activate(db_session, version.id)

        assert result.status == "released"

    def test_auto_activate_missing_raises(self, db_session):
        """``auto_activate`` on a non-existent id raises ``ValueError``."""
        with pytest.raises(ValueError, match="not found"):
            service.auto_activate(db_session, uuid.uuid4())


# ---------------------------------------------------------------------------
# Commit contract
# ---------------------------------------------------------------------------


class TestServiceCommitContract:
    """Service only ``flush``es — the outer transaction stays open."""

    def test_service_does_not_commit(self, db_session):
        """Service calls ``flush`` only; the fixture's outer transaction remains open."""
        user = _make_user(db_session)
        project = _make_project(db_session, user=user)
        created = service.create(db_session, project.id, _payload("1.0.0"), user.id)

        # ``in_transaction()`` must be True — commit would clear it.
        assert db_session.in_transaction()
        assert service.get_by_id(db_session, created.id).id == created.id


# ---------------------------------------------------------------------------
# Zadanie read/write (the version-page editor's source of truth)
# ---------------------------------------------------------------------------


class TestZadanie:
    """read_zadanie ↔ write_zadanie round-trip — the editor loads from the SAVED FILE (source of truth),
    NOT version.description (2026-06-30 fix: description was never written → empty editor on re-open)."""

    def test_read_empty_before_written(self, db_session, monkeypatch, tmp_path):
        user = _make_user(db_session)
        project = _make_project(db_session, user=user)
        version = service.create(db_session, project.id, _payload("0.1.0"), user.id)
        monkeypatch.setattr(service, "_PROJECTS_ROOT", tmp_path)
        assert service.read_zadanie(db_session, version.id) == ""  # not created yet → empty, never crashes

    def test_write_then_read_round_trips(self, db_session, monkeypatch, tmp_path):
        user = _make_user(db_session)
        project = _make_project(db_session, user=user)
        version = service.create(db_session, project.id, _payload("0.1.0"), user.id)
        monkeypatch.setattr(service, "_PROJECTS_ROOT", tmp_path)
        service.write_zadanie(db_session, version.id, "Zadanie: postav aplikáciu X.")
        assert service.read_zadanie(db_session, version.id) == "Zadanie: postav aplikáciu X."

    def test_read_reflects_a_direct_file_edit(self, db_session, monkeypatch, tmp_path):
        # The Director's case: the Zadanie was edited DIRECTLY on disk (an appended CSV-export spec). Because
        # the editor loads from the file, that edit IS reflected — loading from version.description would miss it.
        user = _make_user(db_session)
        project = _make_project(db_session, user=user)
        version = service.create(db_session, project.id, _payload("0.1.0"), user.id)
        monkeypatch.setattr(service, "_PROJECTS_ROOT", tmp_path)
        service.write_zadanie(db_session, version.id, "pôvodné zadanie")
        f = tmp_path / project.slug / "docs/specs/versions/v0.1.0/customer-requirements.md"
        f.write_text(f.read_text(encoding="utf-8") + "\n\nCSV-Export-Specifikacia\n…", encoding="utf-8")
        content = service.read_zadanie(db_session, version.id)
        assert "pôvodné zadanie" in content and "CSV-Export-Specifikacia" in content

    def test_read_missing_version_raises(self, db_session, monkeypatch, tmp_path):
        monkeypatch.setattr(service, "_PROJECTS_ROOT", tmp_path)
        with pytest.raises(ValueError):
            service.read_zadanie(db_session, uuid.uuid4())
