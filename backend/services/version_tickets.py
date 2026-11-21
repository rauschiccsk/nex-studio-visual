"""The Plane tickets a version solves (DEV-455) — put in on the Plane tikety tab, given to the Príprava, closed after
PROD.

Director 21.11.2026: „Zásobník by sme mali teraz už úplne zrušiť a ponechať len jediný zdroj - PLANE tikety.
… zjednoduší spracovanie požiadaviek do novej verzie a hlavne zjednoduší uzatváranie tiketov. Budeme presne
vedieť, ktoré tikety boli riešené v jednotlivých verziach.“ And: „Uzatvárať jednoznačne tie tikety, ktoré boli
riešené v danej verzii. Súvisiace tikety sú len informatívne ak ich neriešime.“

The link is the cockpit's own (the Manažér's click), never read from a text. The version keeps the ticket's text as
it was put in, so the Príprava has it when Plane does not answer. The ticket's state follows, each move with a comment
for people; a move Plane does not take is a sentence (a warning), never an undone link.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db.models.foundation import User
from backend.db.models.pipeline import PipelineState
from backend.db.models.projects import Project
from backend.db.models.version_tickets import VersionTicket
from backend.db.models.versions import Version
from backend.services import plane_tickets
from backend.services.plane_gateway import PlaneError, PlaneGateway

#: A version that is finished takes no more tickets.
FINISHED_STATUSES = ("done", "released", "cancelled")

#: Where the ticket goes when it moves, and what its comment says.
MOVES = {
    "assigned": ("In Progress", "Tiket je zaradený do verzie {version} projektu {project}. Zaradil: {who}."),
    "removed": ("Todo", "Tiket je vyradený z verzie {version} projektu {project}. Vyradil: {who}."),
    "deployed": ("Done", "Verzia {version}, ktorá tiket rieši, je nasadená na PROD u zákazníka {customer}."),
}


class TicketRefused(ValueError):
    """The ticket cannot go into (or out of) the version — the sentence says why."""


def assign_refusal(db: Session, version: Version) -> Optional[str]:
    """Why a ticket can no longer be put into ``version`` — ``None`` when it can (DEV-361, DEV-455).

    The Staviteľ takes the version's tickets into the Špecifikácia, and only in Príprava: in its opening brief or with
    its next round. A version past Príprava has an approved Špecifikácia, a fast fix has none, a finished version
    builds no more — a ticket put in there would be closed at PROD without anyone building it.
    """
    if version.status in FINISHED_STATUSES:
        return f"Verzia {version.version_number} je hotová — tiket by sa do nej už nedostal."
    state = db.execute(select(PipelineState).where(PipelineState.version_id == version.id)).scalar_one_or_none()
    if state is None:
        return None
    if state.flow_type == "fast_fix":
        return (
            f"Verzia {version.version_number} je rýchla oprava — nemá Špecifikáciu, do ktorej by sa tiket dostal. "
            "Zaraď ho do novej verzie alebo z neho spusti vlastnú rýchlu opravu."
        )
    if state.current_stage != "priprava":
        return (
            f"Verzia {version.version_number} má schválenú Špecifikáciu — tiket by sa do nej už nedostal. "
            "Zaraď ho do ďalšej verzie."
        )
    return None


def open_versions(db: Session, project_id) -> list[Version]:
    """The project's versions a ticket may still go into, oldest first — what the tab offers."""
    versions = db.execute(
        select(Version).where(Version.project_id == project_id).order_by(Version.created_at)
    ).scalars()
    return [v for v in versions if assign_refusal(db, v) is None]


def of_issue(db: Session, issue_id: str) -> Optional[VersionTicket]:
    return db.execute(select(VersionTicket).where(VersionTicket.plane_issue_id == issue_id)).scalar_one_or_none()


def versions_of_project(db: Session, project_id) -> dict[str, str]:
    """``{issue id: "vX"}`` — the version each ticket of the project went into (DEV-455)."""
    rows = db.execute(
        select(VersionTicket.plane_issue_id, Version.version_number)
        .join(Version, Version.id == VersionTicket.version_id)
        .where(Version.project_id == project_id)
    ).all()
    return {issue_id: f"v{number}" for issue_id, number in rows}


def for_version(db: Session, version_id, *, not_yet_delivered: bool = False) -> list[VersionTicket]:
    """The tickets the version solves, in the order they were put in — with ``not_yet_delivered`` only those no round
    of Príprava has carried to the Staviteľ yet."""
    query = select(VersionTicket).where(VersionTicket.version_id == version_id)
    if not_yet_delivered:
        query = query.where(VersionTicket.delivered_at.is_(None))
    return list(db.execute(query.order_by(VersionTicket.created_at, VersionTicket.plane_identifier)).scalars())


def mark_delivered(db: Session, tickets: list[VersionTicket]) -> None:
    now = datetime.now(timezone.utc)
    for ticket in tickets:
        if ticket.delivered_at is None:
            ticket.delivered_at = now
    if tickets:
        db.flush()


def _move(plane: PlaneGateway, issue_id: str, event: str, **words: str) -> None:
    state, sentence = MOVES[event]
    plane.comment(issue_id, sentence.format(**words))
    plane.set_state(issue_id, state)


def assign(
    db: Session, plane: PlaneGateway, project: Project, version: Version, issue_id: str, user: User
) -> tuple[VersionTicket, Optional[str]]:
    """Put the project's ticket into the version — ``(link, warning)``; a second click returns the same link.

    Raises:
        TicketRefused: the version takes no more tickets, or the ticket is solved elsewhere.
        ValueError: the ticket is not in the project's module.
        PlaneError: Plane did not answer when the ticket was read.
    """
    if version.project_id != project.id:
        raise TicketRefused(f"Verzia {version.version_number} patrí inému projektu.")
    db.execute(select(Project.id).where(Project.id == project.id).with_for_update())
    existing = of_issue(db, issue_id)
    if existing is not None and existing.version_id == version.id:
        return existing, None
    if existing is not None:
        taken = db.get(Version, existing.version_id)
        raise TicketRefused(
            f"Tiket {existing.plane_identifier} už rieši verzia v{taken.version_number if taken else '?'}."
        )
    fix = plane_tickets.fix_of_ticket(db, project.id, issue_id)
    if fix is not None:
        raise TicketRefused(f"Z tohto tiketu už je spustená rýchla oprava v{fix.version_number}.")
    refusal = assign_refusal(db, version)
    if refusal:
        raise TicketRefused(refusal)
    issue = plane_tickets.find_project_ticket(plane, project.plane_module_id, project.name, issue_id)
    link = VersionTicket(
        version_id=version.id,
        plane_issue_id=issue["id"],
        plane_identifier=plane_tickets.identifier(plane, issue),
        plane_url=plane.ticket_url(issue),
        title=(issue.get("name") or "Tiket z Plane")[:500],
        text=plane_tickets.ticket_text(issue),
        added_by=user.id,
    )
    db.add(link)
    db.flush()
    warning = _follow(
        plane,
        link,
        "assigned",
        version=f"v{version.version_number}",
        project=project.name,
        who=plane_tickets.person(user),
    )
    return link, warning


def remove(
    db: Session, plane: PlaneGateway, project: Project, version: Version, issue_id: str, user: User
) -> Optional[str]:
    """Take the ticket out of a version that has not taken it into its Špecifikácia yet — the warning, if any.

    Raises:
        TicketRefused: the ticket is not in the version, or the version already works on it.
    """
    link = of_issue(db, issue_id)
    if link is None or link.version_id != version.id:
        raise TicketRefused(f"Tiket vo verzii {version.version_number} nie je.")
    refusal = assign_refusal(db, version)
    if refusal or link.delivered_at is not None:
        raise TicketRefused(
            f"Verzia {version.version_number} už na tikete {link.plane_identifier} pracuje — vyradiť sa nedá."
        )
    db.delete(link)
    db.flush()
    return _follow(
        plane,
        link,
        "removed",
        version=f"v{version.version_number}",
        project=project.name,
        who=plane_tickets.person(user),
    )


def _follow(plane: PlaneGateway, link: VersionTicket, event: str, **words: str) -> Optional[str]:
    try:
        _move(plane, link.plane_issue_id, event, **words)
    except PlaneError as exc:
        return f"Zmena je uložená, ale tiket {link.plane_identifier} v Plane sa neposunul: {exc}"
    return None


@dataclass(frozen=True)
class Deployed:
    """A ticket of a version just deployed to PROD — plain data, safe to hand to a thread."""

    link_id: object
    issue_id: str
    identifier: str


def deployed_tickets(db: Session, version_id) -> list[Deployed]:
    """The version's tickets not closed yet — a second PROD deploy closes nothing again."""
    rows = db.execute(
        select(VersionTicket).where(VersionTicket.version_id == version_id, VersionTicket.closed_at.is_(None))
    ).scalars()
    return [Deployed(r.id, r.plane_issue_id, r.plane_identifier) for r in rows]


def close_deployed(
    plane_factory, tickets: list[Deployed], *, version: str, customer: str
) -> tuple[list[object], list[str]]:
    """Move every ticket of a version deployed to PROD to Done — ``(closed link ids, sentences of those that did not
    follow)``."""
    if not tickets:
        return [], []
    try:
        plane = plane_factory()
    except PlaneError as exc:
        return [], [f"Tikety verzie {version} sa neuzavreli: {exc}"]
    closed: list[object] = []
    warnings: list[str] = []
    for ticket in tickets:
        try:
            _move(plane, ticket.issue_id, "deployed", version=version, customer=customer)
        except PlaneError as exc:
            warnings.append(f"Tiket {ticket.identifier} sa po nasadení na PROD neuzavrel: {exc}")
        else:
            closed.append(ticket.link_id)
    return closed, warnings


def mark_closed(db: Session, link_ids: list[object]) -> None:
    now = datetime.now(timezone.utc)
    for link_id in link_ids:
        link = db.get(VersionTicket, link_id)
        if link is not None and link.closed_at is None:
            link.closed_at = now
    if link_ids:
        db.flush()
