"""Z odpovede Poradcu k činu — dve tlačidlá pod odpoveďou (ICCINT-167, návrh §3 „Keď treba niečo zmeniť").

Poradca nič nemení; keď treba zmenu, vloží do odpovede jeden z dvoch blokov (charta, časť 4):

  * ``<pokyn-pre-agenta>…</pokyn-pre-agenta>`` → „Vložiť do Riadiaceho centra": text sa vloží do poľa, ktoré
    v Riadiacom centre práve prijíma text, a odošle ho človek sám (rieši frontend — nič sa tu neposiela);
  * ``<poziadavka-do-zasobnika>…</poziadavka-do-zasobnika>`` → „Uložiť do Zásobníka": požiadavka REQ-N
    v Zásobníku projektu — a nič viac (DEV-29).

DEV-29, Director 08.10.2026: „O verziách rozhodujem ja. Treba, aby zapísal len do zásobníku. To je všetko."
Tlačidlo predtým založilo aj ďalšiu verziu so zadaním z požiadavky; do ktorej verzie požiadavka pôjde,
rozhoduje Director. Odpovede spred tejto zmeny nesú blok ``<poziadavka-na-novu-verziu>`` — číta sa rovnako.

Text požiadavky sa berie z ULOŽENEJ odpovede (prešla filtrom tajomstiev), nikdy z prehliadača. Druhé
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


def extract_block(text: str, name: str) -> Optional[str]:
    """Obsah prvého bloku ``<name>…</name>`` bez okrajových medzier; ``None``, keď blok nie je alebo je prázdny."""
    match = re.search(rf"<{name}>\s*(.*?)\s*</{name}>", text or "", re.S)
    if not match:
        return None
    body = match.group(1).strip()
    return body or None


def backlog_request(text: str) -> Optional[str]:
    """Požiadavka do Zásobníka v odpovedi — nový blok, inak blok odpovedí spred DEV-29."""
    return extract_block(text, BLOCK_BACKLOG) or extract_block(text, BLOCK_BACKLOG_LEGACY)


def _title(summary: str) -> str:
    first = summary.strip().splitlines()[0].strip(" #*-")
    return first[:_TITLE_MAX] or summary[:_TITLE_MAX]


def backlog_item_from_message(db: Session, message: PoradcaMessage) -> SavedRequest:
    """Uloží požiadavku z odpovede Poradcu do Zásobníka projektu (alebo vráti už uloženú). Verziu nezakladá.

    Raises:
        HandoffRefused: správa nie je hotová odpoveď Poradcu alebo nemá blok požiadavky.
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
