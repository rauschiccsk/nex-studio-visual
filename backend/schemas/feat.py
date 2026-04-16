"""Pydantic schemas for Feat domain objects.

Mirrors :mod:`backend.db.models.tasks.Feat`.  Field names, max lengths
and default values match the SQLAlchemy model exactly so that
``FeatRead.model_validate(feat_orm_instance)`` round-trips cleanly.

Status values correspond to the ``ck_feats_status`` CHECK constraint on
the ``feats`` table (``todo | in_progress | done | failed``).  The ORM
column is a ``String`` type guarded by a DB-level CHECK rather than a
Python Enum, so ``Literal`` is the narrowest faithful representation —
consistent with the approach used in :mod:`backend.schemas.epic`,
:mod:`backend.schemas.bug`, :mod:`backend.schemas.bug_fix_task`,
:mod:`backend.schemas.project_module`, :mod:`backend.schemas.project`
and :mod:`backend.schemas.user`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# Mirrors the CHECK constraint
# ``status IN ('todo', 'in_progress', 'done', 'failed')``
# on the ``feats`` table.
FeatStatus = Literal["todo", "in_progress", "done", "failed"]


class FeatCreate(BaseModel):
    """Payload for creating a new feat.

    ``id``, ``number``, ``created_at`` and ``updated_at`` are
    server-generated and therefore excluded — ``number`` is
    auto-assigned as ``max(number) + 1`` per epic by the service layer
    (see DESIGN.md §2.6 ``POST /epics/{id}/feats``).  ``status`` and
    ``description`` default to the values set by the DB-level
    ``server_default`` (``todo`` and ``''`` respectively) so callers may
    omit them.  ``actual_minutes``, ``task_count`` and ``auto_fix_count``
    are server-managed (measured from delegation duration / maintained
    by the service layer as counters) and are not accepted here.
    Nullable columns default to ``None``.
    """

    epic_id: UUID = Field(
        ...,
        description="Epic the feat belongs to.",
    )
    title: str = Field(
        ...,
        min_length=1,
        max_length=500,
        description="Short feat title.",
    )
    description: str = Field(
        default="",
        description="Detailed feat description. Defaults to an empty string.",
    )
    status: FeatStatus = Field(
        default="todo",
        description="Lifecycle status: todo | in_progress | done | failed.",
    )
    estimated_minutes: Optional[int] = Field(
        default=None,
        description="Architect's estimated duration in minutes.",
    )


class FeatUpdate(BaseModel):
    """Partial update for an existing feat.

    ``id``, ``epic_id``, ``number`` and ``created_at`` are immutable:
    the feat identity and its position within the epic must not be
    rewritten after the fact.  ``updated_at`` is managed by the ORM via
    ``onupdate=func.now()`` and must not be set by clients.
    ``task_count`` and ``auto_fix_count`` are server-managed counters
    maintained by the service layer and are therefore not exposed for
    direct edits.  ``actual_minutes`` is typically set automatically
    from delegation duration but is exposed here for backfill /
    correction flows — consistent with the handling of ``resolved_at``
    in :mod:`backend.schemas.bug`.  All remaining fields are optional to
    support PATCH-style semantics.
    """

    title: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=500,
        description="Updated feat title.",
    )
    description: Optional[str] = Field(
        default=None,
        description="Updated feat description.",
    )
    status: Optional[FeatStatus] = Field(
        default=None,
        description="Updated lifecycle status: todo | in_progress | done | failed.",
    )
    estimated_minutes: Optional[int] = Field(
        default=None,
        description="Updated architect estimate in minutes.",
    )
    actual_minutes: Optional[int] = Field(
        default=None,
        description="Updated measured duration in minutes.",
    )


class FeatRead(BaseModel):
    """Serialised representation of a feat row.

    Mirrors every column on :class:`backend.db.models.tasks.Feat`.
    ``from_attributes=True`` enables construction directly from an ORM
    instance via ``FeatRead.model_validate(obj)``.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    epic_id: UUID
    number: int
    title: str = Field(..., min_length=1, max_length=500)
    description: str
    status: FeatStatus
    estimated_minutes: Optional[int] = None
    actual_minutes: Optional[int] = None
    task_count: int
    auto_fix_count: int
    created_at: datetime
    updated_at: datetime
