"""Pydantic schemas for Task domain objects.

Mirrors :mod:`backend.db.models.tasks.Task`.  Field names, max lengths
and default values match the SQLAlchemy model exactly so that
a payload maps onto the model one to one.

Task-type and status values correspond to the ``ck_tasks_task_type`` and
``ck_tasks_status`` CHECK constraints on the ``tasks`` table
(``backend | frontend | migration | test | docs`` and
``todo | in_progress | done | failed`` respectively).  The ORM columns
are ``String`` types guarded by DB-level CHECKs rather than Python
Enums, so ``Literal`` is the narrowest faithful representation —
consistent with :mod:`backend.schemas.feat`, :mod:`backend.schemas.epic`,
:mod:`backend.schemas.project` and :mod:`backend.schemas.user`.
"""

from __future__ import annotations

from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field

# Mirrors the CHECK constraint
# ``task_type IN ('backend', 'frontend', 'migration', 'test', 'docs')``
# on the ``tasks`` table.
TaskType = Literal["backend", "frontend", "migration", "test", "docs"]

# Mirrors the CHECK constraint
# ``status IN ('todo', 'in_progress', 'done', 'failed')``
# on the ``tasks`` table.
TaskStatus = Literal["todo", "in_progress", "done", "failed"]

# Mirrors the CHECK constraint
# ``priority IN ('normal', 'high', 'urgent')``
# on the ``tasks`` table.
TaskPriority = Literal["normal", "high", "urgent"]

# DEV-75 — mirrors ``billing_category IN ('bug', 'request', 'gift')``: what the task's delivered work is billed as —
# a fix of an error in code we delivered, what the customer asked for, a gift of ICC.
BillingCategory = Literal["bug", "request", "gift"]


class TaskCreate(BaseModel):
    """Payload for creating a new task.

    ``id``, ``number``, ``created_at`` and ``updated_at`` are
    server-generated and therefore excluded — ``number`` is
    auto-assigned as ``max(number) + 1`` per feat by the service layer
    (see DESIGN.md §1.6 — display ``{epic.number}.{feat.number}.{number}``).
    ``description`` and ``status`` default to the values set by the
    DB-level ``server_default`` (``''`` and ``todo`` respectively) so
    callers may omit them.  Nullable columns default to ``None``.
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
    plain_description: Optional[str] = Field(
        default=None,
        description=(
            "Plain-language, jargon-free one-liner for the STEP 3 Plán úloh rail (step3-plan-design.md) — "
            "distinct from the technical ``description``. Nullable; ``None`` when the AI partner emitted none."
        ),
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
    checklist_type: Optional[str] = Field(
        default=None,
        max_length=30,
        description=(
            "Checklist type injected into the CC delegation context "
            "(e.g. 'model', 'schema', 'service', 'router', 'frontend')."
        ),
    )
    priority: TaskPriority = Field(
        default="normal",
        description="Task priority: normal | high | urgent.",
    )
    billing_category: Optional[BillingCategory] = Field(
        default=None,
        description="DEV-75: bug | request | gift; None = the version's category.",
    )
