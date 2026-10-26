"""DEV-231 — „Znova overiť bez opravy“ spends no round of fixes, and the round cap still offers it.

26.10.2026, Author Asistent 0.1.0: the Director pressed „Znova overiť bez opravy“ three times (15:12, 15:38, 16:12 UTC)
because the causes were in the cockpit (DEV-227, DEV-229). The card promises „nespustí sa žiadna oprava a nemíňa sa
kolo“, yet every press did ``state.iteration += 1`` — after four verdicts and not one fix by the agent the build hit
``AUDITOR_LOOP_MAX`` and its card offered only „Usmerniť opravu“ and „Zatiaľ podržať“; the board no longer offered the
re-verify. Director: „Áno, súhlasím, založ oba tikety a oprav to“.

Pinned: the re-verify — pressed on the bar or chosen on the card — leaves the round counter as it was; at the round
cap the board offers it and the engine takes it; a cap card raised after such a re-verify is a new card, not the old
one again.
"""

from __future__ import annotations

import pytest

from backend.api.routes import pipeline as pipeline_route
from backend.services import orchestrator
from tests.test_orchestrator_v2_verifikacia import _make_version, _rec_engine_override_verdict, _seed_verifikacia


def _in_the_fix_loop(db_session, *, iteration: int):
    version, _ = _make_version(db_session, project_dial="plna")
    state = _seed_verifikacia(db_session, version.id, iteration=iteration)
    _rec_engine_override_verdict(db_session, version.id)
    state.current_stage, state.status, state.block_reason = "programovanie", "blocked", "decision_needed"
    db_session.flush()
    return version, state


def _at_the_cap(db_session):
    version, _ = _make_version(db_session, project_dial="plna")
    state = _seed_verifikacia(db_session, version.id, iteration=orchestrator.AUDITOR_LOOP_MAX + 1)
    _rec_engine_override_verdict(db_session, version.id)
    state.status, state.block_reason = "blocked", "decision_needed"
    db_session.flush()
    return version, state


@pytest.mark.asyncio
async def test_the_reverify_on_the_bar_spends_no_round(db_session, monkeypatch):
    version, _ = _in_the_fix_loop(db_session, iteration=2)
    monkeypatch.setattr(orchestrator, "_begin_dispatch", lambda db, st: None)

    new = await orchestrator.apply_action(db_session, version_id=version.id, action="overit_bez_opravy", payload={})

    assert (new.current_stage, new.iteration) == ("verifikacia", 2)


@pytest.mark.asyncio
async def test_choosing_it_on_the_card_spends_no_round_either(db_session, monkeypatch):
    version, state = _in_the_fix_loop(db_session, iteration=2)
    monkeypatch.setattr(orchestrator, "_begin_dispatch", lambda db, st: None)
    consult = orchestrator._build_fix_consultation(db_session, version.id, state)
    orchestrator._record_message(
        db_session,
        version_id=version.id,
        stage="programovanie",
        author="ai_agent",
        recipient="manazer",
        kind="consultation",
        content=consult.intro,
        payload={"consultation": consult.model_dump(mode="json"), "phase": "verifikacia"},
    )
    db_session.flush()

    new = await orchestrator.apply_action(
        db_session,
        version_id=version.id,
        action="decide",
        payload={"decision_key": "verifikacia_fix_next", "option_id": "overit_bez_opravy"},
    )

    assert (new.current_stage, new.iteration) == ("verifikacia", 2)


@pytest.mark.asyncio
async def test_at_the_round_cap_the_board_offers_it_and_the_engine_takes_it(db_session, monkeypatch):
    version, _ = _at_the_cap(db_session)

    offered = pipeline_route._board(db_session, version.id).available_actions
    monkeypatch.setattr(orchestrator, "_begin_dispatch", lambda db, st: None)
    new = await orchestrator.apply_action(db_session, version_id=version.id, action="overit_bez_opravy", payload={})

    assert "overit_bez_opravy" in offered
    assert (new.current_stage, new.block_reason, new.iteration) == (
        "verifikacia",
        None,
        orchestrator.AUDITOR_LOOP_MAX + 1,
    )


def test_a_cap_card_after_a_reverify_is_a_new_card(db_session):
    """The cap does not move the counter, so the counter alone would name the next cap card like the last one."""
    version, state = _at_the_cap(db_session)
    first = orchestrator._verifikacia_fail_consultation_id(db_session, version.id, state)
    orchestrator._record_message(
        db_session,
        version_id=version.id,
        stage="verifikacia",
        author="system",
        recipient="manazer",
        kind="consultation",
        content="Auditor po 5 kolách stále FAIL",
        payload={"consultation": {"id": first, "source": "verifikacia_fail", "decisions": []}},
    )
    db_session.flush()

    second = orchestrator._verifikacia_fail_consultation_id(db_session, version.id, state)

    assert second != first
