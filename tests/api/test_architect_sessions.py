"""Tests for the project-scoped Architect session router.

Covers the endpoints in :mod:`backend.api.routes.architect`:

* ``POST /api/v1/projects/{project_id}/architect`` — create session (ri-only).
* ``GET  /api/v1/projects/{project_id}/architect`` — list sessions (authenticated).
* ``GET  /api/v1/architect/sessions/{session_id}`` — detail (authenticated).
* ``POST /api/v1/architect/sessions/{session_id}/close`` — close session (ri-only).

Uses the same private-app + dependency-override pattern as
:mod:`tests.api.test_versions`.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.routes.architect import router as architect_router
from backend.core.security import get_current_user, require_ri_role
from backend.db.models.foundation import User
from backend.db.models.projects import Project, ProjectModule
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


def _make_module(db_session, *, project: Project) -> ProjectModule:
    module = ProjectModule(
        project_id=project.id,
        code=f"M{uuid.uuid4().hex[:4].upper()}",
        name=f"Module {uuid.uuid4().hex[:8]}",
        category="General",
    )
    db_session.add(module)
    db_session.flush()
    return module


# ---------------------------------------------------------------------------
# App builder + fixtures
# ---------------------------------------------------------------------------


def _build_app(db_session, *, current_user: User | None) -> FastAPI:
    """Mount the architect router on a fresh app with overrides applied.

    ``current_user=None`` leaves auth dependencies untouched → returns 401.
    """
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
def project(db_session, ri_user) -> Project:
    proj = _make_project(db_session, owner=ri_user)
    return proj


@pytest.fixture()
def module(db_session, project) -> ProjectModule:
    return _make_module(db_session, project=project)


@pytest.fixture()
def ri_client(db_session, ri_user):
    app = _build_app(db_session, current_user=ri_user)
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


@pytest.fixture()
def ha_client(db_session, ha_user):
    app = _build_app(db_session, current_user=ha_user)
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


@pytest.fixture()
def anon_client(db_session):
    app = _build_app(db_session, current_user=None)
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# POST /projects/{project_id}/architect — create session
# ---------------------------------------------------------------------------


class TestCreateArchitectSession:
    def test_create_minimal(self, ri_client, project):
        resp = ri_client.post(
            f"/api/v1/projects/{project.id}/architect",
            json={},
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["project_id"] == str(project.id)
        assert body["status"] == "active"
        assert body["module_id"] is None
        assert body["id"]
        assert body["created_at"]

    def test_create_with_module(self, ri_client, project, module):
        resp = ri_client.post(
            f"/api/v1/projects/{project.id}/architect",
            json={"module_id": str(module.id)},
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["module_id"] == str(module.id)

    def test_create_sets_created_by_from_auth(self, ri_client, project, ri_user):
        resp = ri_client.post(
            f"/api/v1/projects/{project.id}/architect",
            json={},
        )
        assert resp.status_code == 201
        assert resp.json()["created_by"] == str(ri_user.id)

    def test_create_forbidden_for_ha(self, ha_client, project):
        resp = ha_client.post(
            f"/api/v1/projects/{project.id}/architect",
            json={},
        )
        assert resp.status_code == 403

    def test_create_unauthorized_without_token(self, anon_client, project):
        resp = anon_client.post(
            f"/api/v1/projects/{project.id}/architect",
            json={},
        )
        assert resp.status_code in (401, 403)


# ---------------------------------------------------------------------------
# GET /projects/{project_id}/architect — list sessions
# ---------------------------------------------------------------------------


class TestListArchitectSessions:
    def test_list_returns_paginated_envelope(self, ri_client, project):
        # Create 3 sessions
        for _ in range(3):
            ri_client.post(
                f"/api/v1/projects/{project.id}/architect",
                json={},
            ).raise_for_status()

        resp = ri_client.get(
            f"/api/v1/projects/{project.id}/architect",
            params={"skip": 0, "limit": 2},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert set(body.keys()) >= {"items", "total", "skip", "limit"}
        assert body["total"] >= 3
        assert body["skip"] == 0
        assert body["limit"] == 2
        assert len(body["items"]) == 2

    def test_list_pagination_second_page(self, ri_client, project):
        for _ in range(3):
            ri_client.post(
                f"/api/v1/projects/{project.id}/architect",
                json={},
            ).raise_for_status()

        page1 = ri_client.get(
            f"/api/v1/projects/{project.id}/architect",
            params={"skip": 0, "limit": 2},
        ).json()
        page2 = ri_client.get(
            f"/api/v1/projects/{project.id}/architect",
            params={"skip": 2, "limit": 2},
        ).json()

        page1_ids = {row["id"] for row in page1["items"]}
        page2_ids = {row["id"] for row in page2["items"]}
        assert page1_ids.isdisjoint(page2_ids)

    def test_list_scoped_to_project(self, ri_client, db_session, ri_user):
        proj_a = _make_project(db_session, owner=ri_user)
        proj_b = _make_project(db_session, owner=ri_user)

        ri_client.post(f"/api/v1/projects/{proj_a.id}/architect", json={}).raise_for_status()
        ri_client.post(f"/api/v1/projects/{proj_b.id}/architect", json={}).raise_for_status()

        resp = ri_client.get(f"/api/v1/projects/{proj_a.id}/architect")
        body = resp.json()
        assert body["total"] >= 1
        assert all(item["project_id"] == str(proj_a.id) for item in body["items"])

    def test_list_filter_by_status(self, ri_client, project):
        ri_client.post(
            f"/api/v1/projects/{project.id}/architect",
            json={},
        ).raise_for_status()

        resp = ri_client.get(
            f"/api/v1/projects/{project.id}/architect",
            params={"status": "active"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] >= 1
        assert all(item["status"] == "active" for item in body["items"])

    def test_list_filter_by_module(self, ri_client, project, module):
        ri_client.post(
            f"/api/v1/projects/{project.id}/architect",
            json={"module_id": str(module.id)},
        ).raise_for_status()
        ri_client.post(
            f"/api/v1/projects/{project.id}/architect",
            json={},
        ).raise_for_status()

        resp = ri_client.get(
            f"/api/v1/projects/{project.id}/architect",
            params={"module_id": str(module.id)},
        )
        body = resp.json()
        assert body["total"] >= 1
        assert all(item["module_id"] == str(module.id) for item in body["items"])

    def test_list_limit_over_100_returns_422(self, ri_client, project):
        resp = ri_client.get(
            f"/api/v1/projects/{project.id}/architect",
            params={"limit": 101},
        )
        assert resp.status_code == 422

    def test_list_unauthorized_without_token(self, anon_client, project):
        resp = anon_client.get(f"/api/v1/projects/{project.id}/architect")
        assert resp.status_code in (401, 403)


# ---------------------------------------------------------------------------
# GET /architect/sessions/{session_id} — detail
# ---------------------------------------------------------------------------


class TestGetArchitectSession:
    def test_get_by_id(self, ri_client, project):
        created = ri_client.post(
            f"/api/v1/projects/{project.id}/architect",
            json={},
        ).json()

        resp = ri_client.get(f"/api/v1/architect/sessions/{created['id']}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == created["id"]
        assert body["project_id"] == str(project.id)

    def test_get_missing_returns_404(self, ri_client):
        resp = ri_client.get(f"/api/v1/architect/sessions/{uuid.uuid4()}")
        assert resp.status_code == 404

    def test_get_invalid_uuid_returns_422(self, ri_client):
        resp = ri_client.get("/api/v1/architect/sessions/not-a-uuid")
        assert resp.status_code == 422

    def test_get_unauthorized_without_token(self, anon_client):
        resp = anon_client.get(f"/api/v1/architect/sessions/{uuid.uuid4()}")
        assert resp.status_code in (401, 403)


# ---------------------------------------------------------------------------
# POST /architect/sessions/{session_id}/close — close session
# ---------------------------------------------------------------------------


class TestCloseArchitectSession:
    def test_close_sets_status_and_closed_at(self, ri_client, project):
        created = ri_client.post(
            f"/api/v1/projects/{project.id}/architect",
            json={},
        ).json()
        assert created["status"] == "active"
        assert created["closed_at"] is None

        resp = ri_client.post(f"/api/v1/architect/sessions/{created['id']}/close")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "closed"
        assert body["closed_at"] is not None

    def test_close_missing_returns_404(self, ri_client):
        resp = ri_client.post(f"/api/v1/architect/sessions/{uuid.uuid4()}/close")
        assert resp.status_code == 404

    def test_close_forbidden_for_ha(self, ha_client):
        resp = ha_client.post(f"/api/v1/architect/sessions/{uuid.uuid4()}/close")
        assert resp.status_code == 403

    def test_close_unauthorized_without_token(self, anon_client):
        resp = anon_client.post(f"/api/v1/architect/sessions/{uuid.uuid4()}/close")
        assert resp.status_code in (401, 403)

    def test_close_persists_on_subsequent_get(self, ri_client, project):
        created = ri_client.post(
            f"/api/v1/projects/{project.id}/architect",
            json={},
        ).json()

        ri_client.post(f"/api/v1/architect/sessions/{created['id']}/close").raise_for_status()

        resp = ri_client.get(f"/api/v1/architect/sessions/{created['id']}")
        assert resp.status_code == 200
        assert resp.json()["status"] == "closed"
