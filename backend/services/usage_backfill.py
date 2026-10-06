"""Doplnenie spotreby starších ťahov zo záznamov sedení (ICCINT-168).

**Prečo.** Ťahy zapísané pred v4.43.0 nesú len vstupné a výstupné tokeny z výsledku behu — vyrovnávaciu pamäť
kokpit vtedy nevidel (pri Dedo Home 2,27 miliardy prečítaných tokenov), a preto sa oceniť nedajú. Záznamy sedení,
ktoré si vedie Claude Code, však na disku zväčša ostali. Director 06.10.2026 schválil: „Staršie verzie doplním
zo záznamov sedení"; kde záznam chýba alebo je neúplný, Náklady napíšu „nevyčíslené".

**Ako.** Záznam sedenia sa rozdelí na behy — každý ``claude -p`` začína zadaním (riadok ``user`` s textom). Beh,
ktorý Claude Code uzavrel, končí riadkom ``cost-state``; jeho spotreba je rozdiel súčtov aj s cenou (presne ako
pri novom ťahu, :mod:`usage_ledger`). Prerušený beh súčet nemá; jeho spotreba sú dokončené správy, aj pomocníkov.

Behy sa priradia k ťahom kokpitu podľa času: ťah sa zapisuje hneď po svojom behu (a pri opakovaní po všetkých
pokusoch), takže beh patrí prvému ťahu zapísanému po jeho skončení, ak padne do trvania toho ťahu. Ťah sa doplní,
len keď priradené behy pokryjú jeho zapísaný výstup — inak ostane nevyčíslený (radšej „nevyčíslené" než cudzí ťah).

Nič sa nemaže ani neprepisuje: k spotrebe ťahu pribudnú ``parts`` a značka ``backfill``; vstup a výstup, z ktorých
sa ráta ľudský čas, ostávajú.
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

#: Značka doplnenej spotreby — aby sa dala odlíšiť (a keby treba, odstrániť) bez dotyku zvyšku záznamu.
BACKFILL_MARK = "transcript-2026-10"

#: Beh môže skončiť toľko pred začiatkom ťahu (meranie ťahu začína až po príprave) a ťah sa zapíše toľko po behu.
_BEFORE_SLACK = timedelta(seconds=180)
_AFTER_SLACK = timedelta(seconds=15)
#: Priradené behy musia pokryť zapísaný výstup ťahu aspoň takto (výsledok behu niekedy nerátal pomocníkov,
#: preto horná hranica nie je 1, ale :data:`_MAX_COVERAGE`).
_MIN_COVERAGE = 0.98
_MAX_COVERAGE = 4.0


@dataclass
class Invocation:
    """Jeden beh ``claude -p`` zo záznamu sedenia."""

    start: datetime
    end: datetime
    parts: list[UsagePart]
    closed: bool  # Claude Code ho uzavrel — časti nesú jeho cenu
    taken: bool = False

    @property
    def output_tokens(self) -> int:
        return sum(p.output_tokens for p in self.parts)


def _is_prompt(entry: dict) -> bool:
    """Zadanie behu: správa človeka s textom (nie výsledok nástroja, nie meta, nie pomocník, nie zhrnutie, ktoré
    Claude Code vloží pri zhustení dlhého rozhovoru — to je pokračovanie toho istého behu)."""
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
    """Behy jedného záznamu sedenia, v poradí, ako bežali."""
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
            messages[key] = (stamp, model, tokens)  # posledný zápis správy je konečný
    close(None)
    _attach_helpers(transcript, out)
    return out


def _attach_helpers(transcript: Path, invocations: list[Invocation]) -> None:
    """Pomocníci prerušeného behu — ich správy patria behu, počas ktorého vznikli. Uzavretý beh ich už má v súčte."""
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
                if run.start <= stamp <= run.end + _AFTER_SLACK:
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


# ── ťahy, ktoré treba doplniť ─────────────────────────────────────────────────


#: Druhy ťahov pri priraďovaní behov. Do priradenia idú VŠETKY ťahy, ktoré bežali — aj tie, čo spotrebu už majú
#: (nový zápis alebo skoršie doplnenie): ich behy si tak vezmú ony a nepripadnú susednému ťahu. Zapisuje sa len
#: do ``legacy`` (vstup a výstup bez častí) a ``failed`` (zlyhal bez akejkoľvek spotreby).
LEGACY, FAILED, METERED = "legacy", "failed", "metered"


@dataclass
class Turn:
    """Ťah kokpitu s oknom, v ktorom bežal."""

    record: object  # PipelineMessage | PoradcaMessage
    label: str  # "projekt verzia" pre výkaz
    recorded_at: datetime
    window_start: datetime
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
            return bool(self.matched)  # zapísaný výstup nemá — nie je s čím porovnať, beh v jeho okne je jeho
        if self.kind == LEGACY:
            return bool(self.matched) and _MIN_COVERAGE <= self.coverage <= _MAX_COVERAGE
        return False


def _kind(payload_usage: object, timing: object) -> Optional[str]:
    """Druh ťahu podľa toho, čo o spotrebe zapísal; ``None`` = nebežal (správa bez spotreby aj trvania)."""
    if isinstance(payload_usage, dict):
        if parts_from_usage_payload(payload_usage) is not None:
            return METERED
        has_tokens = int(payload_usage.get("input_tokens") or 0) or int(payload_usage.get("output_tokens") or 0)
        return LEGACY if has_tokens else METERED
    timing = timing if isinstance(timing, dict) else {}
    ran = float(timing.get("duration_seconds") or 0.0) or int(timing.get("parse_attempts") or 0)
    return FAILED if ran else None


def _match(turns: list[Turn], invocations: list[Invocation]) -> None:
    """Každý beh prvému ťahu zapísanému po jeho skončení — ak padne do trvania toho ťahu."""
    ordered = sorted(turns, key=lambda t: t.recorded_at)
    for run in sorted(invocations, key=lambda i: i.end):
        for turn in ordered:
            if turn.recorded_at + _AFTER_SLACK < run.end:
                continue
            if run.end >= turn.window_start:
                turn.matched.append(run)
                run.taken = True
            break


@dataclass
class Plan:
    turns: list[Turn]
    #: projekt → počet behov v záznamoch, ktoré nepatria žiadnemu doplňovanému ťahu (informatívne)
    unmatched_runs: dict[str, int]


def plan(db: Session, *, claude_home: Path, poradca_sessions: Path) -> Plan:
    """Čo by sa doplnilo — nič nezapisuje."""
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
            recorded_at=msg.created_at,
            window_start=msg.created_at - timedelta(seconds=duration) - _BEFORE_SLACK,
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
            recorded_at=answer.finished_at or answer.updated_at,
            window_start=answer.created_at - _AFTER_SLACK,
            recorded_output=int((answer.usage or {}).get("output_tokens") or 0),
            kind=kind,
        )
        by_conversation.setdefault(conversation.id, (conversation, []))[1].append(turn)
    for conversation, conversation_turns in by_conversation.values():
        if not any(t.kind != METERED for t in conversation_turns):
            continue
        # Všetky odpovede rozhovoru naraz — beh tesne pred ďalšou otázkou patrí predchádzajúcej odpovedi.
        transcript = poradca_sessions / str(conversation.id) / f"{conversation.claude_session_id}.jsonl"
        if transcript.exists():
            _match(conversation_turns, read_invocations(transcript))
        turns.extend(t for t in conversation_turns if t.kind != METERED)
    return Plan(turns, unmatched)


def apply(db: Session, the_plan: Plan) -> int:
    """Zapíše časti doplnených ťahov; vráti ich počet. Vstup a výstup ťahu sa nemenia."""
    written = 0
    for turn in the_plan.turns:
        if not turn.accepted:
            continue
        parts = [p.to_payload() for run in turn.matched for p in run.parts]
        record = turn.record
        current = (record.payload or {}).get("usage") if isinstance(record, PipelineMessage) else record.usage
        # Ťah, ktorý zlyhal, dostane spotrebu len do ceny — vstup a výstup ostanú nulové, ľudský čas sa nemení.
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
    """Výkaz po verziách: koľko ťahov sa doplní, koľko nie a prečo."""
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
