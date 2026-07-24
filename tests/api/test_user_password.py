"""Tests for POST /api/v1/users/{id}/change-password endpoint.

Covers:
    * ``ri`` user changes another user's password → 200 (admin reset, no current password).
    * ``ha`` user changes own password WITH the correct current password → 200 (v4.0.32 self-service).
    * ``ha`` self change WITHOUT the current password → 400 (v4.0.32).
    * ``ha`` user attempts to change another user's password → 403.
    * Short password (< 5 chars) → 422 validation error.
    * Non-existent user_id → 404.
"""

from __future__ import annotations

import bcrypt

from .conftest import login_user, seed_user


class TestRiChangesOtherUser:
    """ri role changes another user's password — allowed."""

    def test_returns_200_and_updates_hash(self, client, db_session):
        # Seed ri admin and ha target
        seed_user(db_session, username="admin", password="Nex12345", role="ri")
        ha_user = seed_user(db_session, username="developer", password="OldPass123", role="ha")

        token = login_user(client, username="admin", password="Nex12345")

        resp = client.post(
            f"/api/v1/users/{ha_user.id}/change-password",
            json={"new_password": "NewSecure99"},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == str(ha_user.id)
        assert body["username"] == "developer"

        # Verify password was actually changed in DB
        db_session.expire_all()
        db_session.refresh(ha_user)
        assert bcrypt.checkpw(b"NewSecure99", ha_user.password_hash.encode("utf-8"))


class TestHaChangesSelf:
    """ha role changes own password — allowed, but must confirm the current password (v4.0.32)."""

    def test_returns_200(self, client, db_session):
        ha_user = seed_user(db_session, username="devha", password="OldPass123", role="ha")

        token = login_user(client, username="devha", password="OldPass123")

        resp = client.post(
            f"/api/v1/users/{ha_user.id}/change-password",
            json={"new_password": "BrandNew88", "current_password": "OldPass123"},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert resp.status_code == 200

    def test_missing_current_password_returns_400(self, client, db_session):
        """A non-admin self change WITHOUT the current password is rejected (v4.0.32)."""
        ha_user = seed_user(db_session, username="devha2", password="OldPass123", role="ha")
        token = login_user(client, username="devha2", password="OldPass123")

        resp = client.post(
            f"/api/v1/users/{ha_user.id}/change-password",
            json={"new_password": "BrandNew88"},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert resp.status_code == 400


class TestHaChangesOtherUser:
    """ha role tries to change another user's password — forbidden."""

    def test_returns_403(self, client, db_session):
        seed_user(db_session, username="dev1", password="HaPass123", role="ha")
        other_user = seed_user(db_session, username="dev2", password="OtherPass1", role="shu")

        token = login_user(client, username="dev1", password="HaPass123")

        resp = client.post(
            f"/api/v1/users/{other_user.id}/change-password",
            json={"new_password": "HackedPass1"},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert resp.status_code == 403


class TestPasswordValidation:
    """Short password rejected by schema validation."""

    def test_short_password_returns_422(self, client, db_session):
        """Password below the schema min_length (5 — Director directive
        2026-05-13) is rejected by Pydantic with 422."""
        user = seed_user(db_session, username="admin2", password="Nex12345", role="ri")
        token = login_user(client, username="admin2", password="Nex12345")

        resp = client.post(
            f"/api/v1/users/{user.id}/change-password",
            json={"new_password": "abc"},  # 3 chars, below min_length=5
            headers={"Authorization": f"Bearer {token}"},
        )

        assert resp.status_code == 422


class TestNonExistentUser:
    """Target user does not exist → 404."""

    def test_returns_404(self, client, db_session):
        seed_user(db_session, username="admin3", password="Nex12345", role="ri")
        token = login_user(client, username="admin3", password="Nex12345")

        import uuid

        fake_id = uuid.uuid4()
        resp = client.post(
            f"/api/v1/users/{fake_id}/change-password",
            json={"new_password": "ValidPass88"},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert resp.status_code == 404
