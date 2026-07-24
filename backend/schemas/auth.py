"""Pydantic schemas for authentication endpoints.

Covers the ``POST /auth/login`` request/response cycle (DESIGN.md
Section 2.1).  ``LoginResponse`` embeds a safe user representation
(``AuthUser``) that omits ``password_hash`` — callers never receive
the hash over the wire.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from backend.schemas.user import UserRole


class LoginRequest(BaseModel):
    """Payload for ``POST /auth/login``."""

    username: str = Field(
        ...,
        min_length=1,
        max_length=50,
        description="Login name.",
    )
    password: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Plaintext password (validated against bcrypt hash).",
    )


class AuthUser(BaseModel):
    """Safe user representation for auth responses (no password_hash)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    username: str
    email: str
    role: UserRole
    is_active: bool
    # Given/family name — nullable (legacy users may lack them). Carried in the
    # auth payload so clients can show a full display name (CR-NS-089); populated
    # from the ORM columns via ``from_attributes``.
    first_name: str | None = None
    last_name: str | None = None
    # The user's OWN Telegram chat id (agent notifications) — carried so "Moje konto" can show + edit it
    # (v4.0.33). Self data on a self endpoint; never another user's.
    telegram_chat_id: str | None = None
    # True when the user still has an admin-set initial password that must be changed before using
    # the app (v4.0.32). The client blocks the app behind a change-password screen while this is set.
    must_change_password: bool = False
    created_at: datetime
    updated_at: datetime


class LoginResponse(BaseModel):
    """Response from ``POST /auth/login``."""

    access_token: str = Field(..., description="JWT access token (HS256).")
    token_type: Literal["bearer"] = Field(
        default="bearer",
        description="Token type — always 'bearer'.",
    )
    expires_in: int = Field(
        ...,
        description="Token lifetime in seconds.",
    )
    user: AuthUser = Field(..., description="Authenticated user details.")
