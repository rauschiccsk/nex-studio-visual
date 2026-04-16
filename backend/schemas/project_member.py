"""Pydantic schemas for ProjectMember domain objects.

Mirrors :mod:`backend.db.models.projects.ProjectMember`.  Field names and
types match the SQLAlchemy model exactly so that
``ProjectMemberRead.model_validate(orm_instance)`` round-trips cleanly.

The natural key of the table is ``(project_id, user_id)`` — enforced by
the ``uq_project_members_project_id_user_id`` UNIQUE constraint.  Both
``project_id`` and ``user_id`` are immutable: a project membership is a
join row that is either created or deleted, never rewritten in place.
Therefore :class:`ProjectMemberUpdate` exposes no mutable fields.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ProjectMemberCreate(BaseModel):
    """Payload for creating a new project membership.

    ``id``, ``created_at`` and ``updated_at`` are server-generated and
    therefore excluded.  Both ``project_id`` and ``user_id`` are
    required and together form the natural key of the row.
    """

    project_id: UUID = Field(
        ...,
        description="Project the user is being added to.",
    )
    user_id: UUID = Field(
        ...,
        description="User being granted membership in the project.",
    )


class ProjectMemberUpdate(BaseModel):
    """Partial update for an existing project membership.

    ``id``, ``created_at`` and ``updated_at`` are immutable.  The natural
    key ``(project_id, user_id)`` is also immutable — a membership is
    either created or deleted, never rewritten.  No mutable fields are
    therefore exposed; the schema exists for symmetry with the rest of
    the codebase.
    """


class ProjectMemberRead(BaseModel):
    """Serialised representation of a project membership row.

    Mirrors every column on
    :class:`backend.db.models.projects.ProjectMember`.
    ``from_attributes=True`` enables construction directly from an ORM
    instance via ``ProjectMemberRead.model_validate(obj)``.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    user_id: UUID
    created_at: datetime
    updated_at: datetime
