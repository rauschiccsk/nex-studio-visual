"""The „Plane tikety" tab — the tickets of a project's module in the DEV register (DEV-120).

* ``GET /projects/{slug}/plane-tickets?closed=false`` → open tickets (``closed=true`` → done and cancelled).
* ``POST /projects/{slug}/plane-tickets/{issue_id}/version`` → the ticket goes into a version that solves it
  (DEV-455); ``DELETE …/version/{version_id}`` takes it out while the version has not started on it. The ticket
  moves in Plane with a comment. A fast fix from a ticket starts at ``/pipeline/fast-fix``.

Reading founds nothing in Plane. Access as everywhere in the cockpit: the project's owner and the admin.
"""

from __future__ import annotations

from dataclasses import asdict
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.core import authz
from backend.core.security import get_current_user
from backend.db.models.foundation import User
from backend.db.models.versions import Version
from backend.db.session import get_db
from backend.schemas.plane_tickets import (
    OpenVersionRead,
    PlaneTicketRead,
    PreparedTicketCreate,
    PreparedTicketFounded,
    ProjectPlaneTicketsRead,
    VersionTicketRead,
)
from backend.services import plane_tickets, prepared_tickets, version_tickets
from backend.services.plane_gateway import PlaneError

router = APIRouter(tags=["Plane tikety"])


@router.get("/projects/{slug}/plane-tickets", response_model=ProjectPlaneTicketsRead)
def list_project_plane_tickets(
    slug: str,
    closed: bool = Query(default=False, description="Done and cancelled tickets instead of the open ones."),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ProjectPlaneTicketsRead:
    """The tickets of the project's module, newest change first; a ticket in a version names it (DEV-455)."""
    project = authz.assert_project_slug_access(db, current_user, slug)
    try:
        found = plane_tickets.project_tickets(db, plane_tickets.gateway(), project, closed=closed)
    except PlaneError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    return ProjectPlaneTicketsRead(
        project_id=project.id,
        project_name=project.name,
        module=found.module,
        tickets=[
            PlaneTicketRead(**asdict(t), work_kind_label=plane_tickets.work_kind_words(t.work_kind))
            for t in found.tickets
        ],
        open_versions=[
            OpenVersionRead(id=v.id, version_number=v.version_number, fast_fix=v.fast_fix)
            for v in version_tickets.open_versions(db, project.id)
        ],
    )


class _VersionChoice(BaseModel):
    version_id: UUID


def _version_of(db: Session, project, version_id: UUID) -> Version:
    version = db.get(Version, version_id)
    if version is None or version.project_id != project.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Verzia v tomto projekte nie je.")
    return version


@router.post("/projects/{slug}/plane-tickets", response_model=PreparedTicketFounded, status_code=201)
def file_prepared_ticket(
    slug: str,
    payload: PreparedTicketCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> PreparedTicketFounded:
    """DEV-492 — „Založiť tiket“ from a text the cockpit prepared (a broken UAT test, dead code); nothing starts.

    The Manažér puts the ticket into a version or a fast fix on this tab, like any other.
    """
    project = authz.assert_project_slug_access(db, current_user, slug)
    try:
        ref = prepared_tickets.file(db, plane_tickets.gateway(), current_user, project, **payload.model_dump())
    except PlaneError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    db.commit()
    return PreparedTicketFounded(identifier=ref.identifier, url=ref.url)


@router.post("/projects/{slug}/plane-tickets/{issue_id}/version", response_model=VersionTicketRead)
def put_plane_ticket_into_version(
    slug: str,
    issue_id: str,
    choice: _VersionChoice,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> VersionTicketRead:
    """„Zaradiť do verzie" — the version solves the ticket; a second click returns the same link (DEV-455).

    The link is saved first; when Plane does not move the ticket, the sentence says so (``plane_warning``).
    """
    project = authz.assert_project_slug_access(db, current_user, slug)
    version = _version_of(db, project, choice.version_id)
    try:
        link, warning = version_tickets.assign(db, plane_tickets.gateway(), project, version, issue_id, current_user)
    except version_tickets.TicketRefused as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except PlaneError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    db.commit()
    return VersionTicketRead(
        issue_id=link.plane_issue_id,
        identifier=link.plane_identifier,
        version_number=version.version_number,
        plane_warning=warning,
    )


@router.delete("/projects/{slug}/plane-tickets/{issue_id}/version/{version_id}", response_model=VersionTicketRead)
def take_plane_ticket_out_of_version(
    slug: str,
    issue_id: str,
    version_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> VersionTicketRead:
    """„Vyradiť z verzie" — while the version has not carried the ticket to the Staviteľ yet (DEV-455)."""
    project = authz.assert_project_slug_access(db, current_user, slug)
    version = _version_of(db, project, version_id)
    link = version_tickets.of_issue(db, issue_id)
    identifier = link.plane_identifier if link else issue_id
    try:
        warning = version_tickets.remove(db, plane_tickets.gateway(), project, version, issue_id, current_user)
    except version_tickets.TicketRefused as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    db.commit()
    return VersionTicketRead(
        issue_id=issue_id, identifier=identifier, version_number=version.version_number, plane_warning=warning
    )
