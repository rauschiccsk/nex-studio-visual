"""Pydantic schemas for MigrationCategoryStatus domain objects.

Mirrors :mod:`backend.db.models.migration.MigrationCategoryStatus`.  Field
names, max lengths and default values match the SQLAlchemy model exactly
so that ``MigrationCategoryStatusRead.model_validate(orm_instance)``
round-trips cleanly.

Status values correspond to the ``ck_migration_category_status_status``
CHECK constraint on the ``migration_category_status`` table
(``pending | in_progress | completed | failed``).  The ORM column is a
``String`` type guarded by a DB-level CHECK rather than a Python Enum,
so ``Literal`` is the narrowest faithful representation — consistent
with the approach used in :mod:`backend.schemas.bug`,
:mod:`backend.schemas.bug_fix_task`, :mod:`backend.schemas.guardian`,
:mod:`backend.schemas.migration_batch`, :mod:`backend.schemas.project`
and :mod:`backend.schemas.user`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# Mirrors the CHECK constraint
# `status IN ('pending', 'in_progress', 'completed', 'failed')`
# on the ``migration_category_status`` table.
MigrationCategoryStatusStatus = Literal[
    "pending",
    "in_progress",
    "completed",
    "failed",
]


class MigrationCategoryStatusCreate(BaseModel):
    """Payload for creating a new migration category status row.

    ``id``, ``created_at`` and ``updated_at`` are server-generated and
    therefore excluded.  ``status`` defaults to the value set by the
    DB-level ``server_default`` so callers may omit it.  Nullable
    columns default to ``None``.

    The combination ``(project_id, category)`` is uniquely constrained
    (``uq_migration_category_status_project_category``) — one status row
    per category per project.
    """

    project_id: UUID = Field(
        ...,
        description="Project the migration category status belongs to.",
    )
    category: str = Field(
        ...,
        min_length=1,
        max_length=20,
        description="Migration category, e.g. 'PAB', 'GSC', 'STK', 'TSH'.",
    )
    status: MigrationCategoryStatusStatus = Field(
        default="pending",
        description=("Lifecycle status: pending | in_progress | completed | failed."),
    )
    last_run_at: Optional[datetime] = Field(
        default=None,
        description="Timestamp of the last batch run for this category.",
    )
    notes: Optional[str] = Field(
        default=None,
        description="Manual notes, e.g. encoding issues found.",
    )


class MigrationCategoryStatusUpdate(BaseModel):
    """Partial update for an existing migration category status row.

    ``id``, ``project_id``, ``category``, ``created_at`` and
    ``updated_at`` are immutable: the row identity (project + category)
    must not be rewritten after the fact, and ``updated_at`` is managed
    by the ORM via ``onupdate=func.now()``.  All remaining fields are
    optional to support PATCH-style semantics.
    """

    status: Optional[MigrationCategoryStatusStatus] = Field(
        default=None,
        description=("Updated status: pending | in_progress | completed | failed."),
    )
    last_run_at: Optional[datetime] = Field(
        default=None,
        description="Updated timestamp of the last batch run.",
    )
    notes: Optional[str] = Field(
        default=None,
        description="Updated manual notes.",
    )


class MigrationCategoryStatusRead(BaseModel):
    """Serialised representation of a migration category status row.

    Mirrors every column on
    :class:`backend.db.models.migration.MigrationCategoryStatus`.
    ``from_attributes=True`` enables construction directly from an ORM
    instance via ``MigrationCategoryStatusRead.model_validate(obj)``.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    category: str = Field(..., min_length=1, max_length=20)
    status: MigrationCategoryStatusStatus
    last_run_at: Optional[datetime] = None
    notes: Optional[str] = None
    created_at: datetime
    updated_at: datetime
