"""Service-layer tests for professional_spec_chat_message.

Covers the append-only log that backs the Vývojová dokumentácia
chat panel rehydration. Keeps pace with the same patterns used in
``tests/test_architect_message_service.py``.
"""

from __future__ import annotations

import uuid

import pytest

from backend.db.models.foundation import User
from backend.db.models.projects import Project
from backend.db.models.specifications import (
    ProfessionalSpecChatMessage,
    ProfessionalSpecification,
    RawSpecification,
)
from backend.schemas.professional_spec_chat_message import (
    ProfessionalSpecChatMessageCreate,
)
from backend.services import professional_spec_chat_message as service


def _seed_spec(db_session) -> ProfessionalSpecification:
    """Create the minimum FK chain (user → project → raw_spec → prof_spec)."""
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

    raw = RawSpecification(
        project_id=project.id,
        input_text="raw",
        created_by=user.id,
    )
    db_session.add(raw)
    db_session.flush()

    spec = ProfessionalSpecification(
        raw_spec_id=raw.id,
        project_id=project.id,
        content="content",
    )
    db_session.add(spec)
    db_session.flush()
    return spec


class TestProfessionalSpecChatMessageService:
    """Create + list against a SAVEPOINT-isolated DB session."""

    def test_create_and_list_preserves_order(self, db_session):
        """``list_by_spec`` returns rows sorted by ``created_at ASC``."""
        spec = _seed_spec(db_session)

        service.create(
            db_session,
            ProfessionalSpecChatMessageCreate(
                professional_spec_id=spec.id, role="user", content="first"
            ),
        )
        service.create(
            db_session,
            ProfessionalSpecChatMessageCreate(
                professional_spec_id=spec.id, role="assistant", content="second"
            ),
        )
        service.create(
            db_session,
            ProfessionalSpecChatMessageCreate(
                professional_spec_id=spec.id, role="user", content="third"
            ),
        )

        rows = service.list_by_spec(db_session, spec.id)
        assert [r.content for r in rows] == ["first", "second", "third"]
        assert [r.role for r in rows] == ["user", "assistant", "user"]

    def test_list_scoped_to_spec(self, db_session):
        """Messages from other specs are not returned."""
        spec_a = _seed_spec(db_session)
        spec_b = _seed_spec(db_session)

        service.create(
            db_session,
            ProfessionalSpecChatMessageCreate(
                professional_spec_id=spec_a.id, role="user", content="a1"
            ),
        )
        service.create(
            db_session,
            ProfessionalSpecChatMessageCreate(
                professional_spec_id=spec_b.id, role="user", content="b1"
            ),
        )

        rows_a = service.list_by_spec(db_session, spec_a.id)
        rows_b = service.list_by_spec(db_session, spec_b.id)
        assert [r.content for r in rows_a] == ["a1"]
        assert [r.content for r in rows_b] == ["b1"]

    def test_cascade_on_parent_delete(self, db_session):
        """Deleting the parent spec wipes its chat messages via FK cascade."""
        spec = _seed_spec(db_session)
        service.create(
            db_session,
            ProfessionalSpecChatMessageCreate(
                professional_spec_id=spec.id, role="user", content="orphan-me"
            ),
        )
        db_session.flush()

        db_session.delete(spec)
        db_session.flush()

        # No rows should remain for the deleted spec id.
        from sqlalchemy import select
        remaining = (
            db_session.execute(
                select(ProfessionalSpecChatMessage).where(
                    ProfessionalSpecChatMessage.professional_spec_id == spec.id
                )
            )
            .scalars()
            .all()
        )
        assert remaining == []

    def test_invalid_role_rejected_by_db(self, db_session):
        """The CHECK constraint rejects any role other than user / assistant."""
        spec = _seed_spec(db_session)
        row = ProfessionalSpecChatMessage(
            professional_spec_id=spec.id,
            role="system",
            content="x",
        )
        db_session.add(row)
        with pytest.raises(Exception):
            db_session.flush()
