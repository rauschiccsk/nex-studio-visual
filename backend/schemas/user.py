"""Pydantic schemas for User domain objects.

Mirrors :mod:`backend.db.models.foundation.User`.  Field names and
constraints (max lengths, role values, defaults) match the SQLAlchemy
model exactly so that ``UserRead.model_validate(user_orm_instance)``
round-trips cleanly.

Role values correspond to the ``ck_users_role`` CHECK constraint on the
``users`` table (``ri | ha | shu``).  The ORM column is a ``String(10)``
guarded by a DB-level CHECK rather than a Python Enum, so ``Literal`` is
the narrowest faithful representation — consistent with the approach
used in :mod:`backend.schemas.guardian`.

``first_name`` / ``last_name`` (migration 042) are nullable on the model
and optional on the schemas because legacy rows (Director + seed users
created before 2026-05-13) don't carry them. UI falls back to displaying
``username`` when both are empty.

Password ``min_length=5`` follows Director directive 2026-05-13: NEX Studio
is an internal application behind auth, bcrypt-hashed, no public exposure.
The relaxed minimum is acceptable for internal team use.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# Mirrors the CHECK constraint `role IN ('ri', 'ha', 'shu')` on the
# ``users`` table.
UserRole = Literal["ri", "ha", "shu"]


class UserCreate(BaseModel):
    """Payload for creating a new user.

    ``id``, ``created_at`` and ``updated_at`` are server-generated and
    therefore excluded.  ``is_active`` defaults to ``True`` in the
    database (``server_default='true'``); we mirror that default here so
    callers may omit it.

    The ``password`` field accepts a plaintext password (min 5, max 128
    characters).  The service layer hashes it with bcrypt before persisting
    to the ``password_hash`` column.

    ``first_name`` / ``last_name`` are optional — the UI form may submit
    them but legacy users created via seeds/migrations may not carry them.
    """

    username: str = Field(
        ...,
        min_length=1,
        max_length=50,
        description="Login name, unique across the system.",
    )
    email: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Contact email, unique across the system.",
    )
    password: str = Field(
        ...,
        min_length=5,
        max_length=128,
        description=(
            "Plaintext password (hashed with bcrypt before storage). "
            "Min 5 — Director directive 2026-05-13, NEX Studio is internal."
        ),
    )
    role: UserRole = Field(
        ...,
        description="Access level: ri (Director/Senior), ha (Medior), shu (Junior).",
    )
    is_active: bool = Field(
        default=True,
        description="Soft-disable flag; False excludes the user from auth.",
    )
    first_name: Optional[str] = Field(
        default=None,
        max_length=100,
        description="Given name. Optional — legacy users may not have it.",
    )
    last_name: Optional[str] = Field(
        default=None,
        max_length=100,
        description="Family name. Optional — legacy users may not have it.",
    )
    telegram_chat_id: Optional[str] = Field(
        default=None,
        max_length=64,
        description="Telegram chat_id for agent notifications (CR-NS-012). Optional.",
    )


class UserUpdate(BaseModel):
    """Partial update for an existing user.

    ``id`` and ``created_at`` are immutable.  ``updated_at`` is managed
    by the ORM via ``onupdate=func.now()`` and must not be set by
    clients.  All remaining fields are optional to support PATCH-style
    semantics.
    """

    username: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=50,
        description="Updated login name.",
    )
    email: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=255,
        description="Updated contact email.",
    )
    role: Optional[UserRole] = Field(
        default=None,
        description="Updated role: ri | ha | shu.",
    )
    is_active: Optional[bool] = Field(
        default=None,
        description="Updated active flag.",
    )
    first_name: Optional[str] = Field(
        default=None,
        max_length=100,
        description="Updated given name.",
    )
    last_name: Optional[str] = Field(
        default=None,
        max_length=100,
        description="Updated family name.",
    )
    telegram_chat_id: Optional[str] = Field(
        default=None,
        max_length=64,
        description="Updated Telegram chat_id for agent notifications.",
    )


class ChangePasswordRequest(BaseModel):
    """Payload for the ``POST /users/{id}/change-password`` endpoint.

    The service layer hashes ``new_password`` with bcrypt before persisting.

    ``current_password`` is REQUIRED for a self-service change (a user changing their OWN password) —
    the service verifies it so a hijacked session can't silently reset the password (v4.0.32). An admin
    (``ri``) resetting ANOTHER user's password omits it (the admin doesn't know the target's password).
    """

    new_password: str = Field(
        ...,
        min_length=5,
        max_length=128,
        description=(
            "New plaintext password (min 5, max 128 characters). "
            "Min 5 — Director directive 2026-05-13, NEX Studio is internal."
        ),
    )
    current_password: Optional[str] = Field(
        default=None,
        max_length=128,
        description="Current plaintext password — required when changing your OWN password (self-service).",
    )


class UserRead(BaseModel):
    """Serialised representation of a user row.

    Mirrors :class:`backend.db.models.foundation.User` columns except
    ``password_hash`` which is deliberately excluded to prevent leaking
    credential hashes to API clients.
    ``from_attributes=True`` enables construction directly from an ORM
    instance via ``UserRead.model_validate(obj)``.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    username: str = Field(..., min_length=1, max_length=50)
    email: str = Field(..., min_length=1, max_length=255)
    role: UserRole
    is_active: bool
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    telegram_chat_id: Optional[str] = None
    created_at: datetime
    updated_at: datetime
