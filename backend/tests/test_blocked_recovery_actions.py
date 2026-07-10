"""Blocked-state recovery actions (self-sufficiency kernel, audit Theme 1).

When a build is BLOCKED on an error/question the Manažér must recover from (agent_error / system_error /
parse_exhaustion / agent_question — framework_issue and decision_needed are handled elsewhere), the offerable
set is ONLY the recovery verbs {ask, uprav, answer}. The phase-ADVANCE verbs (approve_spec / zostav_plan /
schvalit / verdict / …) must NOT be offered at a blocked state — advancing past an unresolved error is a
footgun (e.g. "Schváliť špecifikáciu" appearing right after a parse failure). The settled `awaiting_manazer`
path keeps the advance body unchanged.

`determine_available_actions` is a pure (state-only) function, so these construct an in-memory PipelineState.
"""

from __future__ import annotations

from backend.db.models.pipeline import PipelineState
from backend.services import orchestrator


def _state(stage: str, status: str, reason: str | None = None) -> PipelineState:
    return PipelineState(
        current_stage=stage,
        status=status,
        block_reason=reason,
        current_actor="ai_agent",
        flow_type="new_version",
    )


def test_blocked_error_at_priprava_offers_only_recovery() -> None:
    actions = orchestrator.determine_available_actions(_state("priprava", "blocked", "system_error"))
    assert actions == {"ask", "uprav", "answer"}
    # Critically NOT the premature "Schváliť špecifikáciu" (nor any other advance verb) after a failure.
    assert "approve_spec" not in actions
    assert "zostav_plan" not in actions


def test_blocked_error_at_navrh_does_not_offer_schvalit() -> None:
    actions = orchestrator.determine_available_actions(_state("navrh", "blocked", "agent_error"))
    assert "schvalit" not in actions
    assert {"ask", "uprav", "answer"} == actions


def test_blocked_question_offers_answer_and_retry() -> None:
    actions = orchestrator.determine_available_actions(_state("programovanie", "blocked", "agent_question"))
    assert "answer" in actions and "uprav" in actions


def test_parse_exhaustion_at_verifikacia_no_verdict() -> None:
    actions = orchestrator.determine_available_actions(_state("verifikacia", "blocked", "parse_exhaustion"))
    assert actions == {"ask", "uprav", "answer"}
    assert "verdict" not in actions


def test_awaiting_manazer_priprava_still_offers_approve_spec() -> None:
    # Guardrail — the SETTLED path is unchanged: a normal awaiting_manazer at Príprava still offers the
    # phase-advance body (approve_spec + the STEP verbs), which the board route then DB-post-filters.
    actions = orchestrator.determine_available_actions(_state("priprava", "awaiting_manazer"))
    assert "approve_spec" in actions
    assert {"ask", "uprav"} <= actions


def test_awaiting_manazer_navrh_still_offers_schvalit() -> None:
    actions = orchestrator.determine_available_actions(_state("navrh", "awaiting_manazer"))
    assert "schvalit" in actions
