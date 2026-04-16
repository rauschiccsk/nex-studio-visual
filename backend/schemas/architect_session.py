"""Pydantic schemas for ArchitectSession domain objects.

Mirrors :mod:`backend.db.models.architect.ArchitectSession`.  Field names
and types match the SQLAlchemy model exactly so that
``ArchitectSessionRead.model_validate(orm_instance)`` round-trips
cleanly.

Status values correspond to the ``ck_architect_sessions_status`` CHECK
constraint on the ``architect_sessions`` table
(``active | closed``).  The ORM column is a ``String`` type guarded by a
DB-level CHECK rather than a Python Enum, so ``Literal`` is the narrowest
faithful representation — consistent with the approach used in
:mod:`backend.schemas.project`, :mod:`backend.schemas.project_module`,
:mod:`backend.schemas.migration_batch`, :mod:`backend.schemas.guardian`
and :mod:`backend.schemas.user`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# Mirrors the CHECK constraint `status IN ('active', 'closed')`
# on the ``architect_sessions`` table.
ArchitectSessionStatus = Literal["active", "closed"]


class ArchitectSessionCreate(BaseModel):
    """Payload for creating a new Architect chat session.

    ``id``, ``created_at`` and ``updated_at`` are server-generated and
    therefore excluded.  ``status`` defaults to the value set by the
    DB-level ``server_default`` (``active``) so callers may omit it.
    Nullable columns default to ``None``.
    """

    project_id: UUID = Field(
        ...,
        description="Project the Architect session is scoped to.",
    )
    module_id: Optional[UUID] = Field(
        default=None,
        description=(
            "Optional project module the session is scoped to. ``None`` denotes a project-level Architect session."
        ),
    )
    status: ArchitectSessionStatus = Field(
        default="active",
        description="Lifecycle status: active | closed.",
    )
    created_by: UUID = Field(
        ...,
        description="User who opened the Architect session.",
    )
    closed_at: Optional[datetime] = Field(
        default=None,
        description="Timestamp when the session was closed, if applicable.",
    )


class ArchitectSessionUpdate(BaseModel):
    """Partial update for an existing Architect chat session.

    ``id`` and ``created_at`` are immutable.  ``updated_at`` is managed
    by the ORM via ``onupdate=func.now()`` and must not be set by
    clients.  ``project_id`` and ``created_by`` are immutable foreign
    keys — a session belongs to exactly one project and one creator for
    its lifetime.  ``module_id`` remains mutable because the DB-level
    ``ON DELETE SET NULL`` semantics and project-level sessions are
    expressed through the same column.  All remaining fields are
    optional to support PATCH-style semantics.
    """

    module_id: Optional[UUID] = Field(
        default=None,
        description=("Updated module scope for the session. ``None`` denotes a project-level Architect session."),
    )
    status: Optional[ArchitectSessionStatus] = Field(
        default=None,
        description="Updated lifecycle status: active | closed.",
    )
    closed_at: Optional[datetime] = Field(
        default=None,
        description="Updated session-close timestamp.",
    )


class ArchitectSessionRead(BaseModel):
    """Serialised representation of an Architect session row.

    Mirrors every column on
    :class:`backend.db.models.architect.ArchitectSession`.
    ``from_attributes=True`` enables construction directly from an ORM
    instance via ``ArchitectSessionRead.model_validate(obj)``.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    module_id: Optional[UUID] = None
    status: ArchitectSessionStatus
    created_by: UUID
    closed_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
