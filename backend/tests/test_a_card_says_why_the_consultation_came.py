"""DEV-106 — the decision cards say truthfully why the consultation came: the engine writes it, not the agent.

The review-with-Poradca button ends its question by the kind of card: a card with the Auditor's findings asks
Poradca to check the finding, a card the agent prepared asks which option to choose. Which kind it is reads the
card's ``source``. The engine knows why it asked for the consultation, but it left the field as the agent wrote it —
and an agent that leaves it out gets the schema's default, the Auditor's upfront review. The engine now writes the
true reason onto the cards, the same way it writes the round number.
"""

from __future__ import annotations

from backend.services import orchestrator
from backend.tests.test_the_manager_sees_which_consultation_round_he_is_in import (
    _cards,
    _latest_consultation_payload,
    _seed,
    _stub_turn,
)


async def test_the_card_carries_the_reason_the_engine_asked_for(db_session, monkeypatch) -> None:
    """The agent's cards say ``auditor_upfront`` — what it wrote, and what a left-out field becomes."""
    version, state = _seed(db_session)
    block = _cards(1)
    assert block.consultation.source == "auditor_upfront"
    monkeypatch.setattr(orchestrator, "invoke_agent_with_parse_retry", _stub_turn(db_session, version, block))

    await orchestrator._settle_for_consultation(
        db_session, state, source=orchestrator._VIZUAL_CONFLICT_SOURCE, verdict=None
    )

    assert _latest_consultation_payload(db_session, version.id)["source"] == "vizual_conflict"


async def test_the_auditors_review_stays_the_auditors_review(db_session, monkeypatch) -> None:
    version, state = _seed(db_session)
    monkeypatch.setattr(orchestrator, "invoke_agent_with_parse_retry", _stub_turn(db_session, version, _cards(2)))

    await orchestrator._settle_for_consultation(db_session, state, source="auditor_upfront", verdict=None)

    assert _latest_consultation_payload(db_session, version.id)["source"] == "auditor_upfront"
