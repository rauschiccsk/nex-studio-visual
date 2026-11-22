"""A ticket of the project from a text the cockpit prepared (DEV-492) — a broken UAT test, the cleanup of dead code.

Both used to open the „Rýchla oprava“ dialog with the text and start a fix from it. With one way for every version
(Director 22.11.2026: „Treba mať jednotný postup a to najprv verzia a potom priradzovanie tiketov.“) the text becomes a
ticket of the project's module; the Manažér puts it into a version or a fast fix on the „Plane tikety“ tab.
The ticket is filed like a note made a ticket (DEV-455): who answers for it is the account's person in Plane.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from backend.db.models.foundation import User
from backend.db.models.projects import Project
from backend.services import plane_responsible, plane_tickets
from backend.services.plane_gateway import PlaneError, PlaneGateway, TicketRef
from backend.services.user_notes import NO_RESPONSIBLE


def file(
    db: Session,
    plane: PlaneGateway,
    user: User,
    project: Project,
    *,
    title: str,
    description: str,
    work_kind: str,
    source: str,
) -> TicketRef:
    """File the prepared text as a ticket of ``project`` — a Bug for a fix of delivered code, else a Task.

    Raises:
        PlaneError: the account has no person in Plane, or Plane refused the ticket.
    """
    responsible = plane_responsible.responsible_of(user, plane_responsible.responsible_people(plane))
    if responsible is None:
        raise PlaneError(NO_RESPONSIBLE.format(username=user.username))
    ticket_type = "Bug" if work_kind == "fix" else "Task"
    return plane_tickets.file_ticket(
        db,
        plane,
        project=project,
        user=user,
        source=source,
        title=title.strip(),
        description=description,
        labels=(
            f"{plane_responsible.TICKET_TYPE_GROUP}: {ticket_type}",
            f"{plane_responsible.RESPONSIBLE_GROUP}: {responsible}",
        ),
        doer=responsible,
        work_kind=work_kind,
    )
