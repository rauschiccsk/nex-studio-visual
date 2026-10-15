"""The „Plane tikety" tab — the tickets of a project's module in the DEV register (DEV-120).

* ``GET /projects/{slug}/plane-tickets?closed=false`` → open tickets (``closed=true`` → done and cancelled).

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
from backend.schemas.plane_tickets import PlaneTicketRead, ProjectPlaneTicketsRead
from backend.services import plane_tickets
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
        module=found.module,
        tickets=[
            PlaneTicketRead(**asdict(t), work_kind_label=plane_tickets.work_kind_words(t.work_kind))
            for t in found.tickets
        ],
    )
