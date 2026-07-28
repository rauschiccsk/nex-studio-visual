"""Blocked-state recovery actions (self-sufficiency kernel, audit Theme 1).

When a build is BLOCKED on an error/question the Manažér must recover from (agent_error / system_error /
parse_exhaustion / agent_question — framework_issue and decision_needed are handled elsewhere), the offerable
set is ONLY the recovery verbs {ask, uprav, answer}. The phase-ADVANCE verbs (approve_spec / zostav_plan /
schvalit / verdict / …) must NOT be offered at a blocked state — advancing past an unresolved error is a
footgun (e.g. "Schváliť špecifikáciu" appearing right after a parse failure). The settled `awaiting_manazer`
path keeps the advance body unchanged.

`determine_available_actions` is a pure (state-only) function, so these construct an in-memory PipelineState.

Also pins the OFFER↔EXECUTE structural invariant (audit P0, 2026-07-28): every verb this function can OFFER
must be a member of `orchestrator._ACTIONS`, because `apply_action` rejects anything outside that set on its
first line. `nahlasit_znova` was offered as the SOLE action on a `framework_issue` block while never being
registered — one button on an otherwise locked screen, and pressing it raised "Unknown action". A dead end
with no way out is exactly what these assertions exist to make impossible.
"""

from __future__ import annotations

import ast
import inspect

from backend.db.models.pipeline import BLOCK_REASON_VALUES, STAGE_VALUES, STATUS_VALUES, PipelineState
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


# ── OFFER ⊆ EXECUTE: no offered verb may be un-executable (audit P0, 2026-07-28) ──


def _every_offerable_verb() -> set[str]:
    """The union of `determine_available_actions` over EVERY (stage, status, block_reason) the DB CHECK
    constraints allow — it is a pure function of exactly those three fields, so this is exhaustive."""
    offered: set[str] = set()
    for stage in STAGE_VALUES:
        for status in STATUS_VALUES:
            for reason in (None, *BLOCK_REASON_VALUES):
                offered |= orchestrator.determine_available_actions(_state(stage, status, reason))
    return offered


def test_every_offerable_action_is_executable() -> None:
    # The behavioural invariant: whatever the board can OFFER, `apply_action` must ACCEPT (its first line
    # raises "Unknown action" for anything outside `_ACTIONS`). An offered-but-unregistered verb is a button
    # that can only error — and on a framework_issue block it is the ONLY button there is.
    offered = _every_offerable_verb()
    assert offered, "sanity: the enumeration must actually reach some offers"
    assert offered <= orchestrator._ACTIONS, f"offered but not executable: {sorted(offered - orchestrator._ACTIONS)}"
    # The regression that motivated this: the framework_issue block's sole action.
    assert "nahlasit_znova" in offered
    assert "nahlasit_znova" in orchestrator._ACTIONS


def test_every_action_literal_in_the_source_is_executable() -> None:
    """The static twin of the test above: read the verb literals straight out of
    `determine_available_actions`'s source (set displays + ``actions.add``/``update`` calls) and require the
    same membership. This still fires for a verb hidden behind a condition on some field the exhaustive
    (stage, status, block_reason) sweep does not vary."""
    tree = ast.parse(inspect.getsource(orchestrator.determine_available_actions).lstrip())
    literals: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Set):
            literals |= {e.value for e in node.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)}
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "add":
            literals |= {a.value for a in node.args if isinstance(a, ast.Constant) and isinstance(a.value, str)}
    assert literals, "sanity: the source scan must find verb literals"
    assert literals <= orchestrator._ACTIONS, f"offered but not executable: {sorted(literals - orchestrator._ACTIONS)}"
