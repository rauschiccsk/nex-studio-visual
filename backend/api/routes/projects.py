"""REST router for :class:`~backend.db.models.projects.Project`.

Exposes the standard CRUD surface for projects:

* ``GET    /``              → paginated list (filter by ``status``,
  ``category`` and ``created_by``).
* ``GET    /{project_id}``  → single project by primary key.
* ``POST   /``              → create a new project (with port and GitHub
  repo validation).
* ``PATCH  /{project_id}``  → partial update of the mutable fields.
* ``DELETE /{project_id}``  → hard-delete a project (HTTP 204).

All endpoints are synchronous ``def`` — pg8000 is a synchronous driver and
FastAPI dispatches sync endpoints to a thread pool automatically. The
router delegates every persistence operation to
:mod:`backend.services.project` and handles commit/rollback itself so the
service layer remains transaction-agnostic.

The router is prefix-less; the mount prefix (``/api/v1/projects``) is
applied in ``backend/main.py`` via ``app.include_router``.
"""

from __future__ import annotations

import logging
from typing import Optional
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from backend.api.dependencies import get_knowledge_base_writer
from backend.db.models.foundation import User
from backend.db.session import get_db
from backend.schemas.pagination import PaginatedResponse
from backend.schemas.project import (
    GitHubRepoNotFoundError,
    PortBlockSuggestResponse,
    PortCheckResponse,
    PortConflictError,
    PortSuggestResponse,
    ProjectCategory,
    ProjectCreate,
    ProjectRead,
    ProjectStatus,
    ProjectUpdate,
)
from backend.services import github_validation as github_validation_service
from backend.services import port_registry as port_registry_service
from backend.services import project as project_service
from backend.services.knowledge_base_writer import KnowledgeBaseWriter
from backend.services.live_documents import LiveDocumentService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Projects"])


def _resolve_created_by(db: Session, created_by: Optional[UUID]) -> UUID:
    """Return the supplied UUID or fall back to the first active 'ri' user.

    This is a placeholder until JWT auth is wired up — at that point the
    router will extract the user ID from the token instead.
    """
    if created_by is not None:
        return created_by
    from sqlalchemy import select

    user = db.execute(
        select(User).where(User.is_active.is_(True)).where(User.role == "ri").limit(1)
    ).scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="No active 'ri' user found — cannot resolve created_by.",
        )
    return user.id


def _validate_ports(db: Session, payload: ProjectCreate) -> None:
    """Validate that all supplied ports are in range and not already allocated.

    Raises :class:`~fastapi.HTTPException` directly — 422 for out-of-range,
    409 for conflicts.
    """
    ports = [
        ("backend_port", payload.backend_port),
        ("frontend_port", payload.frontend_port),
        ("db_port", payload.db_port),
    ]

    for field_name, port_value in ports:
        if port_value is None:
            continue

        # Range check (9100–9299)
        if port_value < port_registry_service.PORT_RANGE_MIN or port_value > port_registry_service.PORT_RANGE_MAX:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"Port {port_value} ({field_name}) is outside the allowed range "
                    f"({port_registry_service.PORT_RANGE_MIN}–{port_registry_service.PORT_RANGE_MAX})."
                ),
            )

        # Uniqueness check
        if not port_registry_service.check_port_available(db, port_value):
            conflict_name = port_registry_service.get_conflict_project_name(db, port_value)
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=PortConflictError(
                    detail=(
                        f"Port {port_value} ({field_name}) is already allocated"
                        f"{' to project ' + repr(conflict_name) if conflict_name else ''}."
                    ),
                    port=port_value,
                    conflict_project=conflict_name,
                ).model_dump(),
            )


def _validate_github_repo(repo_url: str | None) -> None:
    """Validate that the GitHub repository exists via the GitHub API.

    Delegates to :func:`backend.services.github_validation.validate_github_repo`
    and translates its exceptions into appropriate HTTP responses.

    Expects *repo_url* in ``owner/repo`` format (e.g.
    ``rauschiccsk/nex-horizont``). Raises :class:`~fastapi.HTTPException`
    422 when the repo is not found or the API call fails.
    """
    if not repo_url:
        return

    try:
        exists = github_validation_service.validate_github_repo(repo_url)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=GitHubRepoNotFoundError(
                detail=str(exc),
                repo_url=repo_url,
            ).model_dump(),
        ) from exc
    except (RuntimeError, httpx.HTTPError) as exc:
        logger.warning("GitHub API request failed for repo %r", repo_url, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Could not verify GitHub repository '{repo_url}': GitHub API unavailable.",
        ) from exc

    if not exists:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=GitHubRepoNotFoundError(
                detail=f"GitHub repository '{repo_url}' not found.",
                repo_url=repo_url,
            ).model_dump(),
        )


def _map_value_error(exc: ValueError) -> HTTPException:
    """Translate a service-layer ``ValueError`` into an HTTP exception.

    Mirrors the ICC error-handling pattern: ``not found`` → 404,
    duplicates/conflicts → 409, everything else (constraint / FK /
    validation failures) → 422.
    """
    message = str(exc)
    lowered = message.lower()
    if "not found" in lowered:
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=message)
    if "already exists" in lowered or "duplicate" in lowered or "conflict" in lowered:
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=message)
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=message)


@router.get("", response_model=PaginatedResponse[ProjectRead])
def list_projects(
    status_filter: Optional[ProjectStatus] = Query(
        default=None,
        alias="status",
        description="Filter by lifecycle status (active | archived | paused).",
    ),
    category: Optional[ProjectCategory] = Query(
        default=None,
        description="Filter by category (singlemodule | multimodule).",
    ),
    created_by: Optional[UUID] = Query(
        default=None,
        description="Filter by the creating user's id.",
    ),
    skip: int = Query(default=0, ge=0, description="Number of rows to skip."),
    limit: int = Query(default=50, ge=1, le=100, description="Maximum rows to return."),
    db: Session = Depends(get_db),
) -> PaginatedResponse[ProjectRead]:
    """Return a paginated list of projects."""
    try:
        rows = project_service.list_projects(
            db,
            status=status_filter,
            category=category,
            created_by=created_by,
            limit=limit,
            offset=skip,
        )
        total = project_service.count_projects(
            db,
            status=status_filter,
            category=category,
            created_by=created_by,
        )
    except ValueError as exc:
        raise _map_value_error(exc) from exc

    return PaginatedResponse[ProjectRead](
        items=[ProjectRead.model_validate(row) for row in rows],
        total=total,
        skip=skip,
        limit=limit,
    )


@router.get("/ports/check", response_model=PortCheckResponse)
def check_port(
    port: int = Query(..., description="Port number to check (10100–14999)."),
    project_id: Optional[str] = Query(
        default=None,
        description="Exclude this project from the conflict check (for editing).",
    ),
    db: Session = Depends(get_db),
) -> PortCheckResponse:
    """Check whether a port is available in the ICC Port Registry range."""
    try:
        available = port_registry_service.check_port_available(db, port, project_id)
    except ValueError as exc:
        raise _map_value_error(exc) from exc

    conflict_project: str | None = None
    if not available:
        conflict_project = port_registry_service.get_conflict_project_name(db, port, project_id)

    return PortCheckResponse(available=available, conflict_project=conflict_project)


@router.get("/ports/suggest", response_model=PortSuggestResponse)
def suggest_port(
    type: str = Query(
        ...,
        alias="type",
        description="Port type: backend | frontend | db.",
    ),
    db: Session = Depends(get_db),
) -> PortSuggestResponse:
    """Suggest the next available port for the given type."""
    try:
        suggested = port_registry_service.suggest_next_port(db, type)
    except ValueError as exc:
        raise _map_value_error(exc) from exc

    return PortSuggestResponse(suggested_port=suggested)


@router.get("/ports/suggest-block", response_model=PortBlockSuggestResponse)
def suggest_port_block(
    db: Session = Depends(get_db),
) -> PortBlockSuggestResponse:
    """Return the base port of the first free 10-port block in the registry.

    Used by the new-project form to auto-fill the four port inputs
    (backend / frontend / db / ui-design) from a contiguous block per
    DECISIONS.md D-020 (Port Registry v2, 10-port blocks).
    """
    try:
        base = port_registry_service.suggest_next_port_block(db)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc

    return PortBlockSuggestResponse(
        base=base,
        block_size=port_registry_service.PORT_BLOCK_SIZE,
    )


@router.get("/{project_id}", response_model=ProjectRead)
def get_project(
    project_id: UUID,
    db: Session = Depends(get_db),
) -> ProjectRead:
    """Return a single project by primary key."""
    try:
        project = project_service.get_by_id(db, project_id)
    except ValueError as exc:
        raise _map_value_error(exc) from exc
    return ProjectRead.model_validate(project)


@router.post(
    "",
    response_model=ProjectRead,
    status_code=status.HTTP_201_CREATED,
    responses={
        409: {"model": PortConflictError, "description": "Port or slug conflict"},
        422: {"model": GitHubRepoNotFoundError, "description": "Validation error or GitHub repo not found"},
    },
)
def create_project(
    payload: ProjectCreate,
    db: Session = Depends(get_db),
    kb_writer: KnowledgeBaseWriter = Depends(get_knowledge_base_writer),
) -> ProjectRead:
    """Create a new project.

    Validates before persisting:

    * **Slug uniqueness** — 409 if another project uses the same slug.
    * **Port range** — 422 if any supplied port is outside 9100–9299.
    * **Port uniqueness** — 409 if any supplied port is already allocated.
    * **GitHub repo** — 422 if ``repo_url`` is set but the repository
      does not exist on GitHub.

    On success, seeds three live documents
    (``STATUS.md`` / ``HISTORY.md`` / ``ARCHITECT.md``) under
    ``{knowledge_base_path}/projects/{slug}/``. The seeding happens
    before the DB commit so a KB write failure rolls the project back
    — a project never exists in the DB without its live documents in
    the KB.
    """
    # Pre-creation validation (ports).  GitHub repo existence is NOT checked
    # here — in NEX Studio workflow the project is registered before the repo
    # is created.  repo_url is stored as metadata only.
    _validate_ports(db, payload)

    # Resolve created_by — use supplied UUID or fall back to active ri user.
    payload.created_by = _resolve_created_by(db, payload.created_by)

    try:
        project = project_service.create(db, payload)
        LiveDocumentService(project.slug, writer=kb_writer).init_live_documents(
            db, project.id
        )
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise _map_value_error(exc) from exc
    except OSError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to initialise live documents: {exc}",
        ) from exc
    db.refresh(project)
    return ProjectRead.model_validate(project)


@router.patch("/{project_id}", response_model=ProjectRead)
def update_project(
    project_id: UUID,
    payload: ProjectUpdate,
    db: Session = Depends(get_db),
) -> ProjectRead:
    """Partially update a project's mutable fields.

    ``id``, ``slug``, ``category``, ``created_by`` and ``created_at`` are
    immutable; ``updated_at`` is refreshed by the ORM. Fields omitted from
    the payload are left unchanged.
    """
    try:
        project = project_service.update(db, project_id, payload)
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise _map_value_error(exc) from exc
    db.refresh(project)
    return ProjectRead.model_validate(project)


@router.delete(
    "/{project_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
def delete_project(
    project_id: UUID,
    db: Session = Depends(get_db),
) -> Response:
    """Hard-delete a project by primary key.

    Every inbound FK to ``projects.id`` uses ``ON DELETE CASCADE``, so
    dependent rows (modules, specifications, design documents,
    KB docs, architect sessions, epics, bugs, delegations, migration
    tables, report configs) are removed automatically. Archiving is the
    preferred soft-disable path — callers should prefer ``PATCH`` with
    ``status='archived'`` and reserve delete for test fixtures / admin
    tooling.
    """
    try:
        project_service.delete(db, project_id)
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise _map_value_error(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
