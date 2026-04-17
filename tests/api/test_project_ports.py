"""Tests for port validation endpoints on the projects router.

Covers:

* ``GET /api/v1/projects/ports/check?port=N`` — availability check with
  range validation (9100–9299), conflict detection and ``project_id``
  exclusion.
* ``GET /api/v1/projects/ports/suggest?type=T`` — next-free-port
  suggestion for ``backend``, ``frontend`` and ``db`` types.
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


def _make_project(db_session, creator, **overrides) -> Project:
    """Insert a project with deterministic defaults and return it."""
    suffix = uuid.uuid4().hex[:8]
    attrs = {
        "name": f"Proj {suffix}",
        "slug": f"proj-{suffix}",
        "category": "singlemodule",
        "description": "Port test project",
        "created_by": creator.id,
    }
    attrs.update(overrides)
    project = Project(**attrs)
    db_session.add(project)
    db_session.flush()
    return project


class TestPortCheck:
    """GET /api/v1/projects/ports/check?port=N"""

    def test_available_port(self, router_client):
        """A port with no allocations should be reported as available."""
        resp = router_client.get("/api/v1/projects/ports/check", params={"port": 9150})
        assert resp.status_code == 200
        body = resp.json()
        assert body["available"] is True
        assert body["conflict_project"] is None

    def test_unavailable_port(self, router_client, db_session, creator):
        """A port allocated to an existing project should be unavailable."""
        proj = _make_project(db_session, creator, backend_port=9150)
        resp = router_client.get("/api/v1/projects/ports/check", params={"port": 9150})
        assert resp.status_code == 200
        body = resp.json()
        assert body["available"] is False
        assert body["conflict_project"] == proj.name

    def test_unavailable_cross_type(self, router_client, db_session, creator):
        """A frontend port should conflict when checked against any type."""
        proj = _make_project(db_session, creator, frontend_port=9200)
        resp = router_client.get("/api/v1/projects/ports/check", params={"port": 9200})
        assert resp.status_code == 200
        body = resp.json()
        assert body["available"] is False
        assert body["conflict_project"] == proj.name

    def test_same_project_excluded(self, router_client, db_session, creator):
        """A project's own port should be available when project_id is passed."""
        proj = _make_project(db_session, creator, backend_port=9160)
        resp = router_client.get(
            "/api/v1/projects/ports/check",
            params={"port": 9160, "project_id": str(proj.id)},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["available"] is True
        assert body["conflict_project"] is None

    def test_below_range(self, router_client):
        """Ports below 9100 should be rejected (422)."""
        resp = router_client.get("/api/v1/projects/ports/check", params={"port": 9099})
        assert resp.status_code == 422

    def test_above_range(self, router_client):
        """Ports above 9299 should be rejected (422)."""
        resp = router_client.get("/api/v1/projects/ports/check", params={"port": 9300})
        assert resp.status_code == 422

    def test_boundary_min(self, router_client):
        """Port 9100 (range minimum) should be accepted."""
        resp = router_client.get("/api/v1/projects/ports/check", params={"port": 9100})
        assert resp.status_code == 200
        assert resp.json()["available"] is True

    def test_boundary_max(self, router_client):
        """Port 9299 (range maximum) should be accepted."""
        resp = router_client.get("/api/v1/projects/ports/check", params={"port": 9299})
        assert resp.status_code == 200
        assert resp.json()["available"] is True

    def test_missing_port_param(self, router_client):
        """Omitting the required port param should return 422."""
        resp = router_client.get("/api/v1/projects/ports/check")
        assert resp.status_code == 422


class TestPortSuggest:
    """GET /api/v1/projects/ports/suggest?type=T"""

    @pytest.mark.parametrize("port_type", ["backend", "frontend", "db"])
    def test_suggest_returns_port_in_range(self, router_client, port_type):
        """Suggestion for each valid type should return a port in 9100–9299."""
        resp = router_client.get("/api/v1/projects/ports/suggest", params={"type": port_type})
        assert resp.status_code == 200
        port = resp.json()["suggested_port"]
        assert 9100 <= port <= 9299

    def test_suggest_skips_allocated(self, router_client, db_session, creator):
        """The suggested port should skip already-allocated ports."""
        _make_project(db_session, creator, backend_port=9100)
        resp = router_client.get("/api/v1/projects/ports/suggest", params={"type": "backend"})
        assert resp.status_code == 200
        assert resp.json()["suggested_port"] != 9100

    def test_suggest_invalid_type(self, router_client):
        """An invalid port type should return 422."""
        resp = router_client.get("/api/v1/projects/ports/suggest", params={"type": "redis"})
        assert resp.status_code == 422

    def test_suggest_missing_type_param(self, router_client):
        """Omitting the required type param should return 422."""
        resp = router_client.get("/api/v1/projects/ports/suggest")
        assert resp.status_code == 422
