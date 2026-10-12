"""DEV-83 — the choice Poradca tells the manager to make is marked in its answer and checked against the card.

12.10.2026 the Director: in a long answer the option to pick got lost — he wants it green. Poradca puts it between
``<volba>`` tags (charter, part 4); the screen shows each one as a badge, and a card choice is green only when the
card really offers that option word for word.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from backend.api.routes.poradca import _message_read
from backend.db.models.pipeline import PipelineMessage
from backend.db.models.poradca import PoradcaConversation, PoradcaMessage
from backend.services.poradca import choices
from tests.test_poradca_tools_db import _consultation_world, world  # noqa: F401 — the fixture is used by name

CHARTER = Path(__file__).resolve().parents[1] / "templates" / "poradca-charter.md"

ANSWER = (
    "Agent má v karte presný plán.\n\n"
    'Na karte 2 vyber <volba karta="2">Zhasnúť hneď, keď sa doručovanie obnoví</volba>.\n'
    'Na karte 1 by som zvolil <volba karta="1">posielať po kúskoch</volba>.\n'
    'Karta 7 <volba karta="7">Nechať 24 hodín</volba>.\n'
    "Agentovi odpovedz <volba>Áno, súhlasím</volba>."
)


def test_the_marked_choices_are_read_in_the_order_of_the_answer():
    assert choices.marked(ANSWER) == [
        (2, "Zhasnúť hneď, keď sa doručovanie obnoví"),
        (1, "posielať po kúskoch"),
        (7, "Nechať 24 hodín"),
        (None, "Áno, súhlasím"),
    ]
    assert choices.marked('<volba karta="2">\n  Nechať   24\nhodín </volba>') == [(2, "Nechať 24 hodín")]
    assert choices.marked("Bez značky „Nechať 24 hodín“.") == []


def test_a_card_choice_is_on_the_card_only_word_for_word(world):  # noqa: F811
    _consultation_world(world, decided=True)

    found = choices.choices(world["db"], ANSWER, world["version"].id)

    assert [(c.card, c.status) for c in found] == [
        (2, choices.ON_CARD),
        (1, choices.NOT_ON_CARD),  # a paraphrase — the card says „Posielať po menších potvrdených kúskoch“
        (7, choices.NOT_ON_CARD),  # there is no card 7
        (None, choices.ANSWER),
    ]
    assert found[0].question == "Kedy zhasne upozornenie na Prehľade po výpadku?"
    assert found[2].question is None


def test_without_cards_a_card_choice_is_not_on_any_card(world):  # noqa: F811
    found = choices.choices(world["db"], '<volba karta="1">Opraviť všetky naraz</volba>', world["version"].id)

    assert [(c.card, c.status) for c in found] == [(1, choices.NOT_ON_CARD)]


def test_the_own_answer_is_on_the_card_only_where_the_card_allows_it(world):  # noqa: F811
    db, version = world["db"], world["version"]
    _consultation_world(world, decided=False)
    db.add(
        PipelineMessage(
            version_id=version.id,
            stage="navrh",
            author="ai_agent",
            recipient="manazer",
            kind="consultation",
            content="Ďalšie kolo.",
            payload={
                "consultation": {
                    "id": "navrh-3",
                    "round": 3,
                    "decisions": [
                        {"key": "a", "question": "Q1", "options": [{"id": "x", "label": "X"}], "allow_free_text": True},
                        {"key": "b", "question": "Q2", "options": [{"id": "y", "label": "Y"}]},
                    ],
                }
            },
        )
    )
    db.flush()

    found = choices.choices(db, '<volba karta="1">Iná odpoveď</volba> <volba karta="2">Iná odpoveď</volba>', version.id)

    assert [(c.card, c.status) for c in found] == [(1, choices.ON_CARD), (2, choices.NOT_ON_CARD)]


def test_the_screen_gets_the_choices_of_a_finished_answer_only(world):  # noqa: F811
    db, project, version = world["db"], world["project"], world["version"]
    _consultation_world(world, decided=True)
    conversation = PoradcaConversation(
        project_id=project.id, version_id=version.id, author_id=project.created_by, title="t",
        claude_session_id=uuid.uuid4(),
    )  # fmt: skip
    db.add(conversation)
    db.flush()
    done = PoradcaMessage(
        conversation_id=conversation.id, author="poradca", content=ANSWER, status="done", version_id=version.id
    )
    running = PoradcaMessage(
        conversation_id=conversation.id, author="poradca", content=ANSWER, status="running", version_id=version.id
    )
    db.add_all([done, running])
    db.flush()

    shown = _message_read(db, done).choices

    assert [(c.card, c.label, c.status) for c in shown] == [
        (2, "Zhasnúť hneď, keď sa doručovanie obnoví", "on_card"),
        (1, "posielať po kúskoch", "not_on_card"),
        (7, "Nechať 24 hodín", "not_on_card"),
        (None, "Áno, súhlasím", "answer"),
    ]
    assert _message_read(db, running).choices == []


def test_the_charter_tells_poradca_to_mark_the_choice():
    text = CHARTER.read_text(encoding="utf-8")

    assert '<volba karta="3">Zhasnúť hneď, keď sa doručovanie obnoví</volba>' in text
    assert "<volba>Áno, súhlasím</volba>" in text
    # what the charter shows is what the cockpit reads
    assert choices.marked(text) == [
        (3, "Zhasnúť hneď, keď sa doručovanie obnoví"),
        (2, "Iná odpoveď"),
        (None, "Áno, súhlasím"),
    ]
