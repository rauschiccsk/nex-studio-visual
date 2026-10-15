"""The „Plane tikety" tab — the tickets of a project's module in the DEV register (DEV-120).

* ``GET /projects/{slug}/plane-tickets?closed=false`` → open tickets (``closed=true`` → done and cancelled).
* ``POST /projects/{slug}/plane-tickets/{issue_id}/backlog`` → the ticket becomes a linked requirement of the
  Zásobník, once (DEV-122); the ticket says so in a comment. A fast fix from a ticket starts at ``/pipeline/fast-fix``.

Reading founds nothing in Plane. Access as everywhere in the cockpit: the project's owner and the admin.
"""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from backend.core import authz
from backend.core.security import get_current_user
from backend.db.models.foundation import User
from backend.db.session import get_db
from backend.schemas.backlog import BacklogItemRead
from backend.schemas.plane_tickets import PlaneTicketRead, ProjectPlaneTicketsRead
from backend.services import backlog_tickets, plane_tickets
from backend.services.plane_gateway import PlaneError

router = APIRouter(tags=["Plane tikety"])


@router.get("/projects/{slug}/plane-tickets", response_model=ProjectPlaneTicketsRead)
def list_project_plane_tickets(
    slug: str,
    closed: bool = Query(default=False, description="Done and cancelled tickets instead of the open ones."),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ProjectPlaneTicketsRead:
    """The tickets of the project's module, newest change first; a ticket of the Zásobník names its REQ-N."""
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
    )


@router.post("/projects/{slug}/plane-tickets/{issue_id}/backlog", response_model=BacklogItemRead)
def plane_ticket_to_backlog(
    slug: str,
    issue_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> BacklogItemRead:
    """„Do Zásobníka" — the requirement linked to the ticket; a second click returns the same one.

    The requirement is saved first; the ticket's comment comes after and, when Plane does not take it, the sentence
    says so (``plane_warning``) instead of undoing the requirement.
    """
    project = authz.assert_project_slug_access(db, current_user, slug)
    plane = plane_tickets.gateway()
    try:
        item, created = plane_tickets.ticket_to_backlog(db, plane, project, issue_id)
    except plane_tickets.TicketTaken as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except PlaneError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    db.commit()
    db.refresh(item)
    warning = None
    if created:
        req = backlog_tickets.req(item)
        sentence = plane_tickets.BACKLOG_COMMENT.format(
            project=project.name, req=req, who=plane_tickets.person(current_user)
        )
        try:
            plane.comment(item.plane_issue_id, sentence)
        except PlaneError as exc:
            warning = f"Požiadavka {req} je v Zásobníku, ale tiket {item.plane_identifier} sa o tom nedozvedel: {exc}"
    return BacklogItemRead.model_validate(item).model_copy(update={"plane_warning": warning})
