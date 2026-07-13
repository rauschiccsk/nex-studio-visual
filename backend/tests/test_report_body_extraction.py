"""Legible-cockpit-output fix — :func:`backend.services.pipeline_status.extract_report_body`.

The agent writes a full human-readable markdown report and THEN the machine status fence; the recorded
message keeps only the one-line ``summary`` as ``content``. ``extract_report_body`` recovers the rich
report body (everything minus the sentinel fences) so the cockpit bubble can render it (payload['report']).
"""

from __future__ import annotations

from backend.services.pipeline_status import extract_report_body

_REPORT = "## Dokončené: export endpoint\n\n- Pridaný `/orders/export` router\n- Testy zelené ✅"
_STATUS_FENCE = (
    '<<<PIPELINE_STATUS>>>\n{"stage": "build", "kind": "done", "summary": "Hotovo", '
    '"awaiting": "none"}\n<<<END_PIPELINE_STATUS>>>'
)


def test_extracts_report_before_status_fence() -> None:
    body = extract_report_body(f"{_REPORT}\n\n{_STATUS_FENCE}\n")
    assert body == _REPORT
    # the machine block never leaks into the human-facing report
    assert "PIPELINE_STATUS" not in body
    assert "summary" not in body


def test_empty_when_only_a_fence() -> None:
    assert extract_report_body(f"{_STATUS_FENCE}\n") == ""


def test_strips_task_plan_fence_too() -> None:
    text = f"{_REPORT}\n\n<<<TASK_PLAN_JSON>>>\n{{}}\n<<<END_TASK_PLAN_JSON>>>"
    body = extract_report_body(text)
    assert body == _REPORT
    assert "TASK_PLAN_JSON" not in body


def test_handles_empty_and_none_safely() -> None:
    assert extract_report_body("") == ""
    assert extract_report_body(None) == ""  # type: ignore[arg-type]


# ── Bare (UNfenced) status block (nex-studio-visual crash-test 2026-07-13) ────────────────────────────
# The model sometimes echoes the status block as raw JSON in its text — no ``<<<PIPELINE_STATUS>>>`` fence —
# alongside the grammar-constrained structured_output the orchestrator actually parses. The fence regex can't
# catch it, so the raw ``{"stage":…,"question":…}`` leaked into payload['report'] and rendered as a raw-JSON
# cockpit bubble. extract_report_body must strip a bare status block too.
_BARE_BLOCK = (
    '{"stage": "priprava", "kind": "question", '
    '"summary": "Načítal som Zadanie a otváram konzultáciu.", '
    '"awaiting": "manazer", "question": "Otázka 1 z ~6 — ako to chápať?"}'
)


def test_strips_a_bare_unfenced_status_block() -> None:
    # A pure-block turn (the observed Príprava question) → the report is empty, not the raw JSON.
    assert extract_report_body(_BARE_BLOCK) == ""


def test_strips_a_bare_block_after_prose() -> None:
    body = extract_report_body(f"{_REPORT}\n\n{_BARE_BLOCK}")
    assert body == _REPORT
    assert "stage" not in body and "question" not in body


def test_keeps_non_status_json_untouched() -> None:
    # A small JSON example inside a real report is NOT a status block (lacks the signature keys) — kept.
    text = 'Príklad konfigurácie: {"port": 8080, "host": "localhost"}'
    assert extract_report_body(text) == text
