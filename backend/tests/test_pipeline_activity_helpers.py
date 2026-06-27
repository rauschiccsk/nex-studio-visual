"""CR-V2-018 — Ephemeral helper spawning + Helpers feed (BE).

Covers the Helpers feed built from the ``claude`` stream-json sub-agent (``Agent`` /
``Task`` tool) events:

* :class:`HelperTracker` lifecycle — spawn → finish, "+ N pomocníci" Slovak line,
  panel HIDDEN (count 0) when none active.
* a *bulk* task (sub-agent spawn events) emits ≥1 Slovak helper feed line; a *small*
  task (no sub-agent events) emits none.
* the **Auditor is never registered as a helper** (independence) — proven at the
  runner ``_activity_callback`` layer, the real Auditor-exclusion enforcer.
* sub-agent-INTERNAL events (``parent_tool_use_id`` set) never register a phantom
  helper, and unrelated tools (``ToolSearch``/``Read``) never count as helpers.

The event-shape builders below mirror the GROUND TRUTH captured from the live CLI
(Claude Code 2.x ``--output-format stream-json``): the spawn is a parent-level
``assistant`` event with an ``Agent`` ``tool_use`` block carrying ``input.description``;
the finish is a parent-level ``user`` event with a ``tool_result`` whose
``tool_use_id`` matches; the helper's own inner work arrives with a non-null
``parent_tool_use_id``.
"""

from __future__ import annotations

import uuid

import pytest

from backend.services import orchestrator
from backend.services.pipeline_activity import (
    HelperFeed,
    HelperTracker,
    _slovak_helper_count,
    activity_line,
)
from backend.services.pipeline_ws import registry

# ── Ground-truth stream-json event builders ───────────────────────────────────────────────


def spawn_evt(tool_id: str, description: str, *, name: str = "Agent", role: str | None = None) -> dict:
    """A parent-level ``assistant`` event spawning a helper (sub-agent)."""
    evt = {
        "type": "assistant",
        "parent_tool_use_id": None,
        "message": {
            "content": [
                {
                    "type": "tool_use",
                    "name": name,
                    "id": tool_id,
                    "input": {"description": description, "prompt": "..."},
                }
            ]
        },
    }
    if role is not None:
        evt["_role"] = role
    return evt


def finish_evt(tool_id: str, *, role: str | None = None) -> dict:
    """A parent-level ``user`` event delivering the helper's ``tool_result`` (finish)."""
    evt = {
        "type": "user",
        "parent_tool_use_id": None,
        "message": {"content": [{"type": "tool_result", "tool_use_id": tool_id}]},
    }
    if role is not None:
        evt["_role"] = role
    return evt


def subagent_inner_evt(parent_id: str, *, role: str | None = None) -> dict:
    """The helper's OWN inner activity (a Bash tool_use inside the sub-agent)."""
    evt = {
        "type": "assistant",
        "parent_tool_use_id": parent_id,
        "message": {
            "content": [{"type": "tool_use", "name": "Bash", "id": "toolu_inner", "input": {"command": "echo hi"}}]
        },
    }
    if role is not None:
        evt["_role"] = role
    return evt


def tool_evt(name: str, tool_id: str, *, role: str | None = None) -> dict:
    """A plain (non-helper) tool_use, e.g. ``Read`` / ``ToolSearch`` — must NOT count."""
    evt = {
        "type": "assistant",
        "parent_tool_use_id": None,
        "message": {"content": [{"type": "tool_use", "name": name, "id": tool_id, "input": {}}]},
    }
    if role is not None:
        evt["_role"] = role
    return evt


# ── _slovak_helper_count grammar ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "n,expected",
    [
        (1, "1 pomocník"),
        (2, "2 pomocníci"),
        (3, "3 pomocníci"),
        (4, "4 pomocníci"),
        (5, "5 pomocníkov"),
        (12, "12 pomocníkov"),
    ],
)
def test_slovak_helper_count_grammar(n, expected):
    assert _slovak_helper_count(n) == expected


# ── HelperTracker lifecycle ───────────────────────────────────────────────────────────────


def test_small_task_emits_no_helpers():
    """A small task — only ordinary tools, no sub-agent — never produces a feed."""
    t = HelperTracker()
    assert t.observe(tool_evt("Read", "t1")) is None
    assert t.observe(tool_evt("ToolSearch", "t2")) is None
    assert t.observe({"type": "result"}) is None
    assert t.feed() == HelperFeed(count=0, line="", descriptions=())  # value-equal empty feed
    assert t.feed().count == 0


def test_bulk_task_emits_helper_line():
    """A bulk task spawning two sub-agents emits a Slovak '+ N pomocníci' line with descriptions."""
    t = HelperTracker()
    f1 = t.observe(spawn_evt("toolu_a", "Preklad UI reťazcov"))
    assert f1 is not None and f1.count == 1
    assert f1.line == "+ 1 pomocník"
    assert f1.descriptions == ("Preklad UI reťazcov",)

    f2 = t.observe(spawn_evt("toolu_b", "Generovanie testov"))
    assert f2 is not None and f2.count == 2
    assert f2.line == "+ 2 pomocníci"  # design-doc literal wording for 2–4
    assert f2.descriptions == ("Preklad UI reťazcov", "Generovanie testov")


def test_helper_finish_hides_panel():
    """When the last helper finishes, the feed reports count 0 so the panel hides."""
    t = HelperTracker()
    t.observe(spawn_evt("toolu_a", "Práca A"))
    t.observe(spawn_evt("toolu_b", "Práca B"))
    f_mid = t.observe(finish_evt("toolu_a"))
    assert f_mid is not None and f_mid.count == 1 and f_mid.descriptions == ("Práca B",)
    f_end = t.observe(finish_evt("toolu_b"))
    assert f_end is not None and f_end.count == 0 and f_end.line == "" and f_end.descriptions == ()


def test_subagent_internal_events_never_register_a_helper():
    """A helper's OWN inner tool_use (non-null parent_tool_use_id) must not spawn a phantom helper."""
    t = HelperTracker()
    t.observe(spawn_evt("toolu_a", "Práca A"))
    assert t.observe(subagent_inner_evt("toolu_a")) is None  # inner work — no change
    # an inner Agent tool_use (a helper spawned BY a helper) is still inner → ignored
    inner_spawn = subagent_inner_evt("toolu_a")
    inner_spawn["message"]["content"][0]["name"] = "Agent"
    inner_spawn["message"]["content"][0]["id"] = "toolu_nested"
    assert t.observe(inner_spawn) is None
    assert t.feed().count == 1  # still just the one top-level helper


def test_plain_tools_and_results_are_ignored():
    """ToolSearch + its tool_result must never register/deregister a helper."""
    t = HelperTracker()
    assert t.observe(tool_evt("ToolSearch", "toolu_search")) is None
    assert t.observe(finish_evt("toolu_search")) is None  # result for a non-helper → no change
    assert t.feed().count == 0


def test_unchanged_set_returns_none():
    """A duplicate spawn id or a finish for an unknown id reports no change."""
    t = HelperTracker()
    t.observe(spawn_evt("toolu_a", "Práca A"))
    assert t.observe(spawn_evt("toolu_a", "Práca A (duplikát)")) is None  # same id → no change
    assert t.observe(finish_evt("toolu_unknown")) is None  # unknown finish → no change


def test_missing_description_falls_back():
    """A spawn with no description still gets a sensible Slovak placeholder."""
    t = HelperTracker()
    evt = spawn_evt("toolu_a", "")
    f = t.observe(evt)
    assert f is not None and f.descriptions == ("pracuje na podúlohe",)


def test_task_tool_name_also_recognised():
    """The historical/SDK 'Task' tool name spawns a helper too (not just 'Agent')."""
    t = HelperTracker()
    f = t.observe(spawn_evt("toolu_a", "Hromadná úloha", name="Task"))
    assert f is not None and f.count == 1


def test_ground_truth_replay_two_parallel_helpers():
    """Replay the captured live-CLI sequence: ToolSearch noise + 2 parallel Agents +
    their inner work + parent-level finishes → peaks at 2 helpers, ends at 0."""
    t = HelperTracker()
    seq = [
        tool_evt("ToolSearch", "toolu_search"),
        finish_evt("toolu_search"),
        spawn_evt("toolu_a", "Run echo helper-one"),
        {"type": "user", "parent_tool_use_id": "toolu_a", "message": {"content": [{"type": "text", "text": "..."}]}},
        spawn_evt("toolu_b", "Run echo helper-two"),
        subagent_inner_evt("toolu_b"),
        subagent_inner_evt("toolu_a"),
        finish_evt("toolu_a"),
        finish_evt("toolu_b"),
    ]
    peak = 0
    last = None
    for evt in seq:
        f = t.observe(evt)
        if f is not None:
            peak = max(peak, f.count)
            last = f
    assert peak == 2
    assert last is not None and last.count == 0  # both finished → panel hides


# ── activity_line still flags the spawn on the rail (complementary to the panel) ────────────


def test_activity_line_reports_delegation_for_spawn():
    line, kind = activity_line(spawn_evt("toolu_a", "Práca A"))
    assert kind == "tool" and line == "deleguje sub-agenta"


# ── Runner-level Auditor exclusion (the real independence enforcer) ──────────────────────────


class _FakeWS:
    """Minimal WebSocket double that records broadcast frames."""

    def __init__(self) -> None:
        self.frames: list[dict] = []

    async def send_json(self, event: dict) -> None:
        self.frames.append(event)


async def _drive(version_id: uuid.UUID, stage: str, fallback_actor: str, events: list[dict]) -> list[dict]:
    """Run events through pipeline_runner._activity_callback with a live registry socket;
    return all broadcast frames."""
    from backend.services import pipeline_runner

    ws = _FakeWS()
    await registry.connect(version_id, ws, uuid.uuid4())
    try:
        cb = pipeline_runner._activity_callback(version_id, stage, fallback_actor)
        for evt in events:
            await cb(evt)
    finally:
        await registry.disconnect(version_id, ws)
    return ws.frames


async def test_runner_broadcasts_helper_frames_for_ai_agent():
    """An AI-Agent dispatch that spawns a helper broadcasts a 'helpers' frame."""
    vid = uuid.uuid4()
    role = orchestrator.AI_AGENT_ROLE
    frames = await _drive(
        vid,
        "build",
        role,
        [spawn_evt("toolu_a", "Hromadná úloha", role=role), finish_evt("toolu_a", role=role)],
    )
    helper_frames = [f for f in frames if f.get("type") == "helpers"]
    assert len(helper_frames) == 2  # spawn (count 1) + finish (count 0)
    assert helper_frames[0]["count"] == 1
    assert helper_frames[0]["line"] == "+ 1 pomocník"
    assert helper_frames[0]["helpers"] == ["Hromadná úloha"]
    assert helper_frames[-1]["count"] == 0  # panel hides


async def test_runner_never_registers_auditor_as_helper():
    """Even if the Auditor's stream emitted an Agent tool_use, NO 'helpers' frame is broadcast.

    The Auditor is independent — explicitly excluded from helpers. The runner only feeds
    the tracker AI-Agent events, so an Auditor-tagged spawn is never surfaced as a helper."""
    vid = uuid.uuid4()
    role = orchestrator.AUDITOR_ROLE
    frames = await _drive(
        vid,
        "verifikacia",
        role,
        [spawn_evt("toolu_a", "Audítor by spustil sub-agenta", role=role), finish_evt("toolu_a", role=role)],
    )
    assert [f for f in frames if f.get("type") == "helpers"] == []  # zero helper frames for the Auditor


async def test_runner_small_ai_agent_task_emits_no_helper_frame():
    """An AI-Agent turn with only ordinary tools broadcasts agent_activity but no helpers frame."""
    vid = uuid.uuid4()
    role = orchestrator.AI_AGENT_ROLE
    frames = await _drive(vid, "build", role, [tool_evt("Read", "t1", role=role)])
    assert [f for f in frames if f.get("type") == "helpers"] == []
    assert any(f.get("type") == "agent_activity" for f in frames)  # the Read still shows on the rail
