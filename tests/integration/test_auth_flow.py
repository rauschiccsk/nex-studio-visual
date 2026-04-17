"""Integration test — login → me → project creation.

E2E scenario:
    1. POST /api/v1/auth/login (admin/Nex123) → 200 + JWT
    2. GET  /api/v1/auth/me (with JWT)        → 200 + user object
    3. POST /api/v1/projects (with JWT)        → 201 project created
"""

from __future__ import annotations

import pytest


@pytest.mark.integration
class TestAuthFlow:
    """Login → /me → create project."""

    def test_login_returns_jwt(self, integration_client, _seed_admin):
        resp = integration_client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "Nex123"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"
        assert data["user"]["username"] == "admin"
        assert data["user"]["role"] == "ri"

    def test_login_then_me(self, integration_client, _seed_admin):
        # Step 1: login
        login_resp = integration_client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "Nex123"},
        )
        assert login_resp.status_code == 200
        token = login_resp.json()["access_token"]

        # Step 2: /me
        me_resp = integration_client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert me_resp.status_code == 200
        me_data = me_resp.json()
        assert me_data["username"] == "admin"
        assert me_data["email"] == "admin@isnex.eu"
        assert me_data["role"] == "ri"
        assert me_data["is_active"] is True

    def test_login_then_create_project(self, integration_client, _seed_admin):
        # Step 1: login
        login_resp = integration_client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "Nex123"},
        )
        assert login_resp.status_code == 200
        token = login_resp.json()["access_token"]
        user_id = login_resp.json()["user"]["id"]

        # Step 2: /me to confirm auth works
        me_resp = integration_client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert me_resp.status_code == 200

        # Step 3: create project
        project_resp = integration_client.post(
            "/api/v1/projects",
            json={
                "name": "Test Auth Project",
                "slug": "test-auth-project",
                "category": "multimodule",
                "description": "Created during auth integration test",
                "created_by": user_id,
            },
        )
        assert project_resp.status_code == 201
        project_data = project_resp.json()
        assert project_data["name"] == "Test Auth Project"
        assert project_data["slug"] == "test-auth-project"
        assert project_data["category"] == "multimodule"
        assert project_data["status"] == "active"

    def test_login_bad_password_returns_401(self, integration_client, _seed_admin):
        resp = integration_client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "wrong"},
        )
        assert resp.status_code == 401

    def test_me_without_token_returns_401(self, integration_client):
        resp = integration_client.get("/api/v1/auth/me")
        assert resp.status_code == 401
