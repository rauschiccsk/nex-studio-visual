"""DEV-138 — the cards of one version and phase share ONE Poradca conversation.

Director 16.10.2026, NEX Inbox 1.9.0 waiting in Návrh on 9 decision cards: „ak kliknem v karte agenta na tlačidlo
"Overiť s Poradcom" každý krát otvorí nový rozhovor. Takou rýchlosťou o chvíľku budem mať zahlcený celý zoznam
rozhovorov.“ Until DEV-138 every card handed off by the button (DEV-103, DEV-106) became a conversation of its own,
although Poradca sees every card of the version in any conversation about it (``tools._cards_lines``).

A conversation founded by the button remembers the phase it was founded in (``review_stage``); the next card of the
same version and phase finds it again. Only the person's own, undeleted conversation of that version counts — a
hand-written conversation has no phase and is never taken over.
"""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db.models.pipeline import PipelineState
from backend.db.models.poradca import PoradcaConversation

#: Where in the build the cards were raised, as the conversation's title says it („1.9.0 — rozhodnutia v Návrhu“).
#: Every phase has its place — a guard compares the keys with ``STAGE_VALUES``.
STAGE_PLACES: dict[str, str] = {
    "priprava": "v Príprave",
    "navrh": "v Návrhu",
    "vizual": "vo Vizuáli",
    "programovanie": "v Programovaní",
    "verifikacia": "vo Verifikácii",
    "done": "po dokončení",
}


def stage_of(db: Session, version_id: UUID) -> Optional[str]:
    """The phase the version's build stands in now; ``None`` for a version that was never built."""
    return db.execute(
        select(PipelineState.current_stage).where(PipelineState.version_id == version_id)
    ).scalar_one_or_none()


def title(version_number: str, stage: str) -> str:
    """A phase's conversation holds the decisions of its cards; after the build it holds the questions „Opýtaj sa
    Poradcu“ asks about the finished version (DEV-191) — no decisions, so the title does not promise them."""
    if stage == "done":
        return f"{version_number} — {STAGE_PLACES[stage]}"
    return f"{version_number} — rozhodnutia {STAGE_PLACES[stage]}"


def find(db: Session, *, project_id: UUID, author_id: UUID, version_id: UUID) -> Optional[UUID]:
    """The person's conversation for the cards of this version in its current phase, or ``None`` to found one."""
    stage = stage_of(db, version_id)
    if stage is None:
        return None
    return db.execute(
        select(PoradcaConversation.id)
        .where(
            PoradcaConversation.project_id == project_id,
            PoradcaConversation.author_id == author_id,
            PoradcaConversation.version_id == version_id,
            PoradcaConversation.review_stage == stage,
            PoradcaConversation.deleted_at.is_(None),
        )
        .order_by(PoradcaConversation.updated_at.desc())
        .limit(1)
    ).scalar_one_or_none()
