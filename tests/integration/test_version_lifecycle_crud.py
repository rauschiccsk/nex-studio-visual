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
from datetime import date

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

    def test_auto_activate_on_epic_in_progress(self, client, project):
        # Create version v2.0 (status=planned)
        resp = client.post(
            f"/api/v1/projects/{project.id}/versions",
            json={"version_number": "2.0.0"},
        )
        assert resp.status_code == 201, resp.text
        version = resp.json()
        version_id = version["id"]
        assert version["status"] == "planned"

        # Create an epic assigned to this version
        resp = client.post(
            "/api/v1/epics/",
            json={
                "project_id": str(project.id),
                "version_id": version_id,
                "title": "Epic for auto-activate",
            },
        )
        assert resp.status_code == 201, resp.text
        epic = resp.json()

        # Version should still be planned (epic status is planned)
        resp = client.get(f"/api/v1/versions/{version_id}")
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "planned"

        # Patch epic to in_progress → triggers auto-activate
        resp = client.patch(
            f"/api/v1/epics/{epic['id']}",
            json={"status": "in_progress"},
        )
        assert resp.status_code == 200, resp.text

        # Version should now be active
        resp = client.get(f"/api/v1/versions/{version_id}")
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "active"


# ---------------------------------------------------------------------------
# Scenario 4 — Release gate with unfinished epics
# ---------------------------------------------------------------------------


class TestReleaseGateBlocked:
    """POST /api/v1/versions/{id}/release with unfinished epics → 422."""

    def test_release_blocked_by_unfinished_epics(self, client, project):
        # Create version v3.0
        resp = client.post(
            f"/api/v1/projects/{project.id}/versions",
            json={"version_number": "3.0.0"},
        )
        assert resp.status_code == 201, resp.text
        version_id = resp.json()["id"]

        # Assign 2 epics (both default to planned)
        epic_ids = []
        for title in ("Epic A", "Epic B"):
            resp = client.post(
                "/api/v1/epics/",
                json={
                    "project_id": str(project.id),
                    "version_id": version_id,
                    "title": title,
                },
            )
            assert resp.status_code == 201, resp.text
            epic_ids.append(resp.json()["id"])

        # Attempt release — should fail (epics are planned, not done)
        resp = client.post(f"/api/v1/versions/{version_id}/release")
        assert resp.status_code == 422, resp.text
        detail = resp.json()["detail"]
        # Structured 422 includes blocking_epic_ids
        assert "blocking_epic_ids" in detail
        assert len(detail["blocking_epic_ids"]) == 2


# ---------------------------------------------------------------------------
# Scenario 5 — Release success
# ---------------------------------------------------------------------------


class TestReleaseSuccess:
    """All epics done → release gate passes → released + release_date."""

    def test_release_after_all_epics_done(self, client, project):
        # Create version
        resp = client.post(
            f"/api/v1/projects/{project.id}/versions",
            json={"version_number": "4.0.0"},
        )
        assert resp.status_code == 201, resp.text
        version_id = resp.json()["id"]

        # Assign 2 epics
        epic_ids = []
        for title in ("Epic X", "Epic Y"):
            resp = client.post(
                "/api/v1/epics/",
                json={
                    "project_id": str(project.id),
                    "version_id": version_id,
                    "title": title,
                },
            )
            assert resp.status_code == 201, resp.text
            epic_ids.append(resp.json()["id"])

        # Patch both epics to done
        for eid in epic_ids:
            resp = client.patch(
                f"/api/v1/epics/{eid}",
                json={"status": "done"},
            )
            assert resp.status_code == 200, resp.text

        # Release — should succeed
        resp = client.post(f"/api/v1/versions/{version_id}/release")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "released"
        assert body["release_date"] is not None
        assert body["release_date"] == date.today().isoformat()


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
