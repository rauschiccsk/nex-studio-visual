"""DEV-4 — the decision card says why its question came about, and what a choice reopens.

Director 12.09.2026 (NEX Inbox 1.5.0): the Manažér saw only that questions kept coming and read it as the build
falling apart; whether a question was his own decision's consequence or a finding that had always been there, the
card did not say. Since DEV-3 the agent names a ``dosledok`` card's source by the decision KEY — and the card printed
that key („Vyplýva z tvojho rozhodnutia o: rozsah.“). Its price line („znovu otvára N uzavreté rozhodnutia“) counted
the unanswered cards of the same round as closed. Both are now read from the version's whole record — the board's
tail of recent messages does not reach the round where the source was decided.
"""

from __future__ import annotations

import uuid

from backend.api.routes import pipeline as pipeline_routes
from backend.db.models.foundation import User
from backend.db.models.pipeline import PipelineState
from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.services import orchestrator


def _version(db) -> uuid.UUID:
    suffix = uuid.uuid4().hex[:8]
    user = User(username=f"dev4_{suffix}", email=f"dev4_{suffix}@test.local", password_hash="x", role="ri")
    db.add(user)
    db.flush()
    project = Project(
        name=f"Karta {suffix}",
        slug=f"karta-{suffix}",
        type="standard",
        auth_mode="password",
        description="DEV-4",
        created_by=user.id,
    )
    db.add(project)
    db.flush()
    version = Version(project_id=project.id, version_number="1.5.0", status="active")
    db.add(version)
    db.flush()
    db.add(
        PipelineState(
            version_id=version.id,
            flow_type="new_version",
            current_stage="navrh",
            current_actor="ai_agent",
            status="blocked",
            block_reason="decision_needed",
        )
    )
    db.flush()
    return version.id


def _decision(key: str, question: str, origin: str = "objav", origin_of: str = "") -> dict:
    return {
        "key": key,
        "question": question,
        "origin": origin,
        "origin_of": origin_of,
        "options": [{"id": "a", "label": "A", "recommended": True}, {"id": "b", "label": "B"}],
    }


def _consult(db, version_id, *decisions: dict) -> None:
    orchestrator._record_message(
        db,
        version_id=version_id,
        stage="navrh",
        author="ai_agent",
        recipient="manazer",
        kind="consultation",
        content="Treba tvoje rozhodnutie.",
        payload={"consultation": {"id": uuid.uuid4().hex, "decisions": list(decisions)}},
    )


def _answer(db, version_id, key: str, label: str) -> None:
    orchestrator._record_message(
        db,
        version_id=version_id,
        stage="navrh",
        author="manazer",
        recipient="ai_agent",
        kind="answer",
        content=label,
        payload={"consultation_decision": {"key": key, "option_id": "a", "label": label}},
    )


def _three_rounds(db) -> uuid.UUID:
    """Round 1 decides the scope and the format; round 2 decides two consequences of them; then a long stretch of
    building; round 3 asks the scope again, next to a consequence of round 2's limit and a new consequence of the
    scope that is not answered yet."""
    v = _version(db)
    _consult(db, v, _decision("rozsah", "Aký rozsah kontrol?"), _decision("format", "Aký formát výstupu?"))
    _answer(db, v, "rozsah", "Široký")
    _answer(db, v, "format", "PDF")
    _consult(
        db,
        v,
        _decision("limit", "Koľko položiek na stranu?", "dosledok", "rozsah"),
        _decision("export", "Pridať export?", "dosledok", "format"),
    )
    _answer(db, v, "limit", "100")
    _answer(db, v, "export", "Áno")
    for i in range(60):  # more than the board's tail of recent messages carries
        orchestrator._record_message(
            db, version_id=v, stage="programovanie", author="system", recipient="manazer", kind="notification",
            content=f"Úloha #{i} — hotovo",
        )  # fmt: skip
    _consult(
        db,
        v,
        _decision("rozsah", "Aký rozsah kontrol?", "odklad"),
        _decision("chyby", "Čo s chybami nad limitom?", "dosledok", "limit"),
        _decision("novy", "Kontrolovať aj prílohy?", "dosledok", "rozsah"),
    )
    return v


def test_a_consequence_names_the_decision_it_follows_from_and_what_was_chosen(db_session):
    context = orchestrator.decision_card_context(db_session, _three_rounds(db_session))

    assert context["chyby"] == {
        "origin_of_question": "Koľko položiek na stranu?",
        "origin_of_choice": "100",
        "reopens_closed": 0,
    }


def test_a_decision_asked_again_counts_only_its_closed_consequences(db_session):
    """„rozsah“ is asked again: „limit“ (answered in round 2) follows from it and would reopen; „novy“ follows from it
    too but is not answered — not closed, not counted; „export“ follows from another decision."""
    context = orchestrator.decision_card_context(db_session, _three_rounds(db_session))

    assert context["rozsah"] == {"origin_of_question": None, "origin_of_choice": None, "reopens_closed": 1}


def test_a_consequence_of_a_decision_open_again_names_it_without_a_choice(db_session):
    context = orchestrator.decision_card_context(db_session, _three_rounds(db_session))

    assert context["novy"] == {
        "origin_of_question": "Aký rozsah kontrol?",
        "origin_of_choice": None,
        "reopens_closed": 0,
    }


def test_a_source_that_is_not_on_record_is_not_invented(db_session):
    v = _version(db_session)
    _consult(db_session, v, _decision("x", "Otázka?", "dosledok", "neznamy"))

    assert orchestrator.decision_card_context(db_session, v) == {
        "x": {"origin_of_question": None, "origin_of_choice": None, "reopens_closed": 0}
    }


def test_no_consultation_no_context(db_session):
    assert orchestrator.decision_card_context(db_session, _version(db_session)) == {}


def test_the_board_carries_the_context_while_a_decision_is_offered(db_session):
    v = _three_rounds(db_session)

    board = pipeline_routes._board(db_session, v)

    assert "decide" in board.available_actions
    assert board.decision_context["chyby"].origin_of_question == "Koľko položiek na stranu?"
    assert board.decision_context["rozsah"].reopens_closed == 1
    seqs = [m.seq for m in board.recent_messages]
    assert len(seqs) == pipeline_routes._DEFAULT_RECENT, "setup: the board carries only the tail of the messages"
    assert not any(m.payload and m.payload.get("consultation_decision") for m in board.recent_messages), (
        "setup: second-round answers must lie outside the tail, otherwise the test does not prove reading the whole log"
    )
