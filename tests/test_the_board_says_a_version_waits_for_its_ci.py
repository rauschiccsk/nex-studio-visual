"""DEV-308 — while a version waits for its checks on GitHub, the board says so, in every phase the cockpit holds it.

05.11.2026, the fast fix 1.0.1 of NEX Knowledge: Verifikácia passed, the version waited for its CI (DEV-201) and the
next step said „… čaká na kontrolu na GitHube … Kokpit to dorieši sám.“ — while the strip above it read „Čaká na
súhlas“. Nothing could be approved: Hotovo is not offered until the checks finish. The Director: „Teraz skončil, ale
na obrazovke nie je mi jasné či niečo ešte beží alebo je to hotové“. The strip did not know: the board carried the wait
(``ci_gate``) after Programovanie only (DEV-261). Director 05.11.2026: „Súhlasím“.
"""

from __future__ import annotations

from backend.api.routes.pipeline import _board
from backend.services import orchestrator
from tests.test_a_version_waits_for_its_ci import GREEN, _passed_while_ci_runs


async def test_after_verification_the_board_says_the_version_waits_for_its_checks(db_session, monkeypatch):
    version, state, _github = await _passed_while_ci_runs(db_session, monkeypatch)
    assert (state.current_stage, state.status) == ("verifikacia", "awaiting_manazer")

    gate = _board(db_session, version.id).ci_gate

    assert gate is not None and gate.state == "pending"
    assert gate.message == orchestrator.VERIFIKACIA_CI_PENDING_GATE


async def test_the_fast_fix_says_it_too(db_session, monkeypatch):
    version, _state, _github = await _passed_while_ci_runs(db_session, monkeypatch, flow_type="fast_fix", dial="plna")

    gate = _board(db_session, version.id).ci_gate

    assert gate is not None and gate.state == "pending"


async def test_once_the_checks_are_green_the_board_waits_for_nothing(db_session, monkeypatch):
    version, _state, github = await _passed_while_ci_runs(db_session, monkeypatch)
    github["rows"] = GREEN

    await orchestrator.settle_ci_waits(db_session)

    board = _board(db_session, version.id)
    assert board.ci_gate is None
    assert "schvalit" in board.available_actions


def test_the_sentence_names_the_step_that_waits():
    assert orchestrator.VERIFIKACIA_CI_PENDING_GATE == (
        "Verifikácia prešla — „Schváliť na Hotovo“ sa ponúkne, keď kontroly na GitHube dobehnú. Kokpit to dorieši sám."
    )
