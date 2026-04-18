"""Integration test — project creation validation errors.

Validates that the project creation endpoint correctly rejects
invalid inputs with appropriate HTTP status codes:

    1. Duplicate slug                      -> 409 CONFLICT
    2. Conflicting port (already allocated) -> 409 CONFLICT
    3. Port out of range (outside 9100-9299) -> 422
    4. Non-existent GitHub repo             -> 422
    5. Verify error response structure
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


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
            "category": "singlemodule",
            "description": "Validation test",
            "created_by": user_id,
        }
        payload.update(overrides)
        with patch(
            "backend.services.github_validation.validate_github_repo",
            return_value=True,
        ):
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

        # Create first project with backend_port 9250
        resp1 = self._create_project(
            integration_client,
            headers,
            user_id,
            name="Port Owner",
            slug="port-owner",
            backend_port=9250,
        )
        assert resp1.status_code == 201

        # Attempt to create second project with same port
        resp2 = self._create_project(
            integration_client,
            headers,
            user_id,
            name="Port Stealer",
            slug="port-stealer",
            backend_port=9250,
        )
        assert resp2.status_code == 409
        detail = resp2.json()["detail"]
        # The detail is a dict (PortConflictError schema)
        assert detail["port"] == 9250
        assert detail["conflict_project"] == "Port Owner"

    def test_port_conflict_across_port_types(self, integration_client, _seed_admin):
        """A port used as backend_port cannot be reused as frontend_port."""
        headers, user_id = self._login_admin(integration_client)

        # Create project with backend_port 9260
        resp1 = self._create_project(
            integration_client,
            headers,
            user_id,
            name="Cross Port Owner",
            slug="cross-port-owner",
            backend_port=9260,
        )
        assert resp1.status_code == 201

        # Try to use 9260 as frontend_port in another project
        resp2 = self._create_project(
            integration_client,
            headers,
            user_id,
            name="Cross Port Stealer",
            slug="cross-port-stealer",
            frontend_port=9260,
        )
        assert resp2.status_code == 409
        detail = resp2.json()["detail"]
        assert detail["port"] == 9260

    def test_port_below_range_returns_422(self, integration_client, _seed_admin):
        """Port below 9100 must be rejected with 422."""
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
        assert "outside the allowed range" in detail.lower() or "9100" in detail

    def test_port_above_range_returns_422(self, integration_client, _seed_admin):
        """Port above 9299 must be rejected with 422."""
        headers, user_id = self._login_admin(integration_client)

        resp = self._create_project(
            integration_client,
            headers,
            user_id,
            name="Port Too High",
            slug="port-too-high",
            frontend_port=9300,
        )
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert "outside the allowed range" in detail.lower() or "9299" in detail

    def test_port_at_boundaries(self, integration_client, _seed_admin):
        """Ports at exact boundaries: 9099 rejected, 9100 accepted, 9299 accepted, 9300 rejected."""
        headers, user_id = self._login_admin(integration_client)

        # 9099 — below range
        resp_below = self._create_project(
            integration_client,
            headers,
            user_id,
            name="Boundary Below",
            slug="boundary-below",
            backend_port=9099,
        )
        assert resp_below.status_code == 422

        # 9100 — lower bound (valid)
        resp_low = self._create_project(
            integration_client,
            headers,
            user_id,
            name="Boundary Low",
            slug="boundary-low",
            backend_port=9100,
        )
        assert resp_low.status_code == 201

        # 9299 — upper bound (valid)
        resp_high = self._create_project(
            integration_client,
            headers,
            user_id,
            name="Boundary High",
            slug="boundary-high",
            frontend_port=9299,
        )
        assert resp_high.status_code == 201

        # 9300 — above range
        resp_above = self._create_project(
            integration_client,
            headers,
            user_id,
            name="Boundary Above",
            slug="boundary-above",
            db_port=9300,
        )
        assert resp_above.status_code == 422

    def test_nonexistent_github_repo_returns_422(self, integration_client, _seed_admin):
        """Non-existent GitHub repo must be rejected with 422."""
        headers, user_id = self._login_admin(integration_client)

        with patch(
            "backend.services.github_validation.validate_github_repo",
            return_value=False,
        ):
            resp = integration_client.post(
                "/api/v1/projects",
                json={
                    "name": "Bad Repo Project",
                    "slug": "bad-repo-proj",
                    "category": "singlemodule",
                    "description": "Non-existent repo",
                    "repo_url": "nonexistent-org/nonexistent-repo",
                    "created_by": user_id,
                },
                headers=headers,
            )
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail["repo_url"] == "nonexistent-org/nonexistent-repo"
        assert "not found" in detail["detail"].lower()

    def test_invalid_github_repo_format_returns_422(self, integration_client, _seed_admin):
        """Invalid GitHub repo format must be rejected with 422."""
        headers, user_id = self._login_admin(integration_client)

        # Do NOT mock — let the real validation service catch the format error
        resp = integration_client.post(
            "/api/v1/projects",
            json={
                "name": "Bad Format Repo",
                "slug": "bad-format-repo",
                "category": "singlemodule",
                "description": "Invalid repo format",
                "repo_url": "no-slash-here",
                "created_by": user_id,
            },
            headers=headers,
        )
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail["repo_url"] == "no-slash-here"

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
            backend_port=9270,
        )
        assert resp1.status_code == 201

        # Port conflict error structure
        resp_port = self._create_project(
            integration_client,
            headers,
            user_id,
            name="Structure Test 2",
            slug="structure-test-2",
            backend_port=9270,
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

        # GitHub repo not found error structure
        with patch(
            "backend.services.github_validation.validate_github_repo",
            return_value=False,
        ):
            resp_repo = integration_client.post(
                "/api/v1/projects",
                json={
                    "name": "Repo Error Structure",
                    "slug": "repo-error-structure",
                    "category": "singlemodule",
                    "description": "Test",
                    "repo_url": "fake/repo",
                    "created_by": user_id,
                },
                headers=headers,
            )
        assert resp_repo.status_code == 422
        repo_detail = resp_repo.json()["detail"]
        assert "detail" in repo_detail
        assert "repo_url" in repo_detail
        assert isinstance(repo_detail["detail"], str)
        assert isinstance(repo_detail["repo_url"], str)
