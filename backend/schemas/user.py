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
    password_hash: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="bcrypt hash of the user's password.",
    )
    role: UserRole = Field(
        ...,
        description="Access level: ri (Director/Senior), ha (Medior), shu (Junior).",
    )
    is_active: bool = Field(
        default=True,
        description="Soft-disable flag; False excludes the user from auth.",
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
    password_hash: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=255,
        description="Updated bcrypt password hash.",
    )
    role: Optional[UserRole] = Field(
        default=None,
        description="Updated role: ri | ha | shu.",
    )
    is_active: Optional[bool] = Field(
        default=None,
        description="Updated active flag.",
    )


class UserRead(BaseModel):
    """Serialised representation of a user row.

    Mirrors every column on :class:`backend.db.models.foundation.User`.
    ``from_attributes=True`` enables construction directly from an ORM
    instance via ``UserRead.model_validate(obj)``.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    username: str = Field(..., min_length=1, max_length=50)
    email: str = Field(..., min_length=1, max_length=255)
    password_hash: str = Field(..., min_length=1, max_length=255)
    role: UserRole
    is_active: bool
    created_at: datetime
    updated_at: datetime
