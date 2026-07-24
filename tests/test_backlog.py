"""Backlog API + lifecycle + realize-on-release (E2, CR-NS-041).

Covers CRUD, per-project REQ-N numbering, assign-to-version (+ cross-project guard), reject,
delete-only-when-open, read/write access split (shu reads, ha writes), and the additive
realize-on-release hook in the version service.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.routes.backlog import router as backlog_router
from backend.core.security import get_current_user
from backend.db.models.foundation import User
from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.db.session import get_db
from backend.schemas.backlog import BacklogItemCreate
from backend.services import backlog as backlog_service
from backend.services import version as version_service


def _make_user(db_session: Any, role: str = "ha") -> User:
    user = User(
        username=f"user_{uuid.uuid4().hex[:8]}",
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        password_hash="hashed_password_placeholder",
        role=role,
    )
    db_session.add(user)
    db_session.flush()
    return user


def _make_project(db_session: Any, owner: User) -> Project:
    project = Project(
        name=f"P {uuid.uuid4().hex[:8]}",
        slug=f"p-{uuid.uuid4().hex[:8]}",
        type="standard",
        auth_mode="password",
        description="d",
        created_by=owner.id,
    )
    db_session.add(project)
    db_session.flush()
    return project


def _make_version(db_session: Any, project: Project) -> Version:
    version = Version(project_id=project.id, version_number=f"1.{uuid.uuid4().hex[:4]}.0")
    db_session.add(version)
    db_session.flush()
    return version


def _client(db_session: Any, current: User) -> TestClient:
    app = FastAPI()
    app.include_router(backlog_router, prefix="/api/v1/backlog")

    def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_current_user] = lambda: current
    return TestClient(app)


# ── CRUD + numbering ────────────────────────────────────────────────────────


def test_create_and_list(db_session):
    user = _make_user(db_session, "ha")
    project = _make_project(db_session, user)
    client = _client(db_session, user)

    r = client.post(
        "/api/v1/backlog",
        json={"project_id": str(project.id), "title": "PDF rotation", "priority": "high"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["number"] == 1  # REQ-1
    assert body["status"] == "open"
    assert body["priority"] == "high"

    rows = client.get("/api/v1/backlog", params={"project_id": str(project.id)}).json()["items"]
    assert [x["number"] for x in rows] == [1]


def test_list_with_limit_200(db_session):
    """Regression (Director 2026-06-12): the backlog query allows ``limit=200`` but the shared
    ``PaginatedResponse`` envelope capped ``limit`` at ``le=100`` → a request with limit in
    (100, 200] 500'd on response validation. The FE lists with limit=200, so the envelope
    must accept it (the per-endpoint Query is the real bound)."""
    user = _make_user(db_session, "ha")
    project = _make_project(db_session, user)
    client = _client(db_session, user)
    client.post("/api/v1/backlog", json={"project_id": str(project.id), "title": "x"})

    r = client.get("/api/v1/backlog", params={"project_id": str(project.id), "limit": 200})
    assert r.status_code == 200, r.text
    assert r.json()["limit"] == 200


def test_per_project_numbering(db_session):
    user = _make_user(db_session, "ha")
    p1, p2 = _make_project(db_session, user), _make_project(db_session, user)
    client = _client(db_session, user)

    n1 = client.post("/api/v1/backlog", json={"project_id": str(p1.id), "title": "A"}).json()["number"]
    n2 = client.post("/api/v1/backlog", json={"project_id": str(p1.id), "title": "B"}).json()["number"]
    n3 = client.post("/api/v1/backlog", json={"project_id": str(p2.id), "title": "C"}).json()["number"]
    assert (n1, n2, n3) == (1, 2, 1)  # increments within p1; p2 restarts at 1


def test_assign_to_version_and_reject(db_session):
    user = _make_user(db_session, "ha")
    project = _make_project(db_session, user)
    version = _make_version(db_session, project)
    client = _client(db_session, user)

    item_id = client.post("/api/v1/backlog", json={"project_id": str(project.id), "title": "feature"}).json()["id"]

    # assign → included + version_id
    r = client.patch(f"/api/v1/backlog/{item_id}", json={"version_id": str(version.id)})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "included"
    assert r.json()["version_id"] == str(version.id)

    # reject (a different item)
    other = client.post("/api/v1/backlog", json={"project_id": str(project.id), "title": "x"}).json()["id"]
    assert client.patch(f"/api/v1/backlog/{other}", json={"status": "rejected"}).json()["status"] == "rejected"


def test_assign_cross_project_rejected(db_session):
    user = _make_user(db_session, "ha")
    p1, p2 = _make_project(db_session, user), _make_project(db_session, user)
    v2 = _make_version(db_session, p2)
    client = _client(db_session, user)

    item_id = client.post("/api/v1/backlog", json={"project_id": str(p1.id), "title": "x"}).json()["id"]
    # assigning p1's item to p2's version → 422
    assert client.patch(f"/api/v1/backlog/{item_id}", json={"version_id": str(v2.id)}).status_code == 422


def test_delete_only_when_open(db_session):
    user = _make_user(db_session, "ha")
    project = _make_project(db_session, user)
    version = _make_version(db_session, project)
    client = _client(db_session, user)

    open_id = client.post("/api/v1/backlog", json={"project_id": str(project.id), "title": "del-me"}).json()["id"]
    assert client.delete(f"/api/v1/backlog/{open_id}").status_code == 204

    included_id = client.post("/api/v1/backlog", json={"project_id": str(project.id), "title": "keep"}).json()["id"]
    client.patch(f"/api/v1/backlog/{included_id}", json={"version_id": str(version.id)})
    # included item is not deletable (history protection)
    assert client.delete(f"/api/v1/backlog/{included_id}").status_code == 422


def test_invalid_priority_rejected(db_session):
    user = _make_user(db_session, "ha")
    project = _make_project(db_session, user)
    client = _client(db_session, user)
    r = client.post("/api/v1/backlog", json={"project_id": str(project.id), "title": "x", "priority": "urgent"})
    assert r.status_code == 422


# ── access control (shu reads, ha writes) ───────────────────────────────────


def test_backlog_scoped_to_owner(db_session):
    """v4.0.43: a Junior reads + writes ONLY their OWN project's backlog; a non-owner Junior is 403."""
    owner = _make_user(db_session, "shu")
    project = _make_project(db_session, owner)
    backlog_service.create(db_session, BacklogItemCreate(project_id=project.id, title="seed"))
    db_session.flush()

    # the OWNER shu reads + writes their own backlog (owner-or-ri/ha tier)
    owner_client = _client(db_session, owner)
    assert owner_client.get("/api/v1/backlog", params={"project_id": str(project.id)}).status_code == 200
    created = owner_client.post("/api/v1/backlog", json={"project_id": str(project.id), "title": "mine"})
    assert created.status_code == 201

    # a DIFFERENT Junior (non-owner) can neither read nor write it
    other_client = _client(db_session, _make_user(db_session, "shu"))
    assert other_client.get("/api/v1/backlog", params={"project_id": str(project.id)}).status_code == 403
    assert (
        other_client.post("/api/v1/backlog", json={"project_id": str(project.id), "title": "nope"}).status_code == 403
    )


# ── realize-on-release (additive hook in version.release) ────────────────────


def test_realize_on_release(db_session):
    user = _make_user(db_session, "ha")
    project = _make_project(db_session, user)
    version = _make_version(db_session, project)

    included = backlog_service.create(db_session, BacklogItemCreate(project_id=project.id, title="ship-me"))
    backlog_service.assign_to_version(db_session, included.id, version.id)
    # an open item NOT assigned to this version — must stay open after release
    untouched = backlog_service.create(db_session, BacklogItemCreate(project_id=project.id, title="later"))
    db_session.flush()

    # release the version (no blocking epics) → realize hook fires
    version_service.release(db_session, version.id)
    db_session.flush()

    db_session.refresh(included)
    db_session.refresh(untouched)
    assert included.status == "realized"
    assert included.realized_at is not None
    # CR-NS-042 polish: the bulk UPDATE stamps updated_at too (Core UPDATE doesn't fire the ORM onupdate),
    # in the SAME statement as realized_at → identical timestamps.
    assert included.updated_at == included.realized_at
    assert untouched.status == "open"  # additive — only this version's included items transition


def test_realize_for_version_returns_count(db_session):
    user = _make_user(db_session, "ha")
    project = _make_project(db_session, user)
    version = _make_version(db_session, project)
    for title in ("a", "b"):
        it = backlog_service.create(db_session, BacklogItemCreate(project_id=project.id, title=title))
        backlog_service.assign_to_version(db_session, it.id, version.id)
    db_session.flush()

    assert backlog_service.realize_for_version(db_session, version.id) == 2
    # idempotent — a second call realizes nothing more
    assert backlog_service.realize_for_version(db_session, version.id) == 0
