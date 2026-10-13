"""DEV-103 — Poradca's status says how long a question may be: the very limit the question is checked against.

The review-with-Poradca button on the verification decision card composes the question from the card. On NEX Inbox
1.8.0 (13.10.2026) a card carried about 16 500 characters — the explanation, the critic's word, the Auditor's
findings and the proposed fix — against Poradca's limit of 20 000. The screen shortens the technical detail to fit,
and it must fit by the backend's own limit, never by a copy of it that could drift.
"""

from __future__ import annotations

from backend.schemas import poradca as schemas
from backend.services.poradca import readiness
from tests.test_poradca_api import _client, _user


def test_the_status_carries_the_limit_a_question_is_checked_against(db_session, monkeypatch):
    monkeypatch.setattr(readiness, "problems", lambda: [])
    c = _client(db_session, _user(db_session))

    limit = c.get("/api/v1/poradca/status").json()["question_max_chars"]

    assert limit == schemas.MAX_QUESTION_CHARS
    for shape in (schemas.PoradcaConversationCreate, schemas.PoradcaAsk):
        assert shape(question="x" * limit).question, f"{shape.__name__} refuses a question within the limit"
        assert not _accepts(shape, "x" * (limit + 1)), f"{shape.__name__} takes a question over the limit it reports"


def _accepts(shape, question: str) -> bool:
    try:
        shape(question=question)
    except ValueError:
        return False
    return True
