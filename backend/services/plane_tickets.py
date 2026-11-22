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

from backend.core.local_time import local_time
from backend.db.models.foundation import User
from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.services.people import person_name
from backend.services.plane_gateway import PlaneError, PlaneGateway, TicketRef, html_to_paragraphs

#: Where a ticket came from — said in its text, so the register shows which door of the cockpit founded it.
SOURCES = {
    "poradca": "z rozhovoru s Poradcom",
    "zasobnik": "zo Zásobníka",  # before DEV-455 — the tickets founded then still say it
    "plane_tikety": "zo záložky Plane tikety",
    "kokpit": "z kokpitu",
    "ideas": "z Nápadov",  # DEV-318 — the ticket of a project founded from an idea
    "uat": "zo skúšky na UAT",  # DEV-492 — a test that did not pass
    "dead_code": "z kontroly nepoužívaného kódu",  # DEV-492 — the cleanup of old dead code
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
    return person_name(user) or "neznámy používateľ"


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
    project: Optional[Project],
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
    A problem of the cockpit met in a new idea (DEV-318) has no project — only such a ticket may come without one.
    """
    if (work_kind is None and module_name is None) or (work_kind is not None and work_kind not in WORK_KIND_LABELS):
        raise PlaneError(WORK_KIND_MISSING)
    if project is None and module_name is None:
        raise PlaneError("Tiket na prácu v projekte potrebuje projekt.")
    if work_kind is not None:
        labels = (*labels, WORK_KIND_LABELS[work_kind])
    module = gateway.ensure_module(module_name) if module_name else module_for(db, project, gateway)
    text = (
        f"Robí: {doer}\n\n{description.strip()}\n\n"
        f"Založil: {person(user)} cez NEX Studio Visual ({SOURCES.get(source, source)}), "
        + (f"projekt {project.name}." if project is not None else "Nápady (projekt zatiaľ neexistuje).")
    )
    return gateway.create_ticket(title=title, description=text, module=module, labels=labels)


# --- the „Plane tikety" tab (DEV-120) ---------------------------------------------------------------------------------

#: Column groups of a finished ticket — whatever the column is called.
CLOSED_GROUPS = ("completed", "cancelled")
TICKET_TYPE_PREFIX = "Typ: "
_DOER = re.compile(r"Robí:\s*([^<\n]+)")


@dataclass(frozen=True)
class ProjectTicket:
    issue_id: str
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
    text: str
    fix_version: Optional[str]
    in_version: Optional[str] = None


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
    A ticket put into a version names it (DEV-455); a fast fix started from it names its version (DEV-122).
    """
    module = gateway.find_module(project.plane_module_id, project.name)
    if module is None:
        return ProjectTickets(None, [])
    states = gateway.state_names()
    labels = {lab["id"]: lab.get("name", "") for lab in gateway.labels()}
    fixes = {
        version.plane_issue_id: f"v{version.version_number}"
        for version in db.execute(
            select(Version).where(Version.project_id == project.id, Version.plane_issue_id.is_not(None))
        ).scalars()
    }
    # DEV-455: the version the Manažér put the ticket into — the cockpit's own link, never read from a text.
    from backend.services import version_tickets

    in_versions = version_tickets.versions_of_project(db, project.id)
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
        rows.append(
            ProjectTicket(
                issue_id=issue["id"],
                identifier=identifier(gateway, issue),
                title=issue.get("name", ""),
                state=state,
                closed=group in CLOSED_GROUPS,
                ticket_type=types[0] if len(types) == 1 else None,
                work_kind=kind,
                warning=warning,
                doer=_doer(issue.get("description_html", "")),
                updated_at=_when(issue.get("updated_at")),
                url=gateway.ticket_url(issue),
                text=ticket_text(issue),
                fix_version=fixes.get(issue["id"]),
                in_version=in_versions.get(issue["id"]),
            )
        )
    rows.sort(key=lambda t: t.updated_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return ProjectTickets(module.get("name"), rows)


# --- from a ticket to a version or to a fast fix (DEV-122, DEV-455) ---------------------------------------------------

#: How the ticket of a fast fix follows its version: the column (``None`` = only the comment), the comment for people,
#: and the sentence when Plane did not follow — the fix itself never waits for Plane.
FIX_MOVES = {
    "uat": (
        None,
        "Rýchla oprava {version} je nasadená na UAT u zákazníka {customer} ({date}).",
        "Tiket {ticket} rýchlej opravy sa o nasadení na UAT nedozvedel",
    ),
    "prod": (
        "Done",
        "Rýchla oprava {version} je nasadená na PROD u zákazníka {customer} ({date}).",
        "Tiket {ticket} rýchlej opravy sa po nasadení na PROD neuzavrel",
    ),
    # DEV-491: a fast fix cancelled before it did anything gives its ticket back to the queue.
    "cancelled": (
        "Todo",
        "Rýchla oprava {version} je zrušená skôr, než niečo urobila — tiket čaká na novú opravu. Zrušil: {who}.",
        "Rýchla oprava je zrušená, ale tiket {ticket} sa v Plane nevrátil do Todo",
    ),
}
_DOER_LINE = re.compile(r"^Robí:.*$", re.MULTILINE)


@dataclass(frozen=True)
class LinkedTicket:
    """A ticket a fast fix was started from before DEV-492 — plain data, safe to hand to a thread; it still follows
    its version to PROD, and back to Todo when the version is cancelled."""

    issue_id: str
    identifier: str
    url: str


def identifier(gateway: PlaneGateway, issue: dict) -> str:
    return f"{gateway.project()['identifier']}-{issue.get('sequence_id')}"


def ticket_text(issue: dict) -> str:
    """The ticket's text without its ``Robí:`` line (who works on the ticket, not what to do)."""
    text = html_to_paragraphs(issue.get("description_html", ""))
    return re.sub(r"\n{3,}", "\n\n", _DOER_LINE.sub("", text)).strip()


def find_project_ticket(gateway: PlaneGateway, module_id: Optional[str], project_name: str, issue_id: str) -> dict:
    """A live ticket of the project's module — else a sentence: a ticket of another project never starts anything."""
    module = gateway.find_module(module_id, project_name)
    if module is None:
        raise ValueError(f"Projekt {project_name} ešte nemá modul v evidencii DEV — tiket do neho nepatrí.")
    for issue in gateway.module_tickets(module["id"]):
        if issue.get("id") == issue_id:
            return issue
    raise ValueError(f"Tiket v module projektu {project_name} nie je (zmazaný, archivovaný alebo z iného projektu).")


def fix_of_ticket(db: Session, project_id, issue_id: str) -> Optional[Version]:
    """The version a fast fix started from this ticket runs in (before DEV-492) — one ticket, one way."""
    return (
        db.execute(
            select(Version)
            .where(Version.project_id == project_id, Version.plane_issue_id == issue_id)
            .order_by(Version.created_at)
        )
        .scalars()
        .first()
    )


def follow_fix(plane_factory, ticket: LinkedTicket, event: str, **words: str) -> Optional[str]:
    """Comment, then (when the event moves it) the column; ``None`` when the ticket followed, else the sentence."""
    state, sentence, _ = FIX_MOVES[event]
    try:
        plane = plane_factory()
        plane.comment(ticket.issue_id, sentence.format(**words))
        if state is not None:
            plane.set_state(ticket.issue_id, state)
    except PlaneError as exc:
        return fix_failed(ticket, event, str(exc))
    return None


def fix_failed(ticket: LinkedTicket, event: str, why: str) -> str:
    """The sentence when the ticket of a fast fix did not follow its version."""
    return f"{FIX_MOVES[event][2].format(ticket=ticket.identifier)}: {why}"


# --- a ticket of DEV, by its number, for the agent (DEV-218) ---------------------------------------------------------


@dataclass(frozen=True)
class TicketComment:
    author: str
    at: Optional[datetime]
    text: str


@dataclass(frozen=True)
class RegisterTicket:
    """A ticket as the agent gets it — what it says and what was said under it, never who works on it now."""

    identifier: str
    register: str
    title: str
    state: str
    archived: bool
    updated_at: Optional[datetime]
    url: str
    text: str
    comments: tuple[TicketComment, ...]

    def document(self) -> str:
        """The ticket as one Markdown document — what Poradca reads and what the file for the agent holds."""
        facts = [f"Tiket z evidencie {self.register} v Plane", f"stav: {self.state or 'neznámy'}"]
        if self.archived:
            facts.append("archivovaný")
        if self.updated_at is not None:
            facts.append(f"naposledy zmenený {_moment(self.updated_at)}")
        lines = [
            f"# {self.identifier} — {self.title}",
            "",
            " · ".join(facts),
            self.url,
            "",
            self.text or "(bez popisu)",
        ]
        if not self.comments:
            lines += ["", "Komentáre: žiadne."]
        else:
            lines += ["", f"## Komentáre ({len(self.comments)}, od najstaršieho)"]
            for c in self.comments:
                heading = " · ".join(p for p in (c.author, _moment(c.at) if c.at else "") if p)
                lines += ["", f"### {heading}", "", c.text or "(prázdny)"]
        return "\n".join(lines) + "\n"


def _moment(at: datetime) -> str:
    return local_time(at).strftime("%d.%m.%Y %H:%M")


def read_ticket(gateway: PlaneGateway, number: str) -> RegisterTicket:
    """The ticket ``number`` (``DEV-186``) of the gateway's register — development is kept in DEV only.

    Raises :class:`PlaneError` with the sentence for a person when the cockpit does not read it."""
    issue, comments = gateway.ticket_by_identifier(number)
    names = gateway.member_names() if comments else {}
    return RegisterTicket(
        identifier=identifier(gateway, issue),
        register=gateway.project()["identifier"],
        title=issue.get("name", ""),
        state=gateway.state_names().get(issue.get("state"), ("", ""))[0],
        archived=bool(issue.get("archived_at")),
        updated_at=_when(issue.get("updated_at")),
        url=gateway.ticket_url(issue),
        text=ticket_text(issue),
        comments=tuple(
            TicketComment(
                author=names.get(c.get("actor") or c.get("created_by") or "", "") or "neznámy autor",
                at=_when(c.get("created_at")),
                text=html_to_paragraphs(c.get("comment_html", "")),
            )
            for c in comments
        ),
    )
