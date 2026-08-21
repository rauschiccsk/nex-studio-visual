"""PATCH /projects/{id} must scrutinise ports exactly like POST does.

Until v4.0.90 it did not check them at all. That made the one path capable of REPAIRING a
bad allocation also the one path capable of writing a port the create route refuses — a
reserved block, or one another project already holds. Found while fixing nex-websites,
which the cockpit had recorded on 10110/10111/10112, inside NEX Automat's reserved block.
"""

from __future__ import annotations

import uuid

import bcrypt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.routes.projects import router as projects_router
from backend.core.security import (
    get_current_user,
    require_ha_or_above,
    require_ri_role,
    require_shu_or_above,
)
from backend.db.models.foundation import User
from backend.db.models.projects import Project
from backend.db.session import get_db
from backend.services import port_registry

pytestmark = pytest.mark.usefixtures("_isolate_create_project_kb")


@pytest.fixture()
def owner(db_session) -> User:
    """The authenticated user AND the projects' creator — the ownership model grants access
    on ``created_by``, so a client authenticated as somebody else gets a 403."""
    suffix = uuid.uuid4().hex[:8]
    user = User(
        username=f"owner_{suffix}",
        email=f"owner_{suffix}@test.local",
        password_hash=bcrypt.hashpw(b"test", bcrypt.gensalt(rounds=4)).decode(),
        role="ri",
        is_active=True,
    )
    db_session.add(user)
    db_session.flush()
    return user


@pytest.fixture()
def client(db_session, owner):
    app = FastAPI()
    app.include_router(projects_router, prefix="/api/v1/projects")

    def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    for dep in (get_current_user, require_ri_role, require_ha_or_above, require_shu_or_above):
        app.dependency_overrides[dep] = lambda: owner
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _project(db_session, owner, *, base: int) -> Project:
    suffix = uuid.uuid4().hex[:6]
    project = Project(
        name=f"projekt-{suffix}",
        slug=f"projekt-{suffix}",
        description="testovací projekt",
        type="standard",
        auth_mode="password",
        status="active",
        backend_port=base,
        frontend_port=base + 1,
        db_port=base + 2,
        created_by=owner.id,
    )
    db_session.add(project)
    db_session.flush()
    return project


def test_update_rejects_a_reserved_port(client, db_session, owner, monkeypatch) -> None:
    monkeypatch.setattr(port_registry, "_ranges_from_registry_file", lambda: (((10110, 10159),), (), True))
    project = _project(db_session, owner, base=10300)
    resp = client.patch(
        f"/api/v1/projects/{project.id}",
        json={"backend_port": 10120, "frontend_port": 10121, "db_port": 10122},
    )
    assert resp.status_code == 422, resp.text
    assert "10110-10159" in resp.json()["detail"]


def test_update_rejects_a_port_another_project_holds(client, db_session, owner) -> None:
    _project(db_session, owner, base=10310)
    mover = _project(db_session, owner, base=10320)
    resp = client.patch(f"/api/v1/projects/{mover.id}", json={"backend_port": 10310})
    assert resp.status_code == 409, resp.text


def test_update_allows_a_project_to_keep_its_own_ports(client, db_session, owner) -> None:
    """A project's own ports must not read as a conflict with itself — otherwise every edit
    that merely re-sends them would be refused."""
    project = _project(db_session, owner, base=10330)
    resp = client.patch(
        f"/api/v1/projects/{project.id}",
        json={"backend_port": 10330, "frontend_port": 10331, "db_port": 10332},
    )
    assert resp.status_code == 200, resp.text


def test_update_without_ports_is_untouched_by_the_check(client, db_session, owner) -> None:
    project = _project(db_session, owner, base=10340)
    resp = client.patch(f"/api/v1/projects/{project.id}", json={"description": "nový popis"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["description"] == "nový popis"
