"""Tests for POST /api/v1/auth/logout endpoint.

Covers:
    * Successful logout — returns 204.
    * Token invalidation — subsequent request with same token → 401.
    * Unauthenticated request → 401.
"""

from __future__ import annotations

from backend.db.models.foundation import UserSession

from .conftest import login_user, seed_user


class TestLogoutSuccess:
    """POST /api/v1/auth/logout — valid token."""

    def test_returns_204(self, client, db_session):
        seed_user(db_session)
        token = login_user(client)

        resp = client.post(
            "/api/v1/auth/logout",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert resp.status_code == 204

    def test_token_version_incremented(self, client, db_session):
        user = seed_user(db_session)
        token = login_user(client)

        # After login, token_version should be 1
        db_session.expire_all()
        session = db_session.query(UserSession).filter_by(user_id=user.id).first()
        assert session.token_version == 1

        client.post(
            "/api/v1/auth/logout",
            headers={"Authorization": f"Bearer {token}"},
        )

        # After logout, token_version should be 2
        db_session.expire_all()
        session = db_session.query(UserSession).filter_by(user_id=user.id).first()
        assert session.token_version == 2


class TestLogoutInvalidatesToken:
    """POST /api/v1/auth/logout — old token rejected after logout."""

    def test_old_token_rejected_after_logout(self, client, db_session):
        seed_user(db_session)
        token = login_user(client)

        # Logout — bumps token_version
        resp = client.post(
            "/api/v1/auth/logout",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 204

        # Same token should now be rejected on /auth/me
        resp = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 401


class TestLogoutUnauthenticated:
    """POST /api/v1/auth/logout — no token."""

    def test_missing_token_returns_401(self, client, db_session):
        resp = client.post("/api/v1/auth/logout")
        assert resp.status_code == 401
