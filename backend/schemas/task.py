"""Pydantic schemas for Task domain objects.

Mirrors :mod:`backend.db.models.tasks.Task`.  Field names, max lengths
and default values match the SQLAlchemy model exactly so that
``TaskRead.model_validate(task_orm_instance)`` round-trips cleanly.

Task-type and status values correspond to the ``ck_tasks_task_type`` and
``ck_tasks_status`` CHECK constraints on the ``tasks`` table
(``backend | frontend | migration | test | docs`` and
``todo | in_progress | done | failed`` respectively).  The ORM columns
are ``String`` types guarded by DB-level CHECKs rather than Python
Enums, so ``Literal`` is the narrowest faithful representation —
consistent with the approach used in :mod:`backend.schemas.bug_fix_task`,
:mod:`backend.schemas.feat`, :mod:`backend.schemas.epic`,
:mod:`backend.schemas.bug`, :mod:`backend.schemas.project_module`,
:mod:`backend.schemas.project` and :mod:`backend.schemas.user`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# Mirrors the CHECK constraint
# ``task_type IN ('backend', 'frontend', 'migration', 'test', 'docs')``
# on the ``tasks`` table.
TaskType = Literal["backend", "frontend", "migration", "test", "docs"]

# Mirrors the CHECK constraint
# ``status IN ('todo', 'in_progress', 'done', 'failed')``
# on the ``tasks`` table.
TaskStatus = Literal["todo", "in_progress", "done", "failed"]


class TaskCreate(BaseModel):
    """Payload for creating a new task.

    ``id``, ``number``, ``created_at`` and ``updated_at`` are
    server-generated and therefore excluded — ``number`` is
    auto-assigned as ``max(number) + 1`` per feat by the service layer
    (see DESIGN.md §1.6 — display ``{epic.number}.{feat.number}.{number}``).
    ``description`` and ``status`` default to the values set by the
    DB-level ``server_default`` (``''`` and ``todo`` respectively) so
    callers may omit them.  Nullable columns default to ``None``.
    ``actual_minutes`` is typically set automatically from delegation
    duration but is accepted on creation to support backfill /
    import flows — consistent with :mod:`backend.schemas.bug_fix_task`.
    """

    feat_id: UUID = Field(
        ...,
        description="Feat the task belongs to.",
    )
    title: str = Field(
        ...,
        min_length=1,
        max_length=500,
        description="Short task title.",
    )
    description: str = Field(
        default="",
        description="Detailed task description. Defaults to an empty string.",
    )
    task_type: TaskType = Field(
        ...,
        description="Task type: backend | frontend | migration | test | docs.",
    )
    status: TaskStatus = Field(
        default="todo",
        description="Lifecycle status: todo | in_progress | done | failed.",
    )
    estimated_minutes: Optional[int] = Field(
        default=None,
        description="Architect's estimated duration in minutes.",
    )
    actual_minutes: Optional[int] = Field(
        default=None,
        description="Measured duration in minutes; typically set after completion.",
    )
    checklist_type: Optional[str] = Field(
        default=None,
        max_length=30,
        description=(
            "Checklist type injected into the CC delegation context "
            "(e.g. 'model', 'schema', 'service', 'router', 'frontend')."
        ),
    )


class TaskUpdate(BaseModel):
    """Partial update for an existing task.

    ``id``, ``feat_id``, ``number`` and ``created_at`` are immutable:
    the task identity and its position within the feat must not be
    rewritten after the fact.  ``updated_at`` is managed by the ORM via
    ``onupdate=func.now()`` and must not be set by clients.  All
    remaining fields are optional to support PATCH-style semantics.
    ``actual_minutes`` is typically set automatically from delegation
    duration but is exposed here for backfill / correction flows —
    consistent with :mod:`backend.schemas.feat` and
    :mod:`backend.schemas.bug_fix_task`.
    """

    title: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=500,
        description="Updated task title.",
    )
    description: Optional[str] = Field(
        default=None,
        description="Updated task description.",
    )
    task_type: Optional[TaskType] = Field(
        default=None,
        description="Updated task type: backend | frontend | migration | test | docs.",
    )
    status: Optional[TaskStatus] = Field(
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
    checklist_type: Optional[str] = Field(
        default=None,
        max_length=30,
        description="Updated checklist type.",
    )


class TaskRead(BaseModel):
    """Serialised representation of a task row.

    Mirrors every column on :class:`backend.db.models.tasks.Task`.
    ``from_attributes=True`` enables construction directly from an ORM
    instance via ``TaskRead.model_validate(obj)``.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    feat_id: UUID
    number: int
    title: str = Field(..., min_length=1, max_length=500)
    description: str
    task_type: TaskType
    status: TaskStatus
    estimated_minutes: Optional[int] = None
    actual_minutes: Optional[int] = None
    checklist_type: Optional[str] = Field(default=None, max_length=30)
    created_at: datetime
    updated_at: datetime
