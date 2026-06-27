"""R2-c (v0.7.0) — BE↔FE parity for the executable-Coordinator-actions set.

v1-SUPERSEDED (CR-V2-017): the BE constant ``_EXECUTABLE_COORDINATOR_ACTIONS`` was excised with the v1
5-role Coordinator engine, and the FE ``ExchangePanel.tsx`` it parsed is re-authored in CR-V2-021 (the
4-phase Vývoj board has no Coordinator executable-actions surface). The whole parity guard is therefore
obsolete; module-skipped (mirrors the other v1 engine test modules) so the deleted-symbol import no longer
breaks collection. There is no v2 successor — the v2 action surface is the schvaľovacie body verbs.
"""

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.skip(reason="v1 engine behaviour — Coordinator executable-actions set removed in CR-V2-017")

_REPO_ROOT = Path(__file__).resolve().parents[1]
_EXCHANGE_PANEL = _REPO_ROOT / "frontend" / "src" / "components" / "cockpit" / "ExchangePanel.tsx"
_EXECUTABLE_COORDINATOR_ACTIONS: frozenset[str] = frozenset()  # v1 stub — module is skipped

# The FE Set literal: `const EXECUTABLE_COORDINATOR_ACTIONS = new Set([ ... ])`.
_SET_LITERAL = re.compile(
    r"EXECUTABLE_COORDINATOR_ACTIONS\s*=\s*new Set\(\[(?P<body>.*?)\]\)",
    re.DOTALL,
)
_LINE_COMMENT = re.compile(r"//[^\n]*")
_QUOTED = re.compile(r'"([^"]+)"')


def _parse_fe_executable_actions() -> set[str]:
    """Extract the FE executable-action string set from ExchangePanel.tsx.

    Strips ``//`` line comments BEFORE extracting quoted tokens (the comments mention
    ``_EXECUTABLE_COORDINATOR_ACTIONS`` and other prose) so only the real array members count —
    the comment-strip discipline for regex code-detection.
    """
    source = _EXCHANGE_PANEL.read_text(encoding="utf-8")
    match = _SET_LITERAL.search(source)
    assert match is not None, f"FE Set literal not found in {_EXCHANGE_PANEL}"
    body = _LINE_COMMENT.sub("", match.group("body"))
    return set(_QUOTED.findall(body))


def test_executable_coordinator_actions_parity():
    fe_actions = _parse_fe_executable_actions()
    be_actions = set(_EXECUTABLE_COORDINATOR_ACTIONS)

    assert fe_actions, "Parsed an empty FE action set — the parser or the source layout changed."
    # Equality (not subset) so a stale value on EITHER side fails: BE-only → FE missing a button case
    # (the capture_backlog_item bug); FE-only → a phantom action the orchestrator won't execute.
    assert fe_actions == be_actions, (
        "Executable Coordinator action sets drifted.\n"
        f"  BE-only (missing from ExchangePanel.tsx): {sorted(be_actions - fe_actions)}\n"
        f"  FE-only (missing from orchestrator):      {sorted(fe_actions - be_actions)}"
    )
