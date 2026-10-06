"""Spotreba jedného behu Claude Code — zo záznamu sedenia, ktorý si Claude Code vedie sám (ICCINT-168).

**Prečo nie z výsledku behu.** Výsledok (``result``) nesie ``total_cost_usd`` a ``modelUsage`` — ale pri
pokračovaní sedenia (``--resume``) sú to SÚČTY ZA CELÉ SEDENIE, nie za beh (zmerané 06.10.2026, Claude Code
2.1.290: druhý beh v sedení hlásil 0,0141886 $ = prvý 0,0113585 $ + druhý sám 0,0028301 $). Kokpit pokračuje
v tom istom sedení pri každom ťahu stavby; sčítanie týchto čísel by Náklady mnohonásobne nafúklo.

**Odkiaľ teda.** Na konci každého dokončeného behu zapíše Claude Code do záznamu sedenia riadok ``cost-state``
so súčtom po modeloch (tokeny všetkých druhov aj cenu). Kokpit si posledný súčet prečíta PRED behom a znova
PO ňom; spotreba behu je ich rozdiel — presne to, čo Claude Code naúčtoval, aj s pomocníkmi (Task) a aj pre
beh, ktorý síce skončil chybou, ale Claude Code ho stihol uzavrieť.

**Prerušený beh** (vypršal čas, zabitý proces) ``cost-state`` nezapíše a v súčte Claude Code chýba (zmerané:
zabitý beh medzi dvoma dokončenými sa do súčtu nedostal). Jeho tokeny však v zázname sú — konečné čísla každej
DOKONČENEJ správy, aj u pomocníkov (``<sedenie>/subagents/*.jsonl``). Správa rozpísaná v okamihu zabitia má
v zázname nuly a jej doterajší výstup sa zistiť nedá. Ceny takej časti Claude Code nehlási (``cost_usd`` je
``None``); ocení ju cenník (:mod:`backend.services.model_pricing`).

**Priebežné správy** zo ``stream-json`` sa na spotrebu nepoužívajú: nesú počet výstupných tokenov z okamihu,
keď správa začala (zmerané: 4 namiesto 2 983).
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

#: Model, ktorým Claude Code označuje vlastné náhradné správy (chyba API, prerušenie) — nič sa za ne neplatí.
SYNTHETIC_MODEL = "<synthetic>"

#: Riadky záznamu s časom pred začiatkom behu, o ktoré sa ešte opiera hranica behu (hodiny hostiteľa aj
#: kontajnera sú tie isté; rezerva kryje len zaokrúhlenie časovej pečiatky).
_CLOCK_SLACK = timedelta(seconds=2)

#: Po koľkých bajtoch sa záznam číta odzadu. Záznam stavby má desiatky MB (Dedo Home 71 MB) a beh je na konci.
_CHUNK = 1 << 20


@dataclass(frozen=True)
class UsagePart:
    """Spotreba jedného modelu v jednom behu.

    ``cost_usd`` je cena, ktorú za presne tieto tokeny vypočítal Claude Code — ``None``, keď ju nenahlásil
    (prerušený beh, alebo sa záznam sedenia nedal prečítať). Tokeny ``cache_write`` sú zápis do vyrovnávacej
    pamäte, ``cache_read`` čítanie z nej; ``input`` je vstup mimo nej."""

    model: Optional[str]
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: Optional[float] = None
    #: Vyhľadávania na webe — Claude Code ich účtuje zvlášť (nie sú tokeny), a v ``cost_usd`` sú (ICCINT-168).
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
    """Časti spotreby uložené pri ťahu, alebo ``None`` pri zázname spred ICCINT-168 (len vstup a výstup bez
    vyrovnávacej pamäte — taký sa oceniť nedá a Náklady to musia povedať, nie ho podceniť)."""
    if not isinstance(usage, dict) or not isinstance(usage.get("parts"), list):
        return None
    return [p for p in (UsagePart.from_payload(d) for d in usage["parts"]) if p is not None]


# ── záznam sedenia ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CostState:
    """Súčet sedenia z riadku ``cost-state``: model → (vstup, výstup, čítanie, zápis, vyhľadávania, cena $).

    ``unknown_cost``: Claude Code cenu niektorého modelu nepozná (``hasUnknownModelCost``) — jeho ``costUSD`` je
    potom odhad a nesmie sa vydávať za cenu Claude Code (ani učiť cenník)."""

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
    """Súbor záznamu sedenia ``<priečinok sedení projektu>/<id sedenia>.jsonl``."""
    return session_dir / f"{claude_session_id}.jsonl"


def _lines_backwards(path: Path) -> Iterator[str]:
    """Riadky súboru od konca — záznam stavby má desiatky MB a to podstatné je na jeho konci."""
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
    """Posledný súčet sedenia; :data:`EMPTY_STATE`, keď sedenie súčet ešte nemá (nové sedenie, žiadny
    dokončený beh), ``None``, keď sa záznam nedá prečítať."""
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
    except Exception:  # noqa: BLE001 — číta sa aj PRED ťahom; meranie nesmie zhodiť ťah
        logger.warning("usage: záznam sedenia %s sa nedá prečítať", transcript, exc_info=True)
        return None


def _delta(before: CostState, after: CostState) -> Optional[list[UsagePart]]:
    """Spotreba medzi dvoma súčtami, po modeloch. ``None``, keď súčet klesol — to nie je ten istý rad
    (sedenie sa založilo nanovo) a rozdiel by klamal."""
    if any(model not in after.models for model in before.models):
        return None  # model zo súčtu zmizol — nový rad, nie pokračovanie
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
    """Čas zápisu; bez časového pásma ho Claude Code píše v UTC (porovnáva sa so začiatkom behu v UTC)."""
    if not isinstance(value, str):
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return stamp if stamp.tzinfo is not None else stamp.replace(tzinfo=timezone.utc)


def _is_cost_state(line: str) -> bool:
    """Riadok so súčtom sedenia — podľa typu, nie podľa textu (ten istý text môže byť aj v nástroji agenta)."""
    if '"cost-state"' not in line:
        return False
    try:
        entry = json.loads(line)
    except json.JSONDecodeError:
        return False
    return isinstance(entry, dict) and entry.get("type") == "cost-state"


def _finished_messages(lines: Iterator[str], since: datetime, *, stop_at_cost_state: bool) -> dict[str, tuple]:
    """Konečná spotreba každej správy modelu od ``since`` — id správy → (model, vstup, výstup, čítanie, zápis).

    Claude Code zapisuje správu po blokoch, každý so spotrebou z toho okamihu; platí POSLEDNÝ zápis správy.
    Číta sa odzadu, takže prvý videný zápis správy je ten posledný."""
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
    """Spotreba prerušeného behu: dokončené správy hlavného sedenia aj pomocníkov od začiatku behu, po modeloch.

    Hlavný záznam sa číta odzadu po posledný ``cost-state`` (koniec posledného DOKONČENÉHO behu; všetko za ním
    je novšie), pomocníci (``<sedenie>/subagents``) celí, ak sa menili počas behu."""
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
    """Spotreba behu, ktorý práve skončil — akokoľvek.

    Pribudol súčet sedenia → beh Claude Code uzavrel a spotreba je rozdiel súčtov (s jeho cenou). Nepribudol →
    beh bol prerušený a spotreba sú jeho dokončené správy (bez ceny). ``None``, keď sa záznam nedá prečítať
    alebo súčet nemá zmysel — volajúci potom použije, čo vie z výsledku behu, a nič nevymýšľa."""
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
    except Exception:  # noqa: BLE001 — meranie nesmie zhodiť ťah, ktorý meria
        logger.warning("usage: spotrebu zo záznamu sedenia %s sa nepodarilo zistiť", transcript, exc_info=True)
        return None
