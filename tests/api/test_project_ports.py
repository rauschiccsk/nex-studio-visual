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


class TestSuggestPortBlockEndpoint:
    """GET /api/v1/projects/ports/suggest-block"""

    def test_empty_db_returns_range_min_with_block_size(self, router_client):
        """With no projects, the first free block starts at 10100 and is 10 wide."""
        resp = router_client.get("/api/v1/projects/ports/suggest-block")
        assert resp.status_code == 200
        body = resp.json()
        assert body["base"] == 10100
        assert body["block_size"] == 10

    def test_first_block_occupied_returns_second(self, router_client, creator, db_session):
        """A single project in the first block pushes the suggestion to 10110."""
        _make_project(db_session, creator, backend_port=10105)
        resp = router_client.get("/api/v1/projects/ports/suggest-block")
        assert resp.status_code == 200
        body = resp.json()
        assert body["base"] == 10110
        assert body["block_size"] == 10

    def test_gap_block_preferred(self, router_client, creator, db_session):
        """Block 1 taken + Block 3 taken → Block 2 wins (first free)."""
        _make_project(db_session, creator, backend_port=10100)
        _make_project(db_session, creator, backend_port=10120)
        resp = router_client.get("/api/v1/projects/ports/suggest-block")
        assert resp.status_code == 200
        assert resp.json()["base"] == 10110


class TestPortEndpointsConsultTheHost:
    """The endpoints answer from the HOST too, not just the cockpit's table.

    Regression cover for the twelve-day silent double-book: the cockpit
    recorded nex-websites on frontend port 10111 while the container
    ``nex-manager-frontend`` was publishing 0.0.0.0:10111.
    """

    def test_suggest_block_skips_host_held_block(self, router_client, host_ports):
        host_ports[10100] = "some-neighbour"
        resp = router_client.get("/api/v1/projects/ports/suggest-block")
        assert resp.status_code == 200
        assert resp.json()["base"] == 10110

    def test_unconfigured_reservations_are_surfaced_to_the_operator(self, router_client):
        """The empty ``reserved_port_ranges`` default is no longer inert."""
        resp = router_client.get("/api/v1/projects/ports/suggest-block")
        assert resp.status_code == 200
        assert any("nie sú nastavené" in w for w in resp.json()["warnings"])


class TestPortEndpointsFailClosed:
    """An unverifiable port is reported as unknown and never suggested."""

    @pytest.fixture()
    def broken_probe(self, monkeypatch):
        from backend.services import port_registry

        def _raise(timeout=port_registry.HOST_PROBE_TIMEOUT_SECONDS):
            raise port_registry.HostProbeError("daemon unreachable")

        monkeypatch.setattr(port_registry, "_docker_published_ports", _raise)
        port_registry.invalidate_host_port_cache()
        return port_registry

    def test_suggest_block_refuses_with_503(self, router_client, broken_probe):
        resp = router_client.get("/api/v1/projects/ports/suggest-block")
        assert resp.status_code == 503
