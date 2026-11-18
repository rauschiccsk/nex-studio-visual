"""DEV-411 — the Staviteľ's report of a cockpit bug stops the build in any task, also a fix after Verifikácia; once Dedo
released it, the check can run again without a fix — and the card after the last round offers that too.

16.11.2026 NEX Inbox 1.10.4: five fix tasks after Verifikácia reported the cockpit's bug (``framework_issue``); the
task loop took each for a finished task, refused it for having no commit and ran the next round — nothing reached Dedo
for ~1.5 h, and after the cockpit was fixed the card had no way to check again without a fix. Director 16.11.2026:
„Založ tiket ale neimplementuj.“; 18.11.2026 in the batch with DEV-425.
"""

from __future__ import annotations

from sqlalchemy import select

from backend.api.routes import pipeline as pipeline_routes
from backend.db.models.pipeline import PipelineState
from backend.services import orchestrator
from backend.services.pipeline_status import PipelineStatusBlock
from tests.test_orchestrator_v2_programovanie import (
    _make_version,
    _no_baseline_git,
    _seed_programovanie,
    _seed_tasks,
    _stub_turns,
    _tasks,
)
from tests.test_orchestrator_v2_verifikacia import _make_version as _make_verifikacia_version
from tests.test_orchestrator_v2_verifikacia import (
    _msgs,
    _seed_done_tasks,
    _seed_verifikacia,
    _stub_auditor,
    _stub_smoke,
    _verdict_fail,
)

REPORT = "Skúška nasadenia padá na prihlasovacích údajoch, ktoré dodáva kokpit; v aplikácii nie je čo opraviť."


def _report() -> PipelineStatusBlock:
    return PipelineStatusBlock(
        stage="programovanie", kind="framework_issue", summary="chyba kokpitu", awaiting="manazer", question=REPORT
    )


async def test_a_cockpit_bug_reported_in_a_task_stops_the_build_for_dedo(db_session, monkeypatch) -> None:
    version, project = _make_version(db_session)
    _seed_programovanie(db_session, version.id)
    _seed_tasks(db_session, version, project, ["Oprava po Verifikácii — 1. kolo"])
    _no_baseline_git(monkeypatch)
    calls = _stub_turns(monkeypatch, [_report()])
    delivered: list[str] = []

    async def _escalate(*_a, **kw):
        delivered.append(kw.get("message") or kw.get("dedo_message") or str(_a))

    monkeypatch.setattr(orchestrator.dedo_escalation, "escalate_to_dedo", _escalate)

    state = await orchestrator.run_dispatch(db_session, version.id)

    assert (state.status, state.block_reason) == ("blocked", "framework_issue")
    assert len(calls) == 1, "no further round after the report"
    assert delivered and REPORT in " ".join(delivered)
    assert [t.status for t in _tasks(db_session, version.id)] != ["done"], "the task is not taken for finished"


def _released_fix_loop(db_session, monkeypatch):
    """A build after the round cap's „Usmerniť opravu“, stopped on a cockpit bug in the fix and released by Dedo."""
    version, project = _make_verifikacia_version(db_session, project_dial="plna")
    _seed_verifikacia(db_session, version.id, iteration=orchestrator.AUDITOR_LOOP_MAX)
    _seed_done_tasks(db_session, version, project, ["T1"])
    _stub_auditor(monkeypatch, _verdict_fail(["skúška nasadenia padá"]))
    _stub_smoke(monkeypatch, acc=(False, "still failing", False))
    monkeypatch.setattr(orchestrator, "_begin_dispatch", lambda db, st: None)
    return version


async def _to_released(db_session, version) -> PipelineState:
    await orchestrator.run_dispatch(db_session, version.id)
    await orchestrator.apply_action(
        db_session,
        version_id=version.id,
        action="decide",
        payload={"decision_key": "verifikacia_fail_next", "option_id": "guide_fix", "free_text": "Oprav to."},
    )
    state = db_session.execute(select(PipelineState).where(PipelineState.version_id == version.id)).scalar_one()
    state.status = "awaiting_manazer"
    state.block_reason = None
    state.resume_after_framework_fix = True
    db_session.flush()
    return state


async def test_released_from_a_cockpit_bug_in_the_fix_the_check_runs_again_without_a_fix(db_session, monkeypatch):
    version = _released_fix_loop(db_session, monkeypatch)
    state = await _to_released(db_session, version)
    iteration = state.iteration

    assert orchestrator.released_in_verifikacia_fix_loop(db_session, version.id, state)
    state.resume_after_framework_fix = False
    assert not orchestrator.released_in_verifikacia_fix_loop(db_session, version.id, state), "only after Dedo's release"
    state.resume_after_framework_fix = True
    state = await orchestrator.apply_action(db_session, version_id=version.id, action="overit_bez_opravy", payload={})

    assert state.current_stage == "verifikacia" and state.is_regate
    assert state.iteration == iteration, "no fix ran — no round spent"


async def test_the_board_offers_both_after_the_release(db_session, monkeypatch) -> None:
    version = _released_fix_loop(db_session, monkeypatch)
    await _to_released(db_session, version)

    board = pipeline_routes._board(db_session, version.id)

    assert {"pokracovat", "overit_bez_opravy"} <= set(board.available_actions)


def test_a_release_outside_a_fix_after_verifikacia_offers_only_pokracovat() -> None:
    state = PipelineState(current_stage="navrh", status="awaiting_manazer", resume_after_framework_fix=True)

    assert not orchestrator.released_in_verifikacia_fix_loop(None, None, state)  # type: ignore[arg-type]


async def test_the_card_after_the_last_round_checks_again_without_a_fix(db_session, monkeypatch) -> None:
    version = _released_fix_loop(db_session, monkeypatch)
    state = await orchestrator.run_dispatch(db_session, version.id)
    iteration = state.iteration
    card = [m for m in _msgs(db_session, version.id) if m.kind == "consultation"][-1]
    options = {o["id"] for o in card.payload["consultation"]["decisions"][0]["options"]}
    assert "overit_bez_opravy" in options

    state = await orchestrator.apply_action(
        db_session,
        version_id=version.id,
        action="decide",
        payload={"decision_key": "verifikacia_fail_next", "option_id": "overit_bez_opravy"},
    )

    assert state.current_stage == "verifikacia" and state.is_regate and state.block_reason is None
    assert state.iteration == iteration
