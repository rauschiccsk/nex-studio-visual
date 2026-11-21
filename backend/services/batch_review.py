"""The fast path with Poradca (DEV-483) — every card of a consultation goes to Poradca at once, the Manažér approves a
summary.

Director 21.11.2026: „Chcel by som však vytvoriť nejakú rýchlejšiu cestu, kde Staviteľ zadal by tie otázky (všetky)
Poradcovi, Následne mi kompaktne a prehľadne ukazál by otázku a návrh staviteľa a taktiež návrh Poradcu. … zo 100
otázok je len maximálne 2-3 keď nesúhlasím s poradcom.“ Switchable per account — the learners keep the one-by-one way.

When the account that leads the project (``users.batch_review``) has it on and a build stops on new cards, the
cockpit asks Poradca about all of them in one question (the phase's review conversation, DEV-138). Poradca writes one
``<posudok karta="N">`` block per card: its choice, one sentence why, and what the Staviteľ should work in beside the
choice (``pokyn``). The summary shows it beside the Staviteľ's recommendation, and one button approves Poradca's
proposal on every card through the ordinary ``decide`` action (``via=batch_review``, checked here again): his choice,
and his instruction as the card's note. A card the Manažér does not agree with he consults with Poradca and decides on
its card. When Poradca does not get the cards or does not answer, the cards simply wait one by one — the build never
waits for the fast path.

DEV-486 — Director 21.11.2026: „Rýchla cesta má byť všetky otázky overené poradcom. … Ja to prečítam a ak všetko mi
vyhovuje s jednám tlačítkom schválim všetky otázky. Chcem však mať možnosť, že v prípade môjho nesúhlasu podrobne
rozobrať a prekonzuľtoať danú otázku. … pri overení Poradca pri cca 80% otázok mal aj nejaké dodatočné informácie pre
Staviteľa, tu som to nevidel.“ Where the two disagree, Poradca's proposal is approved — he is the one who verified it.
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
#: A line that starts a field of the block; the lines after it, up to the next field, carry on its value. „rozhoduje“
#: came in answers before DEV-486 — read so that it never runs into the field above it, and ignored.
_FIELD = re.compile(r"^\s*(voľba|volba|prečo|preco|pokyn(?:\s+pre\s+staviteľa)?|rozhoduje)\s*:\s*(.*)$", re.I)
#: What Poradca writes where the Staviteľ needs no instruction.
_NO_INSTRUCTION = {"", "-", "—", "–", "nič", "žiadny", "netreba", "pokyn netreba"}
#: How the Manažér's own decision starts on a card (``pipeline_status.OWN_DECISION_LABEL``).
OWN_DECISION = "Vlastné rozhodnutie"

# The states of a row of the summary.
AGREE = "agree"  # Poradca chose what the Staviteľ recommends
DISAGREE = "disagree"  # Poradca chose another option, or an own decision — his proposal is what is approved
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
NOT_PROPOSED = (
    "Karta {number}: v súhrne sa schvaľuje len návrh Poradcu tak, ako ho súhrn ukazuje — obnov stránku a schváľ znova, "
    "alebo kartu prekonzultuj."
)
NOT_ON_CARDS = "Stavba nestojí na kartách rozhodnutí — nie je čo posúdiť."
NOT_WANTED = "Konto, ktoré vedie projekt, nemá rýchlu cestu s Poradcom zapnutú."
STILL_ASSESSING = "Poradca karty práve posudzuje — súhrn sa ukáže, keď dopíše."


class Refused(Exception):
    """Why the cards cannot go to Poradca again now — one of the sentences above, for the Manažér (DEV-486). Its own
    kind, not a ``ValueError``: the route shows only these, never an error from deeper in Poradca's machinery."""


#: The request under the cards, in the Director's words of what he wants from Poradca.
QUESTION_HEAD = (
    "Posúď prosím naraz všetky karty rozhodnutí tejto konzultácie ({count}) — každú tak dôkladne, ako keby ťa Manažér "
    "požiadal overiť len ju. Manažér si pri každej karte prečíta návrh Staviteľa a tvoj návrh a jedným tlačidlom "
    "schváli tvoje návrhy: Staviteľ dostane pri každej karte tvoju voľbu a tvoj pokyn. Kartu, s ktorou nesúhlasí, s "
    "tebou prekonzultuje.\n\n"
    "Ku každej karte napíš práve jeden blok v tomto tvare (bez ďalšieho textu vnútri):\n\n"
    '<posudok karta="N">\n'
    "voľba: presný text jednej z možností karty — alebo „Vlastné rozhodnutie: …“, keď nevyhovuje žiadna\n"
    "prečo: jedna veta\n"
    "pokyn: čo má Staviteľ pri tejto voľbe ešte zapracovať — len to, čo plánu v karte chýba (riziko, nepokrytý "
    "prípad, rozpor so Špecifikáciou), krátko a s dôvodom; „—“, keď nič nechýba\n"
    "</posudok>\n\n"
    "Pred posudkom si over, čo treba (Špecifikácia, kód, dokumenty) — ako pri jednej karte. Karty:"
)


@dataclass(frozen=True)
class Assessment:
    """Poradca's word on one card."""

    choice: str
    why: str
    #: What the Staviteľ should work in beside the choice — ``None`` when nothing.
    instruction: Optional[str] = None


@dataclass
class Row:
    key: str
    number: int
    question: str
    builder_label: Optional[str]
    builder_option_id: Optional[str]
    poradca_label: Optional[str] = None
    poradca_why: Optional[str] = None
    poradca_instruction: Optional[str] = None
    state: str = WAITING
    #: What „Schváliť všetko“ sends for the card — Poradca's proposal: one of the card's options …
    option_id: Optional[str] = None
    #: … or, when he proposes what the card does not offer, his own decision word for word.
    free_text: Optional[str] = None
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


def _fields(body: str) -> dict[str, str]:
    """The fields of one block by name (``volba``, ``preco``, ``pokyn``) — a value may go on over several lines."""
    found: dict[str, list[str]] = {}
    current: Optional[str] = None
    for line in body.splitlines():
        started = _FIELD.match(line)
        if started:
            current = started.group(1).casefold().replace("ľ", "l").replace("č", "c").split()[0]
            found.setdefault(current, []).append(started.group(2))
        elif current is not None:
            found[current].append(line)
    return {name: "\n".join(lines).strip() for name, lines in found.items()}


def parse(content: str) -> dict[int, Assessment]:
    """The ``<posudok>`` blocks of an answer, by card number; a block without a choice is no assessment."""
    found: dict[int, Assessment] = {}
    for number, body in _POSUDOK.findall(content or ""):
        fields = _fields(body)
        choice = fields.get("volba", "").strip().strip('„“"')
        if not choice:
            continue
        instruction = fields.get("pokyn", "").strip()
        found.setdefault(
            int(number),
            Assessment(
                choice=choice,
                why=fields.get("preco", ""),
                instruction=None if instruction.casefold().strip(" .") in _NO_INSTRUCTION else instruction,
            ),
        )
    return found


def _own_decision(choice: str) -> str:
    """Poradca's own decision as the card takes it — without the „Vlastné rozhodnutie:“ in front."""
    text = choice.strip()
    if text.casefold().startswith(OWN_DECISION.casefold()):
        text = text[len(OWN_DECISION) :].lstrip(" :—–-")
    return text.strip()


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
            row.poradca_instruction = assessment.instruction
        if answer is not None and not answer.get("held"):
            row.state, row.decided_label = DECIDED, str(answer.get("label") or answer.get("free_text") or "")
        elif status != DONE:
            row.state = WAITING if status == RUNNING else MISSING
        elif assessment is None:
            row.state = MISSING
        else:
            option = _option_for(decision, assessment.choice)
            if option is not None:
                row.option_id = option.get("id")
                agrees = recommended is not None and option.get("id") == recommended.get("id")
                row.state = AGREE if agrees else DISAGREE
            else:
                row.free_text, row.state = _own_decision(assessment.choice), DISAGREE
        rows.append(row)
    counts = {state: sum(1 for r in rows if r.state == state) for state in (AGREE, DISAGREE, MISSING, DECIDED)}
    return {
        "status": status,
        "failure": failure,
        "conversation_id": record.conversation_id,
        "counts": counts,
        "rows": [row.__dict__ for row in rows],
        # DEV-487: the Auditor's points the Staviteľ works in without a card — the Manažér sees them in the summary.
        "applied": [str(a) for a in consultation.get("applied") or []],
    }


def check_proposal(
    db: Session,
    version_id: uuid.UUID,
    key: str,
    option_id: Optional[str],
    free_text: Optional[str],
    note: Optional[str],
) -> dict[str, Any]:
    """``decide`` with ``via=batch_review`` approves only Poradca's proposal as the summary shows it — his option or
    his own decision, with his instruction as the note. Returns the card's row of the summary.

    Raises:
        ValueError: the card has no proposal of Poradca, or the request is not it (the sentence for the Manažér).
    """
    found = summary(db, version_id)
    row = next((r for r in (found or {}).get("rows", []) if r["key"] == key), None)
    proposal = (row["option_id"], row["free_text"], row["poradca_instruction"]) if row else None
    if row is None or row["state"] not in (AGREE, DISAGREE) or proposal != (option_id, free_text, note):
        raise ValueError(NOT_PROPOSED.format(number=row["number"] if row else "?"))
    return row


def approved_in_summary(row: dict[str, Any], approver: str) -> str:
    """What the history adds to the decision approved in the summary — whose proposal it was and who approved it."""
    builder = (
        "Staviteľ navrhol to isté"
        if row["state"] == AGREE
        else f"Staviteľ navrhol „{row['builder_label']}“"
        if row.get("builder_label")
        else "Staviteľ nenavrhol nič"
    )
    return f" — schválené v súhrne: návrh Poradcu, {builder} (schválil {approver})"


def reviewed(db: Session, version_id: uuid.UUID, seq: int) -> bool:
    """Were the cards of the consultation at ``seq`` handed to Poradca at once — does the Manažér decide them in the
    summary, any of them in any order?"""
    return _review_of(db, version_id, seq) is not None


def again(db: Session, version_id: uuid.UUID) -> Optional[BatchReview]:
    """Hand the open cards to Poradca at once again — after a failure, or when the summary came before the instruction
    for the Staviteľ (DEV-486). Must run inside the event loop, like :func:`start_if_wanted`.

    Raises:
        Refused: nothing to assess, or Poradca is assessing them right now (the sentence for the Manažér).
    """
    from backend.db.models.poradca import RUNNING as MESSAGE_RUNNING
    from backend.db.models.poradca import PoradcaMessage
    from backend.services import orchestrator

    state = db.execute(select(PipelineState).where(PipelineState.version_id == version_id)).scalar_one_or_none()
    found = orchestrator._latest_consultation(db, version_id)
    if state is None or not (state.status == "blocked" and state.block_reason == "decision_needed") or found is None:
        raise Refused(NOT_ON_CARDS)
    if not wanted(db, version_id):
        raise Refused(NOT_WANTED)
    record = _review_of(db, version_id, found[1])
    if record is not None:
        message = db.get(PoradcaMessage, record.answer_message_id) if record.answer_message_id else None
        if message is not None and message.status == MESSAGE_RUNNING:
            raise Refused(STILL_ASSESSING)
        db.delete(record)
        db.flush()
    return start_if_wanted(db, version_id)
