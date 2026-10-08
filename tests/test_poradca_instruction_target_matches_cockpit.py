"""The server and the screen agree on where Poradca's instruction can go (DEV-26).

Two rules decide the same thing from two sides. The server greys „Vložiť do Riadiaceho centra" out when no box
of Riadiace centrum takes the text (``_instruction_target``); the screen decides WHICH box takes it
(``BLOCKED_INPUT_OWNER`` in ``frontend/src/components/riadiace/blockRecovery.ts``). If they drift, the button
is offered and the instruction lands nowhere — the Director's „the screen blinked and nothing happened"
(DEV-22) — or a box is there and the button refuses. Read from both sources, never copied into this test.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from backend.api.routes.poradca import _instruction_target
from backend.db.models.pipeline import BLOCK_REASON_VALUES, PipelineState

ROOT = Path(__file__).resolve().parents[1]
SCREEN_RULE = ROOT / "frontend" / "src" / "components" / "riadiace" / "blockRecovery.ts"


def _screen_owners() -> dict[str, str | None]:
    text = SCREEN_RULE.read_text(encoding="utf-8")
    found = re.search(r"BLOCKED_INPUT_OWNER: Record<BlockReason, InputOwner \| null> = \{(.*?)\};", text, re.S)
    assert found, "the screen's rule BLOCKED_INPUT_OWNER is gone or renamed — this guard must follow it"
    pairs = re.findall(r"(\w+):\s*(null|\"\w+\")", found.group(1))
    return {key: None if value == "null" else value.strip('"') for key, value in pairs}


def test_the_screen_rule_names_every_reason_the_engine_knows() -> None:
    assert set(_screen_owners()) == set(BLOCK_REASON_VALUES)


@pytest.mark.parametrize("reason", BLOCK_REASON_VALUES)
def test_poradca_offers_the_insert_exactly_where_a_box_takes_it(reason: str) -> None:
    state = PipelineState(current_stage="navrh", status="blocked", block_reason=reason)
    is_open, why = _instruction_target(state)
    assert is_open == (_screen_owners()[reason] is not None), (reason, why)
