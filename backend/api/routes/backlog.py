"""REST router for :class:`~backend.db.models.backlog.BacklogItem` (E2, CR-NS-041).

* ``GET    /``         → paginated list (filter by ``project_id`` + ``status``).
* ``GET    /{id}``     → single item.
* ``POST   /``         → create (``number`` → ``REQ-N`` auto-assigned per project; status ``open``).
* ``PATCH  /{id}``     → edit (title/desc/priority) | reject (status) | assign-to-version (``version_id``
  → status ``included``).
* ``DELETE /{id}``     → delete, ONLY when ``open`` (204).
* ``GET    /{id}/plane-ticket`` → the draft „Založiť tiket" offers (or the ticket that exists) — DEV-121.
* ``POST   /{id}/plane-ticket`` → found the requirement's ticket in Plane once; a second click returns it.

DEV-121: after a change is saved, the requirement's ticket follows it (included → In Progress, rejected → Cancelled
with the reason, returned → Todo). When Plane does not follow, the change stays and ``plane_warning`` says so.

Reads are ``shu``+ (any authenticated user); writes are ``ha``+ (Director / Medior) — mirrors the epics
convention but split read/write per CR-NS-041. The router owns commit/rollback; the service stays
transaction-agnostic. Prefix-less; ``/api/v1/backlog`` is mounted in ``backend/main.py``.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from backend.core import authz
from backend.core.security import get_current_user
from backend.db.models.backlog import BacklogItem
from backend.db.models.foundation import User
from backend.db.models.versions import Version
from backend.db.session import get_db
from backend.schemas.backlog import (
    BacklogItemCreate,
    BacklogItemRead,
    BacklogItemUpdate,
    BacklogStatus,
    BacklogTicketCreate,
    BacklogTicketDraft,
)
from backend.schemas.pagination import PaginatedResponse
from backend.services import backlog as backlog_service
from backend.services import backlog_tickets, plane_tickets
from backend.services.plane_gateway import PlaneError

router = APIRouter(tags=["Backlog"])


def _map_value_error(exc: ValueError) -> HTTPException:
    """``not found`` → 404, duplicate/conflict → 409, else → 422 (ICC convention)."""
    message = str(exc)
    lowered = message.lower()
    if "not found" in lowered:
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=message)
    if "already exists" in lowered or "duplicate" in lowered or "conflict" in lowered:
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=message)
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=message)


@router.get("", response_model=PaginatedResponse[BacklogItemRead])
def list_backlog(
    project_id: Optional[UUID] = Query(default=None, description="Filter by project."),
    status_filter: Optional[BacklogStatus] = Query(
        default=None, alias="status", description="Filter by lifecycle status."
    ),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=200),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PaginatedResponse[BacklogItemRead]:
    """Paginated backlog list (ordered ``number ASC`` = ``REQ-1, REQ-2, …``).

    v4.0.43: a Junior (``shu``) MUST scope to a project they OWN (``project_id`` required + owner-checked);
    ri/ha may list across all projects.
    """
    if not authz.is_admin(current_user):
        if project_id is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="project_id je povinný — nemôžeš vypísať zásobník naprieč projektami.",
            )
        authz.assert_project_id_access(db, current_user, project_id)
    rows = backlog_service.list_backlog(db, project_id=project_id, status=status_filter, limit=limit, offset=skip)
    total = backlog_service.count_backlog(db, project_id=project_id, status=status_filter)
    return PaginatedResponse[BacklogItemRead](
        items=[BacklogItemRead.model_validate(r) for r in rows],
        total=total,
        skip=skip,
        limit=limit,
    )


@router.get("/{item_id}", response_model=BacklogItemRead)
def get_backlog_item(
    item_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BacklogItemRead:
    """Return one backlog item."""
    try:
        item = backlog_service.get_by_id(db, item_id)
    except ValueError as exc:
        raise _map_value_error(exc) from exc
    authz.assert_project_id_access(db, current_user, item.project_id)
    return BacklogItemRead.model_validate(item)


@router.post(
    "",
    response_model=BacklogItemRead,
    status_code=status.HTTP_201_CREATED,
)
def create_backlog_item(
    payload: BacklogItemCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BacklogItemRead:
    """Create a backlog item (``REQ-N`` auto-assigned; status ``open``). v4.0.43: owner-or-admin/ha."""
    authz.assert_project_id_access(db, current_user, payload.project_id)
    try:
        item = backlog_service.create(db, payload)
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise _map_value_error(exc) from exc
    db.refresh(item)
    return BacklogItemRead.model_validate(item)


@router.patch("/{item_id}", response_model=BacklogItemRead)
def update_backlog_item(
    item_id: UUID,
    payload: BacklogItemUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BacklogItemRead:
    """Edit, reject, or assign-to-version.

    A payload that sets ``version_id`` is an **assign** (→ ``status=included``); otherwise it edits
    ``title`` / ``description`` / ``priority`` / ``status`` (reject = ``status='rejected'``). v4.0.43:
    owner-or-admin/ha of the item's project.
    """
    try:
        before = backlog_service.get_by_id(db, item_id)
        authz.assert_project_id_access(db, current_user, before.project_id)
        was_status, was_version = before.status, before.version_id
        if "version_id" in payload.model_fields_set and payload.version_id is not None:
            item = backlog_service.assign_to_version(db, item_id, payload.version_id)
        else:
            item = backlog_service.update(db, item_id, payload)
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise _map_value_error(exc) from exc
    db.refresh(item)
    warning = _ticket_follows(db, item, was_status, was_version, current_user)
    return BacklogItemRead.model_validate(item).model_copy(update={"plane_warning": warning})


def _ticket_follows(db: Session, item: BacklogItem, was_status: str, was_version, user: User) -> Optional[str]:
    """DEV-121: move the requirement's ticket after its saved change; the sentence when Plane did not follow."""
    who = plane_tickets.person(user)
    if item.status == "included" and (was_status != "included" or was_version != item.version_id):
        version = db.get(Version, item.version_id)
        return backlog_tickets.follow(
            backlog_tickets.gateway, item, "included", version=f"v{version.version_number}", who=who
        )
    if item.status == "rejected" and was_status != "rejected":
        reason = (item.rejection_reason or "").strip().rstrip(".")
        return backlog_tickets.follow(backlog_tickets.gateway, item, "rejected", reason=reason, who=who)
    if item.status == "open" and was_status == "rejected":
        return backlog_tickets.follow(backlog_tickets.gateway, item, "reopened", who=who)
    return None


@router.get("/{item_id}/plane-ticket", response_model=BacklogTicketDraft)
def plane_ticket_draft(
    item_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BacklogTicketDraft:
    """What „Založiť tiket" offers to edit — or the requirement's ticket, when it exists (DEV-121)."""
    try:
        item = backlog_service.get_by_id(db, item_id)
    except ValueError as exc:
        raise _map_value_error(exc) from exc
    authz.assert_project_id_access(db, current_user, item.project_id)
    try:
        found = backlog_tickets.draft(None, item, current_user)
    except PlaneError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    return BacklogTicketDraft(**asdict(found))


@router.post("/{item_id}/plane-ticket", response_model=BacklogItemRead)
def found_plane_ticket(
    item_id: UUID,
    payload: BacklogTicketCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BacklogItemRead:
    """Found the requirement's ticket in its project's module of the DEV register — once (DEV-121)."""
    try:
        authz.assert_project_id_access(db, current_user, backlog_service.get_by_id(db, item_id).project_id)
        item, founded_now = backlog_tickets.found(
            db, backlog_tickets.gateway(), item_id, current_user, **payload.model_dump()
        )
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise _map_value_error(exc) from exc
    except PlaneError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    db.refresh(item)
    warning = None
    if founded_now and item.status == "included":
        warning = _ticket_follows(db, item, "open", None, current_user)
    return BacklogItemRead.model_validate(item).model_copy(update={"plane_warning": warning})


@router.delete(
    "/{item_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
def delete_backlog_item(
    item_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    """Delete a backlog item — when ``open`` or ``rejected`` (never realized/included History, DEV-98). v4.0.43:
    owner-or-admin/ha of the item's project."""
    try:
        authz.assert_project_id_access(db, current_user, backlog_service.get_by_id(db, item_id).project_id)
        backlog_service.delete(db, item_id)
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise _map_value_error(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
