"""Translate raw claude ``stream-json`` events into Director-facing Slovak
activity lines for the cockpit live feed (CR-NS-018).

Presentation logic lives on the runner side (the board is Director-facing, and
charter §7.2 makes agents' human-facing fields Slovak). The orchestrator engine
stays free of this — it only forwards raw events.

CR-V2-018 adds the **Helpers feed**: the AI Agent dynamically spawns ephemeral
helper agents (via the ``claude`` session's own sub-agent / ``Task`` tool —
CLI-internal, NOT a backend helper orchestrator) for parallel/bulk sub-tasks.
:class:`HelperTracker` reads those sub-agent events out of the same stream-json
feed and produces the Slovak ``"+ N pomocníci"`` panel line (with each helper's
one-line description), hidden when none are active. The Auditor is EXCLUDED from
helpers (independence — no agent fully audits itself); enforcement lives in the
runner, which only feeds the tracker the AI Agent's events (see
``pipeline_runner._activity_callback``).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

_MAX_TEXT = 140
_MAX_CMD = 60
#: One-line helper description cap — the panel shows a terse "what each is doing".
_MAX_HELPER_DESC = 80
#: stream-json tool names that spawn an ephemeral sub-agent (helper). The CLI emits
#: ``Agent`` (Claude Code 2.x); ``Task`` is the historical/SDK name. We key on both so a
#: CLI rename can't silently stop helper capture.
_HELPER_SPAWN_TOOLS = frozenset({"Agent", "Task"})


def _short(path: str) -> str:
    """Last two path segments — enough to identify a file without the full tree."""
    path = path.rstrip("/")
    parts = path.split("/")
    return "/".join(parts[-2:]) if len(parts) > 1 else (os.path.basename(path) or path)


def _tool_line(name: str, tool_input: dict) -> Optional[str]:
    fp = tool_input.get("file_path") or tool_input.get("path") or ""
    if name == "Read":
        return f"číta {_short(fp)}" if fp else "číta súbor"
    if name == "Write":
        return f"píše {_short(fp)}" if fp else "píše súbor"
    if name in ("Edit", "MultiEdit", "NotebookEdit"):
        return f"upravuje {_short(fp)}" if fp else "upravuje súbor"
    if name == "Bash":
        cmd = str(tool_input.get("command", "")).strip().replace("\n", " ")
        return f"spúšťa: {cmd[:_MAX_CMD]}" if cmd else "spúšťa príkaz"
    if name in ("Grep", "Glob"):
        pat = str(tool_input.get("pattern", "")).strip()
        return f"hľadá {pat}" if pat else "hľadá v kóde"
    if name in ("Task", "Agent"):
        return "deleguje sub-agenta"
    if name in ("WebFetch", "WebSearch"):
        return "hľadá na webe"
    if name == "TodoWrite":
        return "aktualizuje plán"
    return name  # unknown tool — show its name


def activity_line(evt: dict) -> tuple[Optional[str], str]:
    """Map one stream-json event to ``(line, kind)``.

    ``line`` is ``None`` for events that aren't worth showing (init, rate-limit,
    tool results, the final result). ``kind`` ∈ {``"tool"``, ``"text"``, ``""``}.
    For an ``assistant`` event with multiple blocks, the first tool_use wins,
    else the first non-empty text.
    """
    if not isinstance(evt, dict) or evt.get("type") != "assistant":
        return None, ""
    message = evt.get("message") or {}
    content = message.get("content") or []
    if not isinstance(content, list):
        return None, ""

    text_fallback: Optional[str] = None
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "tool_use":
            line = _tool_line(str(block.get("name", "")), block.get("input") or {})
            if line:
                return line, "tool"
        elif btype == "text" and text_fallback is None:
            text = " ".join(str(block.get("text", "")).split())
            if text:
                text_fallback = text[:_MAX_TEXT]

    if text_fallback:
        return text_fallback, "text"
    return None, ""


def _slovak_helper_count(n: int) -> str:
    """Grammatically-correct Slovak noun for ``n`` helpers.

    SK count grammar: 1 → ``pomocník``; 2–4 → ``pomocníci``; 0 & 5+ → ``pomocníkov``
    (the genitive plural). The design doc's literal ``"+ N pomocníci"`` is the 2–4
    form; we keep that exact wording for 2–4 and grammar-correct the other cases
    (flagged for Manažér — see CR-V2-018 design note)."""
    if n == 1:
        return "1 pomocník"
    if 2 <= n <= 4:
        return f"{n} pomocníci"
    return f"{n} pomocníkov"


@dataclass(frozen=True)
class HelperFeed:
    """A snapshot of the currently-active ephemeral helpers, for the Helpers panel.

    ``line`` is the Slovak ``"+ N pomocníci"`` header; ``descriptions`` are the
    per-helper one-liners (insertion order). An empty feed (``count == 0``) carries
    ``line == ""`` and an empty list — the panel is HIDDEN when none are active."""

    count: int
    line: str
    descriptions: tuple[str, ...]


_EMPTY_FEED = HelperFeed(count=0, line="", descriptions=())


@dataclass
class HelperTracker:
    """Stateful capture of ephemeral helper (sub-agent) lifecycle from stream-json.

    Fed every event of ONE AI-Agent dispatch turn (the Auditor's events are never
    fed — independence, enforced by the runner). It tracks the set of in-flight
    helpers by their spawning ``tool_use`` id and exposes the current
    :class:`HelperFeed` whenever that set changes.

    Lifecycle (confirmed against the live CLI stream-json, Claude Code 2.x):
      * **spawn** — a parent-level ``assistant`` event (``parent_tool_use_id is
        None``) carrying a ``tool_use`` block whose ``name`` is ``Agent``/``Task``;
        ``input.description`` is the helper's one-line description.
      * **finish** — a parent-level ``user`` event carrying a ``tool_result`` block
        whose ``tool_use_id`` matches a spawned helper.
      * sub-agent-internal events (``parent_tool_use_id is not None``) are the
        helper's OWN inner activity — never a new helper, never counted (so a helper
        that itself uses a tool can't register a phantom helper, and the Auditor
        can never appear as a helper-of-a-helper)."""

    #: tool_use id → one-line description, in spawn order.
    _active: dict[str, str] = field(default_factory=dict)

    def feed(self) -> HelperFeed:
        """The current feed snapshot (``_EMPTY_FEED`` when no helpers active)."""
        if not self._active:
            return _EMPTY_FEED
        descs = tuple(self._active.values())
        return HelperFeed(count=len(descs), line=f"+ {_slovak_helper_count(len(descs))}", descriptions=descs)

    def observe(self, evt: dict) -> Optional[HelperFeed]:
        """Process one stream-json event; return the new :class:`HelperFeed` IFF the
        active-helper set changed, else ``None`` (no broadcast needed).

        Returns ``_EMPTY_FEED`` when the last helper finishes so the panel hides."""
        if not isinstance(evt, dict):
            return None
        # Sub-agent-internal events are the helper's own inner work — never a new helper.
        if evt.get("parent_tool_use_id") is not None:
            return None

        etype = evt.get("type")
        if etype not in ("assistant", "user"):
            return None
        content = ((evt.get("message") or {}).get("content")) or []
        if not isinstance(content, list):
            return None

        changed = False
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if etype == "assistant" and btype == "tool_use" and block.get("name") in _HELPER_SPAWN_TOOLS:
                tid = block.get("id")
                if not tid or tid in self._active:
                    continue
                desc = " ".join(str((block.get("input") or {}).get("description", "")).split())
                self._active[tid] = desc[:_MAX_HELPER_DESC] or "pracuje na podúlohe"
                changed = True
            elif etype == "user" and btype == "tool_result":
                tid = block.get("tool_use_id")
                if tid in self._active:
                    del self._active[tid]
                    changed = True

        return self.feed() if changed else None
