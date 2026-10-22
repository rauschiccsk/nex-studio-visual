"""Pydantic schemas for the Credentials domain.

Mirrors :mod:`backend.db.models.credentials.Credential`. Schemas are
intentionally minimal — credentials are admin-only, ``ri``-gated data
that never ships to public APIs.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class CredentialCreate(BaseModel):
    """Payload to create a new credentials entry.

    The caller supplies a title and the desired filename (relative to
    ``settings.credentials_storage_path``); the service writes the
    initial content to disk and inserts the registry row.
    """

    title: str = Field(..., min_length=1, max_length=500)
    filename: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description=("Filename relative to credentials_storage_path. Must not contain slashes — flat directory only."),
    )
    content: str = Field(default="", description="Initial file content (UTF-8 markdown).")


class CredentialRead(BaseModel):
    """Serialised credentials registry row."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str
    file_path: str
    created_at: datetime
    updated_at: datetime


class CredentialContent(BaseModel):
    """On-disk content of a credentials file."""

    model_config = ConfigDict(from_attributes=True)

    credential_id: UUID
    file_path: str
    content: str
    size_bytes: int = Field(..., ge=0)


class CredentialContentUpdate(BaseModel):
    """Payload for ``PUT /credentials/{id}/content`` — overwrite the file."""

    content: str = Field(..., description="New full file content (UTF-8 markdown).")
