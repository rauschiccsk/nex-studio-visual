"""Pydantic schemas for BugFixTask domain objects.

Mirrors :mod:`backend.db.models.bugs.BugFixTask`.  Field names, max
lengths and default values match the SQLAlchemy model exactly so that
``BugFixTaskRead.model_validate(bug_fix_task_orm_instance)`` round-trips
cleanly.

Task-type and status values correspond to the
``ck_bug_fix_tasks_task_type`` and ``ck_bug_fix_tasks_status`` CHECK
constraints on the ``bug_fix_tasks`` table
(``backend | frontend | migration | test | docs`` and
``todo | in_progress | done | failed`` respectively).  The ORM columns
are ``String`` types guarded by DB-level CHECKs rather than Python
Enums, so ``Literal`` is the narrowest faithful representation —
consistent with the approach used in :mod:`backend.schemas.bug`,
:mod:`backend.schemas.guardian`, :mod:`backend.schemas.user` and
:mod:`backend.schemas.project`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# Mirrors the CHECK constraint
# `task_type IN ('backend', 'frontend', 'migration', 'test', 'docs')`
# on the ``bug_fix_tasks`` table.
BugFixTaskType = Literal["backend", "frontend", "migration", "test", "docs"]

# Mirrors the CHECK constraint
# `status IN ('todo', 'in_progress', 'done', 'failed')`
# on the ``bug_fix_tasks`` table.
BugFixTaskStatus = Literal["todo", "in_progress", "done", "failed"]


class BugFixTaskCreate(BaseModel):
    """Payload for creating a new bug fix task.

    ``id``, ``number``, ``created_at`` and ``updated_at`` are
    server-generated and therefore excluded — ``number`` is
    auto-assigned as ``max(number) + 1`` per bug by the service layer.
    ``description`` and ``status`` default to the values set by the
    DB-level ``server_default`` so callers may omit them.  Nullable
    columns default to ``None``.
    """

    bug_id: UUID = Field(
        ...,
        description="Bug this fix task belongs to.",
    )
    title: str = Field(
        ...,
        min_length=1,
        max_length=500,
        description="Short fix task title.",
    )
    description: str = Field(
        default="",
        description="Detailed fix task description.",
    )
    task_type: BugFixTaskType = Field(
        ...,
        description="Task type: backend | frontend | migration | test | docs.",
    )
    status: BugFixTaskStatus = Field(
        default="todo",
        description="Lifecycle status: todo | in_progress | done | failed.",
    )
    estimated_minutes: Optional[int] = Field(
        default=None,
        description="Estimated effort in minutes.",
    )
    actual_minutes: Optional[int] = Field(
        default=None,
        description="Actual effort in minutes; typically set after completion.",
    )
    checklist_type: Optional[str] = Field(
        default=None,
        max_length=30,
        description="Checklist type applied to this fix task.",
    )


class BugFixTaskUpdate(BaseModel):
    """Partial update for an existing bug fix task.

    ``id``, ``bug_id``, ``number``, ``created_at`` are immutable: the
    fix task identity must not be rewritten after the fact.
    ``updated_at`` is managed by the ORM via ``onupdate=func.now()`` and
    must not be set by clients.  All remaining fields are optional to
    support PATCH-style semantics.
    """

    title: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=500,
        description="Updated fix task title.",
    )
    description: Optional[str] = Field(
        default=None,
        description="Updated fix task description.",
    )
    task_type: Optional[BugFixTaskType] = Field(
        default=None,
        description="Updated task type: backend | frontend | migration | test | docs.",
    )
    status: Optional[BugFixTaskStatus] = Field(
        default=None,
        description="Updated status: todo | in_progress | done | failed.",
    )
    estimated_minutes: Optional[int] = Field(
        default=None,
        description="Updated estimated effort in minutes.",
    )
    actual_minutes: Optional[int] = Field(
        default=None,
        description="Updated actual effort in minutes.",
    )
    checklist_type: Optional[str] = Field(
        default=None,
        max_length=30,
        description="Updated checklist type.",
    )


class BugFixTaskRead(BaseModel):
    """Serialised representation of a bug fix task row.

    Mirrors every column on :class:`backend.db.models.bugs.BugFixTask`.
    ``from_attributes=True`` enables construction directly from an ORM
    instance via ``BugFixTaskRead.model_validate(obj)``.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    bug_id: UUID
    number: int
    title: str = Field(..., min_length=1, max_length=500)
    description: str
    task_type: BugFixTaskType
    status: BugFixTaskStatus
    estimated_minutes: Optional[int] = None
    actual_minutes: Optional[int] = None
    checklist_type: Optional[str] = Field(default=None, max_length=30)
    created_at: datetime
    updated_at: datetime
