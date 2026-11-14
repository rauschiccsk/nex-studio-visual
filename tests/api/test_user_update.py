"""Tests for PATCH /api/v1/users/{id} (user update).

Covers:
    * ``ri`` user updates another user's role/email → 200.
    * ``ri`` user cannot deactivate self → 400.
    * ``ha`` user cannot update → 403.
"""

from __future__ import annotations

import uuid

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


class TestRiUpdatesUser:
    """ri role can update another user — 200."""

    def test_returns_200_with_updated_fields(self, client, db_session):
        seed_user(db_session, username="ri_upd", password="Nex12345", role="ri")
        token = login_user(client, username="ri_upd", password="Nex12345")

        # Create a target user
        payload = _create_payload(username="target_upd", email="target_upd@example.com", role="ha")
        create_resp = client.post(
            "/api/v1/users",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )
        assert create_resp.status_code == 201
        target_id = create_resp.json()["id"]

        # Update role and email
        resp = client.patch(
            f"/api/v1/users/{target_id}",
            json={"role": "shu", "email": "updated@example.com"},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["role"] == "shu"
        assert body["email"] == "updated@example.com"
        # Unchanged fields preserved
        assert body["username"] == "target_upd"
        assert body["is_active"] is True


class TestRiCannotDeactivateSelf:
    """ri user cannot set is_active=False on own account — 400."""

    def test_returns_400(self, client, db_session):
        ri = seed_user(db_session, username="ri_self", password="Nex12345", role="ri")
        token = login_user(client, username="ri_self", password="Nex12345")

        resp = client.patch(
            f"/api/v1/users/{ri.id}",
            json={"is_active": False},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert resp.status_code == 400
        assert resp.json()["detail"] == "Vlastný účet vypnúť nemôžeš."


class TestHaCannotUpdate:
    """ha role cannot update users — 403."""

    def test_returns_403(self, client, db_session):
        # Create an ri user and a target user first
        seed_user(db_session, username="ri_for_ha", password="Nex12345", role="ri")
        target = seed_user(db_session, username="target_ha", password="Nex12345", role="shu")

        # Login as ha user
        seed_user(db_session, username="ha_upd", password="HaPass123", role="ha")
        token = login_user(client, username="ha_upd", password="HaPass123")

        resp = client.patch(
            f"/api/v1/users/{target.id}",
            json={"role": "ha"},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert resp.status_code == 403
