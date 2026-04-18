"""Integration test — Settings page access (role-based).

E2E scenario:
    1. Login as ri (admin) → GET /api/v1/users → 200 (Správa používateľov visible)
    2. Login as ha → GET /api/v1/users → 403 (Správa používateľov hidden)

The Settings page has two sections:
  - "Vzhľad" (dark mode toggle) — accessible to all roles
  - "Správa používateľov" (user management) — ri only

The "Správa používateľov" tab loads its content from GET /api/v1/users,
which requires ri role.  Non-ri users receive 403 Forbidden.
"""

from __future__ import annotations

import bcrypt
import pytest

from backend.db.models.foundation import User, UserSession

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_user(db_session, *, username: str, password: str, role: str) -> User:
    """Create a user with bcrypt-hashed password and a UserSession row."""
    password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=4)).decode()
    user = User(
        username=username,
        email=f"{username}@isnex.eu",
        password_hash=password_hash,
        role=role,
        is_active=True,
    )
    db_session.add(user)
    db_session.flush()

    session = UserSession(user_id=user.id, token_version=0)
    db_session.add(session)
    db_session.flush()
    return user


def _login(client, *, username: str, password: str) -> dict:
    """Login and return (token, headers) dict."""
    resp = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
    )
    assert resp.status_code == 200, f"Login failed for {username}: {resp.text}"
    data = resp.json()
    token = data["access_token"]
    return {
        "token": token,
        "headers": {"Authorization": f"Bearer {token}"},
        "user": data["user"],
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestSettingsPageAccess:
    """Verify role-based visibility of 'Správa používateľov' tab on Settings page."""

    def test_ri_can_access_user_management(self, integration_client, _seed_admin):
        """RI role → GET /api/v1/users returns 200 (tab content loads)."""
        auth = _login(integration_client, username="admin", password="Nex123")

        resp = integration_client.get("/api/v1/users", headers=auth["headers"])
        assert resp.status_code == 200

        data = resp.json()
        assert "items" in data
        assert "total" in data
        # Admin user should appear in the list
        usernames = [u["username"] for u in data["items"]]
        assert "admin" in usernames

    def test_ri_can_access_own_profile(self, integration_client, _seed_admin):
        """RI role → GET /api/v1/auth/me returns 200 (Vzhľad section always available)."""
        auth = _login(integration_client, username="admin", password="Nex123")

        resp = integration_client.get("/api/v1/auth/me", headers=auth["headers"])
        assert resp.status_code == 200

        me = resp.json()
        assert me["username"] == "admin"
        assert me["role"] == "ri"

    def test_ha_cannot_access_user_management(self, integration_client, db_session):
        """HA role → GET /api/v1/users returns 403 (tab hidden / content blocked)."""
        _seed_user(db_session, username="tester_ha", password="Test123", role="ha")

        auth = _login(integration_client, username="tester_ha", password="Test123")

        resp = integration_client.get("/api/v1/users", headers=auth["headers"])
        assert resp.status_code == 403

    def test_ha_can_access_own_profile(self, integration_client, db_session):
        """HA role → GET /api/v1/auth/me returns 200 (Vzhľad section available)."""
        _seed_user(db_session, username="tester_ha2", password="Test123", role="ha")

        auth = _login(integration_client, username="tester_ha2", password="Test123")

        resp = integration_client.get("/api/v1/auth/me", headers=auth["headers"])
        assert resp.status_code == 200

        me = resp.json()
        assert me["username"] == "tester_ha2"
        assert me["role"] == "ha"

    def test_shu_cannot_access_user_management(self, integration_client, db_session):
        """SHU role → GET /api/v1/users returns 403 (same as HA)."""
        _seed_user(db_session, username="tester_shu", password="Test123", role="shu")

        auth = _login(integration_client, username="tester_shu", password="Test123")

        resp = integration_client.get("/api/v1/users", headers=auth["headers"])
        assert resp.status_code == 403

    def test_ha_cannot_create_user(self, integration_client, db_session):
        """HA role → POST /api/v1/users returns 403 (cannot create users)."""
        _seed_user(db_session, username="tester_ha3", password="Test123", role="ha")

        auth = _login(integration_client, username="tester_ha3", password="Test123")

        resp = integration_client.post(
            "/api/v1/users",
            json={
                "username": "newuser",
                "email": "newuser@example.com",
                "password": "NewPass123",
                "role": "shu",
            },
            headers=auth["headers"],
        )
        assert resp.status_code == 403

    def test_ha_cannot_delete_user(self, integration_client, _seed_admin, db_session):
        """HA role → DELETE /api/v1/users/{id} returns 403."""
        _seed_user(db_session, username="tester_ha4", password="Test123", role="ha")

        # Get admin user id via ri login
        ri_auth = _login(integration_client, username="admin", password="Nex123")
        admin_id = ri_auth["user"]["id"]

        # Login as ha and try to delete admin
        ha_auth = _login(integration_client, username="tester_ha4", password="Test123")

        resp = integration_client.delete(
            f"/api/v1/users/{admin_id}",
            headers=ha_auth["headers"],
        )
        assert resp.status_code == 403
