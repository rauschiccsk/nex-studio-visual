"""Tests for project creation validation in POST /api/v1/projects.

Covers:

* Slug uniqueness → 409 Conflict.
* Port uniqueness → 409 Conflict (across all three port types).
* Port range (9100–9299) → 422 Unprocessable Entity.
* Invalid category → 422 (Pydantic rejects invalid Literal values).
* GitHub repo_url → accepted as-is, no existence check.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.routes.projects import router as projects_router
from backend.db.models.foundation import User
from backend.db.models.projects import Project
from backend.db.session import get_db


@pytest.fixture()
def router_client(db_session):
    """Mount the projects router on a fresh app with the DB override."""
    app = FastAPI()
    app.include_router(projects_router, prefix="/api/v1/projects")

    def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db

    with TestClient(app) as client:
        yield client

    app.dependency_overrides.clear()


@pytest.fixture()
def creator(db_session) -> User:
    """Persist a user that may own projects created in a test."""
    user = User(
        username=f"owner_{uuid.uuid4().hex[:8]}",
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        password_hash="hashed_password_placeholder",
        role="ri",
    )
    db_session.add(user)
    db_session.flush()
    return user


def _payload(creator_id, **overrides) -> dict:
    """Return a valid project-create payload with sensible defaults."""
    suffix = uuid.uuid4().hex[:8]
    body = {
        "name": f"Project {suffix}",
        "slug": f"project-{suffix}",
        "category": "singlemodule",
        "description": "Test project description",
        "created_by": str(creator_id),
    }
    body.update(overrides)
    return body


def _make_project(db_session, creator, **overrides) -> Project:
    """Insert a project directly via ORM for test setup."""
    suffix = uuid.uuid4().hex[:8]
    attrs = {
        "name": f"Existing {suffix}",
        "slug": f"existing-{suffix}",
        "category": "singlemodule",
        "description": "Existing project",
        "created_by": creator.id,
    }
    attrs.update(overrides)
    project = Project(**attrs)
    db_session.add(project)
    db_session.flush()
    return project


class TestSlugConflict:
    """POST /api/v1/projects — slug uniqueness validation."""

    def test_duplicate_slug_returns_409(self, router_client, creator, db_session):
        """Creating a project with an existing slug should return 409."""
        _make_project(db_session, creator, slug="taken-slug")
        payload = _payload(creator.id, slug="taken-slug")
        resp = router_client.post("/api/v1/projects", json=payload)
        assert resp.status_code == 409
        assert "slug" in resp.json()["detail"].lower() or "already exists" in resp.json()["detail"].lower()

    def test_unique_slug_succeeds(self, router_client, creator):
        """A unique slug should create the project successfully."""
        payload = _payload(creator.id, slug="unique-slug-abc")
        resp = router_client.post("/api/v1/projects", json=payload)
        assert resp.status_code == 201
        assert resp.json()["slug"] == "unique-slug-abc"


class TestPortConflict:
    """POST /api/v1/projects — port uniqueness validation (409)."""

    def test_backend_port_conflict(self, router_client, creator, db_session):
        """A backend_port already allocated should return 409 with PortConflictError body."""
        _make_project(db_session, creator, backend_port=9150)
        payload = _payload(creator.id, backend_port=9150)
        resp = router_client.post("/api/v1/projects", json=payload)
        assert resp.status_code == 409
        error = resp.json()["detail"]
        assert error["port"] == 9150
        assert "9150" in error["detail"]

    def test_frontend_port_conflict(self, router_client, creator, db_session):
        """A frontend_port already allocated should return 409 with PortConflictError body."""
        _make_project(db_session, creator, frontend_port=9200)
        payload = _payload(creator.id, frontend_port=9200)
        resp = router_client.post("/api/v1/projects", json=payload)
        assert resp.status_code == 409
        error = resp.json()["detail"]
        assert error["port"] == 9200
        assert "9200" in error["detail"]

    def test_db_port_conflict(self, router_client, creator, db_session):
        """A db_port already allocated should return 409 with PortConflictError body."""
        _make_project(db_session, creator, db_port=9180)
        payload = _payload(creator.id, db_port=9180)
        resp = router_client.post("/api/v1/projects", json=payload)
        assert resp.status_code == 409
        error = resp.json()["detail"]
        assert error["port"] == 9180
        assert "9180" in error["detail"]

    def test_cross_type_port_conflict(self, router_client, creator, db_session):
        """A port used as backend in one project should conflict when used as frontend."""
        _make_project(db_session, creator, backend_port=9170)
        payload = _payload(creator.id, frontend_port=9170)
        resp = router_client.post("/api/v1/projects", json=payload)
        assert resp.status_code == 409
        error = resp.json()["detail"]
        assert error["port"] == 9170
        assert "9170" in error["detail"]

    def test_no_port_conflict_when_ports_differ(self, router_client, creator, db_session):
        """Different ports should not conflict."""
        _make_project(db_session, creator, backend_port=9150)
        payload = _payload(creator.id, backend_port=9151)
        resp = router_client.post("/api/v1/projects", json=payload)
        assert resp.status_code == 201

    def test_null_ports_no_conflict(self, router_client, creator):
        """Projects without ports should not trigger port conflict."""
        payload = _payload(creator.id)
        resp = router_client.post("/api/v1/projects", json=payload)
        assert resp.status_code == 201


class TestPortRange:
    """POST /api/v1/projects — port range validation (9100–9299, 422)."""

    def test_backend_port_below_range(self, router_client, creator):
        """A backend_port below 9100 should return 422."""
        payload = _payload(creator.id, backend_port=9099)
        resp = router_client.post("/api/v1/projects", json=payload)
        assert resp.status_code == 422
        assert "9099" in resp.json()["detail"]

    def test_frontend_port_above_range(self, router_client, creator):
        """A frontend_port above 9299 should return 422."""
        payload = _payload(creator.id, frontend_port=9300)
        resp = router_client.post("/api/v1/projects", json=payload)
        assert resp.status_code == 422
        assert "9300" in resp.json()["detail"]

    def test_db_port_below_range(self, router_client, creator):
        """A db_port below 9100 should return 422."""
        payload = _payload(creator.id, db_port=8000)
        resp = router_client.post("/api/v1/projects", json=payload)
        assert resp.status_code == 422
        assert "8000" in resp.json()["detail"]

    def test_boundary_min_accepted(self, router_client, creator):
        """Port 9100 (range minimum) should be accepted."""
        payload = _payload(creator.id, backend_port=9100)
        resp = router_client.post("/api/v1/projects", json=payload)
        assert resp.status_code == 201
        assert resp.json()["backend_port"] == 9100

    def test_boundary_max_accepted(self, router_client, creator):
        """Port 9299 (range maximum) should be accepted."""
        payload = _payload(creator.id, frontend_port=9299)
        resp = router_client.post("/api/v1/projects", json=payload)
        assert resp.status_code == 201
        assert resp.json()["frontend_port"] == 9299


class TestInvalidCategory:
    """POST /api/v1/projects — category validation (422)."""

    def test_invalid_category_returns_422(self, router_client, creator):
        """A category value not in ('singlemodule', 'multimodule') should return 422."""
        payload = _payload(creator.id, category="invalid")
        resp = router_client.post("/api/v1/projects", json=payload)
        assert resp.status_code == 422

    def test_single_typo_returns_422(self, router_client, creator):
        """'single' (without 'module') is not a valid category."""
        payload = _payload(creator.id, category="single")
        resp = router_client.post("/api/v1/projects", json=payload)
        assert resp.status_code == 422

    def test_valid_singlemodule_accepted(self, router_client, creator):
        """'singlemodule' is a valid category."""
        payload = _payload(creator.id, category="singlemodule")
        resp = router_client.post("/api/v1/projects", json=payload)
        assert resp.status_code == 201

    def test_valid_multimodule_accepted(self, router_client, creator):
        """'multimodule' is a valid category."""
        payload = _payload(creator.id, category="multimodule")
        resp = router_client.post("/api/v1/projects", json=payload)
        assert resp.status_code == 201


class TestGitHubRepoValidation:
    """POST /api/v1/projects — GitHub repo_url is stored as metadata only.

    GitHub repo existence is NOT validated at project creation time.
    In the NEX Studio workflow a project is registered before the repo is
    created, so validation would block legitimate use.  repo_url is accepted
    as-is regardless of whether the repository exists on GitHub.
    """

    def test_nonexistent_repo_accepted(self, router_client, creator):
        """A repo_url for a non-existent repo is stored without validation."""
        payload = _payload(creator.id, repo_url="nonexistent/repo")
        resp = router_client.post("/api/v1/projects", json=payload)
        assert resp.status_code == 201
        assert resp.json()["repo_url"] == "nonexistent/repo"

    def test_any_repo_url_accepted(self, router_client, creator):
        """Any org/repo string is accepted — existence check is not performed."""
        payload = _payload(creator.id, repo_url="valid-org/valid-repo")
        resp = router_client.post("/api/v1/projects", json=payload)
        assert resp.status_code == 201
        assert resp.json()["repo_url"] == "valid-org/valid-repo"

    def test_null_repo_url_accepted(self, router_client, creator):
        """When repo_url is omitted the project is created with repo_url=null."""
        payload = _payload(creator.id)
        assert "repo_url" not in payload
        resp = router_client.post("/api/v1/projects", json=payload)
        assert resp.status_code == 201
        assert resp.json()["repo_url"] is None
