"""Pydantic schemas for MigrationBatch domain objects.

Mirrors :mod:`backend.db.models.migration.MigrationBatch`.  Field names,
max lengths and default values match the SQLAlchemy model exactly so
that ``MigrationBatchRead.model_validate(migration_batch_orm_instance)``
round-trips cleanly.

Direction and status values correspond to the
``ck_migration_batches_direction`` and ``ck_migration_batches_status``
CHECK constraints on the ``migration_batches`` table
(``extract | load`` and
``pending | running | completed | failed`` respectively).  The ORM
columns are ``String`` types guarded by DB-level CHECKs rather than
Python Enums, so ``Literal`` is the narrowest faithful representation —
consistent with the approach used in :mod:`backend.schemas.bug`,
:mod:`backend.schemas.bug_fix_task`, :mod:`backend.schemas.guardian`,
:mod:`backend.schemas.user` and :mod:`backend.schemas.project`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# Mirrors the CHECK constraint `direction IN ('extract', 'load')`
# on the ``migration_batches`` table.
MigrationBatchDirection = Literal["extract", "load"]

# Mirrors the CHECK constraint
# `status IN ('pending', 'running', 'completed', 'failed')`
# on the ``migration_batches`` table.
MigrationBatchStatus = Literal["pending", "running", "completed", "failed"]


class MigrationBatchCreate(BaseModel):
    """Payload for creating a new migration batch.

    ``id`` and ``created_at`` are server-generated and therefore
    excluded.  ``direction``, ``status`` and ``error_count`` default to
    the values set by the DB-level ``server_default`` so callers may
    omit them.  Nullable columns default to ``None``.
    """

    project_id: UUID = Field(
        ...,
        description="Project the migration batch belongs to.",
    )
    category: str = Field(
        ...,
        min_length=1,
        max_length=10,
        description="Migration category, e.g. 'PAB', 'GSC', 'STK', 'TSH'.",
    )
    direction: MigrationBatchDirection = Field(
        default="extract",
        description="Batch direction: extract | load.",
    )
    status: MigrationBatchStatus = Field(
        default="pending",
        description="Lifecycle status: pending | running | completed | failed.",
    )
    source_count: Optional[int] = Field(
        default=None,
        description="Records counted in the source (Btrieve) system.",
    )
    target_count: Optional[int] = Field(
        default=None,
        description="Records loaded into PostgreSQL target.",
    )
    error_count: Optional[int] = Field(
        default=0,
        description="Number of errors encountered during the batch.",
    )
    error_log: Optional[str] = Field(
        default=None,
        description="First N errors (truncated) captured during the batch.",
    )
    started_at: Optional[datetime] = Field(
        default=None,
        description="Timestamp when the batch started running.",
    )
    completed_at: Optional[datetime] = Field(
        default=None,
        description="Timestamp when the batch finished.",
    )


class MigrationBatchUpdate(BaseModel):
    """Partial update for an existing migration batch.

    ``id``, ``project_id``, ``category``, ``direction`` and
    ``created_at`` are immutable: the batch identity (project, category
    and direction) must not be rewritten after the fact.  All remaining
    fields are optional to support PATCH-style semantics.
    """

    status: Optional[MigrationBatchStatus] = Field(
        default=None,
        description="Updated status: pending | running | completed | failed.",
    )
    source_count: Optional[int] = Field(
        default=None,
        description="Updated source record count.",
    )
    target_count: Optional[int] = Field(
        default=None,
        description="Updated target record count.",
    )
    error_count: Optional[int] = Field(
        default=None,
        description="Updated error count.",
    )
    error_log: Optional[str] = Field(
        default=None,
        description="Updated error log.",
    )
    started_at: Optional[datetime] = Field(
        default=None,
        description="Updated batch start timestamp.",
    )
    completed_at: Optional[datetime] = Field(
        default=None,
        description="Updated batch completion timestamp.",
    )


class MigrationBatchRead(BaseModel):
    """Serialised representation of a migration batch row.

    Mirrors every column on
    :class:`backend.db.models.migration.MigrationBatch`.
    ``from_attributes=True`` enables construction directly from an ORM
    instance via ``MigrationBatchRead.model_validate(obj)``.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    category: str = Field(..., min_length=1, max_length=10)
    direction: MigrationBatchDirection
    status: MigrationBatchStatus
    source_count: Optional[int] = None
    target_count: Optional[int] = None
    error_count: Optional[int] = None
    error_log: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    created_at: datetime
