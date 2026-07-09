"""Tests for POST /api/v1/auth/refresh — sliding session renewal.

Covers the spec (docs/specs/session-keepalive.md §Tests, Backend):
    * A VALID, non-expired token → 200 + a NEW token whose ``exp`` is later
      than the old one, for the SAME user + SAME ``token_version`` (no bump).
    * An EXPIRED token → 401 (a dead session must NOT be renewed).
    * A bumped ``token_version`` (superseded session) → 401.
    * The renewed token is actually usable on a protected route.
    * A missing token → 401.

The refresh endpoint reuses the ``get_current_user`` dependency (same gate as
every protected route), so the 401 paths exercise the real JWT verification —
these tests therefore use the ROOT ``client`` fixture (which does NOT override
auth), exactly like the other ``tests/api/test_auth_*.py`` files.
"""

from __future__ import annotations

from jose import jwt

from backend.config.settings import settings
from backend.db.models.foundation import UserSession
from backend.services.auth import create_access_token

from .conftest import login_user, seed_user

_EXPIRE_MINUTES = 480  # default access_token_expire_minutes (see system_setting)


def _decode(token: str) -> dict:
    return jwt.decode(token, settings.secret_key, algorithms=["HS256"])


class TestRefreshSuccess:
    """POST /api/v1/auth/refresh — valid token issues a fresh, later token."""

    def test_valid_token_returns_new_token_with_later_expiry(self, client, db_session):
        user = seed_user(db_session)
        login_user(client)  # bumps DB token_version → 1

        # A still-VALID token issued earlier in the session (near expiry): its
        # exp is only ~1 min out, so the renewed full-lifetime token is
        # unambiguously LATER.
        old_token, _ = create_access_token(user, token_version=1, expire_minutes=1)

        resp = client.post(
            "/api/v1/auth/refresh",
            headers={"Authorization": f"Bearer {old_token}"},
        )

        assert resp.status_code == 200
        body = resp.json()

        # Same response shape as /login.
        assert set(body) >= {"access_token", "token_type", "expires_in", "user"}
        assert body["token_type"] == "bearer"
        assert body["expires_in"] == _EXPIRE_MINUTES * 60
        assert body["user"]["username"] == "admin"

        old_claims = _decode(old_token)
        new_claims = _decode(body["access_token"])
        # Fresh, strictly-later expiry …
        assert new_claims["exp"] > old_claims["exp"]
        # … for the SAME user + SAME token_version (NOT bumped, unlike login).
        assert new_claims["sub"] == old_claims["sub"] == str(user.id)
        assert new_claims["tv"] == old_claims["tv"] == 1

    def test_renewed_token_is_usable_on_a_protected_route(self, client, db_session):
        seed_user(db_session)
        token = login_user(client)

        resp = client.post(
            "/api/v1/auth/refresh",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        new_token = resp.json()["access_token"]

        # The freshly-issued token authenticates a subsequent request.
        me = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {new_token}"},
        )
        assert me.status_code == 200
        assert me.json()["username"] == "admin"


class TestRefreshRejectsDeadSessions:
    """POST /api/v1/auth/refresh — expired / superseded / missing → 401."""

    def test_expired_token_returns_401(self, client, db_session):
        user = seed_user(db_session)
        login_user(client)  # DB token_version → 1

        # exp in the PAST → python-jose raises on decode → get_current_user 401.
        expired_token, _ = create_access_token(user, token_version=1, expire_minutes=-1)

        resp = client.post(
            "/api/v1/auth/refresh",
            headers={"Authorization": f"Bearer {expired_token}"},
        )
        assert resp.status_code == 401

    def test_bumped_token_version_returns_401(self, client, db_session):
        user = seed_user(db_session)
        login_user(client)  # DB token_version → 1

        # A non-expired token for tv=1 …
        stale_token, _ = create_access_token(user, token_version=1, expire_minutes=_EXPIRE_MINUTES)

        # … but the session was superseded (e.g. password change / logout
        # elsewhere) → DB token_version is now 2, so tv=1 is stale.
        session = db_session.query(UserSession).filter_by(user_id=user.id).first()
        session.token_version = 2
        db_session.flush()

        resp = client.post(
            "/api/v1/auth/refresh",
            headers={"Authorization": f"Bearer {stale_token}"},
        )
        assert resp.status_code == 401

    def test_missing_token_returns_401(self, client, db_session):
        resp = client.post("/api/v1/auth/refresh")
        assert resp.status_code == 401
