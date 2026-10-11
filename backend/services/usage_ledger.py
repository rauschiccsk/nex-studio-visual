"""Usage of a single Claude Code run — from the session log that Claude Code keeps itself (ICCINT-168).

**Why not from the run result.** The result (``result``) carries ``total_cost_usd`` and ``modelUsage`` — but when
a session is resumed (``--resume``) they are TOTALS FOR THE WHOLE SESSION, not the run (measured 2026-10-06, Claude Code
2.1.290: 2nd run in a session reported $0.0141886 = 1st $0.0113585 + 2nd alone $0.0028301). The cockpit continues
in the same session on every build turn; summing these numbers would inflate Costs many times over.

**So where from.** At the end of every completed run Claude Code writes a ``cost-state`` line into the session log
with the per-model total (tokens of all kinds and the cost). The cockpit reads the last total BEFORE the run and again
AFTER it; the run's usage is their difference — exactly what Claude Code billed, including helpers (Task) and also for a
run that ended with an error but that Claude Code managed to close.

**An interrupted run** (timed out, killed) writes no ``cost-state`` and is missing from Claude Code's total (measured:
a run killed between two completed ones missed the total). Its tokens are in the log though — final numbers of each
COMPLETED message, helpers' too (``<session>/subagents/*.jsonl``). A message being written at the moment of the kill has
zeros in the log and its output so far is unknown. Claude Code does not report the cost of such a part (``cost_usd`` is
``None``); the price list prices it (:mod:`backend.services.model_pricing`).

**Intermediate messages** from ``stream-json`` are not used for usage: they carry the output-token count from the moment
the message started (measured: 4 instead of 2,983).
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator, Optional

logger = logging.getLogger(__name__)

#: The model Claude Code uses to label its own substitute messages (API error, interruption) — nothing is paid for them.
SYNTHETIC_MODEL = "<synthetic>"

#: Log lines timestamped before the run start that the run boundary still relies on (host and
#: container clocks are the same; the margin only covers timestamp rounding).
_CLOCK_SLACK = timedelta(seconds=2)

#: After how many bytes the log is read from the end. A build log is tens of MB (Dedo Home 71 MB); the run is last.
_CHUNK = 1 << 20


@dataclass(frozen=True)
class UsagePart:
    """Usage of one model in one run.

    ``cost_usd`` is the cost Claude Code computed for exactly these tokens — ``None`` when it did not report it
    (interrupted run, or the session log could not be read). ``cache_write`` tokens are writes to the cache,
    ``cache_read`` reads from it; ``input`` is input outside it."""

    model: Optional[str]
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: Optional[float] = None
    #: Web searches — Claude Code bills them separately (not tokens) and they are in ``cost_usd`` (ICCINT-168).
    web_search_requests: int = 0

    @property
    def tokens(self) -> int:
        return self.input_tokens + self.output_tokens + self.cache_read_tokens + self.cache_write_tokens

    def to_payload(self) -> dict:
        return {
            "model": self.model,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "cost_usd": self.cost_usd,
            "web_search_requests": self.web_search_requests,
        }

    @classmethod
    def from_payload(cls, data: object) -> Optional["UsagePart"]:
        if not isinstance(data, dict):
            return None
        cost = data.get("cost_usd")
        model = data.get("model")
        return cls(
            model=model if isinstance(model, str) else None,
            input_tokens=int(data.get("input_tokens") or 0),
            output_tokens=int(data.get("output_tokens") or 0),
            cache_read_tokens=int(data.get("cache_read_tokens") or 0),
            cache_write_tokens=int(data.get("cache_write_tokens") or 0),
            cost_usd=float(cost) if isinstance(cost, (int, float)) else None,
            web_search_requests=int(data.get("web_search_requests") or 0),
        )


def parts_from_usage_payload(usage: object) -> Optional[list[UsagePart]]:
    """Parts of usage stored at the turn, or ``None`` for a record from before ICCINT-168 (only input and output without
    the cache — such a record cannot be priced and Costs must say so, not underestimate it)."""
    if not isinstance(usage, dict) or not isinstance(usage.get("parts"), list):
        return None
    return [p for p in (UsagePart.from_payload(d) for d in usage["parts"]) if p is not None]


# ── session log ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CostState:
    """Session total from the ``cost-state`` line: model → (input, output, read, write, searches, cost $).

    ``unknown_cost``: Claude Code does not know the price of some model (``hasUnknownModelCost``) — its ``costUSD`` is
    then an estimate and must not be passed off as Claude Code's price (nor teach the price list)."""

    models: dict[str, tuple[int, int, int, int, int, float]]
    unknown_cost: bool = False

    @classmethod
    def from_line(cls, entry: dict) -> "CostState":
        models: dict[str, tuple[int, int, int, int, int, float]] = {}
        for model, u in (entry.get("modelUsage") or {}).items():
            if not isinstance(u, dict):
                continue
            models[model] = (
                int(u.get("inputTokens") or 0),
                int(u.get("outputTokens") or 0),
                int(u.get("cacheReadInputTokens") or 0),
                int(u.get("cacheCreationInputTokens") or 0),
                int(u.get("webSearchRequests") or 0),
                float(u.get("costUSD") or 0.0),
            )
        return cls(models, bool(entry.get("hasUnknownModelCost")))


EMPTY_STATE = CostState({})


def build_transcript(session_dir: Path, claude_session_id: object) -> Path:
    """Session log file ``<project session directory>/<session id>.jsonl``."""
    return session_dir / f"{claude_session_id}.jsonl"


def _lines_backwards(path: Path) -> Iterator[str]:
    """Lines of the file from the end — a build log is tens of MB and what matters is at its end."""
    with open(path, "rb") as fh:
        fh.seek(0, os.SEEK_END)
        pos = fh.tell()
        rest = b""
        while pos > 0:
            step = min(_CHUNK, pos)
            pos -= step
            fh.seek(pos)
            block = fh.read(step) + rest
            lines = block.split(b"\n")
            rest = lines[0]
            for raw in reversed(lines[1:]):
                if raw.strip():
                    yield raw.decode("utf-8", errors="replace")
        if rest.strip():
            yield rest.decode("utf-8", errors="replace")


def last_cost_state(transcript: Path) -> Optional[CostState]:
    """The last session total; :data:`EMPTY_STATE` when the session has no total yet (new session, no
    completed run), ``None`` when the log cannot be read."""
    try:
        if not transcript.exists():
            return EMPTY_STATE
        for line in _lines_backwards(transcript):
            if '"cost-state"' not in line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(entry, dict) and entry.get("type") == "cost-state":
                return CostState.from_line(entry)
        return EMPTY_STATE
    except Exception:  # noqa: BLE001 — also read BEFORE a turn; the measurement must not bring the turn down
        logger.warning("usage: záznam sedenia %s sa nedá prečítať", transcript, exc_info=True)
        return None


def _delta(before: CostState, after: CostState) -> Optional[list[UsagePart]]:
    """Usage between two totals, per model. ``None`` when the total decreased — that is not the same series
    (the session was created anew) and the difference would lie."""
    if any(model not in after.models for model in before.models):
        return None  # the model vanished from the total — a new series, not a continuation
    parts: list[UsagePart] = []
    for model, now in after.models.items():
        then = before.models.get(model, (0, 0, 0, 0, 0, 0.0))
        diff = [a - b for a, b in zip(now, then)]
        if any(d < 0 for d in diff[:5]) or diff[5] < -1e-9:
            return None
        if not any(diff[:5]):
            continue
        parts.append(
            UsagePart(
                model=model,
                input_tokens=int(diff[0]),
                output_tokens=int(diff[1]),
                cache_read_tokens=int(diff[2]),
                cache_write_tokens=int(diff[3]),
                cost_usd=None if after.unknown_cost else round(max(diff[5], 0.0), 9),
                web_search_requests=int(diff[4]),
            )
        )
    return parts


def _parse_ts(value: object) -> Optional[datetime]:
    """Write time; without a time zone Claude Code writes it in UTC (it is compared with the run start in UTC)."""
    if not isinstance(value, str):
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return stamp if stamp.tzinfo is not None else stamp.replace(tzinfo=timezone.utc)


def _is_cost_state(line: str) -> bool:
    """The line with the session total — by type, not by text (the same text may also be in an agent's tool)."""
    if '"cost-state"' not in line:
        return False
    try:
        entry = json.loads(line)
    except json.JSONDecodeError:
        return False
    return isinstance(entry, dict) and entry.get("type") == "cost-state"


def _finished_messages(lines: Iterator[str], since: datetime, *, stop_at_cost_state: bool) -> dict[str, tuple]:
    """Final usage of every model message since ``since`` — message id → (model, input, output, read, write).

    Claude Code writes a message in blocks, each with the usage from that moment; the LAST write of a message counts.
    It is read from the end, so the first write of a message seen is the last one."""
    seen: dict[str, tuple] = {}
    for line in lines:
        if stop_at_cost_state and _is_cost_state(line):
            break
        if '"assistant"' not in line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        message = entry.get("message") if isinstance(entry, dict) and entry.get("type") == "assistant" else None
        if not isinstance(message, dict) or not isinstance(message.get("usage"), dict):
            continue
        stamp = _parse_ts(entry.get("timestamp"))
        if stamp is None or stamp < since:
            continue
        key = message.get("id") or entry.get("uuid")
        if not key or key in seen:
            continue
        u = message["usage"]
        seen[key] = (
            message.get("model"),
            int(u.get("input_tokens") or 0),
            int(u.get("output_tokens") or 0),
            int(u.get("cache_read_input_tokens") or 0),
            int(u.get("cache_creation_input_tokens") or 0),
            _searches(u),
        )
    return seen


def _searches(usage: dict) -> int:
    tool_use = usage.get("server_tool_use")
    return int(tool_use.get("web_search_requests") or 0) if isinstance(tool_use, dict) else 0


def unfinished_parts(transcript: Path, started_at: datetime) -> list[UsagePart]:
    """Usage of an interrupted run: completed messages of the main session and helpers since the run start, per model.

    The main log is read from the end up to the last ``cost-state`` (end of the last COMPLETED run; everything after it
    is newer), helpers (``<session>/subagents``) whole, if they changed during the run."""
    since = started_at - _CLOCK_SLACK
    seen: dict[str, tuple] = {}
    try:
        if transcript.exists():
            seen.update(_finished_messages(_lines_backwards(transcript), since, stop_at_cost_state=True))
        helpers = transcript.with_suffix("") / "subagents"
        if helpers.is_dir():
            for path in sorted(helpers.glob("*.jsonl")):
                if datetime.fromtimestamp(path.stat().st_mtime, timezone.utc) < since:
                    continue
                for key, row in _finished_messages(_lines_backwards(path), since, stop_at_cost_state=False).items():
                    seen.setdefault(key, row)
    except OSError:
        logger.warning("usage: záznam sedenia %s sa nedá prečítať", transcript, exc_info=True)
        return []
    totals: dict[str, list[int]] = {}
    for model, *tokens in seen.values():
        if not model or model == SYNTHETIC_MODEL or not any(tokens):
            continue
        acc = totals.setdefault(model, [0, 0, 0, 0, 0])
        for i, value in enumerate(tokens):
            acc[i] += value
    return [
        UsagePart(
            model=m,
            input_tokens=t[0],
            output_tokens=t[1],
            cache_read_tokens=t[2],
            cache_write_tokens=t[3],
            web_search_requests=t[4],
        )
        for m, t in totals.items()
    ]


def settle(transcript: Path, before: Optional[CostState], started_at: datetime) -> Optional[list[UsagePart]]:
    """Usage of a run that has just ended — however it ended.

    A session total was added → Claude Code closed the run, usage is the difference of totals (with cost). None added →
    the run was interrupted and the usage is its completed messages (without cost). ``None`` when the log cannot be read
    or the total makes no sense — the caller then uses what it knows from the run result and invents nothing."""
    if before is None:
        return None
    try:
        after = last_cost_state(transcript)
        if after is None:
            return None
        if after != before:
            parts = _delta(before, after)
            if parts is None:
                logger.warning("usage: súčet sedenia %s klesol — beh sa nedá odčítať, beriem výsledok behu", transcript)
            return parts
        return unfinished_parts(transcript, started_at)
    except Exception:  # noqa: BLE001 — the measurement must not bring down the turn it measures
        logger.warning("usage: spotrebu zo záznamu sedenia %s sa nepodarilo zistiť", transcript, exc_info=True)
        return None
