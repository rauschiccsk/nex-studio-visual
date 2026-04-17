"""Integration test — token rotation on logout.

E2E scenario:
    1. Login → receive JWT (token_version bumped)
    2. Logout (token_version incremented again)
    3. Attempt request with old JWT → 401
"""

from __future__ import annotations

import pytest


@pytest.mark.integration
class TestTokenRotation:
    """Login → logout → old JWT rejected."""

    def test_logout_invalidates_token(self, integration_client, _seed_admin):
        # Step 1: login
        login_resp = integration_client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "Nex123"},
        )
        assert login_resp.status_code == 200
        old_token = login_resp.json()["access_token"]

        # Verify old token works before logout
        me_resp = integration_client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {old_token}"},
        )
        assert me_resp.status_code == 200

        # Step 2: logout — bumps token_version
        logout_resp = integration_client.post(
            "/api/v1/auth/logout",
            headers={"Authorization": f"Bearer {old_token}"},
        )
        assert logout_resp.status_code == 204

        # Step 3: old token should now be rejected (tv < current token_version)
        me_resp2 = integration_client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {old_token}"},
        )
        assert me_resp2.status_code == 401

    def test_new_login_after_logout_works(self, integration_client, _seed_admin):
        # Login
        login_resp = integration_client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "Nex123"},
        )
        assert login_resp.status_code == 200
        old_token = login_resp.json()["access_token"]

        # Logout
        logout_resp = integration_client.post(
            "/api/v1/auth/logout",
            headers={"Authorization": f"Bearer {old_token}"},
        )
        assert logout_resp.status_code == 204

        # Old token is dead
        me_old = integration_client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {old_token}"},
        )
        assert me_old.status_code == 401

        # Fresh login gets a new valid token
        login_resp2 = integration_client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "Nex123"},
        )
        assert login_resp2.status_code == 200
        new_token = login_resp2.json()["access_token"]

        me_new = integration_client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {new_token}"},
        )
        assert me_new.status_code == 200
        assert me_new.json()["username"] == "admin"
