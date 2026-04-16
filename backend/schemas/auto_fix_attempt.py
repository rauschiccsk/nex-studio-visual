"""Pydantic schemas for AutoFixAttempt domain objects.

Mirrors :mod:`backend.db.models.delegations.AutoFixAttempt`.  Field names
and default values match the SQLAlchemy model exactly so that
``AutoFixAttemptRead.model_validate(orm_instance)`` round-trips cleanly.

``attempt_number`` is auto-assigned as ``max(attempt_number) + 1`` per
feat by the service layer — following the same convention used for
``number`` on :class:`backend.schemas.feat.FeatCreate` and
:class:`backend.schemas.bug_fix_task.BugFixTaskCreate`.  The combination
``(feat_id, attempt_number)`` is uniquely constrained at the DB level by
``uq_auto_fix_attempts_feat_id_attempt_number``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class AutoFixAttemptCreate(BaseModel):
    """Payload for creating a new auto-fix attempt.

    ``id``, ``attempt_number``, ``created_at`` and ``updated_at`` are
    server-generated and therefore excluded — ``attempt_number`` is
    auto-assigned as ``max(attempt_number) + 1`` per feat by the service
    layer.  Nullable columns default to ``None``.
    """

    feat_id: UUID = Field(
        ...,
        description="Feat whose failed delegation triggered this auto-fix attempt.",
    )
    error_description: str = Field(
        ...,
        min_length=1,
        description="Accumulated error context from the failed delegation.",
    )
    fix_description: Optional[str] = Field(
        default=None,
        description=(
            "Human/AI-readable summary of the remediation performed; "
            "typically populated once the fix delegation completes."
        ),
    )
    delegation_id: Optional[UUID] = Field(
        default=None,
        description=(
            "Optional reference to the auto-fix delegation spawned for "
            "this attempt; set to NULL if the delegation is later deleted."
        ),
    )


class AutoFixAttemptUpdate(BaseModel):
    """Partial update for an existing auto-fix attempt.

    ``id``, ``feat_id``, ``attempt_number`` and ``created_at`` are
    immutable: the attempt identity and its position within the feat's
    retry sequence must not be rewritten after the fact.  ``updated_at``
    is managed by the ORM via ``onupdate=func.now()`` and must not be
    set by clients.  All remaining fields are optional to support
    PATCH-style semantics — in particular, ``fix_description`` and
    ``delegation_id`` are typically populated after the attempt is
    initially recorded.
    """

    error_description: Optional[str] = Field(
        default=None,
        min_length=1,
        description="Updated accumulated error context.",
    )
    fix_description: Optional[str] = Field(
        default=None,
        description="Updated remediation summary.",
    )
    delegation_id: Optional[UUID] = Field(
        default=None,
        description="Updated reference to the auto-fix delegation.",
    )


class AutoFixAttemptRead(BaseModel):
    """Serialised representation of an auto-fix attempt row.

    Mirrors every column on
    :class:`backend.db.models.delegations.AutoFixAttempt`.
    ``from_attributes=True`` enables construction directly from an ORM
    instance via ``AutoFixAttemptRead.model_validate(obj)``.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    feat_id: UUID
    attempt_number: int
    error_description: str
    fix_description: Optional[str] = None
    delegation_id: Optional[UUID] = None
    created_at: datetime
    updated_at: datetime
