"""Pydantic schemas for ProfessionalSpecChatMessage.

Mirrors :class:`backend.db.models.specifications.ProfessionalSpecChatMessage`.
Chat turns are immutable (no Update schema) — a new turn is always a
new row; corrections come as further turns, not edits.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# Mirrors ``ck_professional_spec_chat_messages_role`` (migration 035).
ProfessionalSpecChatRole = Literal["user", "assistant"]


class ProfessionalSpecChatMessageCreate(BaseModel):
    """Payload used by the backend when persisting a turn at the end of a stream."""

    professional_spec_id: UUID
    role: ProfessionalSpecChatRole
    content: str = Field(..., min_length=1)


class ProfessionalSpecChatMessageRead(BaseModel):
    """Serialised representation of a chat message row."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    professional_spec_id: UUID
    role: ProfessionalSpecChatRole
    content: str
    created_at: datetime
