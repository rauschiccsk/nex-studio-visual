"""The decision queue is used wherever it appears — not only where the block called itself one (ICCINT-26).

Found by Dedo 24.08.2026 on nex-productcatalogs, reading the record after the Director asked what the build
was waiting for. The independent review had raised three points; the AI Agent verified them, added two of its
own, and returned **five decisions** — options, recommendations, rationale, all of it — on a block it had
labelled ``kind='question'``.

Every lookup in the engine keyed on that label:

* ``_settle_for_consultation`` threw the queue away and settled with the finding list;
* the Manažér was told *"agent ich rozporuje — neurobil rozhodovacie karty, posúdil ich ako už vyriešené"*,
  while the agent's own message on the same screen said *"Neopravil som zatiaľ nič — predkladám päť
  rozhodnutí a opravím ich naraz."* The app asserted something about the agent that the agent had not said;
* ``_latest_consultation`` could not find the queue, so ``decide`` had nothing to answer;
* the re-consult cap counted the round as never having happened.

So the two halves: use the queue when it is there, and when it is NOT there, report what the agent did
without inventing a motive for it.
"""

from __future__ import annotations

import uuid as _uuid

import pytest
from sqlalchemy import select

from backend.db.models.foundation import User
from backend.db.models.pipeline import PipelineMessage, PipelineState
from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.services import orchestrator
from backend.services.pipeline_status import PipelineStatusBlock, _validate_block

FINDINGS = ["Prázdna sadzba DPH má v dokumentoch dve rôzne odpovede."]


def _seed(db) -> tuple[Version, PipelineState]:
    suffix = _uuid.uuid4().hex[:8]
    user = User(username=f"cc_{suffix}", email=f"cc_{suffix}@test.local", password_hash="x", role="ri")
    db.add(user)
    db.flush()
    project = Project(
        name=f"Queue {suffix}",
        slug=f"queue-{suffix}",
        type="standard",
        auth_mode="password",
        description="Decision-queue test project.",
        created_by=user.id,
        source_path=None,
    )
    db.add(project)
    db.flush()
    version = Version(project_id=project.id, version_number="0.1.0", status="active")
    db.add(version)
    db.flush()
    state = PipelineState(
        version_id=version.id,
        flow_type="new_version",
        current_stage="navrh",
        current_actor="ai_agent",
        status="agent_working",
        mode=None,
    )
    db.add(state)
    db.flush()
    return version, state


def _queue(n_recommended: int = 1) -> dict:
    return {
        "id": "navrh-previerka-2",
        "source": "auditor_upfront",
        "decisions": [
            {
                "key": "dph",
                "question": "Čo s prázdnou sadzbou DPH?",
                "options": [
                    {"id": "priznak", "label": "Prijať s príznakom", "recommended": n_recommended >= 1},
                    {"id": "zahodit", "label": "Kartu zahodiť", "recommended": n_recommended >= 2},
                ],
            }
        ],
    }


def _verdict() -> PipelineStatusBlock:
    return PipelineStatusBlock(
        stage="navrh", kind="verdict", summary="Previerka.", findings=FINDINGS, awaiting="manazer"
    )


@pytest.mark.asyncio
async def test_cards_on_a_question_block_are_used(db_session, monkeypatch) -> None:
    """THE case the Director hit: a real queue, labelled ``question``, must reach him as cards."""
    version, state = _seed(db_session)

    async def _question_with_cards(*args, **kwargs):
        return PipelineStatusBlock(
            stage="navrh",
            kind="question",
            summary="Päť rozhodnutí pred opravou.",
            question="Ktoré z odporúčaní beriete?",
            awaiting="manazer",
            consultation=_queue(),
        )

    monkeypatch.setattr(orchestrator, "invoke_agent_with_parse_retry", _question_with_cards)
    settled = await orchestrator._settle_for_consultation(
        db_session, state, source="auditor_upfront", verdict=_verdict()
    )

    assert settled.status == "blocked"
    assert settled.block_reason == "decision_needed", "a complete decision queue was thrown away"
    assert "rozhodni 1/1" in settled.next_action


def test_the_queue_is_found_whatever_the_block_was_called(db_session) -> None:
    """``decide`` reads the queue back through ``_latest_consultation`` — it must find it here too."""
    version, _state = _seed(db_session)
    orchestrator._record_message(
        db_session,
        version_id=version.id,
        stage="navrh",
        author=orchestrator.AI_AGENT_ROLE,
        recipient="manazer",
        kind="question",
        content="Päť rozhodnutí.",
        payload={"consultation": _queue()},
    )
    db_session.flush()

    found = orchestrator._latest_consultation(db_session, version.id)
    assert found is not None, "the queue was on record and could not be answered"
    assert found[0]["decisions"][0]["key"] == "dph"


def test_a_queue_is_validated_wherever_it_appears(db_session) -> None:
    """A queue that gets USED must be a queue that got CHECKED. The 'exactly one recommended' rule used to
    run only for ``kind='consultation'``, so cards on a question block skipped it entirely."""

    def _block(n_rec: int) -> dict:
        return {
            "stage": "navrh",
            "kind": "question",
            "summary": "s",
            "question": "q",
            "awaiting": "manazer",
            "consultation": _queue(n_rec),
        }

    assert isinstance(_validate_block(_block(1)), PipelineStatusBlock)
    bad = _validate_block(_block(2))
    assert not isinstance(bad, PipelineStatusBlock), "cards with two recommended options passed unchecked"


@pytest.mark.asyncio
async def test_an_answer_without_cards_is_reported_without_inventing_a_motive(db_session, monkeypatch) -> None:
    """No queue: still both sides, but the app states what happened instead of asserting what the agent
    'judged'. The old wording claimed the agent had disputed the findings and considered them resolved —
    an interpretation, and on the day it was found, the opposite of what the agent wrote."""
    version, state = _seed(db_session)

    async def _plain_answer(*args, **kwargs):
        return PipelineStatusBlock(
            stage="navrh",
            kind="gate_report",
            summary="Opravil som to inak, než previerka navrhuje.",
            awaiting="manazer",
        )

    monkeypatch.setattr(orchestrator, "invoke_agent_with_parse_retry", _plain_answer)
    settled = await orchestrator._settle_for_consultation(
        db_session, state, source="auditor_upfront", verdict=_verdict()
    )

    assert settled.status == "awaiting_manazer"
    # The claim about the agent's state of mind is gone; both sides are still shown.
    assert "rozporuje" not in settled.next_action
    assert "Spor" not in settled.next_action
    assert "posúď oba pohľady" in settled.next_action

    note = db_session.execute(
        select(PipelineMessage)
        .where(
            PipelineMessage.version_id == version.id,
            PipelineMessage.author == "system",
            PipelineMessage.kind == "notification",
        )
        .order_by(PipelineMessage.seq.desc())
        .limit(1)
    ).scalar_one()
    assert "rozporuje" not in note.content
    assert "posúdil ich ako už vyriešené" not in note.content
    # Both sides are still there — the finding AND what the agent actually wrote.
    assert FINDINGS[0] in note.content
    assert "Opravil som to inak" in note.content
