"""Integration test — full auth + user management lifecycle.

E2E scenario:
    1. POST /api/v1/auth/login (admin/Nex123) → 200 + JWT
    2. GET  /api/v1/projects (with JWT)        → 200 (dashboard access)
    3. GET  /api/v1/users (with JWT)           → 200 (user management)
    4. POST /api/v1/users (create new user)    → 201
    5. POST /api/v1/users/{id}/change-password → 200
    6. POST /api/v1/auth/logout (admin)        → 204
    7. POST /api/v1/auth/login (new user)      → 200 + JWT
    8. GET  /api/v1/auth/me (new user JWT)     → 200
    9. POST /api/v1/auth/logout (new user)     → 204
"""

from __future__ import annotations

import pytest


@pytest.mark.integration
class TestE2EAuthUserLifecycle:
    """Full E2E: login → dashboard → user mgmt → create user → change pw → logout → login new."""

    def test_full_auth_user_lifecycle(self, integration_client, _seed_admin):
        """Walk through the complete auth + user management flow."""
        # ------------------------------------------------------------------
        # Step 1: Admin login
        # ------------------------------------------------------------------
        login_resp = integration_client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "Nex123"},
        )
        assert login_resp.status_code == 200
        login_data = login_resp.json()
        assert "access_token" in login_data
        assert login_data["token_type"] == "bearer"
        admin_token = login_data["access_token"]
        admin_headers = {"Authorization": f"Bearer {admin_token}"}

        # ------------------------------------------------------------------
        # Step 2: Verify dashboard access (GET /projects)
        # ------------------------------------------------------------------
        projects_resp = integration_client.get(
            "/api/v1/projects",
            headers=admin_headers,
        )
        assert projects_resp.status_code == 200

        # ------------------------------------------------------------------
        # Step 3: Navigate to user management (GET /users)
        # ------------------------------------------------------------------
        users_resp = integration_client.get(
            "/api/v1/users",
            headers=admin_headers,
        )
        assert users_resp.status_code == 200
        users_data = users_resp.json()
        assert "items" in users_data
        # At least admin should exist
        assert users_data["total"] >= 1

        # ------------------------------------------------------------------
        # Step 4: Create new user
        # ------------------------------------------------------------------
        new_user_password = "TestPass123!"
        create_resp = integration_client.post(
            "/api/v1/users",
            json={
                "username": "testuser_e2e",
                "email": "testuser_e2e@example.com",
                "password": new_user_password,
                "role": "ha",
            },
            headers=admin_headers,
        )
        assert create_resp.status_code == 201
        new_user = create_resp.json()
        assert new_user["username"] == "testuser_e2e"
        assert new_user["email"] == "testuser_e2e@example.com"
        assert new_user["role"] == "ha"
        assert new_user["is_active"] is True
        new_user_id = new_user["id"]

        # ------------------------------------------------------------------
        # Step 5: Change new user's password
        # ------------------------------------------------------------------
        changed_password = "ChangedPass456!"
        change_pw_resp = integration_client.post(
            f"/api/v1/users/{new_user_id}/change-password",
            json={"new_password": changed_password},
            headers=admin_headers,
        )
        assert change_pw_resp.status_code == 200
        pw_data = change_pw_resp.json()
        assert pw_data["id"] == new_user_id

        # ------------------------------------------------------------------
        # Step 6: Admin logout
        # ------------------------------------------------------------------
        logout_resp = integration_client.post(
            "/api/v1/auth/logout",
            headers=admin_headers,
        )
        assert logout_resp.status_code == 204

        # ------------------------------------------------------------------
        # Step 7: Login with new user (using changed password)
        # ------------------------------------------------------------------
        new_login_resp = integration_client.post(
            "/api/v1/auth/login",
            json={"username": "testuser_e2e", "password": changed_password},
        )
        assert new_login_resp.status_code == 200
        new_login_data = new_login_resp.json()
        assert "access_token" in new_login_data
        new_user_token = new_login_data["access_token"]
        new_user_headers = {"Authorization": f"Bearer {new_user_token}"}

        # ------------------------------------------------------------------
        # Step 8: Verify new user token works (GET /me)
        # ------------------------------------------------------------------
        me_resp = integration_client.get(
            "/api/v1/auth/me",
            headers=new_user_headers,
        )
        assert me_resp.status_code == 200
        me_data = me_resp.json()
        assert me_data["username"] == "testuser_e2e"
        assert me_data["role"] == "ha"

        # ------------------------------------------------------------------
        # Step 9: New user logout
        # ------------------------------------------------------------------
        new_logout_resp = integration_client.post(
            "/api/v1/auth/logout",
            headers=new_user_headers,
        )
        assert new_logout_resp.status_code == 204

    def test_old_password_rejected_after_change(self, integration_client, _seed_admin):
        """After password change, the original password must be rejected."""
        # Login as admin
        login_resp = integration_client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "Nex123"},
        )
        admin_token = login_resp.json()["access_token"]
        admin_headers = {"Authorization": f"Bearer {admin_token}"}

        # Create user with initial password
        original_password = "Original123!"
        create_resp = integration_client.post(
            "/api/v1/users",
            json={
                "username": "pwtest_user",
                "email": "pwtest@example.com",
                "password": original_password,
                "role": "shu",
            },
            headers=admin_headers,
        )
        assert create_resp.status_code == 201
        user_id = create_resp.json()["id"]

        # Change password
        new_password = "NewSecure789!"
        change_resp = integration_client.post(
            f"/api/v1/users/{user_id}/change-password",
            json={"new_password": new_password},
            headers=admin_headers,
        )
        assert change_resp.status_code == 200

        # Old password should fail
        old_login_resp = integration_client.post(
            "/api/v1/auth/login",
            json={"username": "pwtest_user", "password": original_password},
        )
        assert old_login_resp.status_code == 401

        # New password should work
        new_login_resp = integration_client.post(
            "/api/v1/auth/login",
            json={"username": "pwtest_user", "password": new_password},
        )
        assert new_login_resp.status_code == 200

    def test_admin_token_invalid_after_logout(self, integration_client, _seed_admin):
        """After logout, the admin token should be rejected."""
        # Login
        login_resp = integration_client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "Nex123"},
        )
        token = login_resp.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        # Verify token works
        me_resp = integration_client.get("/api/v1/auth/me", headers=headers)
        assert me_resp.status_code == 200

        # Logout
        logout_resp = integration_client.post("/api/v1/auth/logout", headers=headers)
        assert logout_resp.status_code == 204

        # Token should now be invalid
        me_after = integration_client.get("/api/v1/auth/me", headers=headers)
        assert me_after.status_code == 401
