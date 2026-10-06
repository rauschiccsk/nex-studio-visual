"""Spotreba behu zo záznamu sedenia Claude Code (ICCINT-168, :mod:`backend.services.usage_ledger`).

Záznamy tu majú tvar, aký Claude Code naozaj píše (zmerané 06.10.2026, verzia 2.1.290): správa modelu sa
zapisuje po blokoch, každý so spotrebou z toho okamihu; na konci DOKONČENÉHO behu riadok ``cost-state`` so
súčtom ZA CELÉ SEDENIE; prerušený beh ``cost-state`` nezapíše; pomocníci v ``<sedenie>/subagents/``.
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
    """Záznam stavby má desiatky MB — číta sa odzadu po blokoch; riadok rozdelený hranicou bloku sa musí
    poskladať celý (malý blok to vynúti)."""
    monkeypatch.setattr(usage_ledger, "_CHUNK", 7)
    path = tmp_path / "s.jsonl"
    assert usage_ledger.last_cost_state(path) == EMPTY_STATE  # nové sedenie — súčet ešte nie je
    _write(path, _msg("a", T0), _state(claude_opus_5_5=(10, 50, 1000, 100, 0.01)), _msg("b", T0))
    _write(path, _state(claude_opus_5_5=(20, 100, 2000, 200, 0.02)), {"type": "last-prompt"})
    state = usage_ledger.last_cost_state(path)
    assert state.models == {"claude-opus-5-5": (20, 100, 2000, 200, 0, 0.02)}


def test_a_finished_run_costs_the_difference_of_session_totals(tmp_path):
    """Pri pokračovaní sedenia hlási Claude Code súčet za celé sedenie — beh stojí rozdiel súčtov, nie súčet."""
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
        UsagePart("claude-haiku-4-5", 5, 20, 0, 400, 0.001),  # pomocník (Task) je v súčte sedenia tiež
        UsagePart("claude-opus-5-5", 15, 80, 2000, 50, 0.025),
    ]


def test_a_cut_off_run_counts_its_finished_messages_without_a_price(tmp_path):
    """Prerušený beh súčet nezapíše a Claude Code ho nenaúčtuje — jeho dokončené správy však v zázname sú.
    Platí POSLEDNÝ zápis správy (prvé bloky nesú spotrebu zo začiatku); správa rozpísaná v okamihu zabitia
    má nuly; staršie behy a náhradné správy Claude Code sa nerátajú."""
    path = tmp_path / "s.jsonl"
    _write(path, _msg("old", T0 - timedelta(minutes=5), o=999), _state(claude_opus_5_5=(10, 999, 1000, 100, 0.05)))
    before = usage_ledger.last_cost_state(path)
    start = T0
    _write(
        path,
        _msg("m1", start + timedelta(seconds=1), o=4),  # prvý blok správy — spotreba zo začiatku
        _msg("m1", start + timedelta(seconds=3), o=300),  # posledný blok — konečná spotreba
        _msg("m2", start + timedelta(seconds=5), i=0, o=0, cr=0, cw=0),  # rozpísaná v okamihu zabitia
        _msg("x", start + timedelta(seconds=6), model="<synthetic>", o=7),
    )
    parts = usage_ledger.settle(path, before, start)
    assert parts == [UsagePart("claude-opus-5-5", 10, 300, 1000, 100, None)]


def test_a_cut_off_run_counts_its_helpers_too(tmp_path):
    """Pomocníci (Task) píšu vlastné záznamy pod ``<sedenie>/subagents``; tie sa pri prerušení dočítajú tiež
    — ale len tie, ktoré sa počas behu menili."""
    path = tmp_path / "s.jsonl"
    helpers = tmp_path / "s" / "subagents"
    helpers.mkdir(parents=True)
    _write(path, _msg("m1", T0 + timedelta(seconds=2)))
    _write(helpers / "agent-a.jsonl", _msg("h1", T0 + timedelta(seconds=3), model="claude-haiku-4-5", o=40))
    stale = helpers / "agent-old.jsonl"
    _write(stale, _msg("h0", T0 + timedelta(seconds=3), model="claude-haiku-4-5", o=999))
    os.utime(stale, (0, 0))  # pomocník z dávno skončeného behu
    parts = usage_ledger.settle(path, EMPTY_STATE, T0)
    assert sorted((p.model, p.output_tokens) for p in parts) == [("claude-haiku-4-5", 40), ("claude-opus-5-5", 50)]


def test_a_session_total_that_dropped_is_not_subtracted(tmp_path):
    """Súčet menší než pred behom nie je ten istý rad (sedenie sa založilo nanovo) — rozdiel by klamal."""
    path = tmp_path / "s.jsonl"
    _write(path, _state(claude_opus_5_5=(10, 50, 1000, 100, 0.05)))
    before = usage_ledger.last_cost_state(path)
    _write(path, _state(claude_opus_5_5=(2, 5, 10, 1, 0.001)))
    assert usage_ledger.settle(path, before, T0) is None


def test_an_unreadable_start_reads_nothing(tmp_path):
    """Keď sa súčet pred behom nedal prečítať, nič sa nedopočítava — volajúci ostane pri výsledku behu."""
    assert usage_ledger.settle(tmp_path / "s.jsonl", None, T0) is None


def test_parts_survive_the_payload_round_trip_and_old_turns_have_none():
    part = UsagePart("claude-opus-5-5", 1, 2, 3, 4, 0.5)
    assert UsagePart.from_payload(part.to_payload()) == part
    assert usage_ledger.parts_from_usage_payload({"parts": [part.to_payload()]}) == [part]
    # ťah spred ICCINT-168: len vstup a výstup — časti nemá a oceniť sa nedá
    assert usage_ledger.parts_from_usage_payload({"input_tokens": 1, "output_tokens": 2, "model": "m"}) is None
    assert usage_ledger.parts_from_usage_payload(None) is None


def test_a_price_claude_code_does_not_know_is_not_passed_on_as_its_price(tmp_path):
    """``hasUnknownModelCost``: Claude Code cenu modelu nepozná — jeho číslo je odhad; tokeny áno, cena nie."""
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
    """Agent, ktorý pracuje na tomto kóde, môže hľadať text ``"cost-state"`` — taký riadok nie je súčet sedenia."""
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
    """Meranie nesmie zhodiť ťah, ktorý meria — nečitateľný záznam = „nevieme", nie výnimka."""
    path = tmp_path / "s.jsonl"
    _write(path, {"type": "cost-state", "modelUsage": {"m": {"inputTokens": "veľa"}}})
    assert usage_ledger.last_cost_state(path) is None
    assert usage_ledger.settle(path, EMPTY_STATE, T0) is None


def test_a_message_that_cannot_be_understood_never_breaks_the_turn(tmp_path):
    """Aj správa s nezmyselnou spotrebou v prerušenom behu skončí ako „nevieme", nie ako výnimka v ťahu."""
    path = tmp_path / "s.jsonl"
    broken = _msg("m", T0 + timedelta(seconds=1))
    broken["message"]["usage"]["output_tokens"] = "veľa"
    _write(path, broken)
    assert usage_ledger.settle(path, EMPTY_STATE, T0) is None
