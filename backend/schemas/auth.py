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
