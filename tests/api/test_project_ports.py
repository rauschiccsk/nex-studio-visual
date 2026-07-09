"""Tests for port validation endpoints on the projects router.

Covers:

* ``GET /api/v1/projects/ports/check?port=N`` — availability check with
  range validation (10100–14999), conflict detection and ``project_id``
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

# KB-ghost follow-up (docs/specs/kb-ghost-followup.md Fix A): this module shares
# the projects router but only exercises the GET port endpoints today — no create,
# so no KB write. The mark is belt-and-suspenders: it inherits the shared tmp-KB
# isolation + real-KB sentinel so any future create-path test added here is
# covered by construction, and it keeps this module consistent with its
# create-touching siblings named in the follow-up spec.
pytestmark = pytest.mark.usefixtures("_isolate_create_project_kb")


@pytest.fixture()
def router_client(db_session):
    """Mount the projects router on a fresh app with the DB override."""
    app = FastAPI()
    app.include_router(projects_router, prefix="/api/v1/projects")

    def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    # M2.D.2 RBAC overrides — see tests/conftest.py for context.
    import uuid as _uuid_m2

    import bcrypt as _bcrypt

    from backend.core.security import (
        get_current_user as _gcu_m2,
    )
    from backend.core.security import (
        require_ha_or_above as _rha_m2,
    )
    from backend.core.security import (
        require_ri_role as _rri_m2,
    )
    from backend.core.security import (
        require_shu_or_above as _rshu_m2,
    )
    from backend.db.models.foundation import User as _UserM2

    _suffix_m2 = _uuid_m2.uuid4().hex[:8]
    _ri_m2 = _UserM2(
        username=f"ri_m2_{_suffix_m2}",
        email=f"ri_m2_{_suffix_m2}@test.local",
        password_hash=_bcrypt.hashpw(b"test", _bcrypt.gensalt(rounds=4)).decode(),
        role="ri",
        is_active=True,
    )
    db_session.add(_ri_m2)
    db_session.flush()

    def _override_user_m2() -> _UserM2:
        return _ri_m2

    app.dependency_overrides[_gcu_m2] = _override_user_m2
    app.dependency_overrides[_rri_m2] = _override_user_m2
    app.dependency_overrides[_rha_m2] = _override_user_m2
    app.dependency_overrides[_rshu_m2] = _override_user_m2

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
        "type": "standard",
        "auth_mode": "password",
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
        resp = router_client.get("/api/v1/projects/ports/check", params={"port": 10150})
        assert resp.status_code == 200
        body = resp.json()
        assert body["available"] is True
        assert body["conflict_project"] is None

    def test_unavailable_port(self, router_client, db_session, creator):
        """A port allocated to an existing project should be unavailable."""
        proj = _make_project(db_session, creator, backend_port=10150)
        resp = router_client.get("/api/v1/projects/ports/check", params={"port": 10150})
        assert resp.status_code == 200
        body = resp.json()
        assert body["available"] is False
        assert body["conflict_project"] == proj.name

    def test_unavailable_cross_type(self, router_client, db_session, creator):
        """A frontend port should conflict when checked against any type."""
        proj = _make_project(db_session, creator, frontend_port=10200)
        resp = router_client.get("/api/v1/projects/ports/check", params={"port": 10200})
        assert resp.status_code == 200
        body = resp.json()
        assert body["available"] is False
        assert body["conflict_project"] == proj.name

    def test_same_project_excluded(self, router_client, db_session, creator):
        """A project's own port should be available when project_id is passed."""
        proj = _make_project(db_session, creator, backend_port=10160)
        resp = router_client.get(
            "/api/v1/projects/ports/check",
            params={"port": 10160, "project_id": str(proj.id)},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["available"] is True
        assert body["conflict_project"] is None

    def test_below_range(self, router_client):
        """Ports below 10100 should be rejected (422)."""
        resp = router_client.get("/api/v1/projects/ports/check", params={"port": 10099})
        assert resp.status_code == 422

    def test_above_range(self, router_client):
        """Ports above 14999 should be rejected (422)."""
        resp = router_client.get("/api/v1/projects/ports/check", params={"port": 15000})
        assert resp.status_code == 422

    def test_boundary_min(self, router_client):
        """Port 10100 (range minimum) should be accepted."""
        resp = router_client.get("/api/v1/projects/ports/check", params={"port": 10100})
        assert resp.status_code == 200
        assert resp.json()["available"] is True

    def test_boundary_max(self, router_client):
        """Port 14999 (range maximum) should be accepted."""
        resp = router_client.get("/api/v1/projects/ports/check", params={"port": 14999})
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
        """Suggestion for each valid type should return a port in 10100–14999."""
        resp = router_client.get("/api/v1/projects/ports/suggest", params={"type": port_type})
        assert resp.status_code == 200
        port = resp.json()["suggested_port"]
        assert 10100 <= port <= 14999

    def test_suggest_skips_allocated(self, router_client, db_session, creator):
        """The suggested port should skip already-allocated ports."""
        _make_project(db_session, creator, backend_port=10100)
        resp = router_client.get("/api/v1/projects/ports/suggest", params={"type": "backend"})
        assert resp.status_code == 200
        assert resp.json()["suggested_port"] != 10100

    def test_suggest_invalid_type(self, router_client):
        """An invalid port type should return 422."""
        resp = router_client.get("/api/v1/projects/ports/suggest", params={"type": "redis"})
        assert resp.status_code == 422

    def test_suggest_missing_type_param(self, router_client):
        """Omitting the required type param should return 422."""
        resp = router_client.get("/api/v1/projects/ports/suggest")
        assert resp.status_code == 422


class TestSuggestPortBlockEndpoint:
    """GET /api/v1/projects/ports/suggest-block"""

    def test_empty_db_returns_range_min_with_block_size(self, router_client):
        """With no projects, the first free block starts at 10100 and is 10 wide."""
        resp = router_client.get("/api/v1/projects/ports/suggest-block")
        assert resp.status_code == 200
        body = resp.json()
        assert body == {"base": 10100, "block_size": 10}

    def test_first_block_occupied_returns_second(self, router_client, creator, db_session):
        """A single project in the first block pushes the suggestion to 10110."""
        _make_project(db_session, creator, backend_port=10105)
        resp = router_client.get("/api/v1/projects/ports/suggest-block")
        assert resp.status_code == 200
        assert resp.json() == {"base": 10110, "block_size": 10}

    def test_gap_block_preferred(self, router_client, creator, db_session):
        """Block 1 taken + Block 3 taken → Block 2 wins (first free)."""
        _make_project(db_session, creator, backend_port=10100)
        _make_project(db_session, creator, backend_port=10120)
        resp = router_client.get("/api/v1/projects/ports/suggest-block")
        assert resp.status_code == 200
        assert resp.json()["base"] == 10110
