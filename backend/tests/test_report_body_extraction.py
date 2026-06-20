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
