"""Tests for DELETE /api/v1/users/{id} (hard delete — Director directive 2026-05-13).

Covers:
    * ``ri`` user deletes another user → 204, row gone from DB.
    * Email + username freed after delete (recreation works).
    * ``ri`` user cannot delete self → 400.

History: until 2026-05-13 this endpoint was a soft-delete
(``update(is_active=False)``). The semantic mismatch — UI "trash" icon
suggesting a real delete while the row stayed in the DB blocking new
users with the same email/username — was the bug Director hit when
recreating "tibi". The endpoint now does what the verb says.
"""

from __future__ import annotations

import uuid

from .conftest import login_user, seed_user


class TestRiDeletesUser:
    """ri role hard-deletes a user — 204 + row gone."""

    def test_returns_204_and_row_is_gone(self, client, db_session):
        seed_user(db_session, username="ri_del", password="Nex12345", role="ri")
        token = login_user(client, username="ri_del", password="Nex12345")

        # Create a target user via API
        suffix = uuid.uuid4().hex[:8]
        create_resp = client.post(
            "/api/v1/users",
            json={
                "username": f"target_{suffix}",
                "email": f"{suffix}@example.com",
                "password": "SecurePass123",
                "role": "ha",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert create_resp.status_code == 201
        target_id = create_resp.json()["id"]

        # Hard delete
        resp = client.delete(
            f"/api/v1/users/{target_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 204

        # Row is gone — GET returns 404.
        get_resp = client.get(
            f"/api/v1/users/{target_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert get_resp.status_code == 404

    def test_username_and_email_freed_for_reuse(self, client, db_session):
        """After hard delete the username + email are free again.

        Guards against the regression where soft-delete kept the UNIQUE
        constraint and blocked recreation with the same credentials.
        """
        seed_user(db_session, username="ri_reuse", password="Nex12345", role="ri")
        token = login_user(client, username="ri_reuse", password="Nex12345")

        payload = {
            "username": "recycle_target",
            "email": "recycle@example.com",
            "password": "SecurePass123",
            "role": "ha",
        }

        # Create → delete → recreate with the same username + email.
        first = client.post(
            "/api/v1/users",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )
        assert first.status_code == 201
        first_id = first.json()["id"]

        del_resp = client.delete(
            f"/api/v1/users/{first_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert del_resp.status_code == 204

        second = client.post(
            "/api/v1/users",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )
        assert second.status_code == 201, second.text


class TestRiCannotDeleteSelf:
    """ri user cannot delete own account — 400."""

    def test_returns_400(self, client, db_session):
        ri = seed_user(db_session, username="ri_self_del", password="Nex12345", role="ri")
        token = login_user(client, username="ri_self_del", password="Nex12345")

        resp = client.delete(
            f"/api/v1/users/{ri.id}",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert resp.status_code == 400
        assert "delete" in resp.json()["detail"].lower()
