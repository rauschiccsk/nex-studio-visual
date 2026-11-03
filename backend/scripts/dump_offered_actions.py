"""What the engine offers the Manažér in every state a build can be in — the source of the cockpit's guard (DEV-297).

03.11.2026 the build of NEX Manager 1.5.0 stopped at its task plan, and the cockpit, the agent and Poradca all told
the Manažér to press „Uprav“ — the engine offered it, but no control on the screen sent it, so Poradca's instruction
for the plan went out as a question. The frontend guard
(``frontend/src/__tests__/pages/test_RiadiaceCentrum_every_text_action_has_a_control.test.tsx``) renders Riadiace
centrum for every row written here and asks whether an offered action carrying the Manažér's text was left without
a control. The rows come from :func:`backend.services.orchestrator.determine_available_actions` itself, never from a
copy; ``tests/test_offered_actions_export.py`` fails when the committed file no longer matches the engine.

Usage::

    poetry run python -m backend.scripts.dump_offered_actions \
        frontend/src/__tests__/fixtures/offered-actions.generated.json

With no argument (or ``-``) the rows are written to stdout.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Optional

from backend.db.models.pipeline import (
    BLOCK_REASON_VALUES,
    FLOW_TYPE_VALUES,
    STAGE_VALUES,
    STATUS_VALUES,
    PipelineState,
)
from backend.services.orchestrator import determine_available_actions

#: Where the frontend guard reads the rows (relative to the repository root).
OUTPUT = "frontend/src/__tests__/fixtures/offered-actions.generated.json"


def _state(
    *,
    flow_type: str,
    stage: str,
    status: str,
    block_reason: Optional[str],
    resume: Optional[str],
    pause_reason: Optional[str],
) -> PipelineState:
    state = PipelineState(flow_type=flow_type, current_stage=stage)
    # Status first: its listener clears ``block_reason`` whenever the status is not ``blocked``.
    state.status = status
    state.block_reason = block_reason
    state.pause_reason = pause_reason
    state.resume_after_framework_fix = resume == "framework_fix"
    state.resume_after_restart = resume == "restart"
    return state


def rows() -> list[dict[str, Any]]:
    """Every combination of the state fields the offered set reads; a finished build is ``done`` in both."""
    out: list[dict[str, Any]] = []
    for flow_type in FLOW_TYPE_VALUES:
        for stage in STAGE_VALUES:
            for status in STATUS_VALUES:
                if (stage == "done") != (status == "done"):
                    continue
                for block_reason in BLOCK_REASON_VALUES if status == "blocked" else (None,):
                    for resume in (None, "framework_fix", "restart") if status == "awaiting_manazer" else (None,):
                        for pause_reason in (None, "manazer") if status == "agent_working" else (None,):
                            state = _state(
                                flow_type=flow_type,
                                stage=stage,
                                status=status,
                                block_reason=block_reason,
                                resume=resume,
                                pause_reason=pause_reason,
                            )
                            out.append(
                                {
                                    "flow_type": flow_type,
                                    "current_stage": stage,
                                    "status": status,
                                    "block_reason": block_reason,
                                    "resume": resume,
                                    "pause_reason": pause_reason,
                                    "available_actions": sorted(determine_available_actions(state)),
                                }
                            )
    return out


def render() -> str:
    return json.dumps(rows(), indent=1, ensure_ascii=False) + "\n"


def main(argv: list[str]) -> None:
    out = argv[1] if len(argv) > 1 else "-"
    text = render()
    if out == "-":
        sys.stdout.write(text)
    else:
        Path(out).write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main(sys.argv)
