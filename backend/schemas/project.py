"""Pydantic schemas for Project domain objects.

Mirrors :mod:`backend.db.models.projects.Project`.  Field names, max
lengths and default values match the SQLAlchemy model exactly so that
``ProjectRead.model_validate(project_orm_instance)`` round-trips cleanly.

Type, auth-mode and status values correspond to the ``ck_projects_type``,
``ck_projects_auth_mode`` and ``ck_projects_status`` CHECK constraints on the
``projects`` table (``standard | web``, ``password | token`` and
``active | archived | paused`` respectively).  The ORM columns are ``String``
types guarded by DB-level CHECKs rather than Python Enums, so ``Literal`` is the
narrowest faithful representation — consistent with the approach used in
:mod:`backend.schemas.guardian` and :mod:`backend.schemas.user`.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Mirrors the CHECK constraint `type IN ('standard', 'web')` on the
# ``projects`` table — the project archetype (preset surface composition).
ProjectType = Literal["standard", "web"]

# Mirrors the CHECK constraint `auth_mode IN ('password', 'token')` on the
# ``projects`` table — the login flavour wired onto every surface.
ProjectAuthMode = Literal["password", "token"]

# Mirrors the CHECK constraint `status IN ('active', 'archived', 'paused')`
# on the ``projects`` table.
ProjectStatus = Literal["active", "archived", "paused"]

# Tri-state port verdict — mirrors ``backend.services.port_registry.PortState``.
# "unknown" exists because a boolean forced callers to collapse "the host could
# not be consulted" into one of the two answers, and it always collapsed into
# "available" — which is how a port another container was already publishing got
# handed out a second time.
PortAvailabilityState = Literal["free", "taken", "unknown"]


class PortCheckResponse(BaseModel):
    """Response for port availability check.

    ``available`` is the historical boolean; ``state`` is the honest answer.
    Availability is resolved against the cockpit's ``projects`` table, the
    declared reservations AND the host's own published-port map, so a port a
    neighbouring container is serving on no longer reads as free. When the
    host cannot be consulted the state is ``unknown`` — the UI must render
    that distinctly from ``taken`` and must NOT present it as available.
    """

    available: bool = Field(description="True only when the port is provably free on every source.")
    conflict_project: Optional[str] = Field(
        default=None,
        description="Name of the project occupying this port, if any.",
    )
    state: PortAvailabilityState = Field(
        default="free",
        description="free = provably free; taken = held; unknown = could not be verified.",
    )
    holder: Optional[str] = Field(
        default=None,
        description="Who holds the port: project name, container name, or reserved range.",
    )
    source: Optional[str] = Field(
        default=None,
        description="Which source decided: projects | host | probe | reserved.",
    )
    reason: Optional[str] = Field(
        default=None,
        description="Human-readable explanation, always present for taken/unknown.",
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="Configuration warnings, e.g. reserved ranges not configured.",
    )


class PortSuggestResponse(BaseModel):
    """Response for port suggestion."""

    suggested_port: int = Field(description="The first free port in the ICC range.")
    warnings: list[str] = Field(
        default_factory=list,
        description="Configuration warnings gathered while resolving the suggestion.",
    )


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
    warnings: list[str] = Field(
        default_factory=list,
        description="Configuration warnings gathered while resolving the block.",
    )


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


#: The scaffolder's own slug rule (``init.sh``: ``^[a-z][a-z0-9-]*[a-z0-9]$``), mirrored so the
#: cockpit refuses a name it would reject — before a GitHub repo exists to clean up.
_SLUG_RE = re.compile(r"^[a-z][a-z0-9-]*[a-z0-9]$")


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
        description=(
            "URL-safe identifier, unique across the system. Kebab-case, mirroring the scaffolder's own "
            "rule (^[a-z][a-z0-9-]*[a-z0-9]$) so a name it would reject never reaches it."
        ),
    )

    type: ProjectType = Field(
        ...,
        description="Project archetype (surface composition): standard | web.",
    )
    auth_mode: ProjectAuthMode = Field(
        ...,
        description="Login flavour wired onto every surface: password | token. Required.",
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
    custom_development_enabled: bool = Field(
        default=False,
        description=(
            "Vývoj na zákazku — the only switch permitting deviation from the unified default design "
            "(firemné zásady §4). Set once at creation (like type / auth_mode). Default False."
        ),
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

    @field_validator("slug")
    @classmethod
    def _slug_must_match_the_scaffolder(cls, value: str) -> str:
        """Reject here what ``init.sh`` would reject later, in Slovak, before anything is created.

        The scaffolder enforces ``^[a-z][a-z0-9-]*[a-z0-9]$`` and exits 1 on anything else. This field
        accepted any string of 1..100 characters, so a name like ``Demo`` or ``my_project`` sailed
        through validation, the GitHub REPOSITORY WAS ALREADY CREATED, and the create then died in
        Stage 3 with a raw English regex — leaving a repo behind and no project. Same rule, stated at
        the only point where refusing it is free.
        """
        if not _SLUG_RE.match(value):
            raise ValueError(
                "Skratka projektu smie obsahovať len malé písmená bez diakritiky, číslice a spojovník. "
                "Musí sa začínať písmenom, končiť písmenom alebo číslicou a mať aspoň dva znaky "
                "(napríklad „nex-inbox“)."
            )
        return value


class ProjectUpdate(BaseModel):
    """Partial update for an existing project.

    ``id`` and ``created_at`` are immutable.  ``updated_at`` is managed
    by the ORM via ``onupdate=func.now()`` and must not be set by
    clients.  ``created_by`` is an audit column and must not be
    rewritten after the fact.  ``slug`` is auto-generated from ``name``;
    ``type`` and ``auth_mode`` are archetype/login presets fixed at
    creation, so all three are excluded.  All remaining fields are optional
    to support PATCH-style semantics.
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

    #: Slovak sentences naming the founding steps that did NOT finish (CI wiring, the runner, the smoke
    #: test, branch protection). Populated ONLY by ``POST /projects``; every other route leaves it empty,
    #: which is why it is not a column. The post-scaffold steps are best-effort by design and never abort
    #: a create — but the route used to discard their outcome entirely and answer 201, so the cockpit drew
    #: a finished project whose CI had never been wired and said nothing. Empty list = everything ran.
    setup_warnings: list[str] = Field(default_factory=list)

    id: UUID
    name: str = Field(..., min_length=1, max_length=255)
    slug: str = Field(..., min_length=1, max_length=100)
    type: ProjectType
    auth_mode: ProjectAuthMode
    description: str
    status: ProjectStatus
    backend_port: Optional[int] = None
    frontend_port: Optional[int] = None
    db_port: Optional[int] = None
    repo_url: Optional[str] = Field(default=None, max_length=255)
    source_path: Optional[str] = None
    kb_path: Optional[str] = None
    guardian_enabled: bool
    custom_development_enabled: bool
    created_by: UUID
    owner_id: Optional[UUID] = None
    created_at: datetime
    updated_at: datetime
    # Computed on the project-detail endpoint (GET /{id}); ``False`` on list / create / patch responses
    # (not computed there). Drives the FE guard that blocks deleting a PROD-deployed project (CR-V2-027):
    # a project graduates to PROD on its first successful prod deploy and can then only be archived.
    has_prod_deploy: bool = False
