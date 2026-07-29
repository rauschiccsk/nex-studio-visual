"""Tests for the Bug REST router.

Verifies the CRUD surface exposed by :mod:`backend.api.routes.bugs`
against the SAVEPOINT-isolated test database. The router is mounted at
``/api/v1/bugs`` — the same prefix it will have in production via
``backend/main.py`` — but since this router is not yet wired into
``main.py`` we mount it on a dedicated ``TestClient`` app here (same
pattern as :mod:`tests.test_user_router`,
:mod:`tests.test_project_router` and
:mod:`tests.test_guardian_precedent_router`).

Covers:

* Create / get / list / patch / delete happy paths.
* ``PaginatedResponse`` envelope (items / total / skip / limit).
* Pagination via ``skip`` and ``limit``.
* Filter by ``project_id``, ``status``, ``severity``, ``source`` and
  ``created_by``.
* 404 on missing id (get, patch, delete).
* 422 on schema validation failure (invalid severity / status, limit
  > 100).
* Auto-assignment of ``bug_number`` per project (1, 2, 3 ...).
* Auto-stamping of ``resolved_at`` on PATCH ``status=resolved``.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.routes.bugs import router as bugs_router
from backend.db.models.foundation import User
from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.db.session import get_db


@pytest.fixture()
def router_client(db_session):
    """Mount the bugs router on a fresh app with the DB override.

    Keeping this fixture local to the router tests avoids coupling to the
    global ``main.app``, which does not yet include this router.
    """
    app = FastAPI()
    app.include_router(bugs_router, prefix="/api/v1/bugs")

    def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    # Auto-added by M2.D RBAC roll-out — override role gates so existing
    # tests (which never sent JWTs) keep working. Tests that exercise
    # role denial should re-override these to a lower-role user locally.
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
        # The ACCOUNT named admin — bugs now authorize on the OWNING PROJECT, and this client
        # operates on projects other fixtures created. Role "ri" no longer carries that.
        username="admin",
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
def reporter(db_session) -> User:
    """Persist a user that may register the bugs created in a test."""
    user = User(
        username=f"reporter_{uuid.uuid4().hex[:8]}",
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        password_hash="hashed_password_placeholder",
        role="ri",
    )
    db_session.add(user)
    db_session.flush()
    return user


@pytest.fixture()
def project(db_session, reporter) -> Project:
    """Persist a project that bugs may be filed against."""
    proj = Project(
        slug=f"proj-{uuid.uuid4().hex[:8]}",
        name=f"Project {uuid.uuid4().hex[:8]}",
        type="standard",
        auth_mode="password",
        description="Test project description",
        created_by=reporter.id,
    )
    db_session.add(proj)
    db_session.flush()
    return proj


@pytest.fixture()
def version(db_session, project) -> Version:
    """Persist a release version that new bugs can be assigned to.

    DESIGN.md §4.0 Rule 2 — every new BUG must carry a ``version_id``.
    The router payloads below default to this version so the happy-path
    cases remain one-liners.
    """
    v = Version(
        project_id=project.id,
        version_number=f"v{uuid.uuid4().hex[:6]}",
        name="Test version",
        status="planned",
    )
    db_session.add(v)
    db_session.flush()
    return v


def _make_version(db_session, project: Project, **overrides) -> Version:
    """Ad-hoc Version factory for tests that build their own projects."""
    defaults = {
        "project_id": project.id,
        "version_number": f"v{uuid.uuid4().hex[:6]}",
        "name": "Test version",
        "status": "planned",
    }
    defaults.update(overrides)
    v = Version(**defaults)
    db_session.add(v)
    db_session.flush()
    return v


def _payload(*, project_id, created_by, version_id=None, **overrides) -> dict:
    """Return a bug-create payload with deterministic-ish defaults.

    ``version_id`` is required by the service (DESIGN.md §4.0 Rule 2);
    callers that want to exercise the error path can pass
    ``version_id=None`` and the helper omits the key so the service
    surfaces ``ValueError("version_id required for new bugs")`` via
    HTTP 422.
    """
    body = {
        "project_id": str(project_id),
        "title": f"Bug {uuid.uuid4().hex[:8]}",
        "description": "Steps: 1. Do X. Expected Y. Actual Z.",
        "severity": "major",
        "created_by": str(created_by),
    }
    if version_id is not None:
        body["version_id"] = str(version_id)
    body.update(overrides)
    return body


class TestBugRouter:
    """End-to-end HTTP coverage for the router."""

    def test_create_bug(self, router_client, project, reporter, version):
        payload = _payload(
            project_id=project.id,
            created_by=reporter.id,
            version_id=version.id,
            title="Login fails on Safari",
            severity="critical",
            source="customer",
            reported_by="acme-corp",
            environment="production",
        )
        resp = router_client.post("/api/v1/bugs", json=payload)
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["title"] == "Login fails on Safari"
        assert body["severity"] == "critical"
        assert body["status"] == "new"
        assert body["source"] == "customer"
        assert body["reported_by"] == "acme-corp"
        assert body["environment"] == "production"
        assert body["project_id"] == str(project.id)
        assert body["version_id"] == str(version.id)
        assert body["created_by"] == str(reporter.id)
        assert body["bug_number"] == 1
        assert body["id"]
        assert body["created_at"]
        assert body["updated_at"]

    def test_create_missing_version_id_returns_422(self, router_client, project, reporter):
        """Missing ``version_id`` triggers the service-layer guard → HTTP 422.

        DESIGN.md §4.0 Rule 2 — every new BUG must carry a
        ``version_id``. The service raises
        ``ValueError("version_id required for new bugs")`` which the
        router translates to HTTP 422.
        """
        payload = _payload(project_id=project.id, created_by=reporter.id)  # no version_id
        resp = router_client.post("/api/v1/bugs", json=payload)
        assert resp.status_code == 422, resp.text
        assert "version_id required" in resp.text

    def test_create_assigns_sequential_bug_numbers_per_project(self, router_client, project, reporter, version):
        first = router_client.post(
            "/api/v1/bugs",
            json=_payload(project_id=project.id, created_by=reporter.id, version_id=version.id),
        ).json()
        second = router_client.post(
            "/api/v1/bugs",
            json=_payload(project_id=project.id, created_by=reporter.id, version_id=version.id),
        ).json()
        assert first["bug_number"] == 1
        assert second["bug_number"] == 2

    def test_create_invalid_severity_returns_422(self, router_client, project, reporter, version):
        payload = _payload(
            project_id=project.id,
            created_by=reporter.id,
            version_id=version.id,
            severity="bogus",
        )
        resp = router_client.post("/api/v1/bugs", json=payload)
        assert resp.status_code == 422

    def test_create_invalid_status_returns_422(self, router_client, project, reporter, version):
        payload = _payload(
            project_id=project.id,
            created_by=reporter.id,
            version_id=version.id,
            status="bogus",
        )
        resp = router_client.post("/api/v1/bugs", json=payload)
        assert resp.status_code == 422

    def test_create_invalid_source_returns_422(self, router_client, project, reporter, version):
        payload = _payload(
            project_id=project.id,
            created_by=reporter.id,
            version_id=version.id,
            source="bogus",
        )
        resp = router_client.post("/api/v1/bugs", json=payload)
        assert resp.status_code == 422

    def test_get_by_id(self, router_client, project, reporter, version):
        created = router_client.post(
            "/api/v1/bugs",
            json=_payload(project_id=project.id, created_by=reporter.id, version_id=version.id),
        ).json()
        resp = router_client.get(f"/api/v1/bugs/{created['id']}")
        assert resp.status_code == 200
        assert resp.json()["id"] == created["id"]

    def test_get_missing_returns_404(self, router_client):
        resp = router_client.get(f"/api/v1/bugs/{uuid.uuid4()}")
        assert resp.status_code == 404

    def test_list_envelope_and_pagination(self, router_client, project, reporter, version):
        for _ in range(3):
            router_client.post(
                "/api/v1/bugs",
                json=_payload(project_id=project.id, created_by=reporter.id, version_id=version.id),
            ).raise_for_status()

        resp = router_client.get("/api/v1/bugs", params={"project_id": str(project.id), "skip": 0, "limit": 2})
        assert resp.status_code == 200
        body = resp.json()
        assert set(body.keys()) >= {"items", "total", "skip", "limit"}
        assert body["skip"] == 0
        assert body["limit"] == 2
        assert body["total"] >= 3
        assert len(body["items"]) == 2

        page2 = router_client.get(
            "/api/v1/bugs",
            params={"project_id": str(project.id), "skip": 2, "limit": 2},
        ).json()
        page1_ids = {row["id"] for row in body["items"]}
        page2_ids = {row["id"] for row in page2["items"]}
        assert page1_ids.isdisjoint(page2_ids)

    def test_list_filter_by_project_id(self, router_client, db_session, reporter):
        p1 = Project(
            slug=f"p1-{uuid.uuid4().hex[:8]}",
            name=f"P1 {uuid.uuid4().hex[:8]}",
            type="standard",
            auth_mode="password",
            description="P1",
            created_by=reporter.id,
        )
        p2 = Project(
            slug=f"p2-{uuid.uuid4().hex[:8]}",
            name=f"P2 {uuid.uuid4().hex[:8]}",
            type="standard",
            auth_mode="password",
            description="P2",
            created_by=reporter.id,
        )
        db_session.add_all([p1, p2])
        db_session.flush()
        v1 = _make_version(db_session, p1)
        v2 = _make_version(db_session, p2)

        router_client.post(
            "/api/v1/bugs",
            json=_payload(project_id=p1.id, created_by=reporter.id, version_id=v1.id),
        ).raise_for_status()
        router_client.post(
            "/api/v1/bugs",
            json=_payload(project_id=p2.id, created_by=reporter.id, version_id=v2.id),
        ).raise_for_status()

        resp = router_client.get(
            "/api/v1/bugs",
            params={"project_id": str(p2.id)},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] >= 1
        assert all(item["project_id"] == str(p2.id) for item in body["items"])

    def test_list_filter_by_status(self, router_client, project, reporter, version):
        router_client.post(
            "/api/v1/bugs",
            json=_payload(project_id=project.id, created_by=reporter.id, version_id=version.id, status="new"),
        ).raise_for_status()
        router_client.post(
            "/api/v1/bugs",
            json=_payload(
                project_id=project.id,
                created_by=reporter.id,
                version_id=version.id,
                status="accepted",
            ),
        ).raise_for_status()

        resp = router_client.get("/api/v1/bugs", params={"project_id": str(project.id), "status": "accepted"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] >= 1
        assert all(item["status"] == "accepted" for item in body["items"])

    def test_list_filter_by_severity(self, router_client, project, reporter, version):
        router_client.post(
            "/api/v1/bugs",
            json=_payload(
                project_id=project.id,
                created_by=reporter.id,
                version_id=version.id,
                severity="critical",
            ),
        ).raise_for_status()
        router_client.post(
            "/api/v1/bugs",
            json=_payload(
                project_id=project.id,
                created_by=reporter.id,
                version_id=version.id,
                severity="minor",
            ),
        ).raise_for_status()

        resp = router_client.get("/api/v1/bugs", params={"project_id": str(project.id), "severity": "critical"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] >= 1
        assert all(item["severity"] == "critical" for item in body["items"])

    def test_list_filter_by_source(self, router_client, project, reporter, version):
        router_client.post(
            "/api/v1/bugs",
            json=_payload(
                project_id=project.id,
                created_by=reporter.id,
                version_id=version.id,
                source="internal",
            ),
        ).raise_for_status()
        router_client.post(
            "/api/v1/bugs",
            json=_payload(
                project_id=project.id,
                created_by=reporter.id,
                version_id=version.id,
                source="customer",
            ),
        ).raise_for_status()

        resp = router_client.get("/api/v1/bugs", params={"project_id": str(project.id), "source": "customer"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] >= 1
        assert all(item["source"] == "customer" for item in body["items"])

    def test_list_filter_by_created_by(self, router_client, db_session, project, reporter, version):
        other = User(
            username=f"other_{uuid.uuid4().hex[:8]}",
            email=f"{uuid.uuid4().hex[:8]}@example.com",
            password_hash="hashed_password_placeholder",
            role="ri",
        )
        db_session.add(other)
        db_session.flush()

        router_client.post(
            "/api/v1/bugs",
            json=_payload(project_id=project.id, created_by=reporter.id, version_id=version.id),
        ).raise_for_status()
        router_client.post(
            "/api/v1/bugs",
            json=_payload(project_id=project.id, created_by=other.id, version_id=version.id),
        ).raise_for_status()

        resp = router_client.get(
            "/api/v1/bugs",
            params={"project_id": str(project.id), "created_by": str(other.id)},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] >= 1
        assert all(item["created_by"] == str(other.id) for item in body["items"])

    def test_list_limit_over_100_returns_422(self, router_client):
        resp = router_client.get("/api/v1/bugs", params={"limit": 101})
        assert resp.status_code == 422

    def test_patch_partial_update(self, router_client, project, reporter, version):
        created = router_client.post(
            "/api/v1/bugs",
            json=_payload(
                project_id=project.id,
                created_by=reporter.id,
                version_id=version.id,
                title="Original title",
                severity="major",
                status="new",
            ),
        ).json()

        resp = router_client.patch(
            f"/api/v1/bugs/{created['id']}",
            json={"status": "accepted", "severity": "critical"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "accepted"
        assert body["severity"] == "critical"
        # Fields omitted from the PATCH payload are untouched.
        assert body["title"] == "Original title"
        assert body["description"] == created["description"]
        # Immutable fields unchanged.
        assert body["id"] == created["id"]
        assert body["project_id"] == created["project_id"]
        assert body["bug_number"] == created["bug_number"]
        assert body["created_by"] == created["created_by"]
        assert body["created_at"] == created["created_at"]

    def test_patch_status_resolved_auto_stamps_resolved_at(self, router_client, project, reporter, version):
        created = router_client.post(
            "/api/v1/bugs",
            json=_payload(
                project_id=project.id,
                created_by=reporter.id,
                version_id=version.id,
                status="in_progress",
            ),
        ).json()
        assert created["resolved_at"] is None

        resp = router_client.patch(
            f"/api/v1/bugs/{created['id']}",
            json={"status": "resolved"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "resolved"
        assert body["resolved_at"] is not None

    def test_patch_invalid_severity_returns_422(self, router_client, project, reporter, version):
        created = router_client.post(
            "/api/v1/bugs",
            json=_payload(project_id=project.id, created_by=reporter.id, version_id=version.id),
        ).json()
        resp = router_client.patch(
            f"/api/v1/bugs/{created['id']}",
            json={"severity": "bogus"},
        )
        assert resp.status_code == 422

    def test_patch_missing_returns_404(self, router_client):
        resp = router_client.patch(
            f"/api/v1/bugs/{uuid.uuid4()}",
            json={"status": "accepted"},
        )
        assert resp.status_code == 404

    def test_delete_returns_204(self, router_client, project, reporter, version):
        created = router_client.post(
            "/api/v1/bugs",
            json=_payload(project_id=project.id, created_by=reporter.id, version_id=version.id),
        ).json()
        resp = router_client.delete(f"/api/v1/bugs/{created['id']}")
        assert resp.status_code == 204
        # Second read confirms removal.
        assert router_client.get(f"/api/v1/bugs/{created['id']}").status_code == 404

    def test_delete_missing_returns_404(self, router_client):
        resp = router_client.delete(f"/api/v1/bugs/{uuid.uuid4()}")
        assert resp.status_code == 404
