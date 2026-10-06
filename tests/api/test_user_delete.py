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


class TestDeleteKeepsWhatTheUserPaidFor:
    """ICCINT-169: zmazanie používateľa nesmie zobrať jeho rozhovory s Poradcom — s nimi by z Nákladov projektu zmizla
    cena odpovedí, hoci sa naozaj minula. Kokpit zmazanie odmietne a panel ponúkne deaktiváciu."""

    def _ri(self, client, db_session, name):
        seed_user(db_session, username=name, password="Nex12345", role="ri")
        return login_user(client, username=name, password="Nex12345")

    def test_a_user_with_poradca_conversations_is_not_deleted_and_the_cost_stays(self, client, db_session):
        from backend.db.models.poradca import PoradcaConversation, PoradcaMessage
        from backend.db.models.projects import Project

        token = self._ri(client, db_session, "ri_del_poradca")
        owner = seed_user(db_session, username=f"owner_{uuid.uuid4().hex[:6]}", password="Nex12345", role="ri")
        asker = seed_user(db_session, username=f"asker_{uuid.uuid4().hex[:6]}", password="Nex12345", role="ha")
        project = Project(
            name="P",
            slug=f"p-{uuid.uuid4().hex[:8]}",
            type="standard",
            auth_mode="password",
            description="d",
            created_by=owner.id,
        )
        db_session.add(project)
        db_session.flush()
        conversation = PoradcaConversation(
            project_id=project.id, author_id=asker.id, title="t", claude_session_id=uuid.uuid4()
        )
        db_session.add(conversation)
        db_session.flush()
        answer = PoradcaMessage(
            conversation_id=conversation.id,
            author="poradca",
            status="done",
            usage={"input_tokens": 1, "output_tokens": 2, "model": "m"},
        )
        db_session.add(answer)
        db_session.commit()

        resp = client.delete(f"/api/v1/users/{asker.id}", headers={"Authorization": f"Bearer {token}"})

        assert resp.status_code == 422
        assert resp.json()["detail"] == "používateľ má rozhovory s Poradcom a ich cena je v Nákladoch projektov"
        db_session.expire_all()
        assert db_session.get(PoradcaMessage, answer.id) is not None  # odpoveď aj jej cena ostali

    def test_the_database_itself_refuses_to_cascade_the_conversations(self, db_session):
        """Aj mimo kokpitu (priame zmazanie riadku) databáza rozhovor nezoberie — kľúč je RESTRICT, nie CASCADE."""
        import pytest
        from sqlalchemy import text
        from sqlalchemy.exc import DBAPIError

        from backend.db.models.poradca import PoradcaConversation
        from backend.db.models.projects import Project

        owner = seed_user(db_session, username=f"o_{uuid.uuid4().hex[:6]}", password="Nex12345", role="ri")
        asker = seed_user(db_session, username=f"a_{uuid.uuid4().hex[:6]}", password="Nex12345", role="ha")
        project = Project(
            name="P",
            slug=f"p-{uuid.uuid4().hex[:8]}",
            type="standard",
            auth_mode="password",
            description="d",
            created_by=owner.id,
        )
        db_session.add(project)
        db_session.flush()
        db_session.add(
            PoradcaConversation(project_id=project.id, author_id=asker.id, title="t", claude_session_id=uuid.uuid4())
        )
        db_session.flush()
        with pytest.raises(DBAPIError, match="poradca_conversations_author_id_fkey"):
            with db_session.begin_nested():
                db_session.execute(text("DELETE FROM users WHERE id = :id"), {"id": asker.id})

    def test_the_refusal_names_projects_in_slovak_too(self, client, db_session):
        from backend.db.models.projects import Project

        token = self._ri(client, db_session, "ri_del_projects")
        creator = seed_user(db_session, username=f"c_{uuid.uuid4().hex[:6]}", password="Nex12345", role="ha")
        db_session.add(
            Project(
                name="P",
                slug=f"p-{uuid.uuid4().hex[:8]}",
                type="standard",
                auth_mode="password",
                description="d",
                created_by=creator.id,
            )
        )
        db_session.commit()
        resp = client.delete(f"/api/v1/users/{creator.id}", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 422
        assert resp.json()["detail"] == "používateľ založil projekty"
