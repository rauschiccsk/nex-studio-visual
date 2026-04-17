"""Tests for GET /api/v1/auth/me endpoint.

Covers:
    * Authenticated request returns user object.
    * Response omits password_hash.
    * Expired / invalid token → 401.
    * Missing token → 401.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from jose import jwt

from backend.config.settings import settings

from .conftest import login_user, seed_user


class TestMeSuccess:
    """GET /api/v1/auth/me — valid token returns user."""

    def test_returns_user_object(self, client, db_session):
        seed_user(db_session)
        token = login_user(client)

        resp = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["username"] == "admin"
        assert body["email"] == "admin@test.local"
        assert body["role"] == "ri"
        assert body["is_active"] is True
        assert "id" in body

    def test_no_password_hash_in_response(self, client, db_session):
        seed_user(db_session)
        token = login_user(client)

        resp = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert resp.status_code == 200
        assert "password_hash" not in resp.json()


class TestMeExpiredToken:
    """GET /api/v1/auth/me — expired token → 401."""

    def test_expired_token_returns_401(self, client, db_session):
        user = seed_user(db_session)

        # Create a token that expired 1 minute ago
        expired_token = jwt.encode(
            {
                "sub": str(user.id),
                "role": user.role,
                "tv": 0,
                "exp": datetime.now(timezone.utc) - timedelta(minutes=1),
            },
            settings.secret_key,
            algorithm="HS256",
        )

        resp = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {expired_token}"},
        )

        assert resp.status_code == 401


class TestMeUnauthenticated:
    """GET /api/v1/auth/me — missing or invalid token."""

    def test_missing_token_returns_401(self, client, db_session):
        resp = client.get("/api/v1/auth/me")
        assert resp.status_code == 401

    def test_invalid_token_returns_401(self, client, db_session):
        resp = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": "Bearer invalid.token.here"},
        )
        assert resp.status_code == 401
