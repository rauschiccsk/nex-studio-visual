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
user; a session is either created (by the login) or deleted, never reassigned.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


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
    username: Optional[str] = Field(
        default=None,
        description=(
            "Login name of the session's owner, resolved by the list route. The Relácie tab shows one row "
            "per session and offers 'Odvolať' on each; without this it printed the raw ``user_id`` UUID to "
            "anyone who cannot list users (``GET /users`` is ri-only), so a Medior decided whether to cut "
            "someone's session without knowing whose it was. ``None`` when the owner row is gone."
        ),
    )
