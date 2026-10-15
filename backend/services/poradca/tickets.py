"""A ticket in Plane from a Poradca answer — Poradca proposes, a person's click founds it (DEV-119).

Director 14.10.2026: „Chcel by som aby poradca vedel zakladať do plane tiket na dve veci, buď nejaký problém s NSV,
ten tiket potom budeš riešiť ty, alebo pre konkrétny projekt, ktorý robí a čo pojde do nasledujúcej verzie, alebo do
rýchlej opravy.“ The same pattern as the Zásobník: Poradca changes nothing, the answer carries a block
(:func:`handoff.ticket_proposal`) and a person founds the ticket after a preview he may edit.

* A problem of the cockpit (``nsv``) goes to the module NEX Studio Visual of the DEV register; Andros Dedo works on it
  and is responsible. The cockpit adds what Dedo needs to start: the project, the version, where its build stands
  and the way back to the conversation.
* Work on the project (``project``) goes to the project's module with its work kind and a proposal — the next
  version or a fast fix. Only a proposal: the ticket creates and starts no version (DEV-29).

Before founding, the preview names similar open tickets of the module, so the same thing is not filed twice; a second
click returns the ticket the first one founded.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.config.settings import settings
from backend.db.models.foundation import User
from backend.db.models.pipeline import PipelineState
from backend.db.models.poradca import AUTHOR_PORADCA, DONE, PoradcaConversation, PoradcaMessage
from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.services import backlog_tickets, plane_tickets
from backend.services.plane_gateway import PlaneGateway
from backend.services.poradca.handoff import HandoffRefused, TicketProposal, ticket_proposal

#: The DEV module of the cockpit itself and who solves its problems.
COCKPIT_MODULE = "NEX Studio Visual"
COCKPIT_DOER = "Andros Dedo"
#: The proposal a project ticket carries — the Director decides; the ticket starts nothing.
WAY_WORDS = {"version": "do ďalšej verzie", "fast_fix": "do rýchlej opravy"}
WAY_MISSING = "Vyber, či tiket navrhuješ do ďalšej verzie, alebo do rýchlej opravy."
SIMILAR_MAX = 5
_WORD = re.compile(r"\w{4,}")


@dataclass(frozen=True)
class SimilarTicket:
    identifier: str
    title: str
    state: str
    url: str


@dataclass(frozen=True)
class Draft:
    kind: str
    title: str
    description: str
    ticket_type: str
    work_kind: Optional[str]
    way: Optional[str]
    responsible_options: list[str]
    responsible: Optional[str]
    context: list[str] = field(default_factory=list)
    similar: list[SimilarTicket] = field(default_factory=list)
    plane_identifier: Optional[str] = None
    plane_url: Optional[str] = None


def proposal_of(message: PoradcaMessage) -> TicketProposal:
    """The ticket the answer proposes — else the sentence why there is none."""
    if message.author != AUTHOR_PORADCA or message.status != DONE:
        raise HandoffRefused("Tiket sa dá založiť len z hotovej odpovede Poradcu.")
    proposal = ticket_proposal(message.content)
    if proposal is None:
        raise HandoffRefused("V tejto odpovedi nie je návrh tiketu.")
    return proposal


def _conversation_and_project(db: Session, message: PoradcaMessage) -> tuple[PoradcaConversation, Project]:
    conversation = db.get(PoradcaConversation, message.conversation_id)
    project = db.get(Project, conversation.project_id) if conversation else None
    if project is None:
        raise HandoffRefused("Projekt rozhovoru už neexistuje.")
    return conversation, project


def context_lines(
    db: Session, message: PoradcaMessage, conversation: PoradcaConversation, project: Project
) -> list[str]:
    """What Andros Dedo needs to start on a problem of the cockpit — read now, never taken from the browser."""
    lines = [f"Projekt: {project.name}"]
    version_id = message.version_id or conversation.version_id
    version = db.get(Version, version_id) if version_id else None
    if version is None:
        lines.append("Verzia: rozhovor je o celom projekte")
    else:
        lines.append(f"Verzia: v{version.version_number}")
        state = db.execute(select(PipelineState).where(PipelineState.version_id == version.id)).scalar_one_or_none()
        if state is None:
            lines.append("Stavba: ešte nezačala")
        else:
            reason = f", dôvod zastavenia {state.block_reason}" if state.block_reason else ""
            lines.append(f"Stavba: fáza {state.current_stage}, stav {state.status}{reason}")
            if state.next_action:
                lines.append(f"Čo ďalej podľa kokpitu: {state.next_action}")
    if settings.app_public_url:
        lines.append(f"Rozhovor Poradcu: {settings.app_public_url.rstrip('/')}/poradca?c={conversation.id}")
    else:
        lines.append(f"Rozhovor Poradcu: „{conversation.title}“")
    return lines


def _words(text: str) -> set[str]:
    plain = "".join(c for c in unicodedata.normalize("NFKD", text or "") if not unicodedata.combining(c))
    return set(_WORD.findall(plain.casefold()))


def similar_tickets(plane: PlaneGateway, module: Optional[dict], title: str) -> list[SimilarTicket]:
    """Open tickets of the module whose titles share the title's words (two, or all of a shorter title)."""
    wanted = _words(title)
    if module is None or not wanted:
        return []
    states = plane.state_names()
    found: list[tuple[int, SimilarTicket]] = []
    for issue in plane.module_tickets(module["id"]):
        state, group = states.get(issue.get("state"), ("", ""))
        common = wanted & _words(issue.get("name", ""))
        if group in plane_tickets.CLOSED_GROUPS or len(common) < min(2, len(wanted)):
            continue
        ticket = SimilarTicket(
            plane_tickets.identifier(plane, issue), issue.get("name", ""), state, plane.ticket_url(issue)
        )
        found.append((len(common), ticket))
    found.sort(key=lambda pair: -pair[0])
    return [ticket for _, ticket in found[:SIMILAR_MAX]]


def draft(db: Session, plane: PlaneGateway, message: PoradcaMessage, user: Optional[User]) -> Draft:
    """What „Založiť tiket" offers — or the ticket that exists, without asking Plane anything."""
    if message.plane_issue_id:
        proposal = ticket_proposal(message.content)
        return Draft(
            kind=proposal.kind if proposal else "project",
            title=proposal.title if proposal else "",
            description="",
            ticket_type="Task",
            work_kind=None,
            way=None,
            responsible_options=[],
            responsible=None,
            plane_identifier=message.plane_identifier,
            plane_url=message.plane_url,
        )
    proposal = proposal_of(message)
    conversation, project = _conversation_and_project(db, message)
    if proposal.kind == "nsv":
        module = plane.find_module(None, COCKPIT_MODULE)
        return Draft(
            kind="nsv",
            title=proposal.title,
            description=proposal.description,
            ticket_type="Bug",
            work_kind=None,
            way=None,
            responsible_options=[COCKPIT_DOER],
            responsible=COCKPIT_DOER,
            context=context_lines(db, message, conversation, project),
            similar=similar_tickets(plane, module, proposal.title),
        )
    module = plane.find_module(project.plane_module_id, project.name)
    people = backlog_tickets.responsible_people(plane)
    return Draft(
        kind="project",
        title=proposal.title,
        description=proposal.description,
        ticket_type="Bug" if proposal.work_kind == "fix" else "Task",
        work_kind=proposal.work_kind,
        way=proposal.way,
        responsible_options=people,
        responsible=backlog_tickets.responsible_of(user, people),
        similar=similar_tickets(plane, module, proposal.title),
    )


def found(
    db: Session,
    plane: PlaneGateway,
    message_id,
    user: Optional[User],
    *,
    title: str,
    description: str,
    ticket_type: str,
    work_kind: Optional[str],
    way: Optional[str],
    responsible: Optional[str],
) -> tuple[PoradcaMessage, bool]:
    """Found the proposed ticket once; returns ``(message, founded_now)``.

    The answer's row stays locked while Plane is asked, so a second click waits and then finds the ticket.
    """
    message = db.execute(
        select(PoradcaMessage)
        .where(PoradcaMessage.id == message_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if message is None:
        raise HandoffRefused("Správa sa nenašla.")
    if message.plane_issue_id:
        return message, False
    proposal = proposal_of(message)
    conversation, project = _conversation_and_project(db, message)
    labels = (f"{backlog_tickets.TICKET_TYPE_GROUP}: {ticket_type}",)
    if proposal.kind == "nsv":
        context = "\n".join(f"- {line}" for line in context_lines(db, message, conversation, project))
        ref = plane_tickets.file_ticket(
            db,
            plane,
            project=project,
            user=user,
            source="poradca",
            title=title,
            description=f"{description.strip()}\n\nKontext z kokpitu:\n{context}",
            labels=(*labels, f"{backlog_tickets.RESPONSIBLE_GROUP}: {COCKPIT_DOER}"),
            doer=COCKPIT_DOER,
            module_name=COCKPIT_MODULE,
        )
    else:
        if work_kind not in plane_tickets.WORK_KIND_LABELS:
            raise ValueError(plane_tickets.WORK_KIND_MISSING)
        if way not in WAY_WORDS:
            raise ValueError(WAY_MISSING)
        if responsible not in backlog_tickets.responsible_people(plane):
            raise ValueError(f"„{responsible}“ v skupine „{backlog_tickets.RESPONSIBLE_GROUP}“ evidencie DEV nie je.")
        ref = plane_tickets.file_ticket(
            db,
            plane,
            project=project,
            user=user,
            source="poradca",
            title=title,
            description=(
                f"{description.strip()}\n\nNávrh: {WAY_WORDS[way]}. O verzii rozhoduje Director — tiket ju nezakladá "
                "ani nespúšťa."
            ),
            labels=(*labels, f"{backlog_tickets.RESPONSIBLE_GROUP}: {responsible}"),
            doer=responsible,
            work_kind=work_kind,
        )
    message.plane_issue_id, message.plane_identifier, message.plane_url = ref.id, ref.identifier, ref.url
    db.flush()
    return message, True
