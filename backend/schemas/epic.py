"""Pydantic schemas for Epic domain objects.

Mirrors :mod:`backend.db.models.tasks.Epic`.  Field names, max lengths
and default values match the SQLAlchemy model exactly so that
``EpicRead.model_validate(epic_orm_instance)`` round-trips cleanly.

Status values correspond to the ``ck_epics_status`` CHECK constraint on
the ``epics`` table (``planned | in_progress | done``).  The ORM column
is a ``String`` type guarded by a DB-level CHECK rather than a Python
Enum, so ``Literal`` is the narrowest faithful representation —
consistent with the approach used in :mod:`backend.schemas.bug`,
:mod:`backend.schemas.bug_fix_task`,
:mod:`backend.schemas.project_module`,
:mod:`backend.schemas.project` and :mod:`backend.schemas.user`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# Mirrors the CHECK constraint
# ``status IN ('planned', 'in_progress', 'done')``
# on the ``epics`` table.
EpicStatus = Literal["planned", "in_progress", "done"]


class EpicCreate(BaseModel):
    """Payload for creating a new epic.

    ``id``, ``number``, ``created_at`` and ``updated_at`` are
    server-generated and therefore excluded — ``number`` is
    auto-assigned as ``max(number) + 1`` per project by the service
    layer (see DESIGN.md §2.6 ``POST /projects/{id}/epics``).
    ``status`` defaults to the value set by the DB-level
    ``server_default`` (``planned``) so callers may omit it.  Nullable
    columns default to ``None`` — ``module_id`` is optional because a
    project-level epic (no specific module scope) is permitted for
    single-module projects.
    """

    project_id: UUID = Field(
        ...,
        description="Project the epic belongs to.",
    )
    module_id: Optional[UUID] = Field(
        default=None,
        description=(
            "Optional project module the epic is scoped to. ``None`` denotes a "
            "project-level epic (used by single-module projects)."
        ),
    )
    title: str = Field(
        ...,
        min_length=1,
        max_length=500,
        description="Short epic title.",
    )
    status: EpicStatus = Field(
        default="planned",
        description="Lifecycle status: planned | in_progress | done.",
    )


class EpicUpdate(BaseModel):
    """Partial update for an existing epic.

    ``id``, ``project_id``, ``number`` and ``created_at`` are immutable:
    the epic identity and its position within the project must not be
    rewritten after the fact.  ``updated_at`` is managed by the ORM via
    ``onupdate=func.now()`` and must not be set by clients.
    ``module_id`` remains mutable because the DB-level
    ``ON DELETE SET NULL`` semantics and project-level epics are
    expressed through the same column.  All remaining fields are
    optional to support PATCH-style semantics.
    """

    module_id: Optional[UUID] = Field(
        default=None,
        description=("Updated module scope for the epic. ``None`` denotes a project-level epic."),
    )
    title: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=500,
        description="Updated epic title.",
    )
    status: Optional[EpicStatus] = Field(
        default=None,
        description="Updated lifecycle status: planned | in_progress | done.",
    )


class EpicRead(BaseModel):
    """Serialised representation of an epic row.

    Mirrors every column on :class:`backend.db.models.tasks.Epic`.
    ``from_attributes=True`` enables construction directly from an ORM
    instance via ``EpicRead.model_validate(obj)``.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    module_id: Optional[UUID] = None
    number: int
    title: str = Field(..., min_length=1, max_length=500)
    status: EpicStatus
    created_at: datetime
    updated_at: datetime
