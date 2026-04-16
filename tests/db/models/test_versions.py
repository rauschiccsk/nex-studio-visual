"""Tests for the Version model."""

import uuid
from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, ProgrammingError

from backend.db.models.bugs import Bug
from backend.db.models.foundation import User
from backend.db.models.projects import Project
from backend.db.models.tasks import Epic
from backend.db.models.versions import Version


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
    defaults = {
        "name": f"Project {uuid.uuid4().hex[:8]}",
        "slug": f"project-{uuid.uuid4().hex[:8]}",
        "category": "singlemodule",
        "description": "Test project description",
        "created_by": user.id,
    }
    defaults.update(overrides)
    project = Project(**defaults)
    db_session.add(project)
    db_session.flush()
    return project


def _make_version(db_session, *, project: Project | None = None, **overrides) -> Version:
    """Create a Version instance with sensible defaults."""
    if project is None:
        project = _make_project(db_session)
    defaults = {
        "project_id": project.id,
        "version_number": f"v{uuid.uuid4().hex[:6]}",
    }
    defaults.update(overrides)
    return Version(**defaults)


class TestVersionModel:
    """Unit tests for Version ORM model."""

    def test_create_version(self, db_session):
        """Can insert a valid version with default status."""
        version = _make_version(db_session)
        db_session.add(version)
        db_session.flush()

        assert version.id is not None
        assert version.created_at is not None
        assert version.updated_at is not None

    def test_tablename(self, db_session):
        """Table name must be 'versions'."""
        assert Version.__tablename__ == "versions"

    def test_status_defaults_planned(self, db_session):
        """status should default to 'planned' via server_default."""
        version = _make_version(db_session)
        db_session.add(version)
        db_session.flush()

        db_session.expire(version)
        assert version.status == "planned"

    def test_description_is_nullable(self, db_session):
        """description may be NULL."""
        version = _make_version(db_session, description=None)
        db_session.add(version)
        db_session.flush()
        assert version.description is None

    def test_description_accepts_text(self, db_session):
        """description accepts arbitrary text."""
        version = _make_version(db_session, description="Initial public release")
        db_session.add(version)
        db_session.flush()
        assert version.description == "Initial public release"

    def test_target_date_is_nullable(self, db_session):
        """target_date may be NULL."""
        version = _make_version(db_session, target_date=None)
        db_session.add(version)
        db_session.flush()
        assert version.target_date is None

    def test_target_date_accepts_value(self, db_session):
        """target_date accepts a date value."""
        target = date(2026, 6, 1)
        version = _make_version(db_session, target_date=target)
        db_session.add(version)
        db_session.flush()
        assert version.target_date == target

    def test_release_date_is_nullable(self, db_session):
        """release_date may be NULL."""
        version = _make_version(db_session, release_date=None)
        db_session.add(version)
        db_session.flush()
        assert version.release_date is None

    def test_release_date_accepts_value(self, db_session):
        """release_date accepts a date value."""
        released = date(2026, 6, 15)
        version = _make_version(db_session, release_date=released)
        db_session.add(version)
        db_session.flush()
        assert version.release_date == released

    def test_project_id_not_nullable(self, db_session):
        """project_id=NULL must be rejected."""
        _make_project(db_session)  # ensure at least one project exists
        version = Version(project_id=None, version_number="v1.0")
        db_session.add(version)
        with pytest.raises((IntegrityError, ProgrammingError)):
            db_session.flush()
        db_session.rollback()

    def test_version_number_not_nullable(self, db_session):
        """version_number=NULL must be rejected."""
        project = _make_project(db_session)
        version = Version(project_id=project.id, version_number=None)
        db_session.add(version)
        with pytest.raises((IntegrityError, ProgrammingError)):
            db_session.flush()
        db_session.rollback()

    def test_project_id_fk_valid(self, db_session):
        """project_id must reference an existing project."""
        fake_project_id = uuid.uuid4()
        version = Version(project_id=fake_project_id, version_number="v1.0")
        db_session.add(version)
        with pytest.raises((IntegrityError, ProgrammingError)):
            db_session.flush()
        db_session.rollback()

    def test_project_id_fk_cascade(self, db_session):
        """Deleting the parent project must cascade-delete its versions."""
        project = _make_project(db_session)
        version = _make_version(db_session, project=project)
        db_session.add(version)
        db_session.flush()
        version_id = version.id

        db_session.execute(
            text("DELETE FROM projects WHERE id = :id"),
            {"id": str(project.id)},
        )
        db_session.flush()

        remaining = db_session.execute(
            text("SELECT 1 FROM versions WHERE id = :id"),
            {"id": str(version_id)},
        ).fetchone()
        assert remaining is None


class TestVersionStatus:
    """Tests for the status enum CHECK constraint."""

    def test_invalid_status_rejected(self, db_session):
        """status values outside the allowed set must be rejected."""
        version = _make_version(db_session, status="shipped")
        db_session.add(version)
        with pytest.raises((IntegrityError, ProgrammingError)):
            db_session.flush()
        db_session.rollback()

    @pytest.mark.parametrize("status", ["planned", "active", "released"])
    def test_valid_statuses(self, db_session, status):
        """All three valid status values must be accepted."""
        version = _make_version(db_session, status=status)
        db_session.add(version)
        db_session.flush()
        assert version.status == status


class TestVersionUniqueConstraint:
    """Tests for the (project_id, version_number) UNIQUE constraint."""

    def test_same_number_different_project_allowed(self, db_session):
        """The same version_number can be used across different projects."""
        p1 = _make_project(db_session)
        p2 = _make_project(db_session)
        v1 = _make_version(db_session, project=p1, version_number="v1.0")
        v2 = _make_version(db_session, project=p2, version_number="v1.0")
        db_session.add_all([v1, v2])
        db_session.flush()
        assert v1.id != v2.id

    def test_duplicate_number_same_project_rejected(self, db_session):
        """A (project_id, version_number) pair must be unique."""
        project = _make_project(db_session)
        v1 = _make_version(db_session, project=project, version_number="v1.0")
        db_session.add(v1)
        db_session.flush()

        v2 = _make_version(db_session, project=project, version_number="v1.0")
        db_session.add(v2)
        with pytest.raises((IntegrityError, ProgrammingError)):
            db_session.flush()
        db_session.rollback()


class TestVersionRelationships:
    """Tests for Version ORM relationships."""

    def test_version_project_relationship(self, db_session):
        """version.project returns the owning Project."""
        project = _make_project(db_session)
        version = _make_version(db_session, project=project)
        db_session.add(version)
        db_session.flush()

        assert version.project is not None
        assert version.project.id == project.id

    def test_project_versions_relationship(self, db_session):
        """project.versions returns the collection of Versions."""
        project = _make_project(db_session)
        v1 = _make_version(db_session, project=project, version_number="v1.0")
        v2 = _make_version(db_session, project=project, version_number="v2.0")
        db_session.add_all([v1, v2])
        db_session.flush()
        db_session.refresh(project)

        version_ids = {v.id for v in project.versions}
        assert v1.id in version_ids
        assert v2.id in version_ids

    def test_back_populates_bidirectional(self, db_session):
        """Assigning via version.project keeps project.versions in sync."""
        project = _make_project(db_session)
        version = Version(version_number="v3.0")
        version.project = project
        db_session.add(version)
        db_session.flush()

        assert version.project_id == project.id
        assert version in project.versions

    def test_version_epics_relationship(self, db_session):
        """version.epics returns the collection of Epics targeting the version."""
        project = _make_project(db_session)
        version = _make_version(db_session, project=project)
        db_session.add(version)
        db_session.flush()

        epic1 = Epic(
            project_id=project.id,
            version_id=version.id,
            number=1,
            title="Epic One",
        )
        epic2 = Epic(
            project_id=project.id,
            version_id=version.id,
            number=2,
            title="Epic Two",
        )
        db_session.add_all([epic1, epic2])
        db_session.flush()
        db_session.refresh(version)

        epic_ids = {e.id for e in version.epics}
        assert epic1.id in epic_ids
        assert epic2.id in epic_ids

    def test_epic_version_back_populates(self, db_session):
        """epic.version returns the owning Version (inverse of Version.epics)."""
        project = _make_project(db_session)
        version = _make_version(db_session, project=project)
        db_session.add(version)
        db_session.flush()

        epic = Epic(
            project_id=project.id,
            version_id=version.id,
            number=10,
            title="Linked Epic",
        )
        db_session.add(epic)
        db_session.flush()

        assert epic.version is not None
        assert epic.version.id == version.id

    def test_version_bugs_relationship(self, db_session):
        """version.bugs returns the collection of Bugs targeting the version."""
        user = _make_user(db_session)
        project = _make_project(db_session, user=user)
        version = _make_version(db_session, project=project)
        db_session.add(version)
        db_session.flush()

        bug1 = Bug(
            project_id=project.id,
            version_id=version.id,
            bug_number=1,
            title="Bug One",
            description="desc",
            severity="minor",
            created_by=user.id,
        )
        bug2 = Bug(
            project_id=project.id,
            version_id=version.id,
            bug_number=2,
            title="Bug Two",
            description="desc",
            severity="major",
            created_by=user.id,
        )
        db_session.add_all([bug1, bug2])
        db_session.flush()
        db_session.refresh(version)

        bug_ids = {b.id for b in version.bugs}
        assert bug1.id in bug_ids
        assert bug2.id in bug_ids

    def test_bug_version_back_populates(self, db_session):
        """bug.version returns the owning Version (inverse of Version.bugs)."""
        user = _make_user(db_session)
        project = _make_project(db_session, user=user)
        version = _make_version(db_session, project=project)
        db_session.add(version)
        db_session.flush()

        bug = Bug(
            project_id=project.id,
            version_id=version.id,
            bug_number=99,
            title="Linked Bug",
            description="desc",
            severity="minor",
            created_by=user.id,
        )
        db_session.add(bug)
        db_session.flush()

        assert bug.version is not None
        assert bug.version.id == version.id

    def test_version_delete_restricted_by_epic(self, db_session):
        """Deleting a Version referenced by an Epic raises a FK violation (RESTRICT)."""
        project = _make_project(db_session)
        version = _make_version(db_session, project=project)
        db_session.add(version)
        db_session.flush()

        epic = Epic(
            project_id=project.id,
            version_id=version.id,
            number=42,
            title="Blocking Epic",
        )
        db_session.add(epic)
        db_session.flush()

        # Raw SQL DELETE — ORM session.delete() would first UPDATE the child
        # FK to NULL, masking the RESTRICT enforcement.
        with pytest.raises((IntegrityError, ProgrammingError)):
            db_session.execute(
                text("DELETE FROM versions WHERE id = :id"),
                {"id": str(version.id)},
            )
            db_session.flush()
        db_session.rollback()

    def test_version_delete_restricted_by_bug(self, db_session):
        """Deleting a Version referenced by a Bug raises a FK violation (RESTRICT)."""
        user = _make_user(db_session)
        project = _make_project(db_session, user=user)
        version = _make_version(db_session, project=project)
        db_session.add(version)
        db_session.flush()

        bug = Bug(
            project_id=project.id,
            version_id=version.id,
            bug_number=7,
            title="Blocking Bug",
            description="desc",
            severity="minor",
            created_by=user.id,
        )
        db_session.add(bug)
        db_session.flush()

        # Raw SQL DELETE — ORM session.delete() would first UPDATE the child
        # FK to NULL, masking the RESTRICT enforcement.
        with pytest.raises((IntegrityError, ProgrammingError)):
            db_session.execute(
                text("DELETE FROM versions WHERE id = :id"),
                {"id": str(version.id)},
            )
            db_session.flush()
        db_session.rollback()

    def test_version_delete_allowed_without_epics_or_bugs(self, db_session):
        """A Version with no referencing Epics/Bugs can be deleted."""
        project = _make_project(db_session)
        version = _make_version(db_session, project=project)
        db_session.add(version)
        db_session.flush()
        version_id = version.id

        db_session.execute(
            text("DELETE FROM versions WHERE id = :id"),
            {"id": str(version_id)},
        )
        db_session.flush()

        remaining = db_session.execute(
            text("SELECT 1 FROM versions WHERE id = :id"),
            {"id": str(version_id)},
        ).fetchone()
        assert remaining is None
