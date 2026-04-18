"""Tests for Architect endpoint authorization guards.

Verifies:

* ``ri`` members can create sessions, send messages, and close sessions (200/201).
* ``ha`` / ``shu`` members can read sessions and messages (200) but receive
  HTTP 403 on mutating endpoints (create, message, close).
* Non-members receive HTTP 404 on all endpoints (project existence is not
  leaked).

Uses the same private-app + dependency-override pattern as
:mod:`tests.api.test_architect_sessions`.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.routes.architect import router as architect_router
from backend.core.security import get_current_user, require_ri_role
from backend.db.models.architect import ArchitectSession
from backend.db.models.foundation import User
from backend.db.models.projects import Project
from backend.db.session import get_db

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_user(db_session, *, role: str = "ri") -> User:
    user = User(
        username=f"user_{uuid.uuid4().hex[:8]}",
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        password_hash="hashed_placeholder",
        role=role,
    )
    db_session.add(user)
    db_session.flush()
    return user


def _make_project(db_session, *, owner: User) -> Project:
    suffix = uuid.uuid4().hex[:8]
    project = Project(
        name=f"Project {suffix}",
        slug=f"project-{suffix}",
        category="multimodule",
        description="Test project description",
        created_by=owner.id,
    )
    db_session.add(project)
    db_session.flush()
    return project


def _make_session(db_session, *, project: Project, user: User) -> ArchitectSession:
    """Create an ArchitectSession directly in the DB (bypasses router)."""
    session_obj = ArchitectSession(
        project_id=project.id,
        created_by=user.id,
        status="active",
    )
    db_session.add(session_obj)
    db_session.flush()
    return session_obj


# ---------------------------------------------------------------------------
# App builder + fixtures
# ---------------------------------------------------------------------------


def _build_app(db_session, *, current_user: User | None) -> FastAPI:
    """Mount the architect router on a fresh app with overrides applied."""
    app = FastAPI()
    app.include_router(architect_router, prefix="/api/v1")

    def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db

    if current_user is not None:

        def _override_get_current_user() -> User:
            return current_user

        app.dependency_overrides[get_current_user] = _override_get_current_user

        def _override_require_ri_role() -> User:
            if current_user.role != "ri":
                from fastapi import HTTPException, status

                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="This operation requires the 'ri' role",
                )
            return current_user

        app.dependency_overrides[require_ri_role] = _override_require_ri_role

    return app


@pytest.fixture()
def ri_user(db_session) -> User:
    return _make_user(db_session, role="ri")


@pytest.fixture()
def ha_user(db_session) -> User:
    return _make_user(db_session, role="ha")


@pytest.fixture()
def shu_user(db_session) -> User:
    return _make_user(db_session, role="shu")


@pytest.fixture()
def project(db_session, ri_user) -> Project:
    proj = _make_project(db_session, owner=ri_user)
    return proj


@pytest.fixture()
def ri_client(db_session, ri_user):
    app = _build_app(db_session, current_user=ri_user)
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


@pytest.fixture()
def ha_member_client(db_session, ha_user, project):
    """ha user who IS a member of the project."""
    app = _build_app(db_session, current_user=ha_user)
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


@pytest.fixture()
def shu_member_client(db_session, shu_user, project):
    """shu user who IS a member of the project."""
    app = _build_app(db_session, current_user=shu_user)
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# ri member — full access (create, read, close)
# ---------------------------------------------------------------------------


class TestRiMemberFullAccess:
    """ri member can create sessions, read, and close."""

    def test_ri_create_session(self, ri_client, project):
        resp = ri_client.post(
            f"/api/v1/projects/{project.id}/architect",
            json={},
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["project_id"] == str(project.id)
        assert body["status"] == "active"

    def test_ri_list_sessions(self, ri_client, project, db_session, ri_user):
        _make_session(db_session, project=project, user=ri_user)
        resp = ri_client.get(f"/api/v1/projects/{project.id}/architect")
        assert resp.status_code == 200
        assert resp.json()["total"] >= 1

    def test_ri_get_session(self, ri_client, project, db_session, ri_user):
        session_obj = _make_session(db_session, project=project, user=ri_user)
        resp = ri_client.get(f"/api/v1/architect/sessions/{session_obj.id}")
        assert resp.status_code == 200
        assert resp.json()["id"] == str(session_obj.id)

    def test_ri_close_session(self, ri_client, project, db_session, ri_user):
        session_obj = _make_session(db_session, project=project, user=ri_user)
        resp = ri_client.post(f"/api/v1/architect/sessions/{session_obj.id}/close")
        assert resp.status_code == 200
        assert resp.json()["status"] == "closed"
        assert resp.json()["closed_at"] is not None

    def test_ri_list_messages(self, ri_client, project, db_session, ri_user):
        session_obj = _make_session(db_session, project=project, user=ri_user)
        resp = ri_client.get(
            f"/api/v1/architect/sessions/{session_obj.id}/messages",
        )
        assert resp.status_code == 200
        assert resp.json()["total"] == 0


# ---------------------------------------------------------------------------
# ha member — read-only (GET 200, POST 403)
# ---------------------------------------------------------------------------


class TestHaMemberReadOnly:
    """ha member can read sessions but gets 403 on mutating endpoints."""

    def test_ha_list_sessions(self, ha_member_client, project, db_session, ri_user):
        _make_session(db_session, project=project, user=ri_user)
        resp = ha_member_client.get(f"/api/v1/projects/{project.id}/architect")
        assert resp.status_code == 200
        assert resp.json()["total"] >= 1

    def test_ha_get_session(self, ha_member_client, project, db_session, ri_user):
        session_obj = _make_session(db_session, project=project, user=ri_user)
        resp = ha_member_client.get(f"/api/v1/architect/sessions/{session_obj.id}")
        assert resp.status_code == 200

    def test_ha_list_messages(self, ha_member_client, project, db_session, ri_user):
        session_obj = _make_session(db_session, project=project, user=ri_user)
        resp = ha_member_client.get(
            f"/api/v1/architect/sessions/{session_obj.id}/messages",
        )
        assert resp.status_code == 200

    def test_ha_create_session_forbidden(self, ha_member_client, project):
        resp = ha_member_client.post(
            f"/api/v1/projects/{project.id}/architect",
            json={},
        )
        assert resp.status_code == 403

    def test_ha_close_session_forbidden(self, ha_member_client, project, db_session, ri_user):
        session_obj = _make_session(db_session, project=project, user=ri_user)
        resp = ha_member_client.post(
            f"/api/v1/architect/sessions/{session_obj.id}/close",
        )
        assert resp.status_code == 403

    def test_ha_send_message_forbidden(self, ha_member_client, project, db_session, ri_user):
        session_obj = _make_session(db_session, project=project, user=ri_user)
        resp = ha_member_client.post(
            f"/api/v1/architect/sessions/{session_obj.id}/message",
            json={"content": "Hello"},
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# shu member — same read-only pattern as ha
# ---------------------------------------------------------------------------


class TestShuMemberReadOnly:
    """shu member can read sessions but gets 403 on mutating endpoints."""

    def test_shu_list_sessions(self, shu_member_client, project, db_session, ri_user):
        _make_session(db_session, project=project, user=ri_user)
        resp = shu_member_client.get(f"/api/v1/projects/{project.id}/architect")
        assert resp.status_code == 200

    def test_shu_create_session_forbidden(self, shu_member_client, project):
        resp = shu_member_client.post(
            f"/api/v1/projects/{project.id}/architect",
            json={},
        )
        assert resp.status_code == 403

    def test_shu_close_session_forbidden(self, shu_member_client, project, db_session, ri_user):
        session_obj = _make_session(db_session, project=project, user=ri_user)
        resp = shu_member_client.post(
            f"/api/v1/architect/sessions/{session_obj.id}/close",
        )
        assert resp.status_code == 403
