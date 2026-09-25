"""Zadanie od Deda pre prácu, ktorá sa ešte nezačala (ICCINT-152).

Dvojička k :mod:`backend.services.dedo_message`, ktorá to isté robí pre BEŽIACU stavbu. Rozdiel je
jediný a je to celý dôvod existencie tohto modulu: tu stavba neexistuje, takže sa návrh pripína na
projekt. Všetko ostatné je zámerne rovnaké — Dedo píše, Manažér rozhoduje, doručenie je jeho klik.

⚠️ **Návrh sám nikdy nič nespustí.** Táto služba text uloží a archivuje; verziu zakladá až obsluha na
strane Manažéra, pod JEHO účtom, cez tú istú cestu, akú by klikol sám.
"""

from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db.models.dedo_proposal import (
    PROJECT_PROPOSAL_ACTIONS,
    PROPOSED,
    REJECTED,
    SENT,
    SUPERSEDED,
    DedoProjectProposal,
)
from backend.db.models.projects import Project

#: Ľudské zdôvodnenie odmietnutia, keď Manažér koná nad návrhom, ktorý už neplatí. Kľúč je stav, do
#: ktorého sa medzičasom dostal — text musí povedať, ČO sa stalo, nie že „to nejde".
GONE_REASON = {
    SENT: "Tento návrh už bol odoslaný — vznikla z neho verzia.",
    REJECTED: "Tento návrh bol medzitým zamietnutý.",
    SUPERSEDED: "Dedo medzitým napísal novší návrh; tento už neplatí.",
}


class ProposalError(ValueError):
    """Návrh sa nedá zapísať alebo sa oň nedá oprieť — 409 na okraji."""


class ProposalNotFound(LookupError):
    """Taký návrh v tomto projekte nie je — 404 na okraji."""


def open_for_project(db: Session, project_id: uuid.UUID) -> Optional[DedoProjectProposal]:
    """Návrh, ktorý čaká na Manažéra; ``None``, keď žiadny.

    Otvorený je najviac jeden a drží to jedinečný index v databáze, nie poradie príkazov tu.
    """
    return db.execute(
        select(DedoProjectProposal).where(
            DedoProjectProposal.project_id == project_id,
            DedoProjectProposal.status == PROPOSED,
        )
    ).scalar_one_or_none()


def for_decision(db: Session, project_id: uuid.UUID, proposal_id: uuid.UUID) -> DedoProjectProposal:
    """Návrh, o ktorom Manažér KLIKOL — hľadá sa podľa identifikátora, nie ako „ten otvorený".

    ⚠️ Rozdiel nie je kozmetický. Keby sa klik vyhodnocoval proti „čo je otvorené teraz", Dedov novší
    návrh podaný medzi zobrazením a kliknutím by sa odoslal namiesto toho, ktorý mal Manažér pred
    očami — a on by sa to nedozvedel.
    """
    row = db.execute(
        select(DedoProjectProposal).where(
            DedoProjectProposal.id == proposal_id,
            DedoProjectProposal.project_id == project_id,
        )
    ).scalar_one_or_none()
    if row is None:
        raise ProposalNotFound(f"Návrh {proposal_id} v tomto projekte nie je")
    if row.status != PROPOSED:
        raise ProposalError(GONE_REASON.get(row.status, "Tento návrh už neplatí."))
    return row


def record(
    db: Session,
    *,
    project_id: uuid.UUID,
    content: str,
    proposed_action: str,
) -> DedoProjectProposal:
    """Zapíš Dedov návrh. Starší otvorený návrh sa archivuje ako ``superseded``.

    Archivuje sa, nie zamieta: Dedo, ktorý znovu premeral a napísal presnejšie zadanie, je bežný
    prípad — a záznam nesmie tvrdiť, že Manažér rozhodol o niečom, čo nikdy nevidel.
    """
    text = (content or "").strip()
    if not text:
        raise ProposalError("Návrh bez textu nie je návrh")
    if proposed_action not in PROJECT_PROPOSAL_ACTIONS:
        raise ProposalError(f"Neznáme sloveso {proposed_action!r}; poznám: {', '.join(PROJECT_PROPOSAL_ACTIONS)}")
    if db.get(Project, project_id) is None:
        raise ProposalNotFound(f"Projekt {project_id} neexistuje")

    predosly = open_for_project(db, project_id)
    if predosly is not None:
        predosly.status = SUPERSEDED
        db.flush()

    row = DedoProjectProposal(
        project_id=project_id,
        content=text,
        proposed_action=proposed_action,
        status=PROPOSED,
    )
    db.add(row)
    db.flush()
    return row


def mark_sent(
    db: Session,
    proposal: DedoProjectProposal,
    *,
    user_id: uuid.UUID,
    version_id: uuid.UUID,
) -> None:
    """Manažér návrh poslal ďalej — a toto je verzia, ktorá z neho vznikla."""
    proposal.status = SENT
    proposal.resolved_by = user_id
    proposal.version_id = version_id
    db.flush()


def mark_rejected(db: Session, proposal: DedoProjectProposal, *, user_id: uuid.UUID) -> None:
    """Manažér návrh zamietol. Nevzniká z neho nič a Dedo sa o tom dozvie z toho, že zmizol."""
    proposal.status = REJECTED
    proposal.resolved_by = user_id
    db.flush()
