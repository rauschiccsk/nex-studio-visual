"""Tests for POST /api/v1/users (user creation).

Covers:
    * ``ri`` user creates a new user → 201, bcrypt-hashed password,
      UserSession created with ``token_version=0``.
    * Duplicate username → 409.
    * Duplicate email → 409.
    * ``ha`` user cannot create → 403.
    * ``shu`` user cannot create → 403.
"""

from __future__ import annotations

import uuid

import bcrypt
from sqlalchemy import select

from backend.db.models.foundation import UserSession

from .conftest import login_user, seed_user


def _create_payload(**overrides) -> dict:
    """Return a valid user-create payload with unique defaults."""
    suffix = uuid.uuid4().hex[:8]
    body = {
        "username": f"newuser_{suffix}",
        "email": f"{suffix}@example.com",
        "password": "SecurePass123",
        "role": "ha",
    }
    body.update(overrides)
    return body


class TestRiCreatesUser:
    """ri role creates a new user — allowed."""

    def test_returns_201_with_correct_fields(self, client, db_session):
        seed_user(db_session, username="ri_admin", password="Nex12345", role="ri")
        token = login_user(client, username="ri_admin", password="Nex12345")

        payload = _create_payload(
            username="alice",
            email="alice@example.com",
            role="ha",
        )
        resp = client.post(
            "/api/v1/users",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )

        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["username"] == "alice"
        assert body["email"] == "alice@example.com"
        assert body["role"] == "ha"
        assert body["is_active"] is True
        assert "id" in body
        assert "created_at" in body
        assert "updated_at" in body
        # password_hash must NOT be in the response
        assert "password_hash" not in body
        assert "password" not in body

    def test_password_is_bcrypt_hashed(self, client, db_session):
        seed_user(db_session, username="ri_hash", password="Nex12345", role="ri")
        token = login_user(client, username="ri_hash", password="Nex12345")

        payload = _create_payload(username="bob", email="bob@example.com")
        resp = client.post(
            "/api/v1/users",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )

        assert resp.status_code == 201
        user_id = resp.json()["id"]

        # Verify password is bcrypt-hashed in DB
        from backend.db.models.foundation import User

        user = db_session.get(User, uuid.UUID(user_id))
        assert user is not None
        assert bcrypt.checkpw(b"SecurePass123", user.password_hash.encode("utf-8"))

    def test_user_session_created_with_token_version_0(self, client, db_session):
        seed_user(db_session, username="ri_sess", password="Nex12345", role="ri")
        token = login_user(client, username="ri_sess", password="Nex12345")

        payload = _create_payload(username="carol", email="carol@example.com")
        resp = client.post(
            "/api/v1/users",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )

        assert resp.status_code == 201
        user_id = uuid.UUID(resp.json()["id"])

        # Verify UserSession was created
        stmt = select(UserSession).where(UserSession.user_id == user_id)
        session = db_session.execute(stmt).scalar_one_or_none()
        assert session is not None
        assert session.token_version == 0


class TestDuplicateUsername:
    """Duplicate username returns 409."""

    def test_returns_409(self, client, db_session):
        seed_user(db_session, username="ri_dup", password="Nex12345", role="ri")
        token = login_user(client, username="ri_dup", password="Nex12345")

        payload1 = _create_payload(username="dupeuser", email="first@example.com")
        resp1 = client.post(
            "/api/v1/users",
            json=payload1,
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp1.status_code == 201

        payload2 = _create_payload(username="dupeuser", email="second@example.com")
        resp2 = client.post(
            "/api/v1/users",
            json=payload2,
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp2.status_code == 409
        assert "already exists" in resp2.json()["detail"].lower()


class TestSharedEmail:
    """DEV-321: a second account with the same email is created — only the username is unique."""

    def test_returns_201(self, client, db_session):
        seed_user(db_session, username="ri_edup", password="Nex12345", role="ri")
        token = login_user(client, username="ri_edup", password="Nex12345")

        payload1 = _create_payload(username="user_a", email="shared@example.com")
        resp1 = client.post(
            "/api/v1/users",
            json=payload1,
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp1.status_code == 201

        payload2 = _create_payload(username="user_b", email="shared@example.com")
        resp2 = client.post(
            "/api/v1/users",
            json=payload2,
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp2.status_code == 201
        assert resp2.json()["email"] == "shared@example.com"


class TestHaCannotCreate:
    """ha role cannot create users — 403."""

    def test_returns_403(self, client, db_session):
        seed_user(db_session, username="ha_user", password="HaPass123", role="ha")
        token = login_user(client, username="ha_user", password="HaPass123")

        payload = _create_payload()
        resp = client.post(
            "/api/v1/users",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )

        assert resp.status_code == 403


class TestShuCannotCreate:
    """shu role cannot create users — 403."""

    def test_returns_403(self, client, db_session):
        seed_user(db_session, username="shu_user", password="ShuPass12", role="shu")
        token = login_user(client, username="shu_user", password="ShuPass12")

        payload = _create_payload()
        resp = client.post(
            "/api/v1/users",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )

        assert resp.status_code == 403
