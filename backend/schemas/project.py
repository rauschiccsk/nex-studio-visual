"""Pydantic schemas for Project domain objects.

Mirrors :mod:`backend.db.models.projects.Project`.  Field names, max
lengths and default values match the SQLAlchemy model exactly so that
``ProjectRead.model_validate(project_orm_instance)`` round-trips cleanly.

Category and status values correspond to the ``ck_projects_category`` and
``ck_projects_status`` CHECK constraints on the ``projects`` table
(``singlemodule | multimodule`` and ``active | archived | paused``
respectively).  The ORM columns are ``String`` types guarded by DB-level
CHECKs rather than Python Enums, so ``Literal`` is the narrowest faithful
representation — consistent with the approach used in
:mod:`backend.schemas.guardian` and :mod:`backend.schemas.user`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# Mirrors the CHECK constraint `category IN ('singlemodule', 'multimodule')`
# on the ``projects`` table.
ProjectCategory = Literal["singlemodule", "multimodule"]

# Mirrors the CHECK constraint `status IN ('active', 'archived', 'paused')`
# on the ``projects`` table.
ProjectStatus = Literal["active", "archived", "paused"]


class ProjectCreate(BaseModel):
    """Payload for creating a new project.

    ``id``, ``created_at`` and ``updated_at`` are server-generated and
    therefore excluded.  ``status`` and ``guardian_enabled`` default to
    the values set by the DB-level ``server_default`` so callers may
    omit them.  Nullable columns default to ``None``.
    """

    name: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Human-readable project name, unique across the system.",
    )
    slug: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="URL-safe identifier, unique across the system.",
    )
    category: ProjectCategory = Field(
        ...,
        description="Project category: singlemodule | multimodule.",
    )
    description: str = Field(
        ...,
        description="Project description.",
    )
    status: ProjectStatus = Field(
        default="active",
        description="Lifecycle status: active | archived | paused.",
    )
    backend_port: Optional[int] = Field(
        default=None,
        description="Backend service port from the ICC Port Registry.",
    )
    frontend_port: Optional[int] = Field(
        default=None,
        description="Frontend service port from the ICC Port Registry.",
    )
    db_port: Optional[int] = Field(
        default=None,
        description="Database port from the ICC Port Registry.",
    )
    repo_url: Optional[str] = Field(
        default=None,
        max_length=255,
        description="Git repository URL, e.g. 'rauschiccsk/nex-horizont'.",
    )
    source_path: Optional[str] = Field(
        default=None,
        description="Filesystem path to the source checkout, e.g. '/opt/nex-horizont-src/'.",
    )
    kb_path: Optional[str] = Field(
        default=None,
        description="Filesystem path to the project knowledge base directory.",
    )
    guardian_enabled: bool = Field(
        default=False,
        description="Whether Guardian review is enabled for this project.",
    )
    created_by: UUID = Field(
        ...,
        description="User who created the project.",
    )


class ProjectUpdate(BaseModel):
    """Partial update for an existing project.

    ``id`` and ``created_at`` are immutable.  ``updated_at`` is managed
    by the ORM via ``onupdate=func.now()`` and must not be set by
    clients.  ``created_by`` is an audit column and must not be
    rewritten after the fact.  ``slug`` is auto-generated from ``name``
    and ``category`` cannot be changed once the project is created, so
    both are excluded.  All remaining fields are optional to support
    PATCH-style semantics.
    """

    name: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=255,
        description="Updated human-readable project name.",
    )
    description: Optional[str] = Field(
        default=None,
        description="Updated project description.",
    )
    status: Optional[ProjectStatus] = Field(
        default=None,
        description="Updated lifecycle status: active | archived | paused.",
    )
    backend_port: Optional[int] = Field(
        default=None,
        description="Updated backend service port.",
    )
    frontend_port: Optional[int] = Field(
        default=None,
        description="Updated frontend service port.",
    )
    db_port: Optional[int] = Field(
        default=None,
        description="Updated database port.",
    )
    repo_url: Optional[str] = Field(
        default=None,
        max_length=255,
        description="Updated Git repository URL.",
    )
    source_path: Optional[str] = Field(
        default=None,
        description="Updated source checkout path.",
    )
    kb_path: Optional[str] = Field(
        default=None,
        description="Updated knowledge base directory path.",
    )
    guardian_enabled: Optional[bool] = Field(
        default=None,
        description="Updated Guardian-enabled flag.",
    )


class ProjectRead(BaseModel):
    """Serialised representation of a project row.

    Mirrors every column on :class:`backend.db.models.projects.Project`.
    ``from_attributes=True`` enables construction directly from an ORM
    instance via ``ProjectRead.model_validate(obj)``.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str = Field(..., min_length=1, max_length=255)
    slug: str = Field(..., min_length=1, max_length=100)
    category: ProjectCategory
    description: str
    status: ProjectStatus
    backend_port: Optional[int] = None
    frontend_port: Optional[int] = None
    db_port: Optional[int] = None
    repo_url: Optional[str] = Field(default=None, max_length=255)
    source_path: Optional[str] = None
    kb_path: Optional[str] = None
    guardian_enabled: bool
    created_by: UUID
    created_at: datetime
    updated_at: datetime
