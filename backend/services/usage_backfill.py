"""Backfilling usage of older turns from session logs (ICCINT-168).

**Why.** Turns written before v4.43.0 carry only input and output tokens from the run result — the cockpit did not see
then (2.27 billion read tokens on Dedo Home), so they cannot be priced. The session logs that
Claude Code keeps, however, mostly stayed on disk. Director approved on 2026-10-06: „Staršie verzie doplním
zo záznamov sedení"; where a log is missing or incomplete, Costs say „nevyčíslené".

**How.** The session log splits into runs — each ``claude -p`` starts with a prompt (a ``user`` line with text). A run
that Claude Code closed ends with a ``cost-state`` line; its usage is the difference of totals with the cost (exactly as
for a new turn, :mod:`usage_ledger`). An interrupted run has no total; its usage is completed messages, helpers' too.

Runs are assigned to cockpit turns by time. **The write time of a turn is its START**, not its end: the database gives
a message the transaction start time (``now()``), and that opened before the agent was launched (measured 2026-10-06:
a turn written at 13:49:59 with duration 73 s, its run ended at 13:51:11). A run thus belongs to the turn that started
last before it, if within its duration. On Dedo Home 178 of 190 turns match the written output (0.98–1.02). A turn is
backfilled only when assigned runs cover its written output — otherwise it stays unquantified (better „nevyčíslené" than
someone else's turn).

Nothing is deleted or overwritten: ``parts`` and a ``backfill`` marker are added to the turn's usage; input and output
from which human time is computed, stay.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db.models.pipeline import PipelineMessage
from backend.db.models.poradca import AUTHOR_PORADCA, PoradcaConversation, PoradcaMessage
from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.services.usage_ledger import (
    EMPTY_STATE,
    SYNTHETIC_MODEL,
    CostState,
    UsagePart,
    _delta,
    _parse_ts,
    _searches,
    parts_from_usage_payload,
)

#: Marker of backfilled usage — so it can be told apart (and, if needed, removed) without touching the rest of it.
BACKFILL_MARK = "transcript-2026-10"

#: A run may start this long before the turn's write time (if the transaction opened only during the run); 5–30 s gives
#: the same, best assignment on Dedo Home, NEX Inbox and NEX Manager; 60 s and more already takes neighbours' runs.
_START_SLACK = timedelta(seconds=15)
#: A run must end at most this long after the turn's duration (preparation before it and the write after don't count).
_END_SLACK = timedelta(seconds=600)
#: A helper belongs to an interrupted run if its message arose by this moment after the run's last write.
_HELPER_SLACK = timedelta(seconds=15)
#: Assigned runs must cover the turn's written output at least this much (the run result sometimes omitted helpers,
#: so the upper bound is not 1 but :data:`_MAX_COVERAGE`).
_MIN_COVERAGE = 0.98
_MAX_COVERAGE = 4.0


@dataclass
class Invocation:
    """One ``claude -p`` run from a session log."""

    start: datetime
    end: datetime
    parts: list[UsagePart]
    closed: bool  # Claude Code closed it — the parts carry its cost
    taken: bool = False

    @property
    def output_tokens(self) -> int:
        return sum(p.output_tokens for p in self.parts)


def _is_prompt(entry: dict) -> bool:
    """Run prompt: a human message with text (not a tool result, not meta, not a helper, not the summary that
    Claude Code inserts when compacting a long conversation — that is a continuation of the same run)."""
    if entry.get("type") != "user" or entry.get("isMeta") or entry.get("isSidechain") or entry.get("isCompactSummary"):
        return False
    content = (entry.get("message") or {}).get("content")
    if isinstance(content, str):
        return bool(content.strip())
    return (
        isinstance(content, list)
        and bool(content)
        and all(isinstance(b, dict) and b.get("type") == "text" for b in content)
    )


def _message_row(entry: dict) -> Optional[tuple[str, datetime, str, tuple[int, int, int, int, int]]]:
    message = entry.get("message") if entry.get("type") == "assistant" else None
    if not isinstance(message, dict) or not isinstance(message.get("usage"), dict):
        return None
    stamp = _parse_ts(entry.get("timestamp"))
    key = message.get("id") or entry.get("uuid")
    if stamp is None or not key:
        return None
    u = message["usage"]
    tokens = (
        int(u.get("input_tokens") or 0),
        int(u.get("output_tokens") or 0),
        int(u.get("cache_read_input_tokens") or 0),
        int(u.get("cache_creation_input_tokens") or 0),
        _searches(u),
    )
    return key, stamp, message.get("model") or "", tokens


def _parts_of(messages: dict[str, tuple[datetime, str, tuple[int, int, int, int, int]]]) -> list[UsagePart]:
    totals: dict[str, list[int]] = {}
    for _stamp, model, tokens in messages.values():
        if not model or model == SYNTHETIC_MODEL or not any(tokens):
            continue
        acc = totals.setdefault(model, [0, 0, 0, 0, 0])
        for i, value in enumerate(tokens):
            acc[i] += value
    return [UsagePart(m, t[0], t[1], t[2], t[3], None, t[4]) for m, t in totals.items()]


def _entries(path: Path) -> Iterable[dict]:
    with open(path, errors="replace") as fh:
        for line in fh:
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(entry, dict):
                yield entry


def read_invocations(transcript: Path) -> list[Invocation]:
    """Runs of one session log, in the order they ran."""
    out: list[Invocation] = []
    state: CostState = EMPTY_STATE
    start: Optional[datetime] = None
    last: Optional[datetime] = None
    messages: dict[str, tuple[datetime, str, tuple[int, int, int, int, int]]] = {}

    def close(cost_state: Optional[CostState]) -> None:
        nonlocal state, start, last, messages
        if start is not None:
            parts = None
            if cost_state is not None:
                parts = _delta(state, cost_state)
            closed = parts is not None
            out.append(Invocation(start, last or start, parts if closed else _parts_of(messages), closed))
        if cost_state is not None:
            state = cost_state
        start, last, messages = None, None, {}

    for entry in _entries(transcript):
        if entry.get("type") == "cost-state":
            close(CostState.from_line(entry))
            continue
        if _is_prompt(entry):
            close(None)
            start = _parse_ts(entry.get("timestamp"))
            last = start
            continue
        stamp = _parse_ts(entry.get("timestamp"))
        if start is not None and stamp is not None:
            last = max(last or stamp, stamp)
        row = _message_row(entry)
        if row is not None and start is not None:
            key, stamp, model, tokens = row
            messages[key] = (stamp, model, tokens)  # the last write of a message is final
    close(None)
    _attach_helpers(transcript, out)
    return out


def _attach_helpers(transcript: Path, invocations: list[Invocation]) -> None:
    """Helpers of an interrupted run — their messages belong to the run in which they arose; a closed run has them."""
    helpers = transcript.with_suffix("") / "subagents"
    open_runs = [i for i in invocations if not i.closed]
    if not helpers.is_dir() or not open_runs:
        return
    for path in sorted(helpers.glob("*.jsonl")):
        per_run: dict[int, dict] = {}
        for entry in _entries(path):
            row = _message_row(entry)
            if row is None:
                continue
            key, stamp, model, tokens = row
            for n, run in enumerate(open_runs):
                if run.start <= stamp <= run.end + _HELPER_SLACK:
                    per_run.setdefault(n, {})[key] = (stamp, model, tokens)
                    break
        for n, messages in per_run.items():
            run = open_runs[n]
            run.parts = _merge(run.parts, _parts_of(messages))


def _merge(a: list[UsagePart], b: list[UsagePart]) -> list[UsagePart]:
    totals: dict[str, list[int]] = {}
    for p in [*a, *b]:
        acc = totals.setdefault(p.model or "", [0, 0, 0, 0, 0])
        values = (p.input_tokens, p.output_tokens, p.cache_read_tokens, p.cache_write_tokens, p.web_search_requests)
        for i, value in enumerate(values):
            acc[i] += value
    return [UsagePart(m or None, t[0], t[1], t[2], t[3], None, t[4]) for m, t in totals.items()]


# ── turns that need backfilling ───────────────────────────────────────────────


#: Kinds of turns when assigning runs. ALL turns that ran go into assignment — even those that already have usage
#: (new write or earlier backfill): they take their runs, so the runs do not fall to a neighbouring turn. Written only
#: into ``legacy`` (input and output without parts) and ``failed`` (failed with no usage at all).
LEGACY, FAILED, METERED = "legacy", "failed", "metered"


@dataclass
class Turn:
    """A cockpit turn with the window in which it ran."""

    record: object  # PipelineMessage | PoradcaMessage
    label: str  # "project version" for the report
    started_at: datetime  # turn write time = the start of its transaction, hence before the run
    duration: timedelta
    recorded_output: int
    kind: str = LEGACY
    matched: list[Invocation] = field(default_factory=list)

    @property
    def coverage(self) -> float:
        out = sum(i.output_tokens for i in self.matched)
        return out / self.recorded_output if self.recorded_output else 0.0

    @property
    def accepted(self) -> bool:
        if self.kind == FAILED:
            return bool(self.matched)  # it has no written output — nothing to compare with, a run in its window is its
        if self.kind == LEGACY:
            return bool(self.matched) and _MIN_COVERAGE <= self.coverage <= _MAX_COVERAGE
        return False


def _kind(payload_usage: object, timing: object) -> Optional[str]:
    """Kind of turn by what it wrote about usage; ``None`` = did not run (message with neither usage nor duration)."""
    if isinstance(payload_usage, dict):
        if parts_from_usage_payload(payload_usage) is not None:
            return METERED
        has_tokens = int(payload_usage.get("input_tokens") or 0) or int(payload_usage.get("output_tokens") or 0)
        return LEGACY if has_tokens else METERED
    timing = timing if isinstance(timing, dict) else {}
    ran = float(timing.get("duration_seconds") or 0.0) or int(timing.get("parse_attempts") or 0)
    return FAILED if ran else None


def _match(turns: list[Turn], invocations: list[Invocation]) -> None:
    """Every run of a turn that started last before it, if the run ended within the turn's duration. A turn that started
    only after the run start (within tolerance) gets a run only if none started before it — else a short run takes the
    turn, which started just after it."""
    ordered = sorted(turns, key=lambda t: t.started_at)
    for run in invocations:
        before = [t for t in ordered if t.started_at <= run.start]
        soon = [t for t in ordered if run.start < t.started_at <= run.start + _START_SLACK]
        turn = before[-1] if before else (soon[0] if soon else None)
        if turn is not None and run.end <= turn.started_at + turn.duration + _END_SLACK:
            turn.matched.append(run)
            run.taken = True


@dataclass
class Plan:
    turns: list[Turn]
    #: project → number of runs in the logs that belong to no turn being backfilled (informative)
    unmatched_runs: dict[str, int]


def plan(db: Session, *, claude_home: Path, poradca_sessions: Path) -> Plan:
    """What would be backfilled — writes nothing."""
    turns: list[Turn] = []
    unmatched: dict[str, int] = {}

    rows = db.execute(
        select(PipelineMessage, Version.version_number, Project.slug)
        .join(Version, Version.id == PipelineMessage.version_id)
        .join(Project, Project.id == Version.project_id)
    ).all()
    by_project: dict[str, list[Turn]] = {}
    for msg, version_number, slug in rows:
        payload = msg.payload or {}
        kind = _kind(payload.get("usage"), payload.get("timing"))
        if kind is None:
            continue
        duration = float((payload.get("timing") or {}).get("duration_seconds") or 0.0)
        turn = Turn(
            record=msg,
            label=f"{slug} {version_number}",
            started_at=msg.created_at,
            duration=timedelta(seconds=duration),
            recorded_output=int((payload.get("usage") or {}).get("output_tokens") or 0),
            kind=kind,
        )
        by_project.setdefault(slug, []).append(turn)
    for slug, project_turns in by_project.items():
        if not any(t.kind != METERED for t in project_turns):
            continue
        directory = claude_home / "projects" / f"-opt-projects-{slug}"
        runs = [run for path in sorted(directory.glob("*.jsonl")) for run in read_invocations(path)]
        _match(project_turns, runs)
        unmatched[slug] = sum(1 for r in runs if not r.taken)
        turns.extend(t for t in project_turns if t.kind != METERED)

    answers = db.execute(
        select(PoradcaMessage, PoradcaConversation, Project.slug)
        .join(PoradcaConversation, PoradcaConversation.id == PoradcaMessage.conversation_id)
        .join(Project, Project.id == PoradcaConversation.project_id)
        .where(PoradcaMessage.author == AUTHOR_PORADCA)
    ).all()
    by_conversation: dict[uuid.UUID, tuple[PoradcaConversation, list[Turn]]] = {}
    for answer, conversation, slug in answers:
        kind = _kind(answer.usage, {"duration_seconds": answer.duration_seconds})
        if kind is None:
            continue
        turn = Turn(
            record=answer,
            label=f"{slug} Poradca",
            started_at=answer.created_at,
            duration=(answer.finished_at or answer.updated_at) - answer.created_at,
            recorded_output=int((answer.usage or {}).get("output_tokens") or 0),
            kind=kind,
        )
        by_conversation.setdefault(conversation.id, (conversation, []))[1].append(turn)
    for conversation, conversation_turns in by_conversation.values():
        if not any(t.kind != METERED for t in conversation_turns):
            continue
        # All answers of the conversation at once — a run right before the next question belongs to the previous answer.
        transcript = poradca_sessions / str(conversation.id) / f"{conversation.claude_session_id}.jsonl"
        if transcript.exists():
            _match(conversation_turns, read_invocations(transcript))
        turns.extend(t for t in conversation_turns if t.kind != METERED)
    return Plan(turns, unmatched)


def apply(db: Session, the_plan: Plan) -> int:
    """Writes the parts of backfilled turns; returns their count. A turn's input and output do not change."""
    written = 0
    for turn in the_plan.turns:
        if not turn.accepted:
            continue
        parts = [p.to_payload() for run in turn.matched for p in run.parts]
        record = turn.record
        current = (record.payload or {}).get("usage") if isinstance(record, PipelineMessage) else record.usage
        # A turn that failed gets usage only up to the cost — input and output stay zero, human time does not change.
        usage = {**(current or {"input_tokens": 0, "output_tokens": 0, "model": None})}
        usage.update(parts=parts, backfill=BACKFILL_MARK)
        if isinstance(record, PipelineMessage):
            record.payload = {**(record.payload or {}), "usage": usage}
        else:
            record.usage = usage
        written += 1
    db.commit()
    return written


def report(the_plan: Plan) -> list[str]:
    """Report per version: how many turns get backfilled, how many do not and why."""
    by_label: dict[str, list[Turn]] = {}
    for turn in the_plan.turns:
        by_label.setdefault(turn.label, []).append(turn)
    lines = []
    for label, turns in sorted(by_label.items()):
        legacy = [t for t in turns if t.kind == LEGACY]
        ok = [t for t in legacy if t.accepted]
        no_run = sum(1 for t in legacy if not t.matched)
        off = len(legacy) - len(ok) - no_run
        recorded = sum(t.recorded_output for t in legacy)
        share = sum(t.recorded_output for t in ok) / recorded if recorded else 0.0
        failed = [t for t in turns if t.kind == FAILED]
        lines.append(
            f"{label}: ťahov {len(legacy)}, doplní sa {len(ok)} ({share:.0%} výstupu), "
            f"bez záznamu {no_run}, nesedí {off}; zlyhaných {len(failed)}, doplní sa "
            f"{sum(1 for t in failed if t.accepted)}"
        )
    return lines
