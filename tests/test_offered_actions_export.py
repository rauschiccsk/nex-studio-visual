"""DEV-297 — the rows the cockpit's guard reads are what the engine offers today, not a copy that went stale.

The frontend guard renders Riadiace centrum for every state in
``frontend/src/__tests__/fixtures/offered-actions.generated.json`` and asks whether an offered action carrying the
Manažér's text has no control. A guard over yesterday's offers would pass while the screen drifts from the engine —
so the committed file must equal :func:`backend.scripts.dump_offered_actions.render` byte for byte.
"""

from __future__ import annotations

import json
from pathlib import Path

from backend.scripts import dump_offered_actions as dump

ROOT = Path(__file__).resolve().parents[1]
REGENERATE = f"poetry run python -m backend.scripts.dump_offered_actions {dump.OUTPUT}"


def test_the_committed_rows_are_what_the_engine_offers_now():
    committed = (ROOT / dump.OUTPUT).read_text(encoding="utf-8")

    assert committed == dump.render(), f"the engine offers something else now — regenerate: {REGENERATE}"


def test_the_rows_cover_the_stop_after_the_task_plan():
    """The state of 03.11.2026: Programovanie settled at the plan, „Uprav“ offered."""
    rows = json.loads(dump.render())
    wanted = ("new_version", "programovanie", "awaiting_manazer", None)
    plan_stop = [r for r in rows if (r["flow_type"], r["current_stage"], r["status"], r["resume"]) == wanted]

    assert len(plan_stop) == 1
    assert {"ask", "uprav"} <= set(plan_stop[0]["available_actions"])
