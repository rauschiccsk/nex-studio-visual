"""Service layer for :class:`~backend.db.models.specifications.ProfessionalSpecChatMessage`.

Append-only log of user + assistant turns inside the Vývojová
dokumentácia chat. The parent ``/chat`` endpoint persists one
``user`` row + one ``assistant`` row at the end of each successful
stream; the GET sibling is used by the FE to rehydrate the chat
panel on mount so navigation away and back no longer wipes the
conversation.

All methods take ``db: Session`` as the first argument and only
call ``session.flush()`` — the router owns the commit boundary.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db.models.specifications import ProfessionalSpecChatMessage
from backend.schemas.professional_spec_chat_message import (
    ProfessionalSpecChatMessageCreate,
)


def list_by_spec(
    db: Session,
    professional_spec_id: UUID,
) -> list[ProfessionalSpecChatMessage]:
    """Return every chat message for a spec in chronological order.

    Sorted by ``created_at ASC`` so the caller can render the chat
    panel top-to-bottom without another client-side sort.
    """
    stmt = (
        select(ProfessionalSpecChatMessage)
        .where(ProfessionalSpecChatMessage.professional_spec_id == professional_spec_id)
        .order_by(ProfessionalSpecChatMessage.created_at.asc())
    )
    return list(db.execute(stmt).scalars().all())


def create(
    db: Session,
    data: ProfessionalSpecChatMessageCreate,
) -> ProfessionalSpecChatMessage:
    """Append a single chat turn."""
    row = ProfessionalSpecChatMessage(
        professional_spec_id=data.professional_spec_id,
        role=data.role,
        content=data.content,
    )
    db.add(row)
    db.flush()
    return row
