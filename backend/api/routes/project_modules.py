"""REST router for :class:`~backend.db.models.projects.ProjectModule`.

Exposes the standard CRUD surface for project modules:

* ``GET    /``              → paginated list (filter by ``project_id``,
  ``status`` and ``category``).
* ``GET    /{module_id}``   → single module by primary key.
* ``POST   /``              → create a new module.
* ``PATCH  /{module_id}``   → partial update of the mutable fields.
* ``DELETE /{module_id}``   → hard-delete a module (HTTP 204).

All endpoints are synchronous ``def`` — pg8000 is a synchronous driver
and FastAPI dispatches sync endpoints to a thread pool automatically.
The router delegates every persistence operation to
:mod:`backend.services.project_module` and handles commit/rollback
itself so the service layer remains transaction-agnostic.

The router is prefix-less; the mount prefix
(``/api/v1/project-modules``) is applied in ``backend/main.py`` via
``app.include_router``.

Design notes (per DESIGN.md §1.5 ProjectModule, §2.2 project_modules
table, D-04 per-module DESIGN.md and D-10 NEX Horizont module seeding):

* ``project_id`` is an immutable foreign key — a module belongs to
  exactly one project for its lifetime and is deleted rather than
  reassigned. :class:`ProjectModuleUpdate` deliberately omits it and
  the service's allow-list formalises that contract defensively.
* ``code`` is unique *per project* — ``UNIQUE(project_id, code)``
  (``uq_project_modules_project_id_code``). The same short code
  (e.g. ``'PAB'``) may therefore exist in several projects.
* ``status`` is constrained by the ``ck_project_modules_status`` DB
  CHECK (``planned | in_design | in_development | done``). Invalid
  values surface at schema-validation time (HTTP 422) via the
  Pydantic ``Literal``.
* Inbound foreign keys to ``project_modules.id`` all use either
  ``ON DELETE CASCADE`` (``module_dependencies``) or ``ON DELETE
  SET NULL`` (specifications, KB docs, tasks, architect sessions),
  so :func:`delete` performs no RESTRICT check.
* List filters (``project_id``, ``status``, ``category``) back the
  Module Registry UI (DESIGN.md §3.1 ``ModuleRegistryPage``) and the
  dependency-graph visualisation (``ModuleGraph``).
"""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from backend.api.dependencies import get_knowledge_base_writer
from backend.db.models.projects import Project
from backend.db.session import get_db
from backend.schemas.live_documents import ModuleEventData
from backend.schemas.pagination import PaginatedResponse
from backend.schemas.project_module import (
    ProjectModuleCreate,
    ProjectModuleRead,
    ProjectModuleStatus,
    ProjectModuleUpdate,
)
from backend.services import project_module as project_module_service
from backend.services.knowledge_base_writer import KnowledgeBaseWriter
from backend.services.live_documents import LiveDocumentService


def _project_slug(db: Session, project_id: UUID) -> str | None:
    """Return the slug for a project id, or ``None`` if missing."""
    project = db.get(Project, project_id)
    return project.slug if project is not None else None

router = APIRouter(tags=["Project Modules"])


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


@router.get("", response_model=PaginatedResponse[ProjectModuleRead])
def list_project_modules(
    project_id: Optional[UUID] = Query(
        default=None,
        description="Filter by the project the module belongs to.",
    ),
    status_filter: Optional[ProjectModuleStatus] = Query(
        default=None,
        alias="status",
        description="Filter by lifecycle status (planned | in_design | in_development | done).",
    ),
    category: Optional[str] = Query(
        default=None,
        description="Filter by module category (e.g. 'Katalógy', 'Sklad', 'Nákup').",
    ),
    skip: int = Query(default=0, ge=0, description="Number of rows to skip."),
    limit: int = Query(default=50, ge=1, le=100, description="Maximum rows to return."),
    db: Session = Depends(get_db),
) -> PaginatedResponse[ProjectModuleRead]:
    """Return a paginated list of project modules."""
    try:
        rows = project_module_service.list_project_modules(
            db,
            project_id=project_id,
            status=status_filter,
            category=category,
            limit=limit,
            offset=skip,
        )
        total = project_module_service.count_project_modules(
            db,
            project_id=project_id,
            status=status_filter,
            category=category,
        )
    except ValueError as exc:
        raise _map_value_error(exc) from exc

    return PaginatedResponse[ProjectModuleRead](
        items=[ProjectModuleRead.model_validate(row) for row in rows],
        total=total,
        skip=skip,
        limit=limit,
    )


@router.get("/{module_id}", response_model=ProjectModuleRead)
def get_project_module(
    module_id: UUID,
    db: Session = Depends(get_db),
) -> ProjectModuleRead:
    """Return a single project module by primary key."""
    try:
        module = project_module_service.get_by_id(db, module_id)
    except ValueError as exc:
        raise _map_value_error(exc) from exc
    return ProjectModuleRead.model_validate(module)


@router.post(
    "",
    response_model=ProjectModuleRead,
    status_code=status.HTTP_201_CREATED,
)
def create_project_module(
    payload: ProjectModuleCreate,
    db: Session = Depends(get_db),
    kb_writer: KnowledgeBaseWriter = Depends(get_knowledge_base_writer),
) -> ProjectModuleRead:
    """Create a new project module.

    ``UNIQUE(project_id, code)`` is validated pre-flush by the service;
    a duplicate pair within the same project surfaces as HTTP 409. A
    missing ``project_id`` is rejected by the DB-level foreign key and
    surfaces as HTTP 422.

    On success, appends a lifecycle entry to the owning project's
    ``HISTORY.md`` and regenerates ``STATUS.md`` — the module list on
    the status page and the chronological trail both stay in sync
    with the DB.
    """
    try:
        module = project_module_service.create(db, payload)
        slug = _project_slug(db, module.project_id)
        if slug is not None:
            svc = LiveDocumentService(slug, writer=kb_writer)
            svc.append_module_event(
                ModuleEventData(
                    event_type="created",
                    module_code=module.code,
                    module_name=module.name,
                    category=module.category,
                )
            )
            svc.regenerate_status(db, module.project_id)
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise _map_value_error(exc) from exc
    except OSError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update live documents: {exc}",
        ) from exc
    db.refresh(module)
    return ProjectModuleRead.model_validate(module)


@router.patch("/{module_id}", response_model=ProjectModuleRead)
def update_project_module(
    module_id: UUID,
    payload: ProjectModuleUpdate,
    db: Session = Depends(get_db),
    kb_writer: KnowledgeBaseWriter = Depends(get_knowledge_base_writer),
) -> ProjectModuleRead:
    """Partially update a project module's mutable fields.

    ``id``, ``project_id``, ``created_at`` are immutable; ``updated_at``
    is refreshed by the ORM on flush via ``onupdate=func.now()``. Fields
    omitted from the payload are left unchanged. Changing ``code``
    re-validates the ``UNIQUE(project_id, code)`` constraint and surfaces
    a collision as HTTP 409.

    On a status transition, appends a ``HISTORY.md`` entry and
    regenerates ``STATUS.md``. Other field edits regenerate the
    status markdown (so the modules section reflects the new name /
    category) but do not spam the history log.
    """
    # Snapshot the pre-update state before flush so we can detect a
    # status transition.
    try:
        before = project_module_service.get_by_id(db, module_id)
    except ValueError as exc:
        raise _map_value_error(exc) from exc
    previous_status = before.status

    try:
        module = project_module_service.update(db, module_id, payload)

        slug = _project_slug(db, module.project_id)
        if slug is not None:
            svc = LiveDocumentService(slug, writer=kb_writer)
            if module.status != previous_status:
                svc.append_module_event(
                    ModuleEventData(
                        event_type="status_changed",
                        module_code=module.code,
                        module_name=module.name,
                        category=module.category,
                        old_status=previous_status,
                        new_status=module.status,
                    )
                )
            svc.regenerate_status(db, module.project_id)

        db.commit()
    except ValueError as exc:
        db.rollback()
        raise _map_value_error(exc) from exc
    except OSError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update live documents: {exc}",
        ) from exc
    db.refresh(module)
    return ProjectModuleRead.model_validate(module)


@router.delete(
    "/{module_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
def delete_project_module(
    module_id: UUID,
    db: Session = Depends(get_db),
    kb_writer: KnowledgeBaseWriter = Depends(get_knowledge_base_writer),
) -> Response:
    """Hard-delete a project module by primary key.

    Inbound FKs to ``project_modules.id`` use either ``ON DELETE
    CASCADE`` (``module_dependencies``) or ``ON DELETE SET NULL``
    (``raw_specifications``, ``professional_specifications``,
    ``kb_documents``, ``tasks``, ``architect_sessions``), so dependent
    rows are either removed or have their module reference nulled out
    automatically. No inbound FK uses ``RESTRICT``, so no dependency
    guard is required.

    Records the delete in the owning project's ``HISTORY.md`` and
    regenerates ``STATUS.md`` so the modules section no longer lists
    the removed row.
    """
    # Capture identity before delete so we can emit the HISTORY entry
    # and regenerate STATUS after the row is gone.
    try:
        module = project_module_service.get_by_id(db, module_id)
    except ValueError as exc:
        raise _map_value_error(exc) from exc
    project_id = module.project_id
    code = module.code
    name = module.name
    category = module.category

    try:
        project_module_service.delete(db, module_id)

        slug = _project_slug(db, project_id)
        if slug is not None:
            svc = LiveDocumentService(slug, writer=kb_writer)
            svc.append_module_event(
                ModuleEventData(
                    event_type="deleted",
                    module_code=code,
                    module_name=name,
                    category=category,
                )
            )
            svc.regenerate_status(db, project_id)

        db.commit()
    except ValueError as exc:
        db.rollback()
        raise _map_value_error(exc) from exc
    except OSError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update live documents: {exc}",
        ) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
