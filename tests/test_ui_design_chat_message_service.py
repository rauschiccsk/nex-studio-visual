"""Service-layer tests for ui_design_chat_message.

Parallel to ``tests/test_professional_spec_chat_message_service.py``
— same append-only log, same CASCADE + role CHECK guarantees.
"""

from __future__ import annotations

import uuid

import pytest

from backend.db.models.foundation import User
from backend.db.models.projects import Project
from backend.db.models.specifications import UIDesign, UIDesignChatMessage
from backend.schemas.ui_design_chat_message import UIDesignChatMessageCreate
from backend.services import ui_design_chat_message as service


def _seed_ui_design(db_session) -> UIDesign:
    user = User(
        username=f"u_{uuid.uuid4().hex[:8]}",
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        password_hash="hashed",
        role="ri",
    )
    db_session.add(user)
    db_session.flush()

    project = Project(
        name=f"P {uuid.uuid4().hex[:8]}",
        slug=f"p-{uuid.uuid4().hex[:8]}",
        category="singlemodule",
        description="x",
        created_by=user.id,
    )
    db_session.add(project)
    db_session.flush()

    ui = UIDesign(project_id=project.id, content="sketch", html_preview=None)
    db_session.add(ui)
    db_session.flush()
    return ui


class TestUIDesignChatMessageService:
    def test_create_and_list_preserves_order(self, db_session):
        ui = _seed_ui_design(db_session)
        service.create(
            db_session,
            UIDesignChatMessageCreate(ui_design_id=ui.id, role="user", content="first"),
        )
        service.create(
            db_session,
            UIDesignChatMessageCreate(ui_design_id=ui.id, role="assistant", content="second"),
        )
        service.create(
            db_session,
            UIDesignChatMessageCreate(ui_design_id=ui.id, role="user", content="third"),
        )

        rows = service.list_by_ui_design(db_session, ui.id)
        assert [r.content for r in rows] == ["first", "second", "third"]
        assert [r.role for r in rows] == ["user", "assistant", "user"]

    def test_cascade_on_parent_delete(self, db_session):
        ui = _seed_ui_design(db_session)
        service.create(
            db_session,
            UIDesignChatMessageCreate(ui_design_id=ui.id, role="user", content="orphan-me"),
        )
        db_session.flush()

        db_session.delete(ui)
        db_session.flush()

        from sqlalchemy import select

        remaining = (
            db_session.execute(select(UIDesignChatMessage).where(UIDesignChatMessage.ui_design_id == ui.id))
            .scalars()
            .all()
        )
        assert remaining == []

    def test_invalid_role_rejected_by_db(self, db_session):
        ui = _seed_ui_design(db_session)
        row = UIDesignChatMessage(ui_design_id=ui.id, role="system", content="x")
        db_session.add(row)
        with pytest.raises(Exception):
            db_session.flush()
