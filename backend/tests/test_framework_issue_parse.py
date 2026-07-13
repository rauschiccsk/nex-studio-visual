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


# ── Bare (UNfenced) status block fallback (nex-studio-visual crash-test 2026-07-13) ───────────────────
# The model sometimes emits the status block as raw JSON WITHOUT the <<<PIPELINE_STATUS>>> fence, and when
# the CLI also produces no structured_output, parse_status_block was the last resort — and it died with
# "no PIPELINE_STATUS block found" → parse_exhaustion on a turn that had actually done the work. It now
# recovers a bare status block, validated the SAME way as a fenced one.
def test_bare_unfenced_status_block_parses() -> None:
    # A question block emitted as bare JSON (no fence) is recovered + validated like a fenced one.
    bare = json.dumps(
        {
            "stage": "priprava",
            "kind": "question",
            "summary": "Otváram konzultáciu.",
            "awaiting": "manazer",
            "question": "Otázka 1 z ~6 — ako to chápať?",
        }
    )
    result = parse_status_block(bare)
    assert isinstance(result, PipelineStatusBlock)
    assert result.kind == "question"
    assert result.stage == "priprava"


def test_bare_block_after_prose_parses() -> None:
    # Prose report + a trailing bare block (no fence) still recovers the block.
    prose = "## Zhrnutie\n\n- pripravené\n\n"
    bare = json.dumps(
        {"stage": "priprava", "kind": "question", "summary": "x", "awaiting": "manazer", "question": "Prečo?"}
    )
    result = parse_status_block(prose + bare)
    assert isinstance(result, PipelineStatusBlock)
    assert result.kind == "question"


def test_prose_with_no_block_still_fails() -> None:
    # Plain prose with no status block (fenced OR bare) is still a deterministic ParseFailure — the fallback
    # never fabricates a block.
    result = parse_status_block('Len text, žiadny stavový blok. {"port": 8080}')
    assert isinstance(result, ParseFailure)
    assert "no PIPELINE_STATUS block found" in result.reason


# ── Návrh gate_report no longer requires a plan (nex-studio-visual, Director 2026-07-13) ───────────────
# The task plan moved OUT of Návrh — it is built at Programovanie start (from the final design + Vizuál
# changes). So a Návrh gate_report is legitimately plan-less and MUST parse (the old plan-required guard is
# gone). Fenced OR bare (the nex-weblist shape).
_NAVRH_GATE_NO_PLAN = {
    "stage": "navrh",
    "kind": "gate_report",
    "summary": "Návrhový dokument je hotový.",
    "awaiting": "manazer",
    "deliverables": ["docs/specs/versions/v0.1.0/design.md"],
}


def test_navrh_gate_report_without_plan_parses() -> None:
    fenced = parse_status_block(_fence(_NAVRH_GATE_NO_PLAN))
    bare = parse_status_block(json.dumps(_NAVRH_GATE_NO_PLAN))
    assert isinstance(fenced, PipelineStatusBlock) and fenced.kind == "gate_report"
    assert isinstance(bare, PipelineStatusBlock) and bare.kind == "gate_report"
