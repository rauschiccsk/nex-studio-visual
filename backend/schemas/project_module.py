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

# Kebab-case identifier for module codes. Replaces the NEX Genesis 8.3
# legacy of 2–6 uppercase alnum codes (``PAB``, ``MM``) with Clean-Code
# intention-revealing names (``partner-catalog``, ``module-manager``).
# Mirrors ``ck_project_modules_code_format`` (migration 032):
#
#   * starts with a lowercase letter
#   * ends with lowercase letter or digit (so ``foo-`` is rejected)
#   * interior may contain lowercase letters, digits and hyphens
#
# The CHECK regex requires both start + end char, so ``min_length`` is
# 2 at the schema layer as well.
MODULE_CODE_PATTERN = r"^[a-z][a-z0-9-]*[a-z0-9]$"
MODULE_CODE_MIN_LENGTH = 2
MODULE_CODE_MAX_LENGTH = 50

# Allowed module categories, mirroring the NEX Automat module_registry.yaml
# SK-localized labels. Enforced by the ``ck_project_modules_category``
# DB CHECK (migration 031) so the new-project-module form can no longer
# drift across typos like "System" vs "Systém".
ProjectModuleCategory = Literal[
    "Systém",
    "Katalógy",
    "Sklad",
    "Predaj",
    "Nákup",
    "Účtovníctvo",
    "Pokladňa",
]


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
        min_length=MODULE_CODE_MIN_LENGTH,
        max_length=MODULE_CODE_MAX_LENGTH,
        pattern=MODULE_CODE_PATTERN,
        description=("Kebab-case module code, unique within the project (e.g. 'partner-catalog', 'module-manager')."),
    )
    name: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Full human-readable module name (e.g. 'Katalóg partnerov').",
    )
    category: ProjectModuleCategory = Field(
        ...,
        description=(
            "Module grouping — one of the localized ICC labels "
            "(Systém / Katalógy / Sklad / Predaj / Nákup / "
            "Účtovníctvo / Pokladňa)."
        ),
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
        min_length=MODULE_CODE_MIN_LENGTH,
        max_length=MODULE_CODE_MAX_LENGTH,
        pattern=MODULE_CODE_PATTERN,
        description="Updated kebab-case module code.",
    )
    name: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=255,
        description="Updated module name.",
    )
    category: Optional[ProjectModuleCategory] = Field(
        default=None,
        description="Updated module grouping (must be one of the allowed localized labels).",
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
    code: str = Field(
        ...,
        min_length=MODULE_CODE_MIN_LENGTH,
        max_length=MODULE_CODE_MAX_LENGTH,
    )
    name: str = Field(..., min_length=1, max_length=255)
    category: ProjectModuleCategory
    status: ProjectModuleStatus
    design_doc_path: Optional[str] = None
    created_at: datetime
    updated_at: datetime
