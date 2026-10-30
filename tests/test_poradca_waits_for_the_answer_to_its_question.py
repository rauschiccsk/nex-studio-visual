"""DEV-264 — when Poradca asks the person something, it waits for the answer before it prepares anything for the agent.

The Director 30.10.2026: „Poradca často súčasne zadáva otázku a zároveň navrhuje a pripraví informáciu pre agenta.
V prípade otázok, najprv treba počkať na moju odpoveď až potom pokračovať.“ The charter told Poradca to put its one
question at the end and to put what it prepares into blocks, but never that the two do not go together — so both came
in one answer, with the buttons under it. Now the charter says so, and the cockpit holds the blocks of an answer that
ends with a question: no button under it, and the click refused on the server too.
"""

from __future__ import annotations

from pathlib import Path

from backend.services.poradca import handoff
from tests.test_poradca_proposes_a_ticket_and_a_click_founds_it import _answer, plane, setup  # noqa: F401

CHARTER = Path(__file__).resolve().parents[1] / "templates" / "poradca-charter.md"

INSTRUCTION = (
    "<pokyn-pre-agenta>\nV backend/app/kit.py nastav on_setting_change podľa §6.4 Špecifikácie.\n</pokyn-pre-agenta>"
)
BACKLOG = "<poziadavka-do-zasobnika>\nExport do Excelu\nFaktúry sa dajú stiahnuť do Excelu.\n</poziadavka-do-zasobnika>"
TICKET = "<tiket-nsv>\nTlačidlo Nasadiť pri UAT nič nerobí\nPo kliknutí sa nič nestane.\n</tiket-nsv>"
AGENT_TICKETS = "<tiket-pre-agenta>DEV-186</tiket-pre-agenta>"
ASKS = "Pozrel som Špecifikáciu §6.4 aj kód šablóny.\n\nChceš, aby sa do denníka zapisovala aj zmena modelu?"


def _messages(client, conversation):
    return client.get(f"/api/v1/poradca/conversations/{conversation.id}").json()["messages"]


def test_an_answer_ends_with_a_question_to_the_person_whatever_trails_it():
    assert handoff.asks_the_person("Súhlasíš?")
    assert handoff.asks_the_person("Chceš to takto?**  \n")
    assert handoff.asks_the_person("Myslíš „Stav bázy“?”)")
    assert handoff.asks_the_person(f"{INSTRUCTION}\n\n{ASKS}")  # the block before the question
    assert handoff.asks_the_person(f"{ASKS}\n\n{INSTRUCTION}")  # the block after it
    assert not handoff.asks_the_person("Prečo agent stojí? Lebo čaká na teba. Klikni „Pokračovať“.")
    assert not handoff.asks_the_person(f"Hotovo.\n\n{INSTRUCTION}")
    assert not handoff.asks_the_person("")


def test_an_answer_that_asks_offers_nothing_under_it_and_says_why(setup, db_session):  # noqa: F811
    _, _, _, conversation, client = setup
    for block in (INSTRUCTION, BACKLOG, TICKET, AGENT_TICKETS):
        _answer(db_session, conversation, f"{ASKS}\n\n{block}")

    for shown in _messages(client, conversation)[-4:]:
        assert shown["held_for_answer"] is True
        assert shown["instruction"] is None and shown["backlog_request"] is None
        assert shown["ticket_proposal"] is None and shown["agent_tickets"] == []


def test_an_answer_without_a_question_offers_what_it_prepared(setup, db_session):  # noqa: F811
    _, _, _, conversation, client = setup
    _answer(db_session, conversation, f"Tu je pokyn.\n\n{INSTRUCTION}\n\n{BACKLOG}\n\n{TICKET}\n\n{AGENT_TICKETS}")

    shown = _messages(client, conversation)[-1]

    assert shown["held_for_answer"] is False
    assert shown["instruction"] == "V backend/app/kit.py nastav on_setting_change podľa §6.4 Špecifikácie."
    assert shown["backlog_request"].startswith("Export do Excelu")
    assert shown["ticket_proposal"] == {"kind": "nsv", "title": "Tlačidlo Nasadiť pri UAT nič nerobí"}
    assert shown["agent_tickets"] == ["DEV-186"]


def test_a_question_without_anything_prepared_holds_nothing(setup, db_session):  # noqa: F811
    _, _, _, conversation, client = setup
    _answer(db_session, conversation, ASKS)

    assert _messages(client, conversation)[-1]["held_for_answer"] is False


def test_the_clicks_on_an_answer_that_asks_are_refused_with_the_sentence(setup, db_session):  # noqa: F811
    _, _, _, conversation, client = setup
    backlog = _answer(db_session, conversation, f"{ASKS}\n\n{BACKLOG}")
    ticket = _answer(db_session, conversation, f"{ASKS}\n\n{TICKET}")
    tickets = _answer(db_session, conversation, f"{ASKS}\n\n{AGENT_TICKETS}")

    for resp in (
        client.post(f"/api/v1/poradca/messages/{backlog.id}/backlog"),
        client.get(f"/api/v1/poradca/messages/{ticket.id}/plane-ticket"),
        client.post(f"/api/v1/poradca/messages/{tickets.id}/agent-tickets"),
    ):
        assert resp.status_code == 422
        assert resp.json()["detail"] == handoff.HELD_FOR_ANSWER


def test_the_charter_tells_poradca_to_wait():
    text = " ".join(CHARTER.read_text(encoding="utf-8").split())

    assert (
        "Keď sa človeka na niečo pýtaš, odpoveď končí otázkou a nenesie žiadny blok (pokyn pre agenta, požiadavku "
        "do Zásobníka, tiket ani tiket pre agenta) — pripravíš ho až po jeho odpovedi." in text
    )
