"""DEV-221 — an „Uprav“ that interrupts the Decision Cards takes the cards the Manažér has decided to the agent.

24.10.2026 Author Asistent 0.1.0: in the first round of the Návrh consultation the Director decided 12 of 20 cards,
most with a detailed note (15:58–17:10), then sent Dedo's proposal as „Vrátiť agentovi na doplnenie“. The agent's
prompt carried the Uprav text only — the decisions reach the agent with the LAST decide of a round — so the agent
never saw them and the next review asked all of them again: 21 cards. Director: „Áno, súhlasím, založ tiket a
implementuj“.

Pinned: the directive of an Uprav during the cards carries every decided card (option, own decision, note) and
none of the undecided ones, and tells the agent the rest is cancelled; the record of the Uprav names what went
with it; a held card is not a decision; an Uprav outside the cards is as before.
"""

from __future__ import annotations

from backend.db.models.pipeline import PipelineMessage
from backend.services import orchestrator
from tests.test_orchestrator_v2_consultation import _seed_consultation

COMMENT = "Aplikácia má jedného používateľa — rozdelenie právomocí zruš."
BASE = f"Manažér ťa vrátil na úpravu fázy 'navrh': {COMMENT}"
REST = "Ostatné karty tejto konzultácie úprava ruší; ak po nej ešte platia, ďalšie kolo sa na ne spýta."


async def _decide(db, version, **payload):
    await orchestrator.apply_action(db, version_id=version.id, action="decide", payload=payload)


async def _uprav(db, version):
    state = await orchestrator.apply_action(db, version_id=version.id, action="uprav", payload={"comment": COMMENT})
    return state, orchestrator.dispatch_directive(db, version.id, "uprav", {"comment": COMMENT}, "navrh")


def _last_return(db, version) -> PipelineMessage:
    return (
        db.query(PipelineMessage)
        .filter_by(version_id=version.id, author="manazer", kind="return")
        .order_by(PipelineMessage.seq.desc())
        .first()
    )


async def test_the_decided_card_goes_to_the_agent_with_the_uprav_and_the_undecided_does_not(db_session):
    version = _seed_consultation(db_session)
    await _decide(db_session, version, decision_key="d1", option_id="a", note="Len pre nové záznamy.")

    state, directive = await _uprav(db_session, version)

    assert state.status == "agent_working"
    assert directive.splitlines() == [
        BASE,
        "",
        "Manažér ťa vrátil uprostred konzultácie. Karty, ktoré už rozhodol, platia — zapracuj ich spolu s úpravou "
        "a znovu sa na ne nepýtaj:",
        "- Otázka 1? → Voľba A1 (poznámka: Len pre nové záznamy.)",
        REST,
        orchestrator.CONSULTATION_APPLY_CHECK,  # the Uprav and the decisions are checked together
    ]


async def test_an_own_decision_goes_as_the_managers(db_session):
    version = _seed_consultation(db_session)
    await _decide(db_session, version, decision_key="d2", free_text="Nič z toho — urob to podľa zadania.")

    _state, directive = await _uprav(db_session, version)

    assert (
        "- Otázka 2? → Vlastné rozhodnutie Manažéra (žiadna z ponúknutých možností): Nič z toho — urob to podľa "
        "zadania."
    ) in directive.splitlines()
    assert "Otázka 1?" not in directive


async def test_the_record_of_the_uprav_names_what_went_with_it(db_session):
    version = _seed_consultation(db_session)
    await _decide(db_session, version, decision_key="d1", option_id="b")

    await _uprav(db_session, version)

    assert _last_return(db_session, version).payload["carried_decisions"] == [
        {"key": "d1", "question": "Otázka 1?", "chosen": "Voľba B1"}
    ]


async def test_an_uprav_before_any_card_is_decided_says_the_cards_are_cancelled(db_session):
    version = _seed_consultation(db_session)

    _state, directive = await _uprav(db_session, version)

    assert directive.splitlines() == [
        BASE,
        "",
        "Manažér ťa vrátil uprostred konzultácie, skôr než rozhodol niektorú kartu. Karty tejto konzultácie úprava "
        "ruší; ak po nej ešte platia, ďalšie kolo sa na ne spýta.",
    ]
    assert _last_return(db_session, version).payload["carried_decisions"] == []


async def test_a_held_card_is_not_a_decision(db_session):
    version = _seed_consultation(db_session)
    orchestrator._record_message(
        db_session,
        version_id=version.id,
        stage="navrh",
        author="manazer",
        recipient="ai_agent",
        kind="answer",
        content="Otázka 1? → Zatiaľ podržať",
        payload={"consultation_decision": {"key": "d1", "option_id": "hold", "label": "Zatiaľ podržať", "held": True}},
    )

    _state, directive = await _uprav(db_session, version)

    assert "Otázka 1?" not in directive
    assert "skôr než rozhodol niektorú kartu" in directive


async def test_an_uprav_outside_the_cards_is_as_before_even_with_decisions_on_record(db_session):
    version = _seed_consultation(db_session)
    await _decide(db_session, version, decision_key="d1", option_id="a")
    state = await orchestrator.apply_action(
        db_session, version_id=version.id, action="decide", payload={"decision_key": "d2", "option_id": "a"}
    )
    state.status, state.block_reason = "awaiting_manazer", None  # the agent applied them and settled
    db_session.flush()

    _state, directive = await _uprav(db_session, version)

    assert directive == BASE
    assert "carried_decisions" not in _last_return(db_session, version).payload
