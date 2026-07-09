"""Integration test — project creation validation errors.

Validates that the project creation endpoint correctly rejects
invalid inputs with appropriate HTTP status codes:

    1. Duplicate slug                      -> 409 CONFLICT
    2. Conflicting port (already allocated) -> 409 CONFLICT
    3. Port out of range (outside 10100-14999) -> 422
    4. repo_url is stored as metadata — no existence check (201 always)
    5. Verify error response structure
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

# KB isolation (docs/specs/kb-ghost-root-cause.md Fix 1 + kb-ghost-followup.md
# Fix A): every integration test now runs against a tmp KB (and init.sh forced to
# dry-run) via the autouse ``_auto_isolate_create_project_kb`` fixture in
# tests/integration/conftest.py, so a create never touches the shared
# /home/icc/knowledge and the sentinel asserts no ghost dir is left behind.
# No per-module pytestmark needed.


@pytest.fixture(autouse=True)
def _mock_github_for_validation_tests():
    """Auto-mock GitHub API across this module's tests.

    The CI runner's GITHUB_TOKEN doesn't have admin:org scope to actually
    create repos, so unmocked calls return 401 ("Bad credentials") and
    POST /api/v1/projects 500-s before the validation logic under test
    can run. Production deploy uses a token with the right scope.
    """
    with (
        patch(
            "backend.services.github_validation.validate_github_repo",
            return_value=True,
        ),
        patch(
            "backend.services.github_validation.create_github_repo",
            return_value=None,
        ),
    ):
        yield


@pytest.mark.integration
class TestProjectCreationValidation:
    """Validate all error paths for project creation."""

    def _login_admin(self, client):
        """Helper: login as admin and return (headers, user_id)."""
        resp = client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "Nex123"},
        )
        assert resp.status_code == 200
        data = resp.json()
        headers = {"Authorization": f"Bearer {data['access_token']}"}
        return headers, data["user"]["id"]

    def _create_project(self, client, headers, user_id, **overrides):
        """Helper: create a project with default values + overrides."""
        payload = {
            "name": "Validation Test Project",
            "slug": "val-test-proj",
            "type": "standard",
            "auth_mode": "password",
            "description": "Validation test",
            "created_by": user_id,
        }
        payload.update(overrides)
        return client.post(
            "/api/v1/projects",
            json=payload,
            headers=headers,
        )

    def test_duplicate_slug_returns_409(self, integration_client, _seed_admin):
        """Creating a project with an existing slug must return 409."""
        headers, user_id = self._login_admin(integration_client)

        # Create first project
        resp1 = self._create_project(
            integration_client,
            headers,
            user_id,
            name="First Project",
            slug="dup-slug-test",
        )
        assert resp1.status_code == 201

        # Attempt duplicate slug
        resp2 = self._create_project(
            integration_client,
            headers,
            user_id,
            name="Second Project Different Name",
            slug="dup-slug-test",
        )
        assert resp2.status_code == 409
        detail = resp2.json()["detail"]
        assert "already exists" in detail.lower() or "duplicate" in detail.lower()

    def test_conflicting_port_returns_409(self, integration_client, _seed_admin):
        """Creating a project with a port already allocated returns 409."""
        headers, user_id = self._login_admin(integration_client)

        # Create first project with backend_port 10150
        resp1 = self._create_project(
            integration_client,
            headers,
            user_id,
            name="Port Owner",
            slug="port-owner",
            backend_port=10150,
        )
        assert resp1.status_code == 201

        # Attempt to create second project with same port
        resp2 = self._create_project(
            integration_client,
            headers,
            user_id,
            name="Port Stealer",
            slug="port-stealer",
            backend_port=10150,
        )
        assert resp2.status_code == 409
        detail = resp2.json()["detail"]
        # The detail is a dict (PortConflictError schema)
        assert detail["port"] == 10150
        assert detail["conflict_project"] == "Port Owner"

    def test_port_conflict_across_port_types(self, integration_client, _seed_admin):
        """A port used as backend_port cannot be reused as frontend_port."""
        headers, user_id = self._login_admin(integration_client)

        # Create project with backend_port 10160
        resp1 = self._create_project(
            integration_client,
            headers,
            user_id,
            name="Cross Port Owner",
            slug="cross-port-owner",
            backend_port=10160,
        )
        assert resp1.status_code == 201

        # Try to use 10160 as frontend_port in another project
        resp2 = self._create_project(
            integration_client,
            headers,
            user_id,
            name="Cross Port Stealer",
            slug="cross-port-stealer",
            frontend_port=10160,
        )
        assert resp2.status_code == 409
        detail = resp2.json()["detail"]
        assert detail["port"] == 10160

    def test_port_below_range_returns_422(self, integration_client, _seed_admin):
        """Port below 10100 must be rejected with 422."""
        headers, user_id = self._login_admin(integration_client)

        resp = self._create_project(
            integration_client,
            headers,
            user_id,
            name="Port Too Low",
            slug="port-too-low",
            backend_port=8080,
        )
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert "outside the allowed range" in detail.lower() or "10100" in detail

    def test_port_above_range_returns_422(self, integration_client, _seed_admin):
        """Port above 14999 must be rejected with 422."""
        headers, user_id = self._login_admin(integration_client)

        resp = self._create_project(
            integration_client,
            headers,
            user_id,
            name="Port Too High",
            slug="port-too-high",
            frontend_port=15000,
        )
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert "outside the allowed range" in detail.lower() or "14999" in detail

    def test_port_at_boundaries(self, integration_client, _seed_admin):
        """Ports at exact boundaries: 10099 rejected, 10100 accepted, 14999 accepted, 15000 rejected."""
        headers, user_id = self._login_admin(integration_client)

        # 10099 — below range
        resp_below = self._create_project(
            integration_client,
            headers,
            user_id,
            name="Boundary Below",
            slug="boundary-below",
            backend_port=10099,
        )
        assert resp_below.status_code == 422

        # 10100 — lower bound (valid)
        resp_low = self._create_project(
            integration_client,
            headers,
            user_id,
            name="Boundary Low",
            slug="boundary-low",
            backend_port=10100,
        )
        assert resp_low.status_code == 201

        # 14999 — upper bound (valid)
        resp_high = self._create_project(
            integration_client,
            headers,
            user_id,
            name="Boundary High",
            slug="boundary-high",
            frontend_port=14999,
        )
        assert resp_high.status_code == 201

        # 15000 — above range
        resp_above = self._create_project(
            integration_client,
            headers,
            user_id,
            name="Boundary Above",
            slug="boundary-above",
            db_port=15000,
        )
        assert resp_above.status_code == 422

    def test_nonexistent_github_repo_accepted(self, integration_client, _seed_admin):
        """repo_url for a non-existent repo is accepted — no existence check at creation."""
        headers, user_id = self._login_admin(integration_client)

        resp = integration_client.post(
            "/api/v1/projects",
            json={
                "name": "Bad Repo Project",
                "slug": "bad-repo-proj",
                "type": "standard",
                "auth_mode": "password",
                "description": "Non-existent repo — accepted as metadata",
                "repo_url": "nonexistent-org/nonexistent-repo",
                "created_by": user_id,
            },
            headers=headers,
        )
        assert resp.status_code == 201
        assert resp.json()["repo_url"] == "nonexistent-org/nonexistent-repo"

    def test_any_repo_url_format_accepted(self, integration_client, _seed_admin):
        """repo_url is stored as-is — backend does not validate GitHub format or existence."""
        headers, user_id = self._login_admin(integration_client)

        resp = integration_client.post(
            "/api/v1/projects",
            json={
                "name": "Any Format Repo",
                "slug": "any-format-repo",
                "type": "standard",
                "auth_mode": "password",
                "description": "Any repo_url string is accepted",
                "repo_url": "no-slash-here",
                "created_by": user_id,
            },
            headers=headers,
        )
        assert resp.status_code == 201
        assert resp.json()["repo_url"] == "no-slash-here"

    def test_error_response_structure(self, integration_client, _seed_admin):
        """Verify error responses have the expected structure."""
        headers, user_id = self._login_admin(integration_client)

        # Create initial project
        resp1 = self._create_project(
            integration_client,
            headers,
            user_id,
            name="Structure Test",
            slug="structure-test",
            backend_port=10170,
        )
        assert resp1.status_code == 201

        # Port conflict error structure
        resp_port = self._create_project(
            integration_client,
            headers,
            user_id,
            name="Structure Test 2",
            slug="structure-test-2",
            backend_port=10170,
        )
        assert resp_port.status_code == 409
        port_detail = resp_port.json()["detail"]
        assert "detail" in port_detail
        assert "port" in port_detail
        assert "conflict_project" in port_detail
        assert isinstance(port_detail["port"], int)
        assert isinstance(port_detail["detail"], str)

        # Slug conflict error structure
        resp_slug = self._create_project(
            integration_client,
            headers,
            user_id,
            name="Structure Test Different Name",
            slug="structure-test",
        )
        assert resp_slug.status_code == 409
        slug_detail = resp_slug.json()["detail"]
        assert isinstance(slug_detail, str)

        # repo_url is now accepted without existence check — project creates successfully
        resp_repo = integration_client.post(
            "/api/v1/projects",
            json={
                "name": "Repo Error Structure",
                "slug": "repo-error-structure",
                "type": "standard",
                "auth_mode": "password",
                "description": "Test",
                "repo_url": "fake/repo",
                "created_by": user_id,
            },
            headers=headers,
        )
        assert resp_repo.status_code == 201
        assert resp_repo.json()["repo_url"] == "fake/repo"
