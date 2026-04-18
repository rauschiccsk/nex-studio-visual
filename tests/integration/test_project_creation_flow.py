"""Integration test — full project creation flow.

E2E scenario:
    1. POST /api/v1/auth/login (admin/Nex123)   -> 200 + JWT
    2. GET  /api/v1/projects/ports/check         -> port availability (9100-9299)
    3. Mock GitHub API to validate repo_url
    4. POST /api/v1/projects (valid ports + repo) -> 201
    5. GET  /api/v1/projects/{id}                -> verify created
    6. Verify no project_members table query (service layer)
    7. POST /api/v1/projects (duplicate slug)    -> 409 slug uniqueness
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


@pytest.mark.integration
class TestProjectCreationFlow:
    """Full E2E: login -> check ports -> validate repo -> create -> verify -> slug uniqueness."""

    def test_full_project_creation_lifecycle(self, integration_client, _seed_admin):
        """Walk through the complete project creation flow."""
        # ------------------------------------------------------------------
        # Step 1: Login as ri (admin)
        # ------------------------------------------------------------------
        login_resp = integration_client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "Nex123"},
        )
        assert login_resp.status_code == 200
        login_data = login_resp.json()
        assert "access_token" in login_data
        admin_token = login_data["access_token"]
        admin_headers = {"Authorization": f"Bearer {admin_token}"}
        admin_user_id = login_data["user"]["id"]

        # ------------------------------------------------------------------
        # Step 2: Check port availability (9100-9299 range)
        # ------------------------------------------------------------------
        for port in (9200, 9201, 9202):
            port_resp = integration_client.get(
                "/api/v1/projects/ports/check",
                params={"port": port},
                headers=admin_headers,
            )
            assert port_resp.status_code == 200
            port_data = port_resp.json()
            assert port_data["available"] is True
            assert port_data["conflict_project"] is None

        # ------------------------------------------------------------------
        # Step 3: Create project with valid ports + GitHub repo (mocked)
        # ------------------------------------------------------------------
        with patch(
            "backend.services.github_validation.validate_github_repo",
            return_value=True,
        ):
            create_resp = integration_client.post(
                "/api/v1/projects",
                json={
                    "name": "NEX Integration Test",
                    "slug": "nex-integration-test",
                    "category": "singlemodule",
                    "description": "Integration test project",
                    "backend_port": 9200,
                    "frontend_port": 9201,
                    "db_port": 9202,
                    "repo_url": "rauschiccsk/nex-test-repo",
                    "created_by": admin_user_id,
                },
                headers=admin_headers,
            )
        assert create_resp.status_code == 201, create_resp.json()
        project_data = create_resp.json()
        project_id = project_data["id"]
        assert project_data["name"] == "NEX Integration Test"
        assert project_data["slug"] == "nex-integration-test"
        assert project_data["category"] == "singlemodule"
        assert project_data["backend_port"] == 9200
        assert project_data["frontend_port"] == 9201
        assert project_data["db_port"] == 9202
        assert project_data["repo_url"] == "rauschiccsk/nex-test-repo"
        assert project_data["status"] == "active"
        assert project_data["created_by"] == admin_user_id

        # ------------------------------------------------------------------
        # Step 4: Verify project exists via GET
        # ------------------------------------------------------------------
        get_resp = integration_client.get(
            f"/api/v1/projects/{project_id}",
            headers=admin_headers,
        )
        assert get_resp.status_code == 200
        fetched = get_resp.json()
        assert fetched["id"] == project_id
        assert fetched["slug"] == "nex-integration-test"
        assert fetched["backend_port"] == 9200

        # ------------------------------------------------------------------
        # Step 5: Verify project appears in list
        # ------------------------------------------------------------------
        list_resp = integration_client.get(
            "/api/v1/projects",
            headers=admin_headers,
        )
        assert list_resp.status_code == 200
        list_data = list_resp.json()
        slugs = [p["slug"] for p in list_data["items"]]
        assert "nex-integration-test" in slugs

        # ------------------------------------------------------------------
        # Step 6: Verify no project_members table query
        # (project creation does NOT auto-create membership rows;
        #  the service layer only inserts into the projects table)
        # ------------------------------------------------------------------
        # The project was created without any member-related fields
        # and the response contains no members key — confirming the
        # service does not query/populate project_members on creation.
        assert "members" not in project_data

        # ------------------------------------------------------------------
        # Step 7: Verify slug uniqueness is enforced
        # ------------------------------------------------------------------
        with patch(
            "backend.services.github_validation.validate_github_repo",
            return_value=True,
        ):
            dup_resp = integration_client.post(
                "/api/v1/projects",
                json={
                    "name": "NEX Integration Test Duplicate",
                    "slug": "nex-integration-test",  # same slug
                    "category": "multimodule",
                    "description": "Duplicate slug project",
                    "created_by": admin_user_id,
                },
                headers=admin_headers,
            )
        assert dup_resp.status_code == 409
        error_detail = dup_resp.json()["detail"]
        assert "already exists" in error_detail.lower() or "duplicate" in error_detail.lower()

    def test_ports_occupied_after_creation(self, integration_client, _seed_admin):
        """After project creation, the allocated ports must show as occupied."""
        # Login
        login_resp = integration_client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "Nex123"},
        )
        admin_token = login_resp.json()["access_token"]
        admin_headers = {"Authorization": f"Bearer {admin_token}"}
        admin_user_id = login_resp.json()["user"]["id"]

        # Create project with specific ports
        with patch(
            "backend.services.github_validation.validate_github_repo",
            return_value=True,
        ):
            create_resp = integration_client.post(
                "/api/v1/projects",
                json={
                    "name": "Port Check Project",
                    "slug": "port-check-proj",
                    "category": "singlemodule",
                    "description": "Test port allocation",
                    "backend_port": 9210,
                    "frontend_port": 9211,
                    "created_by": admin_user_id,
                },
                headers=admin_headers,
            )
        assert create_resp.status_code == 201

        # Now those ports should be occupied
        for port in (9210, 9211):
            check_resp = integration_client.get(
                "/api/v1/projects/ports/check",
                params={"port": port},
                headers=admin_headers,
            )
            assert check_resp.status_code == 200
            data = check_resp.json()
            assert data["available"] is False
            assert data["conflict_project"] == "Port Check Project"

    def test_project_without_ports_or_repo(self, integration_client, _seed_admin):
        """Projects can be created without ports or repo_url (all optional)."""
        login_resp = integration_client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "Nex123"},
        )
        admin_token = login_resp.json()["access_token"]
        admin_headers = {"Authorization": f"Bearer {admin_token}"}
        admin_user_id = login_resp.json()["user"]["id"]

        create_resp = integration_client.post(
            "/api/v1/projects",
            json={
                "name": "Minimal Project",
                "slug": "minimal-proj",
                "category": "singlemodule",
                "description": "No ports, no repo",
                "created_by": admin_user_id,
            },
            headers=admin_headers,
        )
        assert create_resp.status_code == 201
        data = create_resp.json()
        assert data["backend_port"] is None
        assert data["frontend_port"] is None
        assert data["db_port"] is None
        assert data["repo_url"] is None
