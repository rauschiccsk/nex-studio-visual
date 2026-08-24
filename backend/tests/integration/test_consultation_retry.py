"""A consultation that could not be built can be asked for AGAIN (ICCINT-25).

Found by the Director 24.08.2026 on nex-productcatalogs. The independent review found eleven places where the
documents contradict each other or stay silent, and the engine asked the AI Agent to turn them into Decision
Cards — one question at a time, with options and a recommendation. Anthropic was down; both attempts died.

The fail-open worked exactly as designed: nothing was lost, the findings were listed, the design document was
committed, the state stayed coherent. The problem was afterwards. The outage passed and there was no way to
ask for the cards again — ``navrh`` / ``awaiting_manazer`` offers ``ask`` / ``schvalit`` / ``uprav`` and
nothing else. A transient failure had permanently downgraded HOW the Manažér decides: from cards to a wall of
eleven findings under two buttons. Same shape as ICCINT-9 (one failed refresh and the session never refreshed
again), one layer up — it is not the login that degrades, it is the way a person makes decisions.

Pinned here: the retry is offered where the turn never came back, NOT where the agent answered and disputed
the findings (asking again would re-ask an answered question) and NOT past the re-consult cap.
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
from backend.services.pipeline_status import ParseFailure, PipelineStatusBlock

FINDINGS = [
    "Prázdna sadzba DPH má v dokumentoch dve rôzne odpovede.",
    "Register značiek naberá aj značky z tovaru, ktorý sa zahadzuje.",
]


def _seed(db, *, status: str = "agent_working") -> tuple[Version, PipelineState]:
    suffix = _uuid.uuid4().hex[:8]
    user = User(username=f"cc_{suffix}", email=f"cc_{suffix}@test.local", password_hash="x", role="ri")
    db.add(user)
    db.flush()
    project = Project(
        name=f"Retry {suffix}",
        slug=f"retry-{suffix}",
        type="standard",
        auth_mode="password",
        description="Consultation retry test project.",
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
        status=status,
        mode=None,
    )
    db.add(state)
    db.flush()
    return version, state


def _verdict() -> PipelineStatusBlock:
    return PipelineStatusBlock(
        stage="navrh", kind="verdict", summary="Nezávislá previerka Návrhu.", findings=FINDINGS, awaiting="manazer"
    )


def _cards() -> PipelineStatusBlock:
    return PipelineStatusBlock(
        stage="navrh",
        kind="consultation",
        summary="Dve rozhodnutia.",
        awaiting="manazer",
        consultation={
            "id": "navrh-1",
            "source": "auditor_upfront",
            "decisions": [
                {
                    "key": "dph",
                    "question": "Čo s prázdnou sadzbou DPH?",
                    "options": [
                        {"id": "priznak", "label": "Prijať s príznakom", "recommended": True},
                        {"id": "zahodit", "label": "Kartu zahodiť"},
                    ],
                }
            ],
        },
    )


# ── which failures earn a second attempt ──────────────────────────────────────


@pytest.mark.asyncio
async def test_a_turn_that_never_came_back_can_be_asked_again(db_session, monkeypatch) -> None:
    version, state = _seed(db_session)

    async def _outage(*args, **kwargs):
        return ParseFailure(reason="model unreachable")

    monkeypatch.setattr(orchestrator, "invoke_agent_with_parse_retry", _outage)
    settled = await orchestrator._settle_for_consultation(
        db_session, state, source="auditor_upfront", verdict=_verdict()
    )

    # Fail-open is unchanged: a plain stop, findings intact, never a wedged build.
    assert settled.status == "awaiting_manazer"
    pending = orchestrator.consultation_retry_pending(db_session, version.id)
    assert pending is not None, "an outage left no way back to the Decision Cards"
    source, findings = pending
    assert source == "auditor_upfront"
    assert findings == FINDINGS


@pytest.mark.asyncio
async def test_a_dispute_is_not_offered_a_retry(db_session, monkeypatch) -> None:
    """The agent ANSWERED — it judged the findings already resolved. Asking again re-asks an answered
    question; the Manažér's job here is to weigh two views, which the fallback already lays out."""
    version, state = _seed(db_session)

    async def _dispute(*args, **kwargs):
        return PipelineStatusBlock(
            stage="navrh",
            kind="gate_report",
            summary="Tieto nálezy sú zastarané, v aktuálnych dokumentoch sú vyriešené.",
            awaiting="manazer",
        )

    monkeypatch.setattr(orchestrator, "invoke_agent_with_parse_retry", _dispute)
    await orchestrator._settle_for_consultation(db_session, state, source="auditor_upfront", verdict=_verdict())

    assert orchestrator.consultation_retry_pending(db_session, version.id) is None


@pytest.mark.asyncio
async def test_the_re_consult_cap_is_not_offered_a_retry(db_session, monkeypatch) -> None:
    """The cap exists to stop consultations looping. A retry button on it would hand the loop back."""
    version, state = _seed(db_session)
    for i in range(orchestrator.AUDITOR_LOOP_MAX):
        orchestrator._record_message(
            db_session,
            version_id=version.id,
            stage="navrh",
            author=orchestrator.AI_AGENT_ROLE,
            recipient="manazer",
            kind="consultation",
            content=f"kolo {i}",
            payload={"consultation": {"id": f"c{i}", "decisions": [{"key": "k"}]}},
        )
    db_session.flush()

    async def _never(*args, **kwargs):
        raise AssertionError("the cap must settle without dispatching")

    monkeypatch.setattr(orchestrator, "invoke_agent_with_parse_retry", _never)
    settled = await orchestrator._settle_for_consultation(
        db_session, state, source="auditor_upfront", verdict=_verdict()
    )

    assert settled.status == "awaiting_manazer"
    assert orchestrator.consultation_retry_pending(db_session, version.id) is None


def test_cards_that_arrive_later_close_the_window(db_session) -> None:
    """Once the cards exist the failure is history — the button must not linger and re-ask."""
    version, _state = _seed(db_session)
    orchestrator._record_message(
        db_session,
        version_id=version.id,
        stage="navrh",
        author="system",
        recipient="manazer",
        kind="notification",
        content="Konzultáciu sa nepodarilo pripraviť.",
        payload={"consult_retry": True, "consult_source": "auditor_upfront", "auditor_findings": FINDINGS},
    )
    db_session.flush()
    assert orchestrator.consultation_retry_pending(db_session, version.id) is not None

    orchestrator._record_message(
        db_session,
        version_id=version.id,
        stage="navrh",
        author=orchestrator.AI_AGENT_ROLE,
        recipient="manazer",
        kind="consultation",
        content="Rozhodnutia.",
        payload={"consultation": {"id": "c1", "decisions": [{"key": "k"}]}},
    )
    db_session.flush()
    assert orchestrator.consultation_retry_pending(db_session, version.id) is None


# ── the button and the turn it arms ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_pressing_it_arms_one_consultation_turn(db_session, monkeypatch) -> None:
    version, state = _seed(db_session)

    async def _outage(*args, **kwargs):
        return ParseFailure(reason="model unreachable")

    monkeypatch.setattr(orchestrator, "invoke_agent_with_parse_retry", _outage)
    await orchestrator._settle_for_consultation(db_session, state, source="auditor_upfront", verdict=_verdict())
    db_session.flush()

    armed = await orchestrator.apply_action(
        db_session, version_id=version.id, action="zopakovat_konzultaciu", payload={}
    )
    assert armed.status == "agent_working"
    assert armed.retry_consultation is True
    # The thread records that the Manažér asked — the retry is his move, not a silent engine decision.
    asked = db_session.execute(
        select(PipelineMessage)
        .where(
            PipelineMessage.version_id == version.id,
            PipelineMessage.payload["consult_retry_requested"].astext == "true",
        )
        .limit(1)
    ).scalar_one_or_none()
    assert asked is not None and asked.author == "manazer"


@pytest.mark.asyncio
async def test_the_armed_turn_builds_the_cards_and_does_not_rerun_the_phase(db_session, monkeypatch) -> None:
    """The retry is ONE consultation turn. Re-running Návrh would rewrite the design document and re-run the
    review to answer a question that has already been asked."""
    version, state = _seed(db_session)
    orchestrator._record_message(
        db_session,
        version_id=version.id,
        stage="navrh",
        author="system",
        recipient="manazer",
        kind="notification",
        content="Konzultáciu sa nepodarilo pripraviť.",
        payload={"consult_retry": True, "consult_source": "auditor_upfront", "auditor_findings": FINDINGS},
    )
    state.retry_consultation = True
    state.status = "agent_working"
    db_session.flush()

    async def _navrh_must_not_run(*args, **kwargs):
        raise AssertionError("the retry re-ran the whole Návrh phase")

    seen: dict[str, object] = {}

    async def _cards_now(*args, **kwargs):
        seen["prompt"] = kwargs["prompt"]
        return _cards()

    monkeypatch.setattr(orchestrator, "_run_navrh_round", _navrh_must_not_run)
    monkeypatch.setattr(orchestrator, "invoke_agent_with_parse_retry", _cards_now)

    settled = await orchestrator.run_dispatch(db_session, version.id)

    assert settled is not None
    assert settled.status == "blocked" and settled.block_reason == "decision_needed"
    # The recorded findings went back into the brief — the retry consults about the SAME holes.
    assert FINDINGS[0] in str(seen["prompt"])
    # The flag is spent: it may never survive a turn and re-route the next one.
    assert settled.retry_consultation is False


@pytest.mark.asyncio
async def test_pressing_it_with_nothing_to_retry_is_refused(db_session) -> None:
    version, _state = _seed(db_session, status="awaiting_manazer")
    with pytest.raises(orchestrator.OrchestratorError):
        await orchestrator.apply_action(db_session, version_id=version.id, action="zopakovat_konzultaciu", payload={})


@pytest.mark.asyncio
async def test_a_window_that_closed_between_click_and_dispatch_settles_honestly(db_session, monkeypatch) -> None:
    """The flag is set, but by dispatch time the cards exist. Spend no turn; say so and hand back."""
    version, state = _seed(db_session)
    orchestrator._record_message(
        db_session,
        version_id=version.id,
        stage="navrh",
        author=orchestrator.AI_AGENT_ROLE,
        recipient="manazer",
        kind="consultation",
        content="Rozhodnutia.",
        payload={"consultation": {"id": "c1", "decisions": [{"key": "k"}]}},
    )
    state.retry_consultation = True
    state.status = "agent_working"
    db_session.flush()

    async def _never(*args, **kwargs):
        raise AssertionError("spent a turn re-asking something already answered")

    monkeypatch.setattr(orchestrator, "invoke_agent_with_parse_retry", _never)
    monkeypatch.setattr(orchestrator, "_run_navrh_round", _never)

    settled = await orchestrator.run_dispatch(db_session, version.id)

    assert settled is not None
    assert settled.status == "awaiting_manazer"
    assert settled.retry_consultation is False
