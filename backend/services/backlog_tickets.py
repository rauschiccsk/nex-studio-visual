"""A backlog requirement's ticket in Plane — founded by a person's click, then following the requirement (DEV-121).

Director 14.10.2026: from a requirement of the Zásobník a ticket is founded in its project's module of the DEV
register; the requirement and the ticket stay linked (the requirement remembers the ticket, the ticket names REQ-N)
and the ticket's state follows the requirement on its own, each move with a comment for people:

* included in a version → In Progress,
* that version deployed to PROD → Done,
* rejected → Cancelled with the reason,
* returned to the Zásobník → Todo.

The requirement's own change never waits for Plane: it is saved first, and when Plane does not follow, the sentence
says so (``plane_warning``) instead of failing the change. A second click on „Založiť tiket" returns the ticket
that exists — the row is locked while the first one is being founded.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db.models.backlog import BacklogItem
from backend.db.models.foundation import User
from backend.db.models.projects import Project
from backend.services import plane_tickets
from backend.services.plane_gateway import PlaneError, PlaneGateway, name_key

RESPONSIBLE_GROUP = "Zodpovedný"
TICKET_TYPE_GROUP = "Typ"

#: Where the ticket of a requirement goes when the requirement moves, and what the comment says.
MOVES = {
    "included": ("In Progress", "Požiadavka {req} je zaradená do verzie {version}. Zaradil: {who}."),
    "rejected": ("Cancelled", "Požiadavka {req} je zamietnutá, dôvod: {reason}. Zamietol: {who}."),
    "reopened": ("Todo", "Požiadavka {req} je vrátená do Zásobníka. Vrátil: {who}."),
    "deployed": ("Done", "Verzia {version} s požiadavkou {req} je nasadená na PROD u zákazníka {customer}."),
}

#: A ticket is founded only for a requirement that still waits for its work.
FOUNDABLE_STATUSES = ("open", "included")


def req(item: BacklogItem) -> str:
    return f"REQ-{item.number}"


def gateway() -> PlaneGateway:
    """The cockpit's door to Plane — one per request; tests replace it."""
    return PlaneGateway()


def responsible_people(plane: PlaneGateway) -> list[str]:
    """The people of the „Zodpovedný“ group in the DEV register, by the names its labels carry."""
    prefix = f"{RESPONSIBLE_GROUP}: "
    return sorted(
        label["name"][len(prefix) :] for label in plane.labels() if (label.get("name") or "").startswith(prefix)
    )


def responsible_of(user: Optional[User], people: list[str]) -> Optional[str]:
    """The person who clicked, when the register knows them — by full name, else by first name."""
    if user is None:
        return None
    by_key = {name_key(name): name for name in people}
    full = " ".join(part for part in (user.first_name, user.last_name) if part)
    for candidate in (full, user.first_name or "", user.username or ""):
        if candidate and name_key(candidate) in by_key:
            return by_key[name_key(candidate)]
    return None


@dataclass(frozen=True)
class Draft:
    title: str
    description: str
    ticket_type: str
    responsible_options: list[str]
    responsible: Optional[str]
    plane_identifier: Optional[str]
    plane_url: Optional[str]


def draft(plane: Optional[PlaneGateway], item: BacklogItem, user: Optional[User]) -> Draft:
    """What „Založiť tiket" offers — or the ticket that exists, without asking Plane anything."""
    if item.plane_issue_id:
        return Draft(item.title, item.description or "", "Task", [], None, item.plane_identifier, item.plane_url)
    people = responsible_people(plane or gateway())
    return Draft(item.title, item.description or "", "Task", people, responsible_of(user, people), None, None)


def found(
    db: Session,
    plane: PlaneGateway,
    item_id,
    user: Optional[User],
    *,
    title: str,
    description: str,
    work_kind: str,
    ticket_type: str,
    responsible: str,
) -> tuple[BacklogItem, bool]:
    """Found the requirement's ticket once; returns ``(item, founded_now)``.

    The row stays locked while Plane is asked, so a second click waits and then finds the ticket. A requirement
    already in a version gets its ticket straight into In Progress.
    """
    # populate_existing: the row may already be loaded in this session (the route checked access with it); without
    # it a second click that waited on the lock would still see the ticket as missing and found another one.
    item = db.execute(
        select(BacklogItem).where(BacklogItem.id == item_id).with_for_update().execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if item is None:
        raise ValueError(f"Backlog item {item_id} not found")
    if item.plane_issue_id:
        return item, False
    if item.status not in FOUNDABLE_STATUSES:
        stav = "zamietnutá" if item.status == "rejected" else "hotová"
        raise ValueError(f"Požiadavka {req(item)} je už {stav} — tiket sa pre ňu nezakladá.")
    if responsible not in responsible_people(plane):
        raise ValueError(f"„{responsible}“ v skupine „{RESPONSIBLE_GROUP}“ evidencie DEV nie je.")
    project = db.get(Project, item.project_id)
    text = f"{description.strip()}\n\nPožiadavka {req(item)} zo Zásobníka projektu {project.name}."
    ref = plane_tickets.file_ticket(
        db,
        plane,
        project=project,
        user=user,
        source="zasobnik",
        title=title,
        description=text,
        labels=(f"{TICKET_TYPE_GROUP}: {ticket_type}", f"{RESPONSIBLE_GROUP}: {responsible}"),
        doer=responsible,
        work_kind=work_kind,
    )
    item.plane_issue_id, item.plane_identifier, item.plane_url = ref.id, ref.identifier, ref.url
    db.flush()
    return item, True


def move(plane: PlaneGateway, issue_id: str, event: str, **words: str) -> None:
    """Comment first, then the state — both read back by the gateway; a refusal raises :class:`PlaneError`."""
    state, sentence = MOVES[event]
    plane.comment(issue_id, sentence.format(**words))
    plane.set_state(issue_id, state)


def follow(plane_factory, item: BacklogItem, event: str, **words: str) -> Optional[str]:
    """Move the requirement's ticket after its change is saved; ``None`` when it followed, else the sentence.

    No ticket — nothing to do and Plane is not asked.
    """
    if not item.plane_issue_id:
        return None
    try:
        move(plane_factory(), item.plane_issue_id, event, req=req(item), **words)
    except PlaneError as exc:
        return f"Požiadavka je uložená, ale tiket {item.plane_identifier} v Plane sa neposunul: {exc}"
    return None


@dataclass(frozen=True)
class Deployed:
    """A ticketed requirement of a version just deployed to PROD — plain data, safe to hand to a thread."""

    issue_id: str
    identifier: str
    req: str


def deployed_tickets(db: Session, version_id) -> list[Deployed]:
    rows = db.execute(
        select(BacklogItem).where(BacklogItem.version_id == version_id, BacklogItem.plane_issue_id.is_not(None))
    ).scalars()
    return [Deployed(r.plane_issue_id, r.plane_identifier or r.plane_issue_id, req(r)) for r in rows]


def close_deployed(plane_factory, tickets: list[Deployed], *, version: str, customer: str) -> list[str]:
    """Move every ticket of a version deployed to PROD to Done; the sentences of those that did not follow."""
    if not tickets:
        return []
    warnings: list[str] = []
    try:
        plane = plane_factory()
    except PlaneError as exc:
        return [f"Tikety požiadaviek verzie {version} sa neuzavreli: {exc}"]
    for ticket in tickets:
        try:
            move(plane, ticket.issue_id, "deployed", req=ticket.req, version=version, customer=customer)
        except PlaneError as exc:
            warnings.append(f"Tiket {ticket.identifier} ({ticket.req}) sa po nasadení na PROD neuzavrel: {exc}")
    return warnings
