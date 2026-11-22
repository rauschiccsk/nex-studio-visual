"""„Zrušiť verziu“ (DEV-491, DEV-492) — a version that did nothing yet goes away whole and frees its number.

22.11.2026 a fast fix of Orthodox Hermenia was founded by mistake and the cockpit had no way to take it back; the
Director: „Nevidím nikde možnosť ako zrušiť verziu 1.1.1“. With one way for every version (DEV-492: founded first,
tickets put in, started then) a version founded by mistake is just as possible for a regular version, and the cancel
lives on the version's page: „Zrušenie verzie nemá byť v Riadiacom centre - nepatrí tam. Má byť vo Verzie - Otvoriť.“

„Did nothing yet“: the version never started — or it is a fast fix still in its Príprava with no agent turn running.
On the short path a fast fix only reads and asks in Príprava; code is written from Programovanie on (CR-V2-028). A
regular version that started has the Staviteľ's work in its Špecifikácia dialogue, so it stays.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from backend.db.models.pipeline import PipelineState
from backend.db.models.projects import Project
from backend.db.models.tasks import Epic
from backend.db.models.versions import Version
from backend.services import plane_tickets, version_tickets
from backend.services.plane_gateway import PlaneError

CANCEL_FINISHED = "Verzia {number} je hotová — zrušiť sa dá len verzia, ktorá ešte nič neurobila."
CANCEL_STARTED = "Verzia {number} sa už začala stavať — zrušiť sa dá len verzia, ktorá ešte nič neurobila."
CANCEL_PAST_PRIPRAVA = (
    "Rýchla oprava {number} už prešla Prípravou a pracovala na kóde — zrušiť sa dá len verzia, ktorá ešte nič "
    "neurobila."
)
CANCEL_AGENT_WORKING = "Staviteľ na verzii {number} práve pracuje — zrušiť ju pôjde, keď ťah skončí."


class CancelRefused(Exception):
    """The version cannot be cancelled now — the message is the sentence for the Manažér."""


@dataclass(frozen=True)
class TicketRef:
    issue_id: str
    identifier: str


@dataclass(frozen=True)
class Cancelled:
    """What is left of a cancelled version: its number, its project and the tickets that go back to Todo."""

    version_number: str
    project_name: str
    tickets: tuple[TicketRef, ...]
    #: The ticket a fast fix was started from before DEV-492 (``versions.plane_issue_id``).
    started_from: Optional[plane_tickets.LinkedTicket]


def refusal(version: Version, state: Optional[PipelineState]) -> Optional[str]:
    """Why ``version`` cannot be cancelled now; ``None`` when it did nothing yet."""
    number = version.version_number
    if version.status in version_tickets.FINISHED_STATUSES:
        return CANCEL_FINISHED.format(number=number)
    if state is None:
        return None
    if state.flow_type != "fast_fix":
        return CANCEL_STARTED.format(number=number)
    if state.current_stage != "priprava":
        return CANCEL_PAST_PRIPRAVA.format(number=number)
    if state.status == "agent_working" or state.dispatch_in_flight:
        return CANCEL_AGENT_WORKING.format(number=number)
    return None


def refusal_of(db: Session, version: Version) -> Optional[str]:
    state = db.execute(select(PipelineState).where(PipelineState.version_id == version.id)).scalar_one_or_none()
    return refusal(version, state)


def cancel(db: Session, version_id: UUID) -> Cancelled:
    """Delete a version that did nothing yet — the version and everything that hangs on it.

    The whole version goes, so the next version of the project may take its number again. The refusal is re-checked
    under row locks, so an agent turn started a moment ago cannot be pulled from under the runner. Raises
    :class:`CancelRefused`; the commit and Plane are the router's.
    """
    version = db.execute(select(Version).where(Version.id == version_id).with_for_update()).scalar_one()
    state = db.execute(
        select(PipelineState).where(PipelineState.version_id == version_id).with_for_update()
    ).scalar_one_or_none()
    refused = refusal(version, state)
    if refused is not None:
        raise CancelRefused(refused)
    project_name = db.execute(select(Project.name).where(Project.id == version.project_id)).scalar_one()
    links = version_tickets.for_version(db, version_id)
    tickets = tuple(TicketRef(t.plane_issue_id, t.plane_identifier) for t in links)
    started_from = (
        plane_tickets.LinkedTicket(
            issue_id=version.plane_issue_id,
            identifier=version.plane_identifier or version.plane_issue_id,
            url=version.plane_url or "",
        )
        if version.plane_issue_id
        else None
    )
    cancelled = Cancelled(version.version_number, project_name, tickets, started_from)
    # Epics hold the version with ON DELETE RESTRICT (their feats and tasks go with them); every other row that
    # points at a version — its build, messages, tickets — cascades or lets go of it in the database.
    db.execute(delete(Epic).where(Epic.version_id == version_id))
    db.execute(delete(Version).where(Version.id == version_id))
    db.expire_all()
    return cancelled


def give_tickets_back(plane_factory, cancelled: Cancelled, who: str) -> Optional[str]:
    """Each ticket of the cancelled version back to Todo with a comment; the sentences of those Plane did not take."""
    version = f"v{cancelled.version_number}"
    warnings: list[str] = []
    if cancelled.tickets:
        try:
            plane = plane_factory()
        except PlaneError as exc:
            plane, warnings = None, [version_tickets.cancel_failed(t.identifier, str(exc)) for t in cancelled.tickets]
        for ticket in cancelled.tickets if plane is not None else ():
            warning = version_tickets.follow_cancelled(
                plane, ticket, version=version, project=cancelled.project_name, who=who
            )
            if warning:
                warnings.append(warning)
    if cancelled.started_from is not None:
        warning = plane_tickets.follow_fix(plane_factory, cancelled.started_from, "cancelled", version=version, who=who)
        if warning:
            warnings.append(warning)
    return " ".join(warnings) or None
