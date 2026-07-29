"""REST router for :class:`~backend.db.models.bugs.Bug`.

Exposes the standard CRUD surface for bugs:

* ``GET    /``           → paginated list (filter by ``project_id``,
  ``status``, ``severity``, ``source`` and ``created_by``).
* ``GET    /{bug_id}``   → single bug by primary key.
* ``POST   /``           → create a new bug (``bug_number`` is
  auto-assigned by the service layer as ``MAX(bug_number) + 1`` per
  project).
* ``PATCH  /{bug_id}``   → partial update of the mutable fields.
* ``DELETE /{bug_id}``   → hard-delete a bug (HTTP 204).

All endpoints are synchronous ``def`` — pg8000 is a synchronous driver
and FastAPI dispatches sync endpoints to a thread pool automatically. The
router delegates every persistence operation to
:mod:`backend.services.bug` and handles commit/rollback itself so the
service layer remains transaction-agnostic.

The router is prefix-less; the mount prefix (``/api/v1/bugs``) is
applied in ``backend/main.py`` via ``app.include_router``.
"""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from backend.core import authz
from backend.core.security import require_shu_or_above
from backend.db.models.foundation import User
from backend.db.session import get_db
from backend.schemas.bug import (
    BugCreate,
    BugRead,
    BugSeverity,
    BugSource,
    BugStatus,
    BugUpdate,
)
from backend.schemas.pagination import PaginatedResponse
from backend.services import bug as bug_service

router = APIRouter(
    tags=["Bugs"],
    # Any authenticated user reaches the surface; each route then authorizes on the bug's OWNING
    # PROJECT. The router used to be gated by the ha ROLE and nothing else — not one endpoint
    # resolved the project — so under the ownership model any ha/ri account could read, edit and
    # hard-delete every project's bugs. It was a sibling of the accept-route hole, one file over.
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


@router.get("", response_model=PaginatedResponse[BugRead])
def list_bugs(
    project_id: UUID = Query(
        description=(
            "The project whose bugs to list. REQUIRED — the caller is authorized against it. It used to "
            "be an optional filter, so omitting it returned every project's bugs to anyone the role gate "
            "let through: a list that fails OPEN, raising nothing and returning more."
        ),
    ),
    status_filter: Optional[BugStatus] = Query(
        default=None,
        alias="status",
        description="Filter by lifecycle status (new | accepted | in_progress | resolved | wont_fix).",
    ),
    severity: Optional[BugSeverity] = Query(
        default=None,
        description="Filter by severity (critical | major | minor).",
    ),
    source: Optional[BugSource] = Query(
        default=None,
        description="Filter by source (internal | customer).",
    ),
    created_by: Optional[UUID] = Query(
        default=None,
        description="Filter by the registering user's id.",
    ),
    skip: int = Query(default=0, ge=0, description="Number of rows to skip."),
    limit: int = Query(default=50, ge=1, le=100, description="Maximum rows to return."),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_shu_or_above),
) -> PaginatedResponse[BugRead]:
    """Return a paginated list of one project's bugs — if the caller owns it."""
    authz.assert_project_id_access(db, current_user, project_id)
    try:
        rows = bug_service.list_bugs(
            db,
            project_id=project_id,
            status=status_filter,
            severity=severity,
            source=source,
            created_by=created_by,
            limit=limit,
            offset=skip,
        )
        total = bug_service.count_bugs(
            db,
            project_id=project_id,
            status=status_filter,
            severity=severity,
            source=source,
            created_by=created_by,
        )
    except ValueError as exc:
        raise _map_value_error(exc) from exc

    return PaginatedResponse[BugRead](
        items=[BugRead.model_validate(row) for row in rows],
        total=total,
        skip=skip,
        limit=limit,
    )


@router.get("/{bug_id}", response_model=BugRead)
def get_bug(
    bug_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_shu_or_above),
) -> BugRead:
    """Return a single bug by primary key — if the caller owns its project."""
    try:
        bug = bug_service.get_by_id(db, bug_id)
    except ValueError as exc:
        raise _map_value_error(exc) from exc
    authz.assert_project_id_access(db, current_user, bug.project_id)
    return BugRead.model_validate(bug)


@router.post(
    "",
    response_model=BugRead,
    status_code=status.HTTP_201_CREATED,
)
def create_bug(
    payload: BugCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_shu_or_above),
) -> BugRead:
    """Create a new bug.

    ``bug_number`` is auto-assigned by the service layer as
    ``MAX(bug_number) + 1`` for the supplied ``project_id``. Concurrent
    creates that race on the same project surface as HTTP 409.
    """
    try:
        authz.assert_project_id_access(db, current_user, payload.project_id)
        bug = bug_service.create(db, payload)
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise _map_value_error(exc) from exc
    db.refresh(bug)
    return BugRead.model_validate(bug)


@router.patch("/{bug_id}", response_model=BugRead)
def update_bug(
    bug_id: UUID,
    payload: BugUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_shu_or_above),
) -> BugRead:
    """Partially update a bug's mutable fields.

    ``id``, ``project_id``, ``bug_number``, ``created_by`` and
    ``created_at`` are immutable; ``updated_at`` is refreshed by the ORM.
    Fields omitted from the payload are left unchanged. When ``status``
    transitions to ``resolved`` and ``resolved_at`` is not supplied
    explicitly, the service stamps ``resolved_at = now()`` automatically.
    """
    try:
        authz.assert_project_id_access(db, current_user, bug_service.get_by_id(db, bug_id).project_id)
        bug = bug_service.update(db, bug_id, payload)
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise _map_value_error(exc) from exc
    db.refresh(bug)
    return BugRead.model_validate(bug)


@router.delete(
    "/{bug_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
def delete_bug(
    bug_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_shu_or_above),
) -> Response:
    """Hard-delete a bug by primary key.

    The single inbound FK (``bug_fix_tasks.bug_id``) uses
    ``ON DELETE CASCADE``, so dependent bug-fix tasks are removed
    automatically. ``status='wont_fix'`` via ``PATCH`` is the preferred
    soft-disable path; delete is reserved for test fixtures / admin
    tooling.
    """
    try:
        authz.assert_project_id_access(db, current_user, bug_service.get_by_id(db, bug_id).project_id)
        bug_service.delete(db, bug_id)
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise _map_value_error(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
