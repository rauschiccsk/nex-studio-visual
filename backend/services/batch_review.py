"""The fast path with Poradca (DEV-483) — every card of a consultation goes to Poradca at once, the Manažér approves a
summary.

Director 21.11.2026: „Chcel by som však vytvoriť nejakú rýchlejšiu cestu, kde Staviteľ zadal by tie otázky (všetky)
Poradcovi, Následne mi kompaktne a prehľadne ukazál by otázku a návrh staviteľa a taktiež návrh Poradcu. … zo 100
otázok je len maximálne 2-3 keď nesúhlasím s poradcom.“ Switchable per account — the learners keep the one-by-one way.

When the account that leads the project (``users.batch_review``) has it on and a build stops on new cards, the
cockpit asks Poradca about all of them in one question (the phase's review conversation, DEV-138). Poradca writes one
``<posudok karta="N">`` block per card: its choice, one sentence why, and whether the decision belongs to the Manažér
alone (customer, money, what is offered). The summary compares it with the Staviteľ's recommendation: agreement, a
disagreement, or the Manažér's own decision. Agreements are approved in one go through the ordinary ``decide`` action
(``via=batch_review``, checked here again); everything else stays a card, decided one by one as before. When Poradca
does not get the cards or does not answer, the cards simply wait one by one — the build never waits for the fast path.
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db.models.batch_review import BatchReview
from backend.db.models.foundation import User
from backend.db.models.pipeline import PipelineState
from backend.db.models.projects import Project
from backend.db.models.versions import Version

logger = logging.getLogger(__name__)

#: The block Poradca writes per card.
BLOCK = "posudok"
#: What ``decide`` carries when the Manažér approves agreements in the summary.
VIA = "batch_review"
_POSUDOK = re.compile(r'<posudok\s+karta="(\d{1,3})"\s*>(.*?)</posudok>', re.S)
_FIELD = re.compile(r"^\s*(voľba|volba|prečo|preco|rozhoduje)\s*:\s*(.*)$", re.I | re.M)
#: How the Manažér's own decision starts on a card (``pipeline_status.OWN_DECISION_LABEL``).
OWN_DECISION = "Vlastné rozhodnutie"

# The states of a row of the summary.
AGREE = "agree"  # Poradca chose what the Staviteľ recommends — pre-approved
DISAGREE = "disagree"  # another option, or an own decision
MANAGER = "manager"  # a decision that belongs to the Manažér alone, whatever both proposed
MISSING = "missing"  # Poradca did not assess this card
WAITING = "waiting"  # Poradca is still answering
DECIDED = "decided"  # the card is decided already

# The states of the whole review.
RUNNING = "running"
DONE = "done"
FAILED = "failed"

NOT_READY = "Poradca teraz nevie bežať ({why}) — karty rozhodni po jednej ako doteraz."
BUSY = "Poradca práve odpovedá v rozhovore kariet tejto fázy — karty rozhodni po jednej, alebo počkaj."
NO_ANSWER = "Poradca súhrn nedokončil — karty rozhodni po jednej ako doteraz, s tlačidlom „Overiť s Poradcom“."
NOT_AGREED = "Karta {number} nie je zhoda Staviteľa a Poradca — schváliť ju v súhrne nemožno, rozhodni ju na karte."

#: The request under the cards, in the Director's words of what he wants from Poradca.
QUESTION_HEAD = (
    "Posúď prosím naraz všetky karty rozhodnutí tejto konzultácie ({count}). Manažér chce súhrn: pri každej karte "
    "návrh Staviteľa a tvoj návrh — schváli zhody a rozhodne len tam, kde sa nezhodnete alebo kde rozhoduje sám.\n\n"
    "Ku každej karte napíš práve jeden blok v tomto tvare (bez ďalšieho textu vnútri):\n\n"
    '<posudok karta="N">\n'
    "voľba: presný text jednej z možností karty — alebo „Vlastné rozhodnutie: …“, keď nevyhovuje žiadna\n"
    "prečo: jedna veta\n"
    "rozhoduje: poradca — alebo „manažér“, keď je to rozhodnutie, ktoré patrí len Manažérovi (zákazník, peniaze, "
    "obchod, čo aplikácia ponúka); takú kartu Manažér nikdy neschvaľuje naslepo\n"
    "</posudok>\n\n"
    "Pred posudkom si over, čo treba (Špecifikácia, kód, dokumenty) — ako pri jednej karte. Karty:"
)


@dataclass(frozen=True)
class Assessment:
    """Poradca's word on one card."""

    choice: str
    why: str
    manager: bool


@dataclass
class Row:
    key: str
    number: int
    question: str
    builder_label: Optional[str]
    builder_option_id: Optional[str]
    poradca_label: Optional[str] = None
    poradca_why: Optional[str] = None
    state: str = WAITING
    #: The option both chose — what „Schváliť označené“ sends; only on an agreement.
    option_id: Optional[str] = None
    decided_label: Optional[str] = None
    related: list[int] = field(default_factory=list)


def owner_of(db: Session, version_id: uuid.UUID) -> Optional[User]:
    """The account that leads the version's project — its switch decides (as its agent settings do)."""
    return db.execute(
        select(User)
        .join(Project, Project.created_by == User.id)
        .join(Version, Version.project_id == Project.id)
        .where(Version.id == version_id)
    ).scalar_one_or_none()


def wanted(db: Session, version_id: uuid.UUID) -> bool:
    owner = owner_of(db, version_id)
    return bool(owner is not None and owner.batch_review)


def parse(content: str) -> dict[int, Assessment]:
    """The ``<posudok>`` blocks of an answer, by card number; a block without a choice is no assessment."""
    found: dict[int, Assessment] = {}
    for number, body in _POSUDOK.findall(content or ""):
        fields = {
            name.casefold().replace("ľ", "l").replace("č", "c"): value.strip() for name, value in _FIELD.findall(body)
        }
        choice = fields.get("volba", "").strip().strip('„“"')
        if not choice:
            continue
        found.setdefault(
            int(number),
            Assessment(
                choice=choice,
                why=fields.get("preco", ""),
                manager=fields.get("rozhoduje", "").casefold().startswith("mana"),
            ),
        )
    return found


def _norm(text: str) -> str:
    return " ".join((text or "").split()).casefold().strip(" .")


def _option_for(decision: dict[str, Any], choice: str) -> Optional[dict[str, Any]]:
    wanted_label = _norm(choice)
    return next((o for o in decision.get("options", []) if _norm(o.get("label", "")) == wanted_label), None)


def question(cards: list[dict[str, Any]]) -> str:
    """The one question with every card — question, options (the Staviteľ's recommendation marked), explanation."""
    lines = [QUESTION_HEAD.format(count=len(cards))]
    for number, card in enumerate(cards, start=1):
        lines.append(f"\nKarta {number}: {card.get('question', '')}")
        if card.get("explanation"):
            lines.append(f"Vysvetlenie: {card['explanation']}")
        for option in card.get("options", []):
            mark = " (odporúča Staviteľ)" if option.get("recommended") else ""
            detail = f" — {option['detail']}" if option.get("detail") else ""
            lines.append(f"- {option.get('label', '')}{mark}{detail}")
    return "\n".join(lines)


def _review_of(db: Session, version_id: uuid.UUID, seq: int) -> Optional[BatchReview]:
    return db.execute(
        select(BatchReview).where(BatchReview.version_id == version_id, BatchReview.consultation_seq == seq)
    ).scalar_one_or_none()


def start_if_wanted(db: Session, version_id: uuid.UUID) -> Optional[BatchReview]:
    """Hand the open consultation's cards to Poradca at once — when the build stands on cards, the project's account
    wants the fast path and they were not handed yet. Must run inside the event loop (Poradca answers in the
    background). Never raises for Poradca's own trouble: the reason is recorded and the cards wait one by one."""
    from backend.services import orchestrator
    from backend.services.poradca import readiness, review, runner

    state = db.execute(select(PipelineState).where(PipelineState.version_id == version_id)).scalar_one_or_none()
    if state is None or not (state.status == "blocked" and state.block_reason == "decision_needed"):
        return None
    owner = owner_of(db, version_id)
    if owner is None or not owner.batch_review:
        return None
    found = orchestrator._latest_consultation(db, version_id)
    if found is None:
        return None
    consultation, seq = found
    existing = _review_of(db, version_id, seq)
    if existing is not None:
        return existing
    record = BatchReview(version_id=version_id, consultation_seq=seq)
    db.add(record)
    ready = readiness.status(db)
    if not ready.ready:
        record.failure = NOT_READY.format(why="; ".join(ready.problems))
        db.commit()
        return record
    version = db.get(Version, version_id)
    project = db.get(Project, version.project_id)
    conversation_id = review.find(db, project_id=project.id, author_id=owner.id, version_id=version_id)
    if conversation_id is None:
        stage = review.stage_of(db, version_id)
        conversation = runner.new_conversation(
            db, project=project, author=owner, version_id=version_id, title=review.title(version.version_number, stage)
        )
        conversation.review_stage = stage
        db.flush()
    else:
        from backend.db.models.poradca import PoradcaConversation

        conversation = db.get(PoradcaConversation, conversation_id)
    record.conversation_id = conversation.id
    db.commit()
    try:
        _human, answer = runner.ask(db, conversation, question(consultation.get("decisions", [])), owner)
    except runner.PoradcaBusy:
        record.failure = BUSY
        db.commit()
        return record
    record.answer_message_id = answer.id
    db.commit()
    logger.info("version %s: %d cards handed to Poradca at once", version_id, len(consultation.get("decisions", [])))
    return record


def summary(db: Session, version_id: uuid.UUID) -> Optional[dict[str, Any]]:
    """The summary of the open consultation's cards — ``None`` when Poradca was not asked about them."""
    from backend.db.models.poradca import DONE as MESSAGE_DONE
    from backend.db.models.poradca import RUNNING as MESSAGE_RUNNING
    from backend.db.models.poradca import PoradcaMessage
    from backend.services import orchestrator

    found = orchestrator._latest_consultation(db, version_id)
    if found is None:
        return None
    consultation, seq = found
    record = _review_of(db, version_id, seq)
    if record is None:
        return None
    message = db.get(PoradcaMessage, record.answer_message_id) if record.answer_message_id else None
    if record.failure:
        status, failure = FAILED, record.failure
    elif message is None or message.status not in (MESSAGE_RUNNING, MESSAGE_DONE):
        status, failure = FAILED, NO_ANSWER
    elif message.status == MESSAGE_RUNNING:
        status, failure = RUNNING, None
    else:
        status, failure = DONE, None
    assessed = parse(message.content) if status == DONE else {}
    answers = orchestrator._consultation_answers(db, version_id, seq)
    decisions = consultation.get("decisions", [])
    numbers = {d.get("key"): n for n, d in enumerate(decisions, start=1)}
    rows: list[Row] = []
    for number, decision in enumerate(decisions, start=1):
        recommended = next((o for o in decision.get("options", []) if o.get("recommended")), None)
        row = Row(
            key=str(decision.get("key")),
            number=number,
            question=str(decision.get("question", "")),
            builder_label=recommended.get("label") if recommended else None,
            builder_option_id=recommended.get("id") if recommended else None,
            related=[numbers[r["key"]] for r in decision.get("related") or [] if r.get("key") in numbers],
        )
        answer = answers.get(decision.get("key"))
        assessment = assessed.get(number)
        if assessment is not None:
            row.poradca_label, row.poradca_why = assessment.choice, assessment.why
        if answer is not None and not answer.get("held"):
            row.state, row.decided_label = DECIDED, str(answer.get("label") or answer.get("free_text") or "")
        elif status != DONE:
            row.state = WAITING if status == RUNNING else MISSING
        elif assessment is None:
            row.state = MISSING
        elif assessment.manager:
            row.state = MANAGER
        else:
            option = _option_for(decision, assessment.choice)
            if option is not None and recommended is not None and option.get("id") == recommended.get("id"):
                row.state, row.option_id = AGREE, option.get("id")
            else:
                row.state = DISAGREE
        rows.append(row)
    counts = {state: sum(1 for r in rows if r.state == state) for state in (AGREE, DISAGREE, MANAGER, MISSING, DECIDED)}
    return {
        "status": status,
        "failure": failure,
        "conversation_id": record.conversation_id,
        "counts": counts,
        "rows": [row.__dict__ for row in rows],
    }


def check_agreement(db: Session, version_id: uuid.UUID, key: str, option_id: Optional[str]) -> None:
    """``decide`` with ``via=batch_review`` approves only what the summary shows as an agreement — on that option.

    Raises:
        ValueError: the card is no agreement (the sentence for the Manažér).
    """
    found = summary(db, version_id)
    row = next((r for r in (found or {}).get("rows", []) if r["key"] == key), None)
    if row is None or row["state"] != AGREE or row["option_id"] != option_id:
        raise ValueError(NOT_AGREED.format(number=row["number"] if row else "?"))
