"""DEV-487 — of the Auditor's points, only those that need the Manažér's decision become cards; a technical correction
with one right answer the Staviteľ works in himself, and the Manažér sees it in the list „Zapracujem bez otázky“.

21.11.2026, NEX Inbox 1.11.0: the Auditor wrote „… vraciam ich na dve krátke úpravy … Ostatných 11 nálezov sú
poznámky“ — and the cockpit made 13 cards for the Manažér, because the brief told the Staviteľ „KAŽDÝ bod ako VLASTNÉ
rozhodnutie (nezlučuj a nevynechaj žiadny)“. Director: „prekontroluj prosím kokpit v ktorom robíme nex-inbox, podľa
mňa príliš veľa problémov tam je.“ — and to the proposal: „Súhlasím“.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from backend.db.models.pipeline import PipelineMessage, PipelineState
from backend.services import batch_review, orchestrator
from backend.services.pipeline_status import ConsultationBlock, PipelineStatusBlock
from tests.test_orchestrator_v2_consultation import _seed_consultation
from tests.test_the_builders_questions_go_to_poradca_at_once import _reviewed

CORRECTIONS = ["Doplniť skúšku, že skúška na vzorke nič nedoručí.", "Zladiť typ identifikátora záznamu skúšok."]


def _verdict() -> PipelineStatusBlock:
    return PipelineStatusBlock(
        stage="navrh", kind="verdict", summary="Previerka.", findings=["F1", "F2"], verdict=False, awaiting="manazer"
    )


def _only_corrections() -> PipelineStatusBlock:
    return PipelineStatusBlock(
        stage="navrh",
        kind="consultation",
        summary="Nič na rozhodnutie.",
        awaiting="manazer",
        consultation={"id": "c2", "source": "auditor_upfront", "decisions": [], "applied": CORRECTIONS},
    )


def _with_corrections(db, version) -> None:
    """The open consultation of ``version`` carries the corrections beside its cards."""
    message = (
        db.execute(
            select(PipelineMessage)
            .where(PipelineMessage.version_id == version.id, PipelineMessage.kind == "consultation")
            .order_by(PipelineMessage.seq.desc())
        )
        .scalars()
        .first()
    )
    payload = dict(message.payload)
    payload["consultation"] = {**payload["consultation"], "applied": CORRECTIONS}
    message.payload = payload
    db.flush()


def _latest_notice(db, version) -> PipelineMessage:
    return (
        db.execute(
            select(PipelineMessage)
            .where(PipelineMessage.version_id == version.id, PipelineMessage.kind == "notification")
            .order_by(PipelineMessage.seq.desc())
        )
        .scalars()
        .first()
    )


def test_the_staviter_sorts_the_auditors_points_into_cards_and_corrections():
    text = orchestrator._consultation_directive(None, None, source="auditor_upfront", findings=["F"], proposed_fix=None)

    assert "KAŽDÝ bod previerky patrí práve do jedného z nich" in text
    assert "„Zapracujem bez otázky“ a Auditor ju pri ďalšej previerke overí" in text
    assert "KAŽDÝ bod ako VLASTNÉ rozhodnutie" not in text
    assert "do `decisions[]` (karta pre Manažéra) LEN bod, ktorý potrebuje JEHO rozhodnutie" in text
    assert "žiadny nevynechaj ani nezlúč" in text, "every point still goes somewhere — a card or the list"


def test_other_consultations_keep_one_card_per_point():
    text = orchestrator._consultation_directive(None, None, source="vizual_conflict", findings=["F"], proposed_fix=None)

    assert "KAŽDÝ bod ako VLASTNÉ rozhodnutie (nezlučuj a nevynechaj žiadny)" in text
    assert "applied" not in text


def test_a_consultation_may_carry_only_corrections_but_never_nothing():
    assert ConsultationBlock(id="c", decisions=[], applied=["Oprava A", "  "]).applied == ["Oprava A"]

    with pytest.raises(ValueError, match="a decision for the Manažér or a correction"):
        ConsultationBlock(id="c", decisions=[], applied=[" "])


async def test_the_corrections_go_to_the_staviter_with_the_managers_decisions(db_session):
    version = _seed_consultation(db_session)
    _with_corrections(db_session, version)

    for key in ("d1", "d2"):
        await orchestrator.apply_action(
            db_session, version_id=version.id, action="decide", payload={"decision_key": key, "option_id": "a"}
        )
    directive = orchestrator.dispatch_directive(db_session, version.id, "decide", {}, "navrh")

    assert directive.startswith("Manažér rozhodol v konzultácii:\n- Otázka 1? → Voľba A1")
    assert f"{orchestrator.APPLIED_HEAD}\n- {CORRECTIONS[0]}\n- {CORRECTIONS[1]}\n" in directive


async def test_when_nothing_needs_a_decision_the_corrections_are_worked_in_at_once(db_session, monkeypatch):
    version = _seed_consultation(db_session, status="agent_working")
    state = db_session.query(PipelineState).filter_by(version_id=version.id).one()

    async def _staviter(db, **_kw):
        return _only_corrections()

    reworked: list[str] = []

    async def _rework(directive: str) -> PipelineState:
        reworked.append(directive)
        return state

    monkeypatch.setattr(orchestrator, "invoke_agent_with_parse_retry", _staviter)
    settled = await orchestrator._settle_for_consultation(
        db_session, state, source="auditor_upfront", verdict=_verdict(), apply_now=_rework
    )

    assert settled is state and (state.status, state.block_reason) != ("blocked", "decision_needed")
    assert len(reworked) == 1
    assert f"{orchestrator.APPLIED_HEAD}\n- {CORRECTIONS[0]}\n- {CORRECTIONS[1]}\n" in reworked[0]
    assert reworked[0].endswith("Teraz PREPRACUJ Špecifikáciu/Návrh podľa nich a uzavri fázu (gate_report).")
    notice = _latest_notice(db_session, version)
    assert notice.content == (
        "Previerka Auditora nenašla nič, čo by potrebovalo tvoje rozhodnutie — Staviteľ zapracuje 2 technické opravy "
        f"sám a Auditor ich pri ďalšej previerke overí:\n\n- {CORRECTIONS[0]}\n- {CORRECTIONS[1]}"
    )


async def test_without_a_way_to_rework_an_empty_queue_never_blocks_on_a_decision(db_session, monkeypatch):
    version = _seed_consultation(db_session, status="agent_working")
    state = db_session.query(PipelineState).filter_by(version_id=version.id).one()

    async def _staviter(db, **_kw):
        return _only_corrections()

    monkeypatch.setattr(orchestrator, "invoke_agent_with_parse_retry", _staviter)
    await orchestrator._settle_for_consultation(db_session, state, source="auditor_upfront", verdict=_verdict())

    assert (state.status, state.block_reason) != ("blocked", "decision_needed")


async def test_the_notice_of_a_review_says_what_comes_and_no_longer_one_by_one(db_session, monkeypatch):
    version = _seed_consultation(db_session, status="agent_working")
    state = db_session.query(PipelineState).filter_by(version_id=version.id).one()

    async def _auditor(db, **_kw):
        return _verdict()

    monkeypatch.setattr(orchestrator, "invoke_agent_with_parse_retry", _auditor)
    monkeypatch.setattr(orchestrator, "_auditor_upfront_directive", lambda db, version_id, narrowed_to=None: "brief")
    await orchestrator._run_auditor_upfront_review(db_session, state)

    notice = _latest_notice(db_session, version).content
    assert "po jednom" not in notice
    assert "Staviteľ z nálezov pripraví karty na tvoje rozhodnutie a technické opravy zapracuje sám" in notice


def test_the_summary_shows_the_corrections_beside_the_cards(db_session):
    version = _seed_consultation(db_session)
    _with_corrections(db_session, version)
    _reviewed(db_session, version)

    assert batch_review.summary(db_session, version.id)["applied"] == CORRECTIONS
