"""DEV-431 — the cockpit keeps at a Poradca answer that it was put into Riadiace centrum, and says when.

Director 18.11.2026: „Ak som v záložke Poradca a je na obrazovke tlačidlo "Vložiť do Riadiaceho centra" nevidím tu, či
som to už urobil alebo nie. Potrebujem informáciu, že návrh Poradvu už je vložený.“ Putting an answer there went only
through the browser's storage; the answer kept no record of it.
"""

from __future__ import annotations

from backend.db.models.poradca import PoradcaMessage
from tests.test_poradca_proposes_a_ticket_and_a_click_founds_it import _answer, plane, setup  # noqa: F401
from tests.test_what_a_person_passes_on_is_slovak import SLOVAK_INSTRUCTION, _messages


def test_an_answer_put_into_riadiace_centrum_says_when(setup, db_session):  # noqa: F811
    _, _, _, conversation, client = setup
    message = _answer(db_session, conversation, f"Nálezy sú oprávnené.\n\n{SLOVAK_INSTRUCTION}")
    assert _messages(client, conversation)[-1]["handed_off_at"] is None

    marked = client.post(f"/api/v1/poradca/messages/{message.id}/handed-off")

    assert marked.status_code == 200, marked.text
    shown = _messages(client, conversation)[-1]
    assert shown["handed_off_at"] is not None
    assert shown["handed_off_at"][:19] == marked.json()["handed_off_at"][:19]


def test_only_an_answer_of_poradca_is_marked(setup, db_session):  # noqa: F811
    _, _, _, conversation, client = setup
    question = PoradcaMessage(conversation_id=conversation.id, author="human", content="Čo ďalej?", status="done")
    db_session.add(question)
    db_session.flush()

    refused = client.post(f"/api/v1/poradca/messages/{question.id}/handed-off")

    assert refused.status_code == 422
    assert db_session.get(PoradcaMessage, question.id).handed_off_at is None


def test_an_answer_never_put_there_says_nothing(setup, db_session):  # noqa: F811
    _, _, _, conversation, client = setup
    _answer(db_session, conversation, f"Nálezy sú oprávnené.\n\n{SLOVAK_INSTRUCTION}")

    assert _messages(client, conversation)[-1]["handed_off_at"] is None
