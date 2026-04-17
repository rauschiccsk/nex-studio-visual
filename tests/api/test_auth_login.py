"""Tests for POST /api/v1/auth/login endpoint.

Covers:
    * Successful login with seeded admin credentials (admin / Nex123).
    * Invalid password → 401.
    * Non-existent username → 401.
    * Inactive user → 401.
"""

from __future__ import annotations

from jose import jwt

from backend.config.settings import settings
from backend.db.models.foundation import UserSession

from .conftest import seed_user


class TestLoginSuccess:
    """POST /api/v1/auth/login — valid credentials."""

    def test_returns_200_with_jwt(self, client, db_session):
        user = seed_user(db_session)

        resp = client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "Nex123"},
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["token_type"] == "bearer"
        assert body["expires_in"] == settings.access_token_expire_minutes * 60
        assert "access_token" in body

        # Verify JWT payload
        payload = jwt.decode(
            body["access_token"],
            settings.secret_key,
            algorithms=["HS256"],
        )
        assert payload["sub"] == str(user.id)
        assert payload["role"] == "ri"
        assert "exp" in payload

    def test_response_contains_user_without_password(self, client, db_session):
        seed_user(db_session)

        resp = client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "Nex123"},
        )

        assert resp.status_code == 200
        user_data = resp.json()["user"]
        assert user_data["username"] == "admin"
        assert user_data["email"] == "admin@test.local"
        assert user_data["role"] == "ri"
        assert user_data["is_active"] is True
        assert "password_hash" not in user_data
        assert "id" in user_data

    def test_token_version_incremented(self, client, db_session):
        user = seed_user(db_session)

        client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "Nex123"},
        )

        # Expire cached ORM state to see changes made by the endpoint
        db_session.expire_all()
        session = db_session.query(UserSession).filter_by(user_id=user.id).first()
        assert session is not None
        assert session.token_version == 1


class TestLoginInvalidCredentials:
    """POST /api/v1/auth/login — wrong password or missing user."""

    def test_wrong_password_returns_401(self, client, db_session):
        seed_user(db_session)

        resp = client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "WrongPassword"},
        )

        assert resp.status_code == 401
        assert resp.json()["detail"] == "Invalid username or password"

    def test_nonexistent_user_returns_401(self, client, db_session):
        resp = client.post(
            "/api/v1/auth/login",
            json={"username": "nonexistent", "password": "anything"},
        )

        assert resp.status_code == 401
        assert resp.json()["detail"] == "Invalid username or password"


class TestLoginInactiveUser:
    """POST /api/v1/auth/login — inactive user account."""

    def test_inactive_user_returns_401(self, client, db_session):
        seed_user(db_session, username="inactive_user", is_active=False)

        resp = client.post(
            "/api/v1/auth/login",
            json={"username": "inactive_user", "password": "Nex123"},
        )

        assert resp.status_code == 401
        assert resp.json()["detail"] == "User account is inactive"
