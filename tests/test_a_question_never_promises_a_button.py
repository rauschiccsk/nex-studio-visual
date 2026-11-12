"""DEV-365 — an answer that asks the person never promises a button: the charters do not teach it.

Director 12.11.2026: „V kokpite pod Nazar Poradca ponúka stlačiť tlačidlo "Vližiť do riaduaceho centra" avšak na
obrazovke nemám toto tlačidlo.“ The Poradca wrote the instruction, „Pokyn vložíš tlačidlom „Vložiť do Riadiaceho
centra““ and ended with „Súhlasíš s týmto pokynom?“ — and the cockpit, as the Director decided in DEV-264 („V prípade
otázok, najprv treba počkať na moju odpoveď až potom pokračovať.“), offers nothing under an answer that asks. The
charter taught exactly that answer: its model question was „Súhlasíš s týmto pokynom?“, one line above the rule that an
answer which asks carries no block. Now the model question asks about a decision, not about a block, and both charters
say a button is promised only by an answer that brings it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from backend.services.poradca import handoff

TEMPLATES = Path(__file__).resolve().parents[1] / "templates"
CHARTERS = ("poradca-charter.md", "poradca-ideas-charter.md")

#: Words that name what an answer passes on — a model question about one of them asks about a block.
BLOCK_WORDS = re.compile(r"pokyn|požiadav|tiket|zadan|doplnen|voľb|volb", re.IGNORECASE)

#: Nazar's answer of 12.11.2026, shortened: a block, the promise of the button, a question at the end.
NAZAR = (
    'Na karte 1 vyber <volba karta="1">Usmerniť opravu (napíš vlastný pokyn)</volba>.\n\n'
    "<pokyn-pre-agenta>\nOprav podľa nálezov Audítora, s týmito spresneniami: `GET /calendar/{civil}` nemaž.\n"
    "</pokyn-pre-agenta>\n\n"
    "Pokyn vložíš tlačidlom „Vložiť do Riadiaceho centra“ do poľa pod kartou. Súhlasíš s týmto pokynom?"
)


def _text(name: str) -> str:
    return " ".join((TEMPLATES / name).read_text(encoding="utf-8").split())


def _model_questions(text: str) -> list[str]:
    """The model questions of the rule on how to ask („aby „Súhlasím“ alebo „Áno“ bolo jednoznačné“)."""
    start = text.index("aby „Súhlasím“ alebo „Áno“ bolo jednoznačné")
    rule = text[start : text.index("Kto chce niečo iné", start)]
    return re.findall(r"„([^“]*\?)“", rule)


def test_the_cockpit_holds_what_an_asking_answer_prepared():
    """Why the charters must not teach it: the answer of 12.11. offers no button — DEV-264."""
    assert handoff.held_for_answer(NAZAR)
    assert handoff.withheld(NAZAR) == handoff.HELD_FOR_ANSWER


@pytest.mark.parametrize("charter", CHARTERS)
def test_no_model_question_asks_about_a_block(charter):
    questions = _model_questions(_text(charter))

    assert questions, "the rule on how to ask has its model questions"
    assert [q for q in questions if BLOCK_WORDS.search(q)] == []


@pytest.mark.parametrize("charter", CHARTERS)
def test_a_button_is_promised_only_by_an_answer_that_brings_it(charter):
    text = _text(charter)

    assert "**Tlačidlo sľúb len v odpovedi, ktorá ho prinesie** — teda s blokom a bez otázky na konci." in text
    assert "Odpoveď s otázkou nehovorí, ktorým tlačidlom čo vložíš; povie, čo pripravíš po odpovedi." in text
