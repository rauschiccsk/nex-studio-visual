"""Pydantic schemas for ProjectModule domain objects.

Mirrors :mod:`backend.db.models.projects.ProjectModule`.  Field names,
max lengths and default values match the SQLAlchemy model exactly so
that ``ProjectModuleRead.model_validate(orm_instance)`` round-trips
cleanly.

Status values correspond to the ``ck_project_modules_status`` CHECK
constraint on the ``project_modules`` table
(``planned | in_design | in_development | done``).  The ORM column is a
``String`` type guarded by a DB-level CHECK rather than a Python Enum,
so ``Literal`` is the narrowest faithful representation — consistent
with the approach used in :mod:`backend.schemas.project`,
:mod:`backend.schemas.guardian` and :mod:`backend.schemas.user`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# Mirrors the CHECK constraint
# ``status IN ('planned', 'in_design', 'in_development', 'done')``
# on the ``project_modules`` table.
ProjectModuleStatus = Literal["planned", "in_design", "in_development", "done"]


class ProjectModuleCreate(BaseModel):
    """Payload for creating a new project module.

    ``id``, ``created_at`` and ``updated_at`` are server-generated and
    therefore excluded.  ``status`` defaults to the value set by the
    DB-level ``server_default`` (``planned``) so callers may omit it.
    Nullable columns default to ``None``.
    """

    project_id: UUID = Field(
        ...,
        description="Project that owns this module.",
    )
    code: str = Field(
        ...,
        min_length=1,
        max_length=10,
        description="Short module code, unique within the project (e.g. 'PAB', 'GSC', 'MIG').",
    )
    name: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Full human-readable module name (e.g. 'Katalóg partnerov').",
    )
    category: str = Field(
        ...,
        min_length=1,
        max_length=50,
        description="Module grouping (e.g. 'Katalógy', 'Sklad', 'Nákup').",
    )
    status: ProjectModuleStatus = Field(
        default="planned",
        description="Lifecycle status: planned | in_design | in_development | done.",
    )
    design_doc_path: Optional[str] = Field(
        default=None,
        description="Absolute filesystem path to the module DESIGN.md in the KB.",
    )


class ProjectModuleUpdate(BaseModel):
    """Partial update for an existing project module.

    ``id`` and ``created_at`` are immutable.  ``updated_at`` is managed
    by the ORM via ``onupdate=func.now()`` and must not be set by
    clients.  ``project_id`` is an immutable foreign key — a module
    belongs to exactly one project for its lifetime and is deleted
    rather than reassigned.  All remaining fields are optional to
    support PATCH-style semantics.
    """

    code: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=10,
        description="Updated short module code.",
    )
    name: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=255,
        description="Updated module name.",
    )
    category: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=50,
        description="Updated module grouping.",
    )
    status: Optional[ProjectModuleStatus] = Field(
        default=None,
        description="Updated lifecycle status: planned | in_design | in_development | done.",
    )
    design_doc_path: Optional[str] = Field(
        default=None,
        description="Updated filesystem path to the module DESIGN.md.",
    )


class ProjectModuleRead(BaseModel):
    """Serialised representation of a project module row.

    Mirrors every column on
    :class:`backend.db.models.projects.ProjectModule`.
    ``from_attributes=True`` enables construction directly from an ORM
    instance via ``ProjectModuleRead.model_validate(obj)``.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    code: str = Field(..., min_length=1, max_length=10)
    name: str = Field(..., min_length=1, max_length=255)
    category: str = Field(..., min_length=1, max_length=50)
    status: ProjectModuleStatus
    design_doc_path: Optional[str] = None
    created_at: datetime
    updated_at: datetime
