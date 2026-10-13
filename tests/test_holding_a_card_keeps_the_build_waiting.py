"""DEV-107 — the hold option on a verification card holds, whatever stands in the guidance field.

The verification card and the card after exhausted verification rounds offer to hold the build until the manager
decides. Until 4.47.0 that held only with an EMPTY field: with any text in it, the answer fell through to the
guided fix — the text became a fix brief, a fix task was prepared and the build moved to Programovanie. Text gets
into that field without typing: Poradca's instruction is handed off into it (DEV-22, DEV-26) and a half-written
note is restored (ICCINT-30).

The backend decides that an answer holds and records it with the answer (``held``), so the screen keeps the card
for the decision still to come instead of treating it as decided.
"""

from __future__ import annotations

import pytest

from backend.services import orchestrator
from tests.test_orchestrator_v2_verifikacia import (
    _ban_deploy_calls,
    _make_version,
    _msgs,
    _seed_done_tasks,
    _seed_verifikacia,
    _stub_auditor,
    _stub_critique,
    _stub_smoke,
    _tasks,
    _verdict_fail,
)

NOTE = "Skús najprv overiť, či zlyhanie nespôsobilo zostavenie."


async def _fix_card(db_session, monkeypatch):
    version, project = _make_version(db_session, project_dial="plna")
    _seed_verifikacia(db_session, version.id, iteration=0)
    _seed_done_tasks(db_session, version, project, ["T1"])
    _stub_auditor(monkeypatch, _verdict_fail(["zlyhanie"], proposed_fix="FIX"))
    _stub_smoke(monkeypatch)
    _stub_critique(monkeypatch, verdict="reject", why="nevie sa to overiť")
    _ban_deploy_calls(monkeypatch)
    monkeypatch.setattr(orchestrator, "_begin_dispatch", lambda db, st: setattr(st, "status", "agent_working"))
    await orchestrator.run_dispatch(db_session, version.id)
    return version, "verifikacia_fix_next"


async def _exhausted_card(db_session, monkeypatch):
    version, project = _make_version(db_session, project_dial="plna")
    _seed_verifikacia(db_session, version.id, iteration=orchestrator.AUDITOR_LOOP_MAX)
    _seed_done_tasks(db_session, version, project, ["T1"])
    _stub_auditor(monkeypatch, _verdict_fail(["stále zlyháva"]))
    _stub_smoke(monkeypatch, acc=(False, "still failing", False))
    monkeypatch.setattr(orchestrator, "_begin_dispatch", lambda db, st: setattr(st, "status", "agent_working"))
    await orchestrator.run_dispatch(db_session, version.id)
    return version, "verifikacia_fail_next"


CARDS = pytest.mark.parametrize("card", [_fix_card, _exhausted_card], ids=["verification", "exhausted-rounds"])


async def _decide(db_session, version, key, **answer):
    return await orchestrator.apply_action(
        db_session, version_id=version.id, action="decide", payload={"decision_key": key, **answer}
    )


def _latest_answer(db_session, version):
    answers = [m for m in _msgs(db_session, version.id) if (m.payload or {}).get("consultation_decision")]
    return answers[-1].payload["consultation_decision"]


@CARDS
@pytest.mark.parametrize("text", [{}, {"note": NOTE}], ids=["empty-field", "text-in-field"])
async def test_hold_keeps_the_build_on_the_card(db_session, monkeypatch, card, text):
    version, key = await card(db_session, monkeypatch)
    stage_before = orchestrator._get_state(db_session, version.id).current_stage
    tasks_before = len(_tasks(db_session, version.id))
    returns_before = len([m for m in _msgs(db_session, version.id) if m.kind == "return"])

    state = await _decide(db_session, version, key, option_id="hold", **text)

    assert (state.status, state.block_reason) == ("blocked", "decision_needed")
    assert state.current_stage == stage_before, "the build moved on to a fix"
    assert "decide" in orchestrator.determine_available_actions(state), "the card is no longer the way on"
    assert len(_tasks(db_session, version.id)) == tasks_before, "a fix task was prepared"
    assert len([m for m in _msgs(db_session, version.id) if m.kind == "return"]) == returns_before
    assert _latest_answer(db_session, version)["held"] is True


@CARDS
async def test_a_decision_after_the_hold_still_goes_through(db_session, monkeypatch, card):
    version, key = await card(db_session, monkeypatch)
    await _decide(db_session, version, key, option_id="hold", note=NOTE)

    fix = "fix_it" if key == "verifikacia_fix_next" else "guide_fix"
    state = await _decide(db_session, version, key, option_id=fix, note=NOTE)

    assert state.current_stage == "programovanie"
    assert not _latest_answer(db_session, version).get("held")


async def test_an_answer_that_decides_is_not_marked_as_held(db_session, monkeypatch):
    version, key = await _fix_card(db_session, monkeypatch)

    await _decide(db_session, version, key, option_id="fix_it")

    assert not _latest_answer(db_session, version).get("held")


async def test_an_option_named_hold_on_an_agents_card_is_an_ordinary_choice(db_session):
    """Only the engine's verification cards hold; a card the agent prepared decides with every answer."""
    version, _ = _make_version(db_session)
    state = _seed_verifikacia(db_session, version.id)
    state.current_stage = "navrh"
    state.status = "blocked"
    state.block_reason = "decision_needed"
    orchestrator._record_message(
        db_session,
        version_id=version.id,
        stage="navrh",
        author="ai_agent",
        recipient="manazer",
        kind="consultation",
        content="Rozhodnutia.",
        payload={
            "consultation": {
                "id": "c1",
                "source": "agent_ambiguity",
                "decisions": [
                    {
                        "key": "d1",
                        "question": "Spustiť import hneď, alebo ho podržať do konca mesiaca?",
                        "options": [{"id": "now", "label": "Hneď"}, {"id": "hold", "label": "Podržať"}],
                    }
                ],
            }
        },
    )
    db_session.flush()

    await _decide(db_session, version, "d1", option_id="hold")

    assert not _latest_answer(db_session, version).get("held")
