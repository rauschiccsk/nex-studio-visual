"""Run usage from the Claude Code session record (ICCINT-168, :mod:`backend.services.usage_ledger`).

Records here have the shape Claude Code actually writes (measured 06.10.2026, version 2.1.290): a model message is
written in blocks, each with the usage at that moment; at the end of a COMPLETED run a ``cost-state`` line with
the sum FOR THE WHOLE SESSION; an interrupted run writes no ``cost-state``; helpers in ``<sedenie>/subagents/``.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backend.services import usage_ledger
from backend.services.usage_ledger import EMPTY_STATE, UsagePart

T0 = datetime(2026, 10, 6, 8, 0, 0, tzinfo=timezone.utc)


def _iso(t: datetime) -> str:
    return t.isoformat().replace("+00:00", "Z")


def _msg(mid: str, at: datetime, *, model="claude-opus-5-5", i=10, o=50, cr=1000, cw=100) -> dict:
    return {
        "type": "assistant",
        "timestamp": _iso(at),
        "message": {
            "id": mid,
            "model": model,
            "usage": {
                "input_tokens": i,
                "output_tokens": o,
                "cache_read_input_tokens": cr,
                "cache_creation_input_tokens": cw,
            },
        },
    }


def _state(**models: tuple[int, int, int, int, float]) -> dict:
    return {
        "type": "cost-state",
        "totalCostUSD": sum(v[4] for v in models.values()),
        "modelUsage": {
            m.replace("_", "-"): {
                "inputTokens": v[0],
                "outputTokens": v[1],
                "cacheReadInputTokens": v[2],
                "cacheCreationInputTokens": v[3],
                "costUSD": v[4],
            }
            for m, v in models.items()
        },
    }


def _write(path: Path, *rows: dict) -> None:
    with open(path, "a") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


def test_the_last_session_total_is_read_from_the_end(tmp_path, monkeypatch):
    """A build record has tens of MB — it is read backwards in blocks; a line split by a block boundary must be
    assembled whole (a small block forces it)."""
    monkeypatch.setattr(usage_ledger, "_CHUNK", 7)
    path = tmp_path / "s.jsonl"
    assert usage_ledger.last_cost_state(path) == EMPTY_STATE  # new session — no sum yet
    _write(path, _msg("a", T0), _state(claude_opus_5_5=(10, 50, 1000, 100, 0.01)), _msg("b", T0))
    _write(path, _state(claude_opus_5_5=(20, 100, 2000, 200, 0.02)), {"type": "last-prompt"})
    state = usage_ledger.last_cost_state(path)
    assert state.models == {"claude-opus-5-5": (20, 100, 2000, 200, 0, 0.02)}


def test_a_finished_run_costs_the_difference_of_session_totals(tmp_path):
    """When a session is resumed, Claude Code reports the sum for the whole session — a run costs the difference."""
    path = tmp_path / "s.jsonl"
    _write(path, _msg("a", T0), _state(claude_opus_5_5=(10, 50, 1000, 100, 0.01)))
    before = usage_ledger.last_cost_state(path)
    _write(
        path,
        _msg("b", T0 + timedelta(minutes=1)),
        _state(claude_opus_5_5=(25, 130, 3000, 150, 0.035), claude_haiku_4_5=(5, 20, 0, 400, 0.001)),
    )
    parts = usage_ledger.settle(path, before, T0 + timedelta(seconds=30))
    assert sorted(parts, key=lambda p: p.model) == [
        UsagePart("claude-haiku-4-5", 5, 20, 0, 400, 0.001),  # a helper (Task) is also in the session sum
        UsagePart("claude-opus-5-5", 15, 80, 2000, 50, 0.025),
    ]


def test_a_cut_off_run_counts_its_finished_messages_without_a_price(tmp_path):
    """An interrupted run writes no sum and Claude Code does not bill it — but its completed messages are in the record.
    The LAST write of a message counts (the first blocks carry usage from the start); a message in progress at the kill
    of the kill has zeros; older runs and Claude Code's substitute messages are not counted."""
    path = tmp_path / "s.jsonl"
    _write(path, _msg("old", T0 - timedelta(minutes=5), o=999), _state(claude_opus_5_5=(10, 999, 1000, 100, 0.05)))
    before = usage_ledger.last_cost_state(path)
    start = T0
    _write(
        path,
        _msg("m1", start + timedelta(seconds=1), o=4),  # first block of the message — usage from the start
        _msg("m1", start + timedelta(seconds=3), o=300),  # last block — final usage
        _msg("m2", start + timedelta(seconds=5), i=0, o=0, cr=0, cw=0),  # in progress at the moment of the kill
        _msg("x", start + timedelta(seconds=6), model="<synthetic>", o=7),
    )
    parts = usage_ledger.settle(path, before, start)
    assert parts == [UsagePart("claude-opus-5-5", 10, 300, 1000, 100, None)]


def test_a_cut_off_run_counts_its_helpers_too(tmp_path):
    """Helpers (Task) write their own records under ``<sedenie>/subagents``; on interruption those are read too
    — but only those that changed during the run."""
    path = tmp_path / "s.jsonl"
    helpers = tmp_path / "s" / "subagents"
    helpers.mkdir(parents=True)
    _write(path, _msg("m1", T0 + timedelta(seconds=2)))
    _write(helpers / "agent-a.jsonl", _msg("h1", T0 + timedelta(seconds=3), model="claude-haiku-4-5", o=40))
    stale = helpers / "agent-old.jsonl"
    _write(stale, _msg("h0", T0 + timedelta(seconds=3), model="claude-haiku-4-5", o=999))
    os.utime(stale, (0, 0))  # helper from a long-finished run
    parts = usage_ledger.settle(path, EMPTY_STATE, T0)
    assert sorted((p.model, p.output_tokens) for p in parts) == [("claude-haiku-4-5", 40), ("claude-opus-5-5", 50)]


def test_a_session_total_that_dropped_is_not_subtracted(tmp_path):
    """A sum lower than before the run is not the same series (the session was created anew) — the difference lies."""
    path = tmp_path / "s.jsonl"
    _write(path, _state(claude_opus_5_5=(10, 50, 1000, 100, 0.05)))
    before = usage_ledger.last_cost_state(path)
    _write(path, _state(claude_opus_5_5=(2, 5, 10, 1, 0.001)))
    assert usage_ledger.settle(path, before, T0) is None


def test_an_unreadable_start_reads_nothing(tmp_path):
    """When the sum before the run could not be read, nothing is computed — the caller stays with the run result."""
    assert usage_ledger.settle(tmp_path / "s.jsonl", None, T0) is None


def test_parts_survive_the_payload_round_trip_and_old_turns_have_none():
    part = UsagePart("claude-opus-5-5", 1, 2, 3, 4, 0.5)
    assert UsagePart.from_payload(part.to_payload()) == part
    assert usage_ledger.parts_from_usage_payload({"parts": [part.to_payload()]}) == [part]
    # a turn from before ICCINT-168: only input and output — it has no parts and cannot be priced
    assert usage_ledger.parts_from_usage_payload({"input_tokens": 1, "output_tokens": 2, "model": "m"}) is None
    assert usage_ledger.parts_from_usage_payload(None) is None


def test_a_price_claude_code_does_not_know_is_not_passed_on_as_its_price(tmp_path):
    """``hasUnknownModelCost``: Claude Code does not know the model's price — its number is an estimate; tokens yes."""
    path = tmp_path / "s.jsonl"
    state = _state(claude_opus_5_5=(10, 50, 1000, 100, 0.01))
    _write(path, {**state, "hasUnknownModelCost": True})
    [part] = usage_ledger.settle(path, EMPTY_STATE, T0)
    assert part.output_tokens == 50 and part.cost_usd is None


def test_web_searches_are_carried_from_the_session_total_and_the_messages(tmp_path):
    path = tmp_path / "s.jsonl"
    state = _state(claude_opus_5_5=(10, 50, 1000, 100, 0.05))
    state["modelUsage"]["claude-opus-5-5"]["webSearchRequests"] = 3
    _write(path, state)
    assert usage_ledger.settle(path, EMPTY_STATE, T0)[0].web_search_requests == 3
    cut = tmp_path / "c.jsonl"
    message = _msg("m", T0 + timedelta(seconds=1))
    message["message"]["usage"]["server_tool_use"] = {"web_search_requests": 2}
    _write(cut, message)
    assert usage_ledger.settle(cut, EMPTY_STATE, T0)[0].web_search_requests == 2


def test_a_model_vanishing_from_the_session_total_is_a_new_series(tmp_path):
    path = tmp_path / "s.jsonl"
    _write(path, _state(claude_opus_5_5=(10, 50, 1000, 100, 0.05), claude_haiku_4_5=(1, 1, 1, 1, 0.001)))
    before = usage_ledger.last_cost_state(path)
    _write(path, _state(claude_opus_5_5=(20, 90, 2000, 100, 0.08)))
    assert usage_ledger.settle(path, before, T0) is None


def test_the_word_cost_state_inside_an_agent_tool_call_does_not_end_the_scan(tmp_path):
    """An agent working on this code may search for the text ``"cost-state"`` — such a line is not a session sum."""
    path = tmp_path / "s.jsonl"
    _write(path, _state(claude_opus_5_5=(1, 1, 1, 1, 0.001)))
    before = usage_ledger.last_cost_state(path)
    _write(
        path,
        _msg("m1", T0 + timedelta(seconds=1), o=100),
        {
            "type": "user",
            "timestamp": _iso(T0 + timedelta(seconds=2)),
            "message": {"content": [{"type": "tool_result", "content": "cost-state"}]},
        },
        _msg("m2", T0 + timedelta(seconds=3), o=200),
    )
    [part] = usage_ledger.settle(path, before, T0)
    assert part.output_tokens == 300


def test_a_record_that_cannot_be_understood_never_breaks_the_turn(tmp_path):
    """Measuring must not break the turn it measures — an unreadable record = "we don't know", not an exception."""
    path = tmp_path / "s.jsonl"
    _write(path, {"type": "cost-state", "modelUsage": {"m": {"inputTokens": "veľa"}}})
    assert usage_ledger.last_cost_state(path) is None
    assert usage_ledger.settle(path, EMPTY_STATE, T0) is None


def test_a_message_that_cannot_be_understood_never_breaks_the_turn(tmp_path):
    """A message with nonsensical usage in an interrupted run also ends as "we don't know", not as an exception."""
    path = tmp_path / "s.jsonl"
    broken = _msg("m", T0 + timedelta(seconds=1))
    broken["message"]["usage"]["output_tokens"] = "veľa"
    _write(path, broken)
    assert usage_ledger.settle(path, EMPTY_STATE, T0) is None
