"""Tests for PATCH /api/v1/auth/me — self-service profile edit (v4.0.33).

Every authenticated user may edit their OWN safe fields (email / name / telegram); role, activation and
username are never editable here. A colliding email → 409.
"""

from __future__ import annotations

from .conftest import login_user, seed_user


class TestSelfProfileUpdate:
    def test_user_updates_own_email_and_telegram(self, client, db_session):
        seed_user(db_session, username="nazar", password="InitPass1", role="shu")
        token = login_user(client, username="nazar", password="InitPass1")

        resp = client.patch(
            "/api/v1/auth/me",
            json={"email": "nazar@icc.sk", "telegram_chat_id": "55501", "first_name": "Nazar"},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["email"] == "nazar@icc.sk"
        assert body["telegram_chat_id"] == "55501"
        assert body["first_name"] == "Nazar"
        # role/username untouched
        assert body["role"] == "shu"
        assert body["username"] == "nazar"

    def test_role_field_is_rejected(self, client, db_session):
        """extra='forbid' — a self update can never carry role/is_active (no privilege escalation)."""
        seed_user(db_session, username="nazar2", password="InitPass1", role="shu")
        token = login_user(client, username="nazar2", password="InitPass1")

        resp = client.patch(
            "/api/v1/auth/me",
            json={"role": "ri"},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert resp.status_code == 422

    def test_email_collision_returns_409(self, client, db_session):
        # seed_user derives the email as "<username>@test.local".
        seed_user(db_session, username="taken", password="InitPass1", role="ha")
        seed_user(db_session, username="nazar3", password="InitPass1", role="shu")
        token = login_user(client, username="nazar3", password="InitPass1")

        resp = client.patch(
            "/api/v1/auth/me",
            json={"email": "taken@test.local"},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert resp.status_code == 409
