"""Pydantic schemas for ArchitectMessage domain objects.

Mirrors :mod:`backend.db.models.architect.ArchitectMessage`.  Field names
and types match the SQLAlchemy model exactly so that
``ArchitectMessageRead.model_validate(orm_instance)`` round-trips
cleanly.

Role values correspond to the ``ck_architect_messages_role`` CHECK
constraint on the ``architect_messages`` table
(``user | assistant``).  The ORM column is a ``String`` type guarded by
a DB-level CHECK rather than a Python Enum, so ``Literal`` is the
narrowest faithful representation — consistent with the approach used
in :mod:`backend.schemas.architect_session`,
:mod:`backend.schemas.project`, :mod:`backend.schemas.project_module`,
:mod:`backend.schemas.migration_batch`, :mod:`backend.schemas.guardian`
and :mod:`backend.schemas.user`.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, condecimal

# Mirrors the CHECK constraint `role IN ('user', 'assistant')`
# on the ``architect_messages`` table.
ArchitectMessageRole = Literal["user", "assistant"]

# Mirrors `cost_usd DECIMAL(10, 6)` on the ``architect_messages`` table.
ArchitectMessageCost = condecimal(max_digits=10, decimal_places=6)


class ArchitectMessageCreate(BaseModel):
    """Payload for creating a new Architect chat message.

    ``id``, ``created_at`` and ``updated_at`` are server-generated and
    therefore excluded.  ``session_id``, ``role`` and ``content`` are
    required — a message is meaningless without them.  Token counts and
    cost are nullable because they are typically recorded only after the
    SSE stream completes (see DESIGN.md §1.5, "Streaming").
    """

    session_id: UUID = Field(
        ...,
        description="Architect session the message belongs to.",
    )
    role: ArchitectMessageRole = Field(
        ...,
        description="Message author role: user | assistant.",
    )
    content: str = Field(
        ...,
        min_length=1,
        description="Full message content.",
    )
    input_tokens: Optional[int] = Field(
        default=None,
        ge=0,
        description="Anthropic API input tokens consumed by the message.",
    )
    output_tokens: Optional[int] = Field(
        default=None,
        ge=0,
        description="Anthropic API output tokens produced by the message.",
    )
    cost_usd: Optional[ArchitectMessageCost] = Field(  # type: ignore[valid-type]
        default=None,
        description="USD cost calculated from the token counts.",
    )


class ArchitectMessageUpdate(BaseModel):
    """Partial update for an existing Architect chat message.

    ``id`` and ``created_at`` are immutable.  ``updated_at`` is managed
    by the ORM via ``onupdate=func.now()`` and must not be set by
    clients.  ``session_id``, ``role`` and ``content`` are immutable
    too — chat history is append-only, so a message always belongs to
    one session with a fixed role and content.  Only the usage/cost
    columns remain mutable to support backfilling token counts and cost
    after the SSE stream completes or to correct accounting mistakes.
    All fields are optional to support PATCH-style semantics.
    """

    input_tokens: Optional[int] = Field(
        default=None,
        ge=0,
        description="Updated Anthropic API input tokens.",
    )
    output_tokens: Optional[int] = Field(
        default=None,
        ge=0,
        description="Updated Anthropic API output tokens.",
    )
    cost_usd: Optional[ArchitectMessageCost] = Field(  # type: ignore[valid-type]
        default=None,
        description="Updated USD cost for the message.",
    )


class ArchitectMessageRead(BaseModel):
    """Serialised representation of an Architect message row.

    Mirrors every column on
    :class:`backend.db.models.architect.ArchitectMessage`.
    ``from_attributes=True`` enables construction directly from an ORM
    instance via ``ArchitectMessageRead.model_validate(obj)``.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    session_id: UUID
    role: ArchitectMessageRole
    content: str = Field(..., min_length=1)
    input_tokens: Optional[int] = Field(default=None, ge=0)
    output_tokens: Optional[int] = Field(default=None, ge=0)
    cost_usd: Optional[Decimal] = None
    created_at: datetime
    updated_at: datetime
