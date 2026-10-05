"""Z odpovede Poradcu k činu — dve tlačidlá pod odpoveďou (ICCINT-167, návrh §3 „Keď treba niečo zmeniť").

Poradca nič nemení; keď treba zmenu, vloží do odpovede jeden z dvoch blokov (charta, časť 4):

  * ``<pokyn-pre-agenta>…</pokyn-pre-agenta>`` → „Vložiť do Riadiaceho centra": text sa vloží do poľa
    rozhovoru stavby a odošle ho človek sám (rieši frontend — nič sa tu neposiela);
  * ``<poziadavka-na-novu-verziu>…</poziadavka-na-novu-verziu>`` → „Založiť novú verziu z tejto požiadavky":
    požiadavka do zásobníka a ďalšia verzia ako koncept, bez spustenej stavby — tá istá cesta, akou to
    robila Konzultácia (:func:`backend.services.change_request.mint_from_request`).

Text požiadavky sa berie z ULOŽENEJ odpovede (prešla filtrom tajomstiev), nikdy z prehliadača. Druhé
kliknutie vráti už založenú verziu — nezaloží druhú.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session

from backend.db.models.poradca import AUTHOR_PORADCA, DONE, PoradcaConversation, PoradcaMessage
from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.services import change_request

BLOCK_INSTRUCTION = "pokyn-pre-agenta"
BLOCK_NEW_VERSION = "poziadavka-na-novu-verziu"

#: Najkratšia požiadavka, z ktorej má zmysel zakladať verziu.
_MIN_REQUEST_CHARS = 10


class HandoffRefused(ValueError):
    """Z tejto správy sa verzia založiť nedá — veta pre človeka."""


@dataclass(frozen=True)
class NewVersion:
    version_id: UUID
    version_number: str
    project_slug: str
    created: bool


def extract_block(text: str, name: str) -> Optional[str]:
    """Obsah prvého bloku ``<name>…</name>`` bez okrajových medzier; ``None``, keď blok nie je alebo je prázdny."""
    match = re.search(rf"<{name}>\s*(.*?)\s*</{name}>", text or "", re.S)
    if not match:
        return None
    body = match.group(1).strip()
    return body or None


def _title(summary: str) -> str:
    first = summary.strip().splitlines()[0].strip(" #*-")
    return first[:200] or summary[:200]


def new_version_from_message(db: Session, message: PoradcaMessage, *, user_id: UUID) -> NewVersion:
    """Založí koncept verzie z požiadavky v odpovedi Poradcu (alebo vráti už založenú).

    Raises:
        HandoffRefused: správa nie je hotová odpoveď Poradcu alebo nemá blok požiadavky.
    """
    if message.author != AUTHOR_PORADCA or message.status != DONE:
        raise HandoffRefused("Novú verziu sa dá založiť len z hotovej odpovede Poradcu.")
    summary = extract_block(message.content, BLOCK_NEW_VERSION)
    if summary is None or len(summary) < _MIN_REQUEST_CHARS:
        raise HandoffRefused("V tejto odpovedi nie je požiadavka na novú verziu.")
    conversation = db.get(PoradcaConversation, message.conversation_id)
    project = db.get(Project, conversation.project_id) if conversation else None
    if project is None:
        raise HandoffRefused("Projekt rozhovoru už neexistuje.")
    if message.captured_version_id is not None:
        existing = db.get(Version, message.captured_version_id)
        if existing is not None:
            return NewVersion(existing.id, existing.version_number, project.slug, created=False)
    _item, version = change_request.mint_from_request(
        db, project_id=project.id, summary=summary, title=_title(summary), user_id=user_id
    )
    message.captured_version_id = version.id
    db.flush()
    return NewVersion(version.id, version.version_number, project.slug, created=True)
