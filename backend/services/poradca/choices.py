"""The choice Poradca tells the manager to make, marked in its answer and checked against the card (DEV-83).

12.10.2026 the Director, as the option to pick got lost in a long answer:
„chcem aby voľba, ktorú treba vybrať bolo vypísané zelenou farbou“
The charter (part 4) has Poradca put the choice between tags::

    <volba karta="3">the option's label, word for word</volba>    — an option of Decision Card 3
    <volba>the suggested answer</volba>                         — an answer to the agent's question

The screen shows each one as a badge and sums them up above the answer. A card choice is green only when the card
really offers that option word for word — the manager looks for that exact text on the card, so a paraphrase or a
card that changed meanwhile is shown as such instead of sending him to look for something that is not there.

The cards are read through the engine's own functions, the same ones Poradca's tool and the cards are built from.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session

TAG = "volba"
#: ``<volba karta="N">text</volba>`` or ``<volba>text</volba>``; the text may not span another choice.
CHOICE_RE = re.compile(r'<volba(?:\s+karta="(\d{1,3})")?\s*>(.*?)</volba>', re.S)

#: The card offers exactly this option.
ON_CARD = "on_card"
#: A card is named, but it does not offer this option (or there is no such card any more).
NOT_ON_CARD = "not_on_card"
#: No card — the answer Poradca suggests for the agent's question.
ANSWER = "answer"

#: How the card names its own free-text answer; Poradca recommends it by this name (charter, part 4).
OTHER_ANSWER = "Iná odpoveď"


@dataclass(frozen=True)
class Choice:
    card: Optional[int]
    label: str
    status: str
    #: The card's question, when the card exists.
    question: Optional[str] = None


def _plain(text: str) -> str:
    return " ".join(text.split())


def marked(content: str) -> list[tuple[Optional[int], str]]:
    """Every choice marked in ``content`` — (card number or ``None``, its text) — in the order of the answer."""
    out = []
    for m in CHOICE_RE.finditer(content or ""):
        label = _plain(m.group(2))
        if label:
            out.append((int(m.group(1)) if m.group(1) else None, label))
    return out


def _card_decisions(db: Session, version_id: Optional[UUID]) -> list[dict]:
    if version_id is None:
        return []
    from backend.services.orchestrator import _latest_consultation

    latest = _latest_consultation(db, version_id)
    return (latest[0].get("decisions") or []) if latest else []


def choices(db: Session, content: str, version_id: Optional[UUID]) -> list[Choice]:
    """The choices of one answer, each checked against the latest Decision Cards of ``version_id``."""
    found = marked(content)
    if not any(card is not None for card, _ in found):
        return [Choice(card=None, label=label, status=ANSWER) for _, label in found]
    decisions = _card_decisions(db, version_id)
    out = []
    for card, label in found:
        if card is None:
            out.append(Choice(card=None, label=label, status=ANSWER))
            continue
        decision = decisions[card - 1] if 1 <= card <= len(decisions) else None
        if decision is None:
            out.append(Choice(card=card, label=label, status=NOT_ON_CARD))
            continue
        offered = {_plain(str(o.get("label") or "")) for o in decision.get("options") or []}
        if decision.get("allow_free_text"):
            offered.add(OTHER_ANSWER)
        out.append(
            Choice(
                card=card,
                label=label,
                status=ON_CARD if label in offered else NOT_ON_CARD,
                question=decision.get("question"),
            )
        )
    return out
