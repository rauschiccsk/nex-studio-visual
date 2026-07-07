"""Unit tests for the ``framework_issue`` agent-signal contract (Director observation #6).

The AI Agent escalates a problem it CANNOT fix (because the fix needs a change to NEX Studio ITSELF, §15)
by emitting a ``<<<PIPELINE_STATUS>>>`` block with ``kind='framework_issue'`` — MIRRORS the
``decision_needed``/``agent_question`` mechanism (the message rides in ``question``). These pin the parser
contract deterministically (no DB): a valid escalation parses, a message-less one is a ParseFailure, and
``framework_issue`` is a recognised kind but NOT downgraded to a plain "question".
"""

from __future__ import annotations

import json

from backend.services import pipeline_status
from backend.services.pipeline_status import BLOCK_KINDS, ParseFailure, PipelineStatusBlock, parse_status_block


def _fence(obj: dict) -> str:
    return f"<<<PIPELINE_STATUS>>>\n{json.dumps(obj)}\n<<<END_PIPELINE_STATUS>>>"


def test_framework_issue_is_a_recognised_block_kind() -> None:
    assert "framework_issue" in BLOCK_KINDS
    # It is deliberately NOT a "question kind" — else the orchestrator would record it as a plain question
    # message instead of routing it through the Dedo escalation settle.
    assert "framework_issue" not in pipeline_status._QUESTION_KINDS


def test_valid_framework_issue_block_parses() -> None:
    dedo_msg = "Build engine nevie spustiť smoke test — chýba docker socket mount. Treba upraviť NEX Studio."
    result = parse_status_block(
        _fence(
            {
                "stage": "priprava",
                "kind": "framework_issue",
                "summary": "Eskalujem Dedovi — potrebná zmena NEX Studia.",
                "awaiting": "manazer",
                "question": dedo_msg,
            }
        )
    )
    assert isinstance(result, PipelineStatusBlock)
    assert result.kind == "framework_issue"
    assert result.question == dedo_msg


def test_framework_issue_without_message_is_parse_failure() -> None:
    result = parse_status_block(
        _fence(
            {
                "stage": "priprava",
                "kind": "framework_issue",
                "summary": "Eskalujem Dedovi.",
                "awaiting": "manazer",
                # no 'question' → no message for Dedo → a deterministic ParseFailure (never inferred)
            }
        )
    )
    assert isinstance(result, ParseFailure)
    assert "framework_issue" in result.reason and "question" in result.reason


def test_framework_issue_blank_message_is_parse_failure() -> None:
    result = parse_status_block(
        _fence(
            {
                "stage": "programovanie",
                "kind": "framework_issue",
                "summary": "x",
                "awaiting": "manazer",
                "question": "   \n  ",
            }
        )
    )
    assert isinstance(result, ParseFailure)
