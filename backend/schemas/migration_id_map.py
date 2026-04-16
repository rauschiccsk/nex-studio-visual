"""Pydantic schemas for MigrationIdMap domain objects.

Mirrors :mod:`backend.db.models.migration.MigrationIdMap`.  Field names,
max lengths and default values match the SQLAlchemy model exactly so
that ``MigrationIdMapRead.model_validate(orm_instance)`` round-trips
cleanly.

The natural key of the table is ``(project_id, category, source_key)``
— enforced by the ``uq_migration_id_map_project_category_source_key``
UNIQUE constraint.  ``target_id`` holds the new PostgreSQL UUID as a
``VARCHAR(36)`` and is represented as a plain ``str`` here to match the
column type faithfully (it is *not* necessarily the ID of a row in any
single table, but a stringified UUID reference).
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class MigrationIdMapCreate(BaseModel):
    """Payload for creating a new migration ID-map entry.

    ``id``, ``created_at`` and ``updated_at`` are server-generated and
    therefore excluded.  Nullable columns default to ``None``.

    The combination ``(project_id, category, source_key)`` is uniquely
    constrained (``uq_migration_id_map_project_category_source_key``)
    — a given source key may only map to one target per category per
    project.
    """

    project_id: UUID = Field(
        ...,
        description="Project the ID-map entry belongs to.",
    )
    category: str = Field(
        ...,
        min_length=1,
        max_length=10,
        description="Migration category, e.g. 'PAB', 'GSC', 'STK', 'TSH'.",
    )
    source_key: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Legacy Btrieve source key (string-encoded).",
    )
    target_id: str = Field(
        ...,
        min_length=1,
        max_length=36,
        description="New PostgreSQL UUID mapped from the source key.",
    )
    batch_id: Optional[UUID] = Field(
        default=None,
        description=(
            "Optional migration batch that produced this mapping; "
            "set to NULL if the originating batch is later deleted."
        ),
    )


class MigrationIdMapUpdate(BaseModel):
    """Partial update for an existing migration ID-map entry.

    ``id``, ``project_id``, ``category``, ``source_key``, ``created_at``
    and ``updated_at`` are immutable: the natural key
    ``(project_id, category, source_key)`` must not be rewritten after
    the fact, and ``updated_at`` is managed by the ORM via
    ``onupdate=func.now()``.  Only ``target_id`` and ``batch_id`` remain
    mutable to support corrective re-mapping or re-attaching to a new
    batch.  All fields are optional to support PATCH-style semantics.
    """

    target_id: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=36,
        description="Updated PostgreSQL UUID for this source key.",
    )
    batch_id: Optional[UUID] = Field(
        default=None,
        description="Updated migration batch reference.",
    )


class MigrationIdMapRead(BaseModel):
    """Serialised representation of a migration ID-map row.

    Mirrors every column on
    :class:`backend.db.models.migration.MigrationIdMap`.
    ``from_attributes=True`` enables construction directly from an ORM
    instance via ``MigrationIdMapRead.model_validate(obj)``.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    category: str = Field(..., min_length=1, max_length=10)
    source_key: str = Field(..., min_length=1, max_length=255)
    target_id: str = Field(..., min_length=1, max_length=36)
    batch_id: Optional[UUID] = None
    created_at: datetime
    updated_at: datetime
