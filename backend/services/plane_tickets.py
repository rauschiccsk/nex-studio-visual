"""A project's tickets in Plane — which module they live in and who founded them through the cockpit (DEV-117).

The gateway (:mod:`plane_gateway`) knows Plane; this module knows the cockpit: a project's tickets live in the DEV
register under the module of its application, remembered by id on the project (a renamed project keeps its tickets),
and every ticket says who founded it through the cockpit and from where — the cockpit's account is the author in
Plane, so without that sentence nobody would know which person asked.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db.models.backlog import BacklogItem
from backend.db.models.foundation import User
from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.services.plane_gateway import PlaneError, PlaneGateway, TicketRef

#: Where a ticket came from — said in its text, so the register shows which door of the cockpit founded it.
SOURCES = {
    "poradca": "z rozhovoru s Poradcom",
    "zasobnik": "zo Zásobníka",
    "plane_tikety": "zo záložky Plane tikety",
    "kokpit": "z kokpitu",
}


#: DEV-118 — who pays for the work, as a label of the „Druh práce" group in the DEV register (Director 14.10.2026:
#: „BUG - oprava, GIFT - Dar, a požiadavka zákazníka.“ and „Požiadavka (CHR) - skratka z CHangee Request“), paired
#: with the cockpit's work kind of a version (``backend.schemas.delivery_statement.WorkKind``) — so a version or a
#: fast fix founded from a ticket starts with it.
#: The Dedos' gate creates the group with the same names (``icc_ticket.py druhy-prace``); rename in both.
WORK_KIND_GROUP = "Druh práce"
WORK_KIND_LABELS = {
    "fix": f"{WORK_KIND_GROUP}: Oprava (BUG)",
    "gift": f"{WORK_KIND_GROUP}: Dar (GIFT)",
    "change": f"{WORK_KIND_GROUP}: Požiadavka (CHR)",
}
WORK_KIND_MISSING = (
    "Tiket o práci na projekte musí niesť druh práce — oprava (BUG), dar (GIFT), alebo požiadavka (CHR)."
)


def gateway() -> PlaneGateway:
    """The cockpit's door to Plane — one per request; tests replace it."""
    return PlaneGateway()


def work_kind_of(label_names: Iterable[str]) -> Optional[str]:
    """The work kind a ticket carries, by its „Druh práce" label; ``None`` when it has none.

    Two different kinds on one ticket are a contradiction nobody resolved — said, not guessed.
    """
    by_label = {label.casefold(): kind for kind, label in WORK_KIND_LABELS.items()}
    kinds = {by_label[name.casefold()] for name in label_names if name.casefold() in by_label}
    if len(kinds) > 1:
        raise PlaneError("Tiket nesie dva druhy práce naraz — v Plane nechaj len jeden štítok zo skupiny „Druh práce“.")
    return kinds.pop() if kinds else None


def work_kind_words(kind: Optional[str]) -> Optional[str]:
    """The words of a work kind as its label in Plane says them, without the group — „Oprava (BUG)“."""
    return WORK_KIND_LABELS[kind].removeprefix(f"{WORK_KIND_GROUP}: ") if kind in WORK_KIND_LABELS else None


def person(user: Optional[User]) -> str:
    """The name a person goes by in the register — first and last name, else the user name."""
    if user is None:
        return "neznámy používateľ"
    full = " ".join(part for part in (user.first_name, user.last_name) if part)
    return full or user.username


def module_for(db: Session, project: Project, gateway: PlaneGateway) -> dict:
    """The project's module — by the id it remembers, else by the application's name (created when missing)."""
    if project.plane_module_id:
        kept = [m for m in gateway.modules() if m.get("id") == project.plane_module_id]
        if kept:
            return kept[0]
    module = gateway.ensure_module(project.name)
    project.plane_module_id = module["id"]
    db.flush()
    return module


def file_ticket(
    db: Session,
    gateway: PlaneGateway,
    *,
    project: Project,
    user: Optional[User],
    source: str,
    title: str,
    description: str,
    labels: tuple[str, ...],
    doer: str,
    module_name: Optional[str] = None,
    work_kind: Optional[str] = None,
) -> TicketRef:
    """Found a ticket for ``project`` (or in ``module_name`` — NEX Studio Visual for a problem of the cockpit itself).

    ``doer`` goes to the ``Robí:`` line the Dedos' gate reads (who works on it now). The text ends with who founded
    the ticket through the cockpit and from where. A ticket about work on the project carries its ``work_kind``
    (DEV-118) — nothing is written without it; a problem of the cockpit itself is nobody's bill and carries none.
    """
    if (work_kind is None and module_name is None) or (work_kind is not None and work_kind not in WORK_KIND_LABELS):
        raise PlaneError(WORK_KIND_MISSING)
    if work_kind is not None:
        labels = (*labels, WORK_KIND_LABELS[work_kind])
    module = gateway.ensure_module(module_name) if module_name else module_for(db, project, gateway)
    text = (
        f"Robí: {doer}\n\n{description.strip()}\n\n"
        f"Založil: {person(user)} cez NEX Studio Visual ({SOURCES.get(source, source)}), projekt {project.name}."
    )
    return gateway.create_ticket(title=title, description=text, module=module, labels=labels)


# --- the „Plane tikety" tab (DEV-120) ---------------------------------------------------------------------------------

#: Column groups of a finished ticket — whatever the column is called.
CLOSED_GROUPS = ("completed", "cancelled")
TICKET_TYPE_PREFIX = "Typ: "
_DOER = re.compile(r"Robí:\s*([^<\n]+)")


@dataclass(frozen=True)
class ProjectTicket:
    identifier: str
    title: str
    state: str
    closed: bool
    ticket_type: Optional[str]
    work_kind: Optional[str]
    warning: Optional[str]
    doer: Optional[str]
    updated_at: Optional[datetime]
    url: str
    requirement: Optional[str]
    requirement_version: Optional[str]


@dataclass(frozen=True)
class ProjectTickets:
    module: Optional[str]
    tickets: list[ProjectTicket]


def _doer(description_html: str) -> Optional[str]:
    """Who works on the ticket now — the ``Robí:`` line the Dedos' gate and the cockpit write."""
    found = _DOER.search(html.unescape(description_html or ""))
    return found.group(1).strip() if found else None


def _when(value: Optional[str]) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None
    except ValueError:
        return None


def project_tickets(db: Session, gateway: PlaneGateway, project: Project, *, closed: bool) -> ProjectTickets:
    """The tickets of the project's module in the DEV register, open or finished, newest change first.

    Reading founds nothing: a project without a module yet has no tickets (the module comes with the first one).
    A ticket a requirement of the Zásobník founded names it (REQ-N) and the version the requirement is in.
    """
    module = gateway.find_module(project.plane_module_id, project.name)
    if module is None:
        return ProjectTickets(None, [])
    states = gateway.state_names()
    labels = {lab["id"]: lab.get("name", "") for lab in gateway.labels()}
    linked = {
        item.plane_issue_id: (f"REQ-{item.number}", f"v{version.version_number}" if version else None)
        for item, version in db.execute(
            select(BacklogItem, Version)
            .outerjoin(Version, Version.id == BacklogItem.version_id)
            .where(BacklogItem.project_id == project.id, BacklogItem.plane_issue_id.is_not(None))
        ).all()
    }
    rows: list[ProjectTicket] = []
    for issue in gateway.module_tickets(module["id"]):
        state, group = states.get(issue.get("state"), ("", ""))
        if (group in CLOSED_GROUPS) != closed:
            continue
        names = [labels.get(label_id, "") for label_id in issue.get("labels") or []]
        types = [n[len(TICKET_TYPE_PREFIX) :] for n in names if n.startswith(TICKET_TYPE_PREFIX)]
        try:
            kind, warning = work_kind_of(names), None
        except PlaneError as exc:
            kind, warning = None, str(exc)
        requirement, version = linked.get(issue["id"], (None, None))
        rows.append(
            ProjectTicket(
                identifier=f"{gateway.project()['identifier']}-{issue.get('sequence_id')}",
                title=issue.get("name", ""),
                state=state,
                closed=group in CLOSED_GROUPS,
                ticket_type=types[0] if len(types) == 1 else None,
                work_kind=kind,
                warning=warning,
                doer=_doer(issue.get("description_html", "")),
                updated_at=_when(issue.get("updated_at")),
                url=gateway.ticket_url(issue),
                requirement=requirement,
                requirement_version=version,
            )
        )
    rows.sort(key=lambda t: t.updated_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return ProjectTickets(module.get("name"), rows)
