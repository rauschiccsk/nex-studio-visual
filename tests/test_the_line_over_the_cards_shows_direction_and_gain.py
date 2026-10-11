"""DEV-5 — over the cards: where the build is heading, and what the latest review round gained.

NEX Inbox 1.5.0, Návrh, 11.–12.09.2026: five review rounds, blocking findings 5 → 1 → 0 → 1 → 0, twelve closed — and
the Manažér saw only „rozhodni 1 zo 4“. The fourth round looked like the build falling apart and was a pure
discovery; three findings of the build would have passed every test and shown only at a paying customer. The line
(ICCINT-124) counted from the board's tail of 50 messages, mixed the rounds of different phases, and the sentence of
what a round gained did not exist: no finding said whether the tests would have caught it.
"""

from __future__ import annotations

import pytest

from backend.api.routes import pipeline as pipeline_routes
from backend.services import orchestrator
from backend.services import pipeline_status as ps
from tests.test_the_card_says_where_its_question_came_from import _answer, _consult, _decision, _version


def _round(db, version_id, *findings, stage: str = "navrh") -> None:
    orchestrator._record_message(
        db,
        version_id=version_id,
        stage=stage,
        author="auditor",
        recipient="manazer",
        kind="verdict",
        content="Previerka",
        payload={"verdict": "FAIL", "findings": list(findings)},
    )


def _f(text: str, blocking: bool = True, escapes: bool = False) -> dict:
    return {"text": text, "blocking": blocking, "escapes_tests": escapes}


def _noise(db, version_id, n: int = 60) -> None:
    for i in range(n):  # more than the board's tail of recent messages carries
        orchestrator._record_message(
            db, version_id=version_id, stage="navrh", author="system", recipient="manazer", kind="notification",
            content=f"Krok {i}",
        )  # fmt: skip


def _nex_inbox_150(db):
    """Five Návrh rounds as on NEX Inbox 1.5.0 — blocking 5, 1, 0, 1, 0 next to minor ones — twelve answered cards,
    and the open consultation of the last round with four cards."""
    v = _version(db)
    shape = [(5, 1), (1, 4), (0, 1), (1, 1), (0, 1)]
    answered = 0
    for n_round, (blocking, minor) in enumerate(shape, start=1):
        _round(
            db,
            v,
            *[_f(f"b{n_round}.{i}") for i in range(blocking)],
            *[_f(f"m{n_round}.{i}", False) for i in range(minor)],
        )
        if n_round < len(shape):
            keys = [f"r{n_round}k{i}" for i in range(3)]
            _consult(db, v, *[_decision(k, f"Otázka {k}?") for k in keys])
            for k in keys:
                _answer(db, v, k, "A")
                answered += 1
        _noise(db, v, 15)
    _consult(db, v, *[_decision(f"r5k{i}", f"Otázka {i}?") for i in range(4)])
    assert answered == 12
    return v


def test_the_direction_reads_the_whole_build_not_the_tail(db_session):
    direction = orchestrator.consultation_direction(db_session, _nex_inbox_150(db_session))

    assert direction == {
        "closed": 12,
        "open": 4,
        "blocking_by_round": [5, 1, 0, 1, 0],
        "last_round": {"findings": 1, "blocking": 0, "escaped": 0},
    }


def test_bad_news_reads_as_bad_news(db_session):
    v = _version(db_session)
    for blocking in (0, 2, 5):
        _round(db_session, v, *[_f(f"b{blocking}.{i}") for i in range(blocking)])
    _consult(db_session, v, _decision("k", "Otázka?"))

    assert orchestrator.consultation_direction(db_session, v)["blocking_by_round"] == [0, 2, 5]


def test_another_phase_is_not_a_relapse(db_session):
    """A clean Návrh (3 → 0) and the first Verifikácia round (2): the line over the Verifikácia cards is „2“, not
    „3 → 0 → 2“ — that would read as the build breaking what was already done."""
    v = _version(db_session)
    _round(db_session, v, _f("a"), _f("b"), _f("c"))
    _round(db_session, v)
    _round(db_session, v, _f("x"), _f("y"), stage="verifikacia")
    orchestrator._record_message(
        db_session,
        version_id=v,
        stage="verifikacia",
        author="system",
        recipient="manazer",
        kind="consultation",
        content="Verifikácia našla chybu.",
        payload={"consultation": {"id": "c", "decisions": [_decision("fix", "Ako?")]}},
    )

    assert orchestrator.consultation_direction(db_session, v)["blocking_by_round"] == [2]


def test_findings_stored_as_sentences_still_count_by_their_marker(db_session):
    v = _version(db_session)
    _round(
        db_session, v, "Chýba strážca disku", "Preklep v nadpise (neblokujúce)", {"text": "Poznámka", "blocking": False}
    )  # records before DEV-5: sentences, and objects that say only whether they block
    _consult(db_session, v, _decision("k", "Otázka?"))

    direction = orchestrator.consultation_direction(db_session, v)

    assert direction["blocking_by_round"] == [1]
    assert direction["last_round"] == {"findings": 3, "blocking": 1, "escaped": None}, "sentence omits tests"


def test_the_round_says_how_many_would_have_reached_the_customer(db_session):
    v = _version(db_session)
    _round(db_session, v, _f("a", escapes=True), _f("b"), _f("c", blocking=False, escapes=True), "veta motora")
    _consult(db_session, v, _decision("k", "Otázka?"))

    assert orchestrator.consultation_direction(db_session, v)["last_round"] == {
        "findings": 4,
        "blocking": 3,
        "escaped": 2,
    }


def test_open_counts_the_cards_of_the_open_round_without_an_answer(db_session):
    v = _version(db_session)
    _consult(db_session, v, _decision("a", "A?"), _decision("b", "B?"))
    _answer(db_session, v, "a", "x")
    _answer(db_session, v, "b", "y")
    _consult(db_session, v, _decision("a", "A?"), _decision("c", "C?"), _decision("d", "D?"))
    _answer(db_session, v, "c", "z")

    direction = orchestrator.consultation_direction(db_session, v)

    assert (direction["closed"], direction["open"]) == (3, 2)
    assert direction["blocking_by_round"] == [] and direction["last_round"] is None


def test_no_consultation_no_line(db_session):
    v = _version(db_session)
    _round(db_session, v, _f("a"))

    assert orchestrator.consultation_direction(db_session, v) is None


def test_the_board_carries_it_while_a_decision_is_offered(db_session):
    v = _nex_inbox_150(db_session)

    board = pipeline_routes._board(db_session, v)

    assert "decide" in board.available_actions
    assert board.consultation_direction.blocking_by_round == [5, 1, 0, 1, 0]
    assert board.consultation_direction.closed == 12
    assert not any(m.kind == "verdict" for m in board.recent_messages[:5]), "Príprava: early rounds are off the tail"


# --- the agent says it for every new finding ------------------------------------------------------------------


def _verdict_block(*findings) -> dict:
    return {
        "stage": "navrh",
        "kind": "verdict",
        "verdict": False,
        "awaiting": "manazer",
        "summary": "Previerka",
        "findings": list(findings),
    }


def test_a_new_finding_that_does_not_say_whether_tests_catch_it_is_sent_back():
    result = ps._validate_block(_verdict_block({"text": "Chýba strážca disku", "blocking": True}))

    assert isinstance(result, ps.ParseFailure)
    assert result.reason == "findings: finding 'Chýba strážca disku' must say 'escapes_tests' (true/false)"


@pytest.mark.parametrize("value", [None, "áno", 1])
def test_escapes_tests_must_be_true_or_false(value):
    result = ps._validate_block(_verdict_block({"text": "X", "blocking": True, "escapes_tests": value}))

    assert isinstance(result, ps.ParseFailure)


def test_a_finding_that_says_it_is_kept_in_the_record():
    block = ps._validate_block(
        _verdict_block(_f("Peniaze sa zaokrúhľujú po riadkoch", escapes=True), _f("Preklep", False))
    )

    assert not isinstance(block, ps.ParseFailure)
    assert block.findings_as_data() == [
        {"text": "Peniaze sa zaokrúhľujú po riadkoch", "blocking": True, "escapes_tests": True},
        {"text": "Preklep", "blocking": False, "escapes_tests": False},
    ]


def test_the_writers_schema_demands_it_as_true_or_false():
    finding = ps.PIPELINE_STATUS_JSON_SCHEMA["$defs"]["Finding"]

    assert "escapes_tests" in finding["required"]
    assert finding["properties"]["escapes_tests"]["type"] == "boolean"
    assert (
        "anyOf" not in finding["properties"]["escapes_tests"]
        and "default" not in finding["properties"]["escapes_tests"]
    )


def test_the_rule_and_the_charter_tell_the_agent():
    assert "`escapes_tests: true`" in ps.FINDINGS_AS_DATA_RULE and "u zákazníka" in ps.FINDINGS_AS_DATA_RULE
    charter = (orchestrator.Path(__file__).resolve().parents[1] / "templates" / "auditor-charter.md").read_text(
        encoding="utf-8"
    )
    assert "`{text, blocking, escapes_tests}`" in charter
