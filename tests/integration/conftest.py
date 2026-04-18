"""Shared fixtures for integration tests.

Provides common helpers and pytest fixtures used across all integration
test modules (version lifecycle, release gate, uniqueness, etc.).
"""

from __future__ import annotations

import uuid

import bcrypt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.routes.epics import router as epics_router
from backend.api.routes.versions import router as versions_router
from backend.core.security import get_current_user, require_ri_role
from backend.db.models.foundation import User, UserSession
from backend.db.models.projects import ModuleDependency, Project, ProjectModule  # noqa: F401
from backend.db.session import get_db
from backend.main import app as main_app

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_user(db_session, *, role: str = "ri") -> User:
    """Create a test user with a unique username/email."""
    user = User(
        username=f"user_{uuid.uuid4().hex[:8]}",
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        password_hash="hashed_placeholder",
        role=role,
    )
    db_session.add(user)
    db_session.flush()
    return user


def make_project(db_session, *, owner: User) -> Project:
    """Create a test project with a unique slug."""
    suffix = uuid.uuid4().hex[:8]
    project = Project(
        name=f"Project {suffix}",
        slug=f"project-{suffix}",
        category="multimodule",
        description="Integration test project",
        created_by=owner.id,
    )
    db_session.add(project)
    db_session.flush()
    return project


def build_app(db_session, *, current_user: User) -> FastAPI:
    """Mount version and epic routers on a private app with DI overrides."""
    app = FastAPI()
    app.include_router(versions_router, prefix="/api/v1")
    app.include_router(epics_router, prefix="/api/v1/epics")

    def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db

    def _override_get_current_user() -> User:
        return current_user

    app.dependency_overrides[get_current_user] = _override_get_current_user

    def _override_require_ri_role() -> User:
        return current_user

    app.dependency_overrides[require_ri_role] = _override_require_ri_role

    return app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def ri_user(db_session) -> User:
    """Create an RI-role user for integration tests."""
    return make_user(db_session, role="ri")


@pytest.fixture()
def project(db_session, ri_user) -> Project:
    """Create a project owned by the RI user."""
    return make_project(db_session, owner=ri_user)


@pytest.fixture()
def client(db_session, ri_user):
    """TestClient with versions + epics routers and SAVEPOINT-isolated DB."""
    app = build_app(db_session, current_user=ri_user)
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Auth integration fixtures (shared by test_auth_flow / test_token_rotation)
# ---------------------------------------------------------------------------


@pytest.fixture()
def _seed_admin(db_session):
    """Seed admin user with bcrypt-hashed password into test DB."""
    password_hash = bcrypt.hashpw(b"Nex123", bcrypt.gensalt(rounds=4)).decode()
    user = User(
        username="admin",
        email="admin@isnex.eu",
        password_hash=password_hash,
        role="ri",
        is_active=True,
    )
    db_session.add(user)
    db_session.flush()

    session = UserSession(
        user_id=user.id,
        token_version=0,
    )
    db_session.add(session)
    db_session.flush()
    return user


@pytest.fixture()
def integration_client(db_session, _seed_admin):
    """TestClient wired to the real app with SAVEPOINT-isolated DB."""

    def _override_get_db():
        yield db_session

    main_app.dependency_overrides[get_db] = _override_get_db

    with TestClient(main_app, raise_server_exceptions=False) as c:
        yield c

    main_app.dependency_overrides.clear()
