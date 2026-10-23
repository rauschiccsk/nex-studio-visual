"""Tests for the Version REST router.

Covers the DESIGN.md §2.6 *Version Management* contract end-to-end via a
``TestClient`` mounted on a private FastAPI app — the same isolation
pattern the cockpit's router tests use. Mounting on a private app keeps this file
independent of ``backend.main.app``'s router registration order and lets
us swap the auth dependency in / out per test.

Coverage:

* ``GET /api/v1/projects/{project_id}/versions`` — list ordered by
  ``version_number DESC`` with aggregate counts attached.
* ``GET /api/v1/versions/{version_id}`` — detail with ``epics``
  eagerly loaded (verified via the aggregate counts; the schema
  itself does not embed the relationships, but the service preloads them
  so the same trip can power downstream UI calls).
* ``POST /api/v1/projects/{project_id}/versions`` — happy path + 403
  when the caller is not ``ri``.
* ``PATCH /api/v1/versions/{version_id}`` — happy path + 403 when the
  caller is not ``ri``.
* ``POST /api/v1/versions/{version_id}/release`` —
    * 200 + ``status='released'`` + ``release_date`` stamped on success.
    * 422 + ``blocking_epic_ids`` payload when one or more EPICs are not
      ``done`` (DESIGN.md §4.0 Rule 5).
    * 403 when the caller is not ``ri``.
* Auth tests — every endpoint returns 401 without an authenticated user.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.routes.versions import router as versions_router
from backend.core.security import get_current_user, require_ri_role
from backend.db.models.foundation import User
from backend.db.models.projects import Project
from backend.db.models.tasks import Epic
from backend.db.models.versions import Version
from backend.db.session import get_db

# ---------------------------------------------------------------------------
# Fixtures
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
        type="standard",
        auth_mode="password",
        description="Test project description",
        created_by=owner.id,
    )
    db_session.add(project)
    db_session.flush()
    return project


def _make_version(db_session, *, project: Project, version_number: str, name: str | None = None) -> Version:
    version = Version(
        project_id=project.id,
        version_number=version_number,
        name=name,
    )
    db_session.add(version)
    db_session.flush()
    return version


def _make_epic(db_session, *, project: Project, version: Version, number: int, status_: str = "planned") -> Epic:
    epic = Epic(
        project_id=project.id,
        version_id=version.id,
        number=number,
        title=f"Epic {number}",
        status=status_,
    )
    db_session.add(epic)
    db_session.flush()
    return epic


@pytest.fixture()
def ri_user(db_session) -> User:
    """An ``ri``-role user — passes every authorization check."""
    return _make_user(db_session, role="ri")


@pytest.fixture()
def ha_user(db_session) -> User:
    """An ``ha``-role user — authenticated, but rejected from ri-only endpoints."""
    return _make_user(db_session, role="ha")


@pytest.fixture()
def project(db_session, ri_user) -> Project:
    return _make_project(db_session, owner=ri_user)


def _build_app(db_session, *, current_user: User | None) -> FastAPI:
    """Mount the versions router on a fresh app with overrides applied.

    ``current_user=None`` leaves the auth dependencies untouched so the
    bare ``HTTPBearer(auto_error=False)`` path runs end-to-end and
    returns 401 (the auth-test scenario).
    """
    app = FastAPI()
    app.include_router(versions_router, prefix="/api/v1")

    def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db

    if current_user is not None:

        def _override_get_current_user() -> User:
            return current_user

        app.dependency_overrides[get_current_user] = _override_get_current_user

        # ``require_ri_role`` calls ``get_current_user`` under the hood,
        # but we override it explicitly too so the role check fires
        # against the test user directly without depending on the
        # transitive override.
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
def ri_client(db_session, ri_user):
    """``TestClient`` authenticated as an ``ri`` user."""
    app = _build_app(db_session, current_user=ri_user)
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


@pytest.fixture()
def ha_client(db_session, ha_user):
    """``TestClient`` authenticated as a non-``ri`` user."""
    app = _build_app(db_session, current_user=ha_user)
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


@pytest.fixture()
def anon_client(db_session):
    """``TestClient`` with no auth override → real bearer scheme runs."""
    app = _build_app(db_session, current_user=None)
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# GET /projects/{project_id}/versions — list
# ---------------------------------------------------------------------------


class TestListVersions:
    def test_list_returns_versions_ordered_desc(self, ri_client, db_session, project):
        _make_version(db_session, project=project, version_number="1.0.0")
        _make_version(db_session, project=project, version_number="1.1.0")
        _make_version(db_session, project=project, version_number="2.0.0")

        resp = ri_client.get(f"/api/v1/projects/{project.id}/versions")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert isinstance(body, list)
        numbers = [row["version_number"] for row in body]
        assert numbers == ["2.0.0", "1.1.0", "1.0.0"]
        # Aggregate counts default to 0 with no EPICs.
        assert all(row["epic_count"] == 0 for row in body)
        assert all(row["epics_done"] == 0 for row in body)

    def test_list_attaches_aggregate_counts(self, ri_client, db_session, project, ri_user):
        version = _make_version(db_session, project=project, version_number="1.0.0")
        _make_epic(db_session, project=project, version=version, number=1, status_="done")
        _make_epic(db_session, project=project, version=version, number=2, status_="in_progress")

        resp = ri_client.get(f"/api/v1/projects/{project.id}/versions")
        assert resp.status_code == 200, resp.text
        rows = resp.json()
        assert len(rows) == 1
        row = rows[0]
        assert row["epic_count"] == 2
        assert row["epics_done"] == 1

    def test_list_empty_returns_empty_list(self, ri_client, project):
        resp = ri_client.get(f"/api/v1/projects/{project.id}/versions")
        assert resp.status_code == 200, resp.text
        assert resp.json() == []

    def test_list_unauthenticated_returns_401(self, anon_client, project):
        resp = anon_client.get(f"/api/v1/projects/{project.id}/versions")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET /versions/{version_id} — detail
# ---------------------------------------------------------------------------


class TestGetVersion:
    def test_get_returns_detail(self, ri_client, db_session, project):
        version = _make_version(
            db_session,
            project=project,
            version_number="1.0.0",
            name="Pilot release",
        )

        resp = ri_client.get(f"/api/v1/versions/{version.id}")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["id"] == str(version.id)
        assert body["project_id"] == str(project.id)
        assert body["version_number"] == "1.0.0"
        assert body["name"] == "Pilot release"
        assert body["status"] == "planned"
        assert body["release_date"] is None

    def test_get_missing_returns_404(self, ri_client):
        resp = ri_client.get(f"/api/v1/versions/{uuid.uuid4()}")
        assert resp.status_code == 404

    def test_get_unauthenticated_returns_401(self, anon_client, db_session, project):
        version = _make_version(db_session, project=project, version_number="1.0.0")
        resp = anon_client.get(f"/api/v1/versions/{version.id}")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# POST /projects/{project_id}/versions — create (ri only)
# ---------------------------------------------------------------------------


class TestCreateVersion:
    def test_create_returns_201(self, ri_client, project):
        payload = {
            "version_number": "1.0.0",
            "name": "Pilot release",
            "description": "Initial scope.",
            "target_date": "2026-06-01",
        }
        resp = ri_client.post(f"/api/v1/projects/{project.id}/versions", json=payload)
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["version_number"] == "1.0.0"
        assert body["name"] == "Pilot release"
        assert body["description"] == "Initial scope."
        assert body["target_date"] == "2026-06-01"
        # DB ``server_default`` fires.
        assert body["status"] == "planned"
        assert body["release_date"] is None
        assert body["project_id"] == str(project.id)
        assert body["id"]

    def test_create_duplicate_version_number_returns_409(self, ri_client, project):
        ri_client.post(
            f"/api/v1/projects/{project.id}/versions",
            json={"version_number": "1.0.0"},
        ).raise_for_status()
        resp = ri_client.post(
            f"/api/v1/projects/{project.id}/versions",
            json={"version_number": "1.0.0"},
        )
        assert resp.status_code == 409

    def test_create_invalid_payload_returns_422(self, ri_client, project):
        # ``version_number`` is required and must be non-empty.
        resp = ri_client.post(
            f"/api/v1/projects/{project.id}/versions",
            json={"version_number": ""},
        )
        assert resp.status_code == 422

    def test_create_non_ri_user_returns_403(self, ha_client, project):
        resp = ha_client.post(
            f"/api/v1/projects/{project.id}/versions",
            json={"version_number": "1.0.0"},
        )
        assert resp.status_code == 403

    def test_create_unauthenticated_returns_401(self, anon_client, project):
        resp = anon_client.post(
            f"/api/v1/projects/{project.id}/versions",
            json={"version_number": "1.0.0"},
        )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# PATCH /versions/{version_id} — update (ri only)
# ---------------------------------------------------------------------------


class TestUpdateVersion:
    def test_patch_partial_update(self, ri_client, db_session, project):
        version = _make_version(db_session, project=project, version_number="1.0.0", name="Old name")

        resp = ri_client.patch(
            f"/api/v1/versions/{version.id}",
            json={"name": "New name", "description": "Updated notes."},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["name"] == "New name"
        assert body["description"] == "Updated notes."
        # Untouched fields preserved.
        assert body["version_number"] == "1.0.0"
        assert body["project_id"] == str(project.id)
        assert body["id"] == str(version.id)

    def test_patch_rename_collision_returns_409(self, ri_client, db_session, project):
        _make_version(db_session, project=project, version_number="1.0.0")
        other = _make_version(db_session, project=project, version_number="1.1.0")

        resp = ri_client.patch(
            f"/api/v1/versions/{other.id}",
            json={"version_number": "1.0.0"},
        )
        assert resp.status_code == 409

    def test_patch_missing_returns_404(self, ri_client):
        resp = ri_client.patch(
            f"/api/v1/versions/{uuid.uuid4()}",
            json={"name": "Whatever"},
        )
        assert resp.status_code == 404

    def test_patch_invalid_status_returns_422(self, ri_client, db_session, project):
        version = _make_version(db_session, project=project, version_number="1.0.0")
        resp = ri_client.patch(
            f"/api/v1/versions/{version.id}",
            json={"status": "bogus"},
        )
        assert resp.status_code == 422

    def test_patch_non_ri_user_returns_403(self, ha_client, db_session, project):
        version = _make_version(db_session, project=project, version_number="1.0.0")
        resp = ha_client.patch(
            f"/api/v1/versions/{version.id}",
            json={"name": "New name"},
        )
        assert resp.status_code == 403

    def test_patch_unauthenticated_returns_401(self, anon_client, db_session, project):
        version = _make_version(db_session, project=project, version_number="1.0.0")
        resp = anon_client.patch(
            f"/api/v1/versions/{version.id}",
            json={"name": "New name"},
        )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# POST /versions/{version_id}/release — release gate (ri only)
# ---------------------------------------------------------------------------
