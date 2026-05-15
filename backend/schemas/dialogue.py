"""Pydantic schemas for Customer ↔ Designer dialogue (Gate E).

Mirrors :class:`backend.db.models.dialogue.{DialogueSession,DialogueMessage}`
for REST responses, plus input schemas for session create + Director-
injected messages.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

MessageAuthor = Literal["customer", "designer", "director"]
MessageStatus = Literal["pending", "approved", "delivered", "rejected"]
SessionStatus = Literal["active", "paused", "ended"]
TerminatedBy = Literal["user", "timeout", "server_restart", "coverage_complete"]


class DialogueMessageRead(BaseModel):
    """Single message in a Gate E dialogue (REST response)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    session_id: uuid.UUID
    author: MessageAuthor
    content: str
    status: MessageStatus
    created_at: datetime
    updated_at: datetime


class DialogueSessionRead(BaseModel):
    """Session-level state (REST response)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    project_slug: str
    version_id: Optional[uuid.UUID]
    status: SessionStatus
    message_count: int
    created_at: datetime
    updated_at: datetime
    ended_at: Optional[datetime]
    terminated_by: Optional[TerminatedBy]


class DialogueSessionWithMessages(DialogueSessionRead):
    """Session with all its messages — used by GET /sessions/{id} for
    rehydrating the /dialogue page after a refresh."""

    messages: list[DialogueMessageRead] = Field(default_factory=list)


class DialogueSessionCreate(BaseModel):
    """POST /dialogue/sessions body. Director starts a fresh Gate E
    session for a project + (optional) version."""

    project_slug: str = Field(min_length=1, max_length=100)
    version_id: Optional[uuid.UUID] = None


class DirectorInjectMessage(BaseModel):
    """POST /dialogue/sessions/{id}/director-inject body. Director can
    inject a custom question/clarification targeting either agent."""

    recipient: Literal["customer", "designer"]
    content: str = Field(min_length=1)
