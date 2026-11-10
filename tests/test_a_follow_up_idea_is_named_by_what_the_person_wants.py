"""DEV-350 — a follow-up idea is named by what the person wants, not by the sentence the cockpit wrote for him.

10.11.2026 the first follow-up idea, from NEX Legal Watch, was named „Nadväzuje na projekt NEX Legal Watch
(nex-legal-watch), ktorý vznikol z nápadu …“ — the title is the start of the first message, and the cockpit's own
sentence stood first. Every follow-up idea would have been named the same. The screen now sends the title it means.
"""

from __future__ import annotations

from backend.db.models.poradca import PoradcaConversation
from tests.test_ideas_talk_without_a_project import _client, _user, answers  # noqa: F401 — fixture


def test_an_idea_is_named_by_the_title_the_screen_sends(db_session, answers):  # noqa: F811
    me = _user(db_session)
    r = _client(db_session, me).post(
        "/api/v1/poradca/ideas",
        json={"question": "Nadväzuje na projekt NEX Legal Watch … Čo potrebujem: čítanie", "title": "čítanie"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["title"] == "čítanie"
    assert db_session.get(PoradcaConversation, r.json()["conversation_id"]).title == "čítanie"


def test_without_a_title_the_idea_is_named_by_its_first_words(db_session, answers):  # noqa: F811
    me = _user(db_session)
    r = _client(db_session, me).post("/api/v1/poradca/ideas", json={"question": "Matrika pre farnosť"})
    assert r.json()["title"] == "Matrika pre farnosť"
