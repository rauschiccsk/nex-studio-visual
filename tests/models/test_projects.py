"""Tests for the projects module models (Project, ProjectModule, ModuleDependency).

ProjectMember has been removed — no membership tests belong here.
"""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, ProgrammingError

from backend.db.models.foundation import User
from backend.db.models.projects import ModuleDependency, Project, ProjectModule


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


def _make_module(db_session, *, project: Project | None = None, **overrides) -> ProjectModule:
    """Create and persist a ProjectModule for FK references."""
    if project is None:
        project = _make_project(db_session)
    defaults = {
        "project_id": project.id,
        "code": f"M{uuid.uuid4().hex[:4].upper()}",
        "name": f"Module {uuid.uuid4().hex[:8]}",
        "category": "business",
    }
    defaults.update(overrides)
    module = ProjectModule(**defaults)
    db_session.add(module)
    db_session.flush()
    return module


class TestProjectModel:
    """Unit tests for Project ORM model."""

    def test_create_project(self, db_session):
        """Can insert a valid project."""
        project = _make_project(db_session)
        assert project.id is not None
        assert project.created_at is not None

    def test_status_defaults_active(self, db_session):
        """status should default to 'active' via server_default."""
        project = _make_project(db_session)
        db_session.expire(project)
        assert project.status == "active"

    def test_guardian_enabled_defaults_false(self, db_session):
        """guardian_enabled should default to False via server_default."""
        project = _make_project(db_session)
        db_session.expire(project)
        assert project.guardian_enabled is False

    def test_unique_name(self, db_session):
        """Duplicate name must be rejected."""
        user = _make_user(db_session)
        _make_project(db_session, user=user, name="Duplicate Name")
        p2 = Project(
            name="Duplicate Name",
            slug=f"slug-{uuid.uuid4().hex[:8]}",
            category="singlemodule",
            description="desc",
            created_by=user.id,
        )
        db_session.add(p2)
        with pytest.raises((IntegrityError, ProgrammingError)):
            db_session.flush()
        db_session.rollback()

    def test_unique_slug(self, db_session):
        """Duplicate slug must be rejected."""
        user = _make_user(db_session)
        _make_project(db_session, user=user, slug="dup-slug")
        p2 = Project(
            name=f"Name {uuid.uuid4().hex[:8]}",
            slug="dup-slug",
            category="singlemodule",
            description="desc",
            created_by=user.id,
        )
        db_session.add(p2)
        with pytest.raises((IntegrityError, ProgrammingError)):
            db_session.flush()
        db_session.rollback()

    def test_category_check_constraint(self, db_session):
        """Invalid category value must be rejected."""
        with pytest.raises((IntegrityError, ProgrammingError)):
            _make_project(db_session, category="invalid")
        db_session.rollback()

    def test_created_by_fk_restrict(self, db_session):
        """Deleting a user referenced by project.created_by must be blocked (RESTRICT)."""
        user = _make_user(db_session)
        _make_project(db_session, user=user)

        with pytest.raises((IntegrityError, ProgrammingError)):
            db_session.execute(
                text("DELETE FROM users WHERE id = :id"),
                {"id": str(user.id)},
            )
        db_session.rollback()


class TestProjectModuleModel:
    """Unit tests for ProjectModule ORM model."""

    def test_create_module(self, db_session):
        """Can insert a valid project module."""
        module = _make_module(db_session)
        assert module.id is not None
        assert module.created_at is not None

    def test_status_defaults_planned(self, db_session):
        """status should default to 'planned' via server_default."""
        module = _make_module(db_session)
        db_session.expire(module)
        assert module.status == "planned"

    def test_unique_project_code(self, db_session):
        """Duplicate (project_id, code) pair must be rejected."""
        project = _make_project(db_session)
        _make_module(db_session, project=project, code="DUP1")
        m2 = ProjectModule(
            project_id=project.id,
            code="DUP1",
            name="Another module",
            category="business",
        )
        db_session.add(m2)
        with pytest.raises((IntegrityError, ProgrammingError)):
            db_session.flush()
        db_session.rollback()

    def test_cascade_delete_project(self, db_session):
        """Deleting a project must cascade-delete its modules."""
        project = _make_project(db_session)
        _make_module(db_session, project=project)
        project_id = project.id

        db_session.execute(
            text("DELETE FROM projects WHERE id = :id"),
            {"id": str(project_id)},
        )
        db_session.flush()

        result = db_session.execute(
            text("SELECT count(*) FROM project_modules WHERE project_id = :id"),
            {"id": str(project_id)},
        )
        assert result.scalar() == 0


class TestModuleDependencyModel:
    """Unit tests for ModuleDependency ORM model."""

    def test_create_dependency(self, db_session):
        """Can insert a valid module dependency."""
        project = _make_project(db_session)
        m1 = _make_module(db_session, project=project, code="MOD1")
        m2 = _make_module(db_session, project=project, code="MOD2")

        dep = ModuleDependency(module_id=m1.id, depends_on_module_id=m2.id)
        db_session.add(dep)
        db_session.flush()
        assert dep.id is not None

    def test_unique_dependency(self, db_session):
        """Duplicate (module_id, depends_on_module_id) pair must be rejected."""
        project = _make_project(db_session)
        m1 = _make_module(db_session, project=project, code="MOD1")
        m2 = _make_module(db_session, project=project, code="MOD2")

        dep1 = ModuleDependency(module_id=m1.id, depends_on_module_id=m2.id)
        db_session.add(dep1)
        db_session.flush()

        dep2 = ModuleDependency(module_id=m1.id, depends_on_module_id=m2.id)
        db_session.add(dep2)
        with pytest.raises((IntegrityError, ProgrammingError)):
            db_session.flush()
        db_session.rollback()

    def test_cascade_delete_module(self, db_session):
        """Deleting a module must cascade-delete its dependencies."""
        project = _make_project(db_session)
        m1 = _make_module(db_session, project=project, code="MOD1")
        m2 = _make_module(db_session, project=project, code="MOD2")
        dep = ModuleDependency(module_id=m1.id, depends_on_module_id=m2.id)
        db_session.add(dep)
        db_session.flush()

        db_session.execute(
            text("DELETE FROM project_modules WHERE id = :id"),
            {"id": str(m1.id)},
        )
        db_session.flush()

        result = db_session.execute(
            text("SELECT count(*) FROM module_dependencies WHERE module_id = :id"),
            {"id": str(m1.id)},
        )
        assert result.scalar() == 0
