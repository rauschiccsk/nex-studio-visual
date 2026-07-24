"""REST router for :class:`~backend.db.models.tasks.Epic`.

Exposes the standard CRUD surface for epics — the top level of the
Epic/Feat/Task hierarchy (DESIGN.md §1.9 Tasks hierarchy) — that backs
the ``TasksPage`` / ``EpicList`` UI (DESIGN.md §3.1):

* ``GET    /``          → paginated list (filter by ``project_id``
  and ``status``).
* ``GET    /{epic_id}`` → single epic by primary key.
* ``POST   /``          → create a new epic (``number`` is auto-assigned
  by the service layer as ``MAX(number) + 1`` per project).
* ``PATCH  /{epic_id}`` → partial update of the mutable fields.
* ``DELETE /{epic_id}`` → hard-delete an epic (HTTP 204).

All endpoints are synchronous ``def`` — pg8000 is a synchronous driver
and FastAPI dispatches sync endpoints to a thread pool automatically.
The router delegates every persistence operation to
:mod:`backend.services.epic` and handles commit / rollback itself so the
service layer remains transaction-agnostic.

The router is prefix-less; the mount prefix (``/api/v1/epics``) is
applied in ``backend/main.py`` via ``app.include_router`` (Task 4.27).

Design notes (per DESIGN.md §1.9 Tasks (Epic/Feat/Task hierarchy), §2
``epics`` table, §3.1 ``EpicList`` and §6 REST API Architecture):

* ``id``, ``number``, ``created_at`` and ``updated_at`` are
  server-managed and therefore immutable. ``project_id`` is an immutable
  foreign key — an epic belongs to exactly one project for its lifetime.
  :class:`~backend.schemas.epic.EpicUpdate` deliberately omits all
  immutable fields.
* ``number`` is auto-assigned by the service layer as
  ``MAX(number) + 1`` for the supplied ``project_id`` (starts at ``1``
  for the first epic). Concurrent-create races on the same project
  surface as HTTP 409 via the DB-level
  ``UNIQUE(project_id, number)`` constraint.
* ``status`` is constrained by the ``ck_epics_status`` DB CHECK
  (``planned | in_progress | done``). Invalid values surface at
  schema-validation time (HTTP 422) via the Pydantic ``Literal``.
* ``epics`` has a single inbound FK (``feats.epic_id``) with ``ON DELETE
  CASCADE`` — dependent feats (and the tasks under them, via
  ``tasks.feat_id ON DELETE CASCADE``) are removed automatically at the
  DB level. No RESTRICT dependency check is required.
* List filters (``project_id``, ``status``) match the
  indexed columns (``ix_epics_project_id``) and
  back the Tasks UI ("show every epic in this project", "show every
  in-progress epic").
* List ordering (``number ASC``) is owned by the service so epics
  appear in their stable, human-readable numbering sequence (epic 1,
  epic 2, …) matching the ``EpicList`` UI convention.
"""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from backend.core import authz
from backend.core.security import get_current_user, require_shu_or_above
from backend.db.models.foundation import User
from backend.db.session import get_db
from backend.schemas.epic import (
    EpicCreate,
    EpicRead,
    EpicStatus,
    EpicUpdate,
)
from backend.schemas.pagination import PaginatedResponse
from backend.services import epic as epic_service

router = APIRouter(
    tags=["Epics"],
    # v4.0.35: any authenticated user may reach the epics surface; per-route ownership checks
    # (backend.core.authz) then scope a Junior (shu) to epics under their OWN projects.
    dependencies=[Depends(require_shu_or_above)],
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


@router.get("", response_model=PaginatedResponse[EpicRead])
def list_epics(
    project_id: Optional[UUID] = Query(
        default=None,
        description=(
            "Filter by the project the epic belongs to. Hits the "
            "``ix_epics_project_id`` index — the core Tasks-page query "
            "(DESIGN.md §3.1 ``TasksPage``)."
        ),
    ),
    status_filter: Optional[EpicStatus] = Query(
        default=None,
        alias="status",
        description="Filter by lifecycle status (``planned`` | ``in_progress`` | ``done``).",
    ),
    skip: int = Query(default=0, ge=0, description="Number of rows to skip."),
    limit: int = Query(default=50, ge=1, le=100, description="Maximum rows to return."),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PaginatedResponse[EpicRead]:
    """Return a paginated list of epics.

    Results are ordered by ``number ASC`` (epic 1, epic 2, …) — owned by
    the service layer, matching the hierarchical-numbering convention
    (DESIGN.md §1.9) and the ``EpicList`` UI.

    Ownership (v4.0.35): when ``project_id`` is supplied the caller must own that project
    (ri/ha see all). A Junior (``shu``) MUST supply ``project_id`` — an unscoped list across
    all projects would leak other owners' epics, so it is rejected with HTTP 400.
    """
    if project_id is not None:
        authz.assert_project_id_access(db, current_user, project_id)
    elif current_user.role not in authz.PRIVILEGED_ROLES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="project_id je povinný — nemôžeš vypísať epiky naprieč projektami.",
        )
    try:
        rows = epic_service.list_epics(
            db,
            project_id=project_id,
            status=status_filter,
            limit=limit,
            offset=skip,
        )
        total = epic_service.count_epics(
            db,
            project_id=project_id,
            status=status_filter,
        )
    except ValueError as exc:
        raise _map_value_error(exc) from exc

    return PaginatedResponse[EpicRead](
        items=[EpicRead.model_validate(row) for row in rows],
        total=total,
        skip=skip,
        limit=limit,
    )


@router.get("/{epic_id}", response_model=EpicRead)
def get_epic(
    epic_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> EpicRead:
    """Return a single epic by primary key."""
    authz.assert_epic_access(db, current_user, epic_id)
    try:
        epic = epic_service.get_by_id(db, epic_id)
    except ValueError as exc:
        raise _map_value_error(exc) from exc
    return EpicRead.model_validate(epic)


@router.post(
    "",
    response_model=EpicRead,
    status_code=status.HTTP_201_CREATED,
)
def create_epic(
    payload: EpicCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> EpicRead:
    """Create a new epic.

    ``number`` is auto-assigned by the service layer as
    ``MAX(number) + 1`` for the supplied ``project_id`` (starts at ``1``
    for the first epic). ``status`` defaults to ``planned`` via the
    Pydantic schema / DB ``server_default`` when omitted.
    Concurrent-create races
    on the same project surface as HTTP 409. Missing or invalid foreign
    keys (``project_id``, ``version_id``) are rejected by the DB-level FK
    and surface as HTTP 422.

    Ownership (v4.0.35): the parent project (``payload.project_id``) must be owned by the
    caller (ri/ha may create under any project); a Junior placing an epic under someone
    else's project gets HTTP 403.
    """
    authz.assert_project_id_access(db, current_user, payload.project_id)
    try:
        epic = epic_service.create(db, payload)
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise _map_value_error(exc) from exc
    db.refresh(epic)
    return EpicRead.model_validate(epic)


@router.patch("/{epic_id}", response_model=EpicRead)
def update_epic(
    epic_id: UUID,
    payload: EpicUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> EpicRead:
    """Partially update an epic's mutable fields.

    Only ``title`` and ``status`` are mutable. ``id``,
    ``project_id``, ``number`` and ``created_at`` are immutable — the
    epic identity and its position within the project must not be
    rewritten after the fact; ``updated_at`` is refreshed by the ORM on
    flush via ``onupdate=func.now()``. Fields omitted from the payload
    are left unchanged.

    Ownership (v4.0.35): the caller must own the epic's project (ri/ha see all).
    """
    authz.assert_epic_access(db, current_user, epic_id)
    try:
        epic = epic_service.update(db, epic_id, payload)
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise _map_value_error(exc) from exc
    db.refresh(epic)
    return EpicRead.model_validate(epic)


@router.delete(
    "/{epic_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
def delete_epic(
    epic_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    """Hard-delete an epic by primary key.

    The single inbound FK (``feats.epic_id``) uses ``ON DELETE CASCADE``
    — dependent feats (and the tasks under them, via
    ``tasks.feat_id ON DELETE CASCADE``) are removed automatically at
    the DB level. No RESTRICT dependency check is required.

    Ownership (v4.0.35): the caller must own the epic's project (ri/ha see all).
    """
    authz.assert_epic_access(db, current_user, epic_id)
    try:
        epic_service.delete(db, epic_id)
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise _map_value_error(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
