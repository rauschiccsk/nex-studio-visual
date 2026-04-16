"""Pydantic schemas for UserSession domain objects.

Mirrors :mod:`backend.db.models.foundation.UserSession`.  Field names and
types match the SQLAlchemy model exactly so that
``UserSessionRead.model_validate(orm_instance)`` round-trips cleanly.

A session row represents a per-user JWT lifecycle anchor.  The
``token_version`` column is incremented on logout to invalidate all
outstanding JWTs (the ``tv`` claim is verified against this column on
every authenticated request — see DESIGN.md Section 1.1 "Auth pattern").
``last_seen_at`` is refreshed on each authenticated request.

``user_id`` is the immutable FK that anchors the session to a single
user; a session is either created or deleted, never reassigned to a
different user.  Therefore :class:`UserSessionUpdate` exposes only the
mutable state — ``token_version`` and ``last_seen_at``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class UserSessionCreate(BaseModel):
    """Payload for creating a new user session.

    ``id``, ``created_at`` and ``updated_at`` are server-generated and
    therefore excluded.  ``token_version`` and ``last_seen_at`` mirror
    the DB-level ``server_default`` values (``0`` and ``NOW()``
    respectively); ``last_seen_at`` is optional here so callers may
    delegate to the database default.
    """

    user_id: UUID = Field(
        ...,
        description="User the session belongs to.",
    )
    token_version: int = Field(
        default=0,
        ge=0,
        description=(
            "Monotonically increasing counter; bumped on logout to "
            "invalidate all outstanding JWTs issued for this session."
        ),
    )
    last_seen_at: Optional[datetime] = Field(
        default=None,
        description=(
            "Timestamp of the most recent authenticated request. ``None`` defers to the DB-level ``NOW()`` default."
        ),
    )


class UserSessionUpdate(BaseModel):
    """Partial update for an existing user session.

    ``id`` and ``created_at`` are immutable.  ``updated_at`` is managed
    by the ORM via ``onupdate=func.now()`` and must not be set by
    clients.  ``user_id`` is an immutable FK — a session belongs to
    exactly one user for its lifetime.  All remaining fields are
    optional to support PATCH-style semantics.
    """

    token_version: Optional[int] = Field(
        default=None,
        ge=0,
        description=("Updated token-version counter (typically bumped on logout to rotate outstanding JWTs)."),
    )
    last_seen_at: Optional[datetime] = Field(
        default=None,
        description="Updated last-seen timestamp.",
    )


class UserSessionRead(BaseModel):
    """Serialised representation of a user session row.

    Mirrors every column on
    :class:`backend.db.models.foundation.UserSession`.
    ``from_attributes=True`` enables construction directly from an ORM
    instance via ``UserSessionRead.model_validate(obj)``.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    token_version: int = Field(..., ge=0)
    last_seen_at: datetime
    created_at: datetime
    updated_at: datetime
