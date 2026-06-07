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


class PortCheckResponse(BaseModel):
    """Response for port availability check."""

    available: bool = Field(description="Whether the port is available.")
    conflict_project: Optional[str] = Field(
        default=None,
        description="Name of the project occupying this port, if any.",
    )


class PortSuggestResponse(BaseModel):
    """Response for port suggestion."""

    suggested_port: int = Field(description="The first free port in the ICC range.")


class PortBlockSuggestResponse(BaseModel):
    """Response for block-based port suggestion.

    A port block is a contiguous range of ``block_size`` ports starting
    at ``base`` — per DECISIONS.md D-020 the default block is 10 ports
    with ``+0 backend``, ``+1 frontend``, ``+2 db``, and ``+3..+9`` as
    per-project reserve. Consumers fill the first three slots in the
    new-project form; the reserve stays unallocated until the project
    needs cache / worker / admin-UI.
    """

    base: int = Field(description="Base port of the first free block in the ICC range.")
    block_size: int = Field(description="Number of consecutive ports reserved per project block.")


class PortConflictError(BaseModel):
    """Error detail returned when a requested port is already allocated."""

    detail: str = Field(description="Human-readable conflict description.")
    port: int = Field(description="The conflicting port number.")
    conflict_project: str | None = Field(
        default=None,
        description="Name of the project that occupies the port.",
    )


class GitHubRepoNotFoundError(BaseModel):
    """Error detail returned when the GitHub repository cannot be found."""

    detail: str = Field(description="Human-readable error message.")
    repo_url: str = Field(description="The repository URL that was not found.")


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
    created_by: Optional[UUID] = Field(
        default=None,
        description="User who created the project. If omitted, resolved from the active session.",
    )
    owner_id: Optional[UUID] = Field(
        default=None,
        description=(
            "Notification owner (CR-NS-012). Their Telegram chat_id is written into the "
            "project .env. If omitted, defaults to the creator."
        ),
    )
    # F-004 flags — frontend Create Project form (default per spec §4):
    enable_coordinator: bool = Field(
        default=True,
        description=(
            "F-004 K-003: bootstrap Koordinátor agent (charter + settings + dedo-inbox + "
            "state file). Default True per spec §4 form. Opt-out via False."
        ),
    )
    enable_cicd: bool = Field(
        default=False,
        description=(
            "F-004 K-005: copy github-actions-workflow.yml template + commit + push. "
            "Default False per spec §3.5 (opt-in)."
        ),
    )
    full_smoke: bool = Field(
        default=False,
        description=(
            "F-004 K-004: run full smoke test (build + up + health) instead of minimal "
            "(build only). Default False per spec §3.4."
        ),
    )
    enable_branch_protection: bool = Field(
        default=False,
        description=(
            "F-004 K-001-extension: configure GitHub branch protection (require PR, "
            "no force push). Default False per spec O-3 + Dedo approval."
        ),
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
    owner_id: Optional[UUID] = None
    created_at: datetime
    updated_at: datetime
