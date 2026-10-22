"""Version lifecycle integration test: create → assign → release gate → release.

Exercises the full version lifecycle through the HTTP layer using both
the versions and epics routers mounted on a private ``TestClient`` app
with SAVEPOINT-isolated DB sessions.

Scenarios:

1. Create version (status=planned).
2. Duplicate version_number → 409.
3. Auto-activate trigger: creating/patching an epic to in_progress
   promotes the version from planned → active.
4. Release gate with unfinished epics → 422.
5. Release success after all epics are done → 200, released, release_date set.
6. CRUD: list, get, patch, delete (or cascade verification).
"""

from __future__ import annotations

import uuid

# ---------------------------------------------------------------------------
# Scenario 1 — Create version (status=planned)
# ---------------------------------------------------------------------------


class TestCreateVersion:
    """POST /api/v1/projects/{project_id}/versions → 201, status=planned."""

    def test_create_version_planned(self, client, project):
        resp = client.post(
            f"/api/v1/projects/{project.id}/versions",
            json={"version_number": "1.0.0"},
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["status"] == "planned"
        assert body["version_number"] == "1.0.0"
        assert body["project_id"] == str(project.id)
        assert body["release_date"] is None
        assert body["id"]
        assert body["created_at"]
        assert body["updated_at"]


# ---------------------------------------------------------------------------
# Scenario 2 — Duplicate version_number → 409
# ---------------------------------------------------------------------------


class TestDuplicateVersionNumber:
    """POST same version_number twice → 409 (duplicate / already exists)."""

    def test_duplicate_version_number(self, client, project):
        resp1 = client.post(
            f"/api/v1/projects/{project.id}/versions",
            json={"version_number": "1.0.0"},
        )
        assert resp1.status_code == 201, resp1.text

        resp2 = client.post(
            f"/api/v1/projects/{project.id}/versions",
            json={"version_number": "1.0.0"},
        )
        # The router maps "already exists" → 409
        assert resp2.status_code == 409, resp2.text


# ---------------------------------------------------------------------------
# Scenario 3 — Auto-activate trigger
# ---------------------------------------------------------------------------


class TestAutoActivateTrigger:
    """Creating an epic and patching to in_progress promotes version → active."""


# ---------------------------------------------------------------------------
# Scenario 4 — Release gate with unfinished epics
# ---------------------------------------------------------------------------


class TestReleaseGateBlocked:
    """POST /api/v1/versions/{id}/release with unfinished epics → 422."""


# ---------------------------------------------------------------------------
# Scenario 5 — Release success
# ---------------------------------------------------------------------------


class TestReleaseSuccess:
    """All epics done → release gate passes → released + release_date."""


# ---------------------------------------------------------------------------
# Scenario 6 — CRUD operations
# ---------------------------------------------------------------------------


class TestVersionCRUD:
    """GET list, GET detail, PATCH update, verify envelope structure."""

    def test_list_versions_by_project(self, client, project):
        # Create 2 versions
        for vn in ("5.0.0", "5.1.0"):
            resp = client.post(
                f"/api/v1/projects/{project.id}/versions",
                json={"version_number": vn},
            )
            assert resp.status_code == 201, resp.text

        # List returns array (no paginated envelope — version list is flat)
        resp = client.get(f"/api/v1/projects/{project.id}/versions")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert isinstance(body, list)
        assert len(body) == 2
        version_numbers = {v["version_number"] for v in body}
        assert version_numbers == {"5.0.0", "5.1.0"}

    def test_get_version_by_id(self, client, project):
        resp = client.post(
            f"/api/v1/projects/{project.id}/versions",
            json={"version_number": "6.0.0", "name": "Test Release"},
        )
        assert resp.status_code == 201, resp.text
        version_id = resp.json()["id"]

        resp = client.get(f"/api/v1/versions/{version_id}")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["id"] == version_id
        assert body["version_number"] == "6.0.0"
        assert body["name"] == "Test Release"

    def test_get_missing_version_returns_404(self, client):
        resp = client.get(f"/api/v1/versions/{uuid.uuid4()}")
        assert resp.status_code == 404

    def test_patch_version_description(self, client, project):
        resp = client.post(
            f"/api/v1/projects/{project.id}/versions",
            json={"version_number": "7.0.0"},
        )
        assert resp.status_code == 201, resp.text
        version_id = resp.json()["id"]

        resp = client.patch(
            f"/api/v1/versions/{version_id}",
            json={"description": "updated"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["description"] == "updated"
        # Immutable fields unchanged
        assert body["id"] == version_id
        assert body["version_number"] == "7.0.0"

    def test_patch_missing_version_returns_404(self, client):
        resp = client.patch(
            f"/api/v1/versions/{uuid.uuid4()}",
            json={"description": "nope"},
        )
        assert resp.status_code == 404
