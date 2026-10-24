"""From a Poradca answer to action — two buttons under the answer (ICCINT-167, design §3 „Keď treba niečo zmeniť").

Poradca changes nothing; when a change is needed, it puts one of two blocks into the answer (charter, part 4):

  * ``<pokyn-pre-agenta>…</pokyn-pre-agenta>`` → „Vložiť do Riadiaceho centra": text sa vloží do poľa, ktoré
    v Riadiacom centre práve prijíma text, a odošle ho človek sám (rieši frontend — nič sa tu neposiela);
  * ``<poziadavka-do-zasobnika>…</poziadavka-do-zasobnika>`` → „Uložiť do Zásobníka": požiadavka REQ-N
    v Zásobníku projektu — a nič viac (DEV-29).

DEV-29, Director 08.10.2026: „O verziách rozhodujem ja. Treba, aby zapísal len do zásobníku. To je všetko."
Tlačidlo predtým založilo aj ďalšiu verziu so zadaním z požiadavky; do ktorej verzie požiadavka pôjde,
rozhoduje Director. Odpovede spred tejto zmeny nesú blok ``<poziadavka-na-novu-verziu>`` — číta sa rovnako.

DEV-119: a third block proposes a ticket in Plane — ``<tiket-nsv>`` for a problem of the cockpit itself (Andros Dedo
solves it), ``<tiket-projektu druh="…" cesta="…">`` for work on the project, proposed for the next version or for a
fast fix. The ticket is founded by a person's click after a preview (:mod:`backend.services.poradca.tickets`).

DEV-218: a fourth block names tickets the agent asked for — ``<tiket-pre-agenta>MAGER-66, DEV-186</tiket-pre-agenta>``.
„Priložiť k odpovedi agentovi" attaches each one's content as a file, read from Plane at the click
(:mod:`backend.services.poradca.agent_tickets`); Poradca never retypes a ticket.

The request text is taken from the STORED answer (it passed the secret filter), never from the browser. The second
kliknutie vráti už uloženú požiadavku — nezaloží druhú.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session

from backend.db.models.backlog import BacklogItem
from backend.db.models.poradca import AUTHOR_PORADCA, DONE, PoradcaConversation, PoradcaMessage
from backend.db.models.projects import Project
from backend.schemas.backlog import BacklogItemCreate
from backend.services import backlog as backlog_service

BLOCK_INSTRUCTION = "pokyn-pre-agenta"
BLOCK_BACKLOG = "poziadavka-do-zasobnika"
#: The block answers carried before DEV-29 — still saved to the backlog, never to a version.
BLOCK_BACKLOG_LEGACY = "poziadavka-na-novu-verziu"

BLOCK_TICKET_NSV = "tiket-nsv"
BLOCK_TICKET_PROJECT = "tiket-projektu"
#: What Poradca writes in ``druh="…"`` and ``cesta="…"`` of a project ticket → the cockpit's work kind and way.
TICKET_WORK_KINDS = {"oprava": "fix", "dar": "gift", "poziadavka": "change"}
TICKET_WAYS = {"verzia": "version", "rychla-oprava": "fast_fix"}
_TICKET = re.compile(
    rf"<({BLOCK_TICKET_NSV}|{BLOCK_TICKET_PROJECT})((?:\s+[a-z-]+=\"[^\"]*\")*)\s*>\s*(.*?)\s*</\1>", re.S
)
_ATTRIBUTE = re.compile(r'([a-z-]+)="([^"]*)"')

BLOCK_AGENT_TICKETS = "tiket-pre-agenta"
#: The most tickets one answer attaches for the agent — a brief names a few, never a register.
MAX_AGENT_TICKETS = 5
_TICKET_IDENTIFIER = re.compile(r"\b[A-Za-z][A-Za-z0-9]{0,11}-\d{1,7}\b")

#: Najkratšia požiadavka, ktorú má zmysel ukladať.
_MIN_REQUEST_CHARS = 10
_TITLE_MAX = 200


class HandoffRefused(ValueError):
    """Z tejto správy sa požiadavka uložiť nedá — veta pre človeka."""


@dataclass(frozen=True)
class SavedRequest:
    backlog_item_id: UUID
    number: int
    project_slug: str
    created: bool


@dataclass(frozen=True)
class TicketProposal:
    """A ticket Poradca proposes: ``nsv`` (the cockpit's own problem) or ``project``; the first line is the title."""

    kind: str
    title: str
    description: str
    work_kind: Optional[str]
    way: Optional[str]


def extract_block(text: str, name: str) -> Optional[str]:
    """Content of the first ``<name>…</name>`` block without edge whitespace; ``None`` if absent or empty."""
    match = re.search(rf"<{name}>\s*(.*?)\s*</{name}>", text or "", re.S)
    if not match:
        return None
    body = match.group(1).strip()
    return body or None


def backlog_request(text: str) -> Optional[str]:
    """Požiadavka do Zásobníka v odpovedi — nový blok, inak blok odpovedí spred DEV-29."""
    return extract_block(text, BLOCK_BACKLOG) or extract_block(text, BLOCK_BACKLOG_LEGACY)


def ticket_proposal(text: str) -> Optional[TicketProposal]:
    """The first ticket block of the answer; ``None`` without one (or with an empty one)."""
    match = _TICKET.search(text or "")
    body = match.group(3).strip() if match else ""
    if len(body) < _MIN_REQUEST_CHARS:
        return None
    attributes = {name: value.strip().casefold() for name, value in _ATTRIBUTE.findall(match.group(2))}
    project = match.group(1) == BLOCK_TICKET_PROJECT
    return TicketProposal(
        kind="project" if project else "nsv",
        title=_title(body),
        description="\n".join(body.splitlines()[1:]).strip(),
        work_kind=TICKET_WORK_KINDS.get(attributes.get("druh", "")) if project else None,
        way=TICKET_WAYS.get(attributes.get("cesta", "")) if project else None,
    )


def agent_tickets(text: str) -> list[str]:
    """The tickets the first ``<tiket-pre-agenta>`` block names, as Plane writes them, each once, in its order."""
    found: list[str] = []
    for identifier in _TICKET_IDENTIFIER.findall(extract_block(text, BLOCK_AGENT_TICKETS) or ""):
        if identifier.upper() not in found:
            found.append(identifier.upper())
    return found[:MAX_AGENT_TICKETS]


def _title(summary: str) -> str:
    first = summary.strip().splitlines()[0].strip(" #*-")
    return first[:_TITLE_MAX] or summary[:_TITLE_MAX]


def backlog_item_from_message(db: Session, message: PoradcaMessage) -> SavedRequest:
    """Uloží požiadavku z odpovede Poradcu do Zásobníka projektu (alebo vráti už uloženú). Verziu nezakladá.

    Raises:
        HandoffRefused: the message is not a finished Poradca answer or has no request block.
    """
    if message.author != AUTHOR_PORADCA or message.status != DONE:
        raise HandoffRefused("Požiadavku sa dá uložiť len z hotovej odpovede Poradcu.")
    summary = backlog_request(message.content)
    if summary is None or len(summary) < _MIN_REQUEST_CHARS:
        raise HandoffRefused("V tejto odpovedi nie je požiadavka do Zásobníka.")
    conversation = db.get(PoradcaConversation, message.conversation_id)
    project = db.get(Project, conversation.project_id) if conversation else None
    if project is None:
        raise HandoffRefused("Projekt rozhovoru už neexistuje.")
    if message.captured_backlog_item_id is not None:
        existing = db.get(BacklogItem, message.captured_backlog_item_id)
        if existing is not None:
            return SavedRequest(existing.id, existing.number, project.slug, created=False)
    item = backlog_service.create(
        db, BacklogItemCreate(project_id=project.id, title=_title(summary), description=summary)
    )
    message.captured_backlog_item_id = item.id
    db.flush()
    return SavedRequest(item.id, item.number, project.slug, created=True)
