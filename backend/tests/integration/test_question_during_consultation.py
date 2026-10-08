"""A question typed in the chat during a consultation is answered — it does not re-run the phase (DEV-27).

Found by the Director 08.10.2026 on NEX Inbox 1.7.0. The independent review of the design found nine points
and the cockpit turned them into Decision Cards. He asked Poradca about the first one and sent Poradca's
instruction through the chat instead of the card. The engine handed it to the AI Agent as an ordinary
``ask`` — and in Návrh every Manažér message re-runs the WHOLE phase: the agent rewrote the design and the
specification, the design was committed, the Auditor reviewed it again and opened consultation round 2 of 5.
One answer outside the cards spent a whole round; deciding the remaining points the same way would have run
out of rounds before the points ran out.

The cards are how decisions are made (CR-V2-041); the chat stays open during a consultation so the Manažér
can ASK about a card before deciding. Pinned here: such a question is ONE answer turn — the agent is told
the consultation is open and not to touch the documents, and afterwards the build waits on the SAME cards.
"""

from __future__ import annotations

import uuid as _uuid

import pytest

from backend.db.models.foundation import User
from backend.db.models.pipeline import PipelineState
from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.services import orchestrator
from backend.services.pipeline_status import ParseFailure, PipelineStatusBlock

NEXT = "Manažér: rozhodni 1/2 (2 rozhodnutia, konzultácia — kolo 2 z 5)."
QUESTION = "Nález 1 prijímam. Oprava v Návrhu: pred zápisom over meno súboru."

CARDS = {
    "id": "navrh-2",
    "source": "auditor_upfront",
    "round": 2,
    "round_max": 5,
    "decisions": [
        {
            "key": "velke-faktury",
            "question": "Čo s veľkou faktúrou, ktorá sa nezmestí do limitu?",
            "options": [
                {"id": "dlhsi-limit", "label": "Dlhší limit pre veľké súbory", "recommended": True},
                {"id": "nechat", "label": "Nechať tak"},
            ],
        },
        {
            "key": "sablona",
            "question": "Čo so spoločným priečinkom v šablóne?",
            "options": [{"id": "povinny", "label": "Povinný priečinok pre každého", "recommended": True}],
        },
    ],
}


def _seed(db, *, status: str, block_reason: str | None) -> tuple[Version, PipelineState, int]:
    suffix = _uuid.uuid4().hex[:8]
    user = User(username=f"qc_{suffix}", email=f"qc_{suffix}@test.local", password_hash="x", role="ri")
    db.add(user)
    db.flush()
    project = Project(
        name=f"Otázka {suffix}",
        slug=f"otazka-{suffix}",
        type="standard",
        auth_mode="password",
        description="Question during consultation test project.",
        created_by=user.id,
        source_path=None,
    )
    db.add(project)
    db.flush()
    version = Version(project_id=project.id, version_number="1.7.0", status="active")
    db.add(version)
    db.flush()
    state = PipelineState(
        version_id=version.id,
        flow_type="new_version",
        current_stage="navrh",
        current_actor="ai_agent",
        status=status,
        block_reason=block_reason,
        next_action=NEXT,
        mode=None,
    )
    db.add(state)
    db.flush()
    cards = orchestrator._record_message(
        db,
        version_id=version.id,
        stage="navrh",
        author="ai_agent",
        recipient="manazer",
        kind="consultation",
        content="Previerka našla dva body.",
        payload={"consultation": CARDS},
    )
    db.flush()
    return version, state, cards.seq


async def _ask(db, version: Version) -> str | None:
    await orchestrator.apply_action(db, version_id=version.id, action="ask", payload={"text": QUESTION})
    return orchestrator.dispatch_directive(db, version.id, "ask", {"text": QUESTION}, "navrh")


def _phase_must_not_run(monkeypatch) -> None:
    async def _navrh(*args, **kwargs):
        raise AssertionError("a question during the consultation re-ran the whole Návrh phase")

    async def _review(*args, **kwargs):
        raise AssertionError("a question during the consultation re-ran the Auditor's review")

    monkeypatch.setattr(orchestrator, "_run_navrh_round", _navrh)
    monkeypatch.setattr(orchestrator, "_run_auditor_upfront_review", _review)


@pytest.mark.asyncio
async def test_the_question_is_answered_and_the_same_cards_wait(db_session, monkeypatch) -> None:
    version, _, cards_seq = _seed(db_session, status="blocked", block_reason="decision_needed")
    rounds_before = orchestrator._consult_rounds_used(db_session, version.id, "auditor_upfront")
    _phase_must_not_run(monkeypatch)
    seen: dict[str, object] = {}

    async def _answer(db, **kwargs):
        seen["prompt"] = kwargs["prompt"]
        seen["calls"] = int(seen.get("calls", 0)) + 1
        # The real turn records the agent's reply itself (``invoke_agent``) — so does this stand-in.
        orchestrator._record_message(
            db,
            version_id=kwargs["version_id"],
            stage="navrh",
            author="ai_agent",
            recipient="manazer",
            kind="answer",
            content="Patrí to ku karte 1.",
        )
        return PipelineStatusBlock(stage="navrh", kind="answer", summary="Patrí to ku karte 1.", awaiting="manazer")

    monkeypatch.setattr(orchestrator, "invoke_agent_with_parse_retry", _answer)

    directive = await _ask(db_session, version)
    settled = await orchestrator.run_dispatch(db_session, version.id, directive=directive)

    assert settled is not None
    assert (settled.status, settled.block_reason, settled.next_action) == ("blocked", "decision_needed", NEXT)
    assert seen["calls"] == 1
    prompt = str(seen["prompt"])
    assert QUESTION in prompt
    assert "Špecifikáciu ani Návrh teraz NEMEŇ" in prompt
    # The SAME consultation is still the one waiting, no round was spent, and its cards are still offered.
    assert orchestrator._latest_consultation(db_session, version.id)[1] == cards_seq
    assert orchestrator._consult_rounds_used(db_session, version.id, "auditor_upfront") == rounds_before
    assert "decide" in orchestrator.determine_available_actions(settled)
    assert orchestrator._consultation_answers(db_session, version.id, cards_seq) == {}
    # Answered — the mark can no longer route a later dispatch.
    assert orchestrator.consultation_question_pending(db_session, version.id) is None


@pytest.mark.asyncio
async def test_an_unreadable_answer_still_leaves_the_cards_waiting(db_session, monkeypatch) -> None:
    version, _, cards_seq = _seed(db_session, status="blocked", block_reason="decision_needed")
    _phase_must_not_run(monkeypatch)

    async def _garbled(*args, **kwargs):
        return ParseFailure(reason="no status block")

    monkeypatch.setattr(orchestrator, "invoke_agent_with_parse_retry", _garbled)

    directive = await _ask(db_session, version)
    settled = await orchestrator.run_dispatch(db_session, version.id, directive=directive)

    assert settled is not None
    assert (settled.status, settled.block_reason, settled.next_action) == ("blocked", "decision_needed", NEXT)
    assert orchestrator._latest_consultation(db_session, version.id)[1] == cards_seq


@pytest.mark.asyncio
async def test_a_question_outside_a_consultation_still_steers_the_phase(db_session, monkeypatch) -> None:
    """Only the consultation is protected — at an ordinary Návrh stop the Manažér's message still reworks the
    design, as it always did."""
    version, _, _ = _seed(db_session, status="awaiting_manazer", block_reason=None)
    ran: dict[str, object] = {}

    async def _navrh(db, state, **kwargs):
        ran["directive"] = kwargs.get("directive")
        state.status = "awaiting_manazer"
        return state

    async def _must_not_answer_aside(*args, **kwargs):
        raise AssertionError("an ordinary question was answered aside instead of steering the phase")

    monkeypatch.setattr(orchestrator, "_run_navrh_round", _navrh)
    monkeypatch.setattr(orchestrator, "invoke_agent_with_parse_retry", _must_not_answer_aside)

    directive = await _ask(db_session, version)
    await orchestrator.run_dispatch(db_session, version.id, directive=directive)

    assert ran["directive"] == f"Manažér sa pýta: {QUESTION}"
