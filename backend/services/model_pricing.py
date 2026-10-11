"""Anthropic model price list — read off the turns paid for by Claude Code, not entered by hand (ICCINT-168).

**Source of prices.** Every turn that Claude Code finished carries, per model, tokens of four kinds (input, output,
cache reads and cache writes) and the price Claude Code computed for them (:mod:`usage_ledger`). The price is
linear in tokens, so four independent turns of the same model yield exactly four prices; with more turns
they are fitted by least squares. A fixed ratio (e.g. read = 10 % of input) is NOT ASSUMED — Opus 5.5
reads cache at 5 % of input, Haiku and Sonnet at 10 % (measured 06.10.2026).

**Rounding to the price list.** A turn has only a few non-cached input tokens, so their price comes out of the
equations imprecisely (Dedo Home: 4.9938 instead of 5). Each price is therefore rounded to the coarsest step (1, 0.5,
0.25, 0.1 …) at which the list still reproduces Claude Code prices within tolerance — that is the list Anthropic states.

**Verification.** The list is valid only if it reproduces the sum of Claude Code prices within :data:`TOTAL_TOLERANCE`
and no turn deviates by more than :data:`TURN_TOLERANCE`. Newer turns are compared with it continuously; once it stops
reproducing them (Anthropic changed the price), a new list starts at the first such turn; the old one serves older ones.

**Exchange rate.** The European Central Bank reference rate is downloaded once at list creation and stored with it
with a date (Director 06.10.2026: „kurz zistiť z verejne dostupného zdroja len pri zmene ceny Antropic modelov").
If it cannot be downloaded, the list stays without a rate, euro prices are not computed and the next check tries again.
"""

from __future__ import annotations

import logging
import re
import time
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime
from typing import Callable, Iterable, Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.db.models.model_price import ModelPrice
from backend.db.models.pipeline import PipelineMessage
from backend.db.models.poradca import PoradcaMessage
from backend.services.usage_ledger import UsagePart, parts_from_usage_payload

logger = logging.getLogger(__name__)

#: The sum of Claude Code prices on the turns that verify the price list must be reproduced at least this exactly.
TOTAL_TOLERANCE = 0.005
#: … and no single turn may deviate more. Tiny turns (below :data:`_TINY_USD`) are not counted in this
#: check — for them the relative deviation is rounding, not a different price list.
TURN_TOLERANCE = 0.02
_TINY_USD = 0.001
#: Price rounding steps, from the coarsest.
_STEPS = (1.0, 0.5, 0.25, 0.1, 0.05, 0.01, 0.005, 0.001, 0.0001)
#: The web search price is accepted when every turn with a search reproduces it at least this exactly.
_SEARCH_TOLERANCE = 0.05

ECB_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
ECB_SOURCE = "Európska centrálna banka — referenčný kurz"
#: After a failed rate download the next try is postponed — an unavailable ECB must not slow each opening of Náklady.
_ECB_RETRY_SECONDS = 600
_ecb_failed_at: Optional[float] = None


@dataclass(frozen=True)
class Observation:
    """One turn of one model, paid for by Claude Code."""

    at: datetime
    tokens: tuple[int, int, int, int]  # input, output, cache read, cache write
    cost_usd: float
    #: Web searches — in ``cost_usd`` but not tokens. A turn with them does not teach the token price list (input would
    #: absorb their price: Haiku came out at $1.55 instead of $1); the price of one search is read off such a turn.
    searches: int = 0


@dataclass(frozen=True)
class Prices:
    """Prices in dollars per million tokens."""

    input_usd: float
    output_usd: float
    cache_read_usd: float
    cache_write_usd: float

    def as_tuple(self) -> tuple[float, float, float, float]:
        return (self.input_usd, self.output_usd, self.cache_read_usd, self.cache_write_usd)

    def cost_usd(self, tokens: Iterable[int]) -> float:
        return sum(t * p for t, p in zip(tokens, self.as_tuple())) / 1_000_000.0


@dataclass(frozen=True)
class Rate:
    eur_usd: float
    on: date
    source: str


def _tokens(part: UsagePart) -> tuple[int, int, int, int]:
    return (part.input_tokens, part.output_tokens, part.cache_read_tokens, part.cache_write_tokens)


# ── reading off the prices ─────────────────────────────────────────────────────────────


def _solve(matrix: list[list[float]], rhs: list[float]) -> Optional[list[float]]:
    """Gaussian elimination with pivot selection; ``None`` for a singular system (the turns are not independent)."""
    n = len(rhs)
    a = [row[:] + [rhs[i]] for i, row in enumerate(matrix)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(a[r][col]))
        if abs(a[pivot][col]) < 1e-12:
            return None
        a[col], a[pivot] = a[pivot], a[col]
        for r in range(n):
            if r != col:
                factor = a[r][col] / a[col][col]
                for c in range(col, n + 1):
                    a[r][c] -= factor * a[col][c]
    return [a[i][n] / a[i][i] for i in range(n)]


def _least_squares(observations: list[Observation]) -> Optional[list[float]]:
    """Prices per million tokens by least squares. The columns are first scaled to the same magnitude —
    cache reads are millions, inputs a few units, and without that the system would be numerically blind."""
    rows = [[t / 1_000_000.0 for t in o.tokens] for o in observations]
    scale = [max((abs(r[j]) for r in rows), default=0.0) for j in range(4)]
    if any(s <= 0 for s in scale):
        return None  # some kind of token never occurred — its price cannot be determined
    rows = [[r[j] / scale[j] for j in range(4)] for r in rows]
    ys = [o.cost_usd for o in observations]
    normal = [[sum(r[i] * r[j] for r in rows) for j in range(4)] for i in range(4)]
    rhs = [sum(r[i] * y for r, y in zip(rows, ys)) for i in range(4)]
    solved = _solve(normal, rhs)
    if solved is None:
        return None
    return [solved[j] / scale[j] for j in range(4)]


def deviations(prices: Prices, observations: list[Observation]) -> tuple[float, float]:
    """``(sum deviation, largest turn deviation)`` of the price list from the Claude Code prices — relative."""
    total_actual = sum(o.cost_usd for o in observations)
    total_fit = sum(prices.cost_usd(o.tokens) for o in observations)
    total = abs(total_fit - total_actual) / total_actual if total_actual > 0 else 0.0
    worst = max(
        (abs(prices.cost_usd(o.tokens) - o.cost_usd) / o.cost_usd for o in observations if o.cost_usd >= _TINY_USD),
        default=0.0,
    )
    return total, worst


def _fits(prices: Prices, observations: list[Observation]) -> bool:
    total, worst = deviations(prices, observations)
    return total <= TOTAL_TOLERANCE and worst <= TURN_TOLERANCE


def fit_prices(observations: list[Observation]) -> Optional[Prices]:
    """A price list that reproduces the Claude Code prices on these turns — or ``None`` when they do not determine it
    (fewer than four independent turns, a negative price, or one price list does not reproduce them)."""
    if len(observations) < 4:
        return None
    raw = _least_squares(observations)
    if raw is None or any(p < -1e-9 for p in raw):
        return None
    current = [max(p, 0.0) for p in raw]
    if not _fits(Prices(*current), observations):
        return None
    # The price with the largest share of the sum is rounded first — the almost-free one then no longer moves it.
    weight = [sum(o.tokens[j] for o in observations) * current[j] for j in range(4)]
    for j in sorted(range(4), key=lambda k: -weight[k]):
        for step in _STEPS:
            candidate = current[:]
            candidate[j] = round(round(current[j] / step) * step, 6)
            if candidate[j] < 0:
                continue
            if _fits(Prices(*candidate), observations):
                current = candidate
                break
    return Prices(*current)


# ── kurz ───────────────────────────────────────────────────────────────────────


def ecb_rate(timeout: float = 15.0) -> Optional[Rate]:
    """Reference euro-to-dollar rate from the ECB; ``None`` when it cannot be downloaded or read — and then it is not
    retried for :data:`_ECB_RETRY_SECONDS`. (Tests replace this function to avoid touching the internet; reading the ECB
    response itself and the postponement after a failure are tested through :func:`_throttled_ecb_rate`.)"""
    return _throttled_ecb_rate(timeout)


def _throttled_ecb_rate(timeout: float) -> Optional[Rate]:
    global _ecb_failed_at
    if _ecb_failed_at is not None and time.monotonic() - _ecb_failed_at < _ECB_RETRY_SECONDS:
        return None
    rate = _fetch_ecb_rate(timeout)
    _ecb_failed_at = None if rate is not None else time.monotonic()
    return rate


def _fetch_ecb_rate(timeout: float = 15.0) -> Optional[Rate]:
    try:
        with urllib.request.urlopen(ECB_URL, timeout=timeout) as response:  # noqa: S310 — fixed https address
            body = response.read().decode("utf-8", errors="replace")
        day = re.search(r"time=['\"](\d{4}-\d{2}-\d{2})['\"]", body)
        usd = re.search(r"currency=['\"]USD['\"]\s+rate=['\"]([0-9.]+)['\"]", body)
        if not day or not usd:
            logger.warning("model_pricing: odpoveď ECB nemá kurz USD")
            return None
        return Rate(eur_usd=float(usd.group(1)), on=date.fromisoformat(day.group(1)), source=ECB_SOURCE)
    except Exception:  # noqa: BLE001 — without a rate only the euros are not computed, Náklady must not crash
        logger.warning("model_pricing: kurz ECB sa nepodarilo stiahnuť", exc_info=True)
        return None


# ── data ──────────────────────────────────────────────────────────────────────


def _usage_rows(db: Session) -> Iterable[tuple[datetime, object]]:
    """All stored spend — build turns and Poradca answers — with the write time."""
    for at, usage in db.execute(
        select(PipelineMessage.created_at, PipelineMessage.payload["usage"]).where(
            PipelineMessage.payload["usage"]["parts"].is_not(None)
        )
    ):
        yield at, usage
    for at, usage in db.execute(
        select(PoradcaMessage.created_at, PoradcaMessage.usage).where(PoradcaMessage.usage["parts"].is_not(None))
    ):
        yield at, usage


def observations_by_model(db: Session) -> dict[str, list[Observation]]:
    """Turns paid for by Claude Code, per model, from the oldest."""
    out: dict[str, list[Observation]] = {}
    for at, usage in _usage_rows(db):
        for part in parts_from_usage_payload(usage) or []:
            if part.model and part.cost_usd is not None and part.cost_usd > 0 and part.tokens > 0:
                observation = Observation(at, _tokens(part), part.cost_usd, part.web_search_requests)
                out.setdefault(part.model, []).append(observation)
    for rows in out.values():
        rows.sort(key=lambda o: o.at)
    return out


def price_rows(db: Session) -> dict[str, list[ModelPrice]]:
    """Price lists per model, from the oldest."""
    out: dict[str, list[ModelPrice]] = {}
    for row in db.execute(select(ModelPrice).order_by(ModelPrice.valid_from.asc())).scalars():
        out.setdefault(row.model, []).append(row)
    return out


def _new_row(model: str, prices: Prices, observations: list[Observation], rate: Optional[Rate]) -> ModelPrice:
    _, worst = deviations(prices, observations)
    return ModelPrice(
        model=model,
        valid_from=observations[0].at,
        input_usd=prices.input_usd,
        output_usd=prices.output_usd,
        cache_read_usd=prices.cache_read_usd,
        cache_write_usd=prices.cache_write_usd,
        eur_usd=rate.eur_usd if rate else None,
        rate_date=rate.on if rate else None,
        rate_source=rate.source if rate else None,
        observations=len(observations),
        max_deviation=worst,
        checked_until=observations[-1].at,
    )


def _prices_of(row: ModelPrice) -> Prices:
    return Prices(row.input_usd, row.output_usd, row.cache_read_usd, row.cache_write_usd)


def search_price(prices: Prices, observations: list[Observation]) -> Optional[float]:
    """Price of one web search: what is left of the turn price after tokens, divided by the number of searches —
    rounded to the coarsest step every such turn reproduces; ``None`` when there is no such turn or it does not fit."""
    with_search = [o for o in observations if o.searches > 0]
    if not with_search:
        return None
    each = sorted((o.cost_usd - prices.cost_usd(o.tokens)) / o.searches for o in with_search)
    middle = each[len(each) // 2]
    for step in _STEPS:
        candidate = round(round(middle / step) * step, 6)
        if candidate > 0 and all(
            abs(prices.cost_usd(o.tokens) + candidate * o.searches - o.cost_usd) <= _SEARCH_TOLERANCE * o.cost_usd
            for o in with_search
        ):
            return candidate
    return None


def refresh(db: Session, *, fetch_rate: Optional[Callable[[], Optional[Rate]]] = None) -> list[str]:
    """Completes and verifies price lists from the turns paid since the last check; returns models that gained one.

    * model without a price list → read off all its turns (when possible) and the rate is downloaded;
    * model with a price list → new turns are compared with it; when it stops reproducing them and turns from then on
      determine another list, a new one starts from the first (Anthropic changed a price); the old one serves older;
    * price list without a rate → the rate download is tried again.

    The rate is looked up only at call time (``ecb_rate`` from the module), not at definition — so tests can replace it
    and never reach the internet."""
    fetcher = fetch_rate or ecb_rate
    fetched: list[Optional[Rate]] = []

    def rate_once() -> Optional[Rate]:
        """The rate at most once per check — the same for all price lists created during it."""
        if not fetched:
            fetched.append(fetcher())
        return fetched[0]

    created: list[str] = []
    existing = price_rows(db)
    for model, everything in observations_by_model(db).items():
        observations = [o for o in everything if o.searches == 0]
        rows = existing.get(model, [])
        if not rows:
            prices = fit_prices(observations)
            if prices is None:
                continue
            row = _new_row(model, prices, observations, rate_once())
            row.web_search_usd = search_price(prices, everything)
            db.add(row)
            created.append(model)
            continue
        latest = rows[-1]
        if latest.web_search_usd is None:
            latest.web_search_usd = search_price(
                _prices_of(latest), [o for o in everything if o.at >= latest.valid_from]
            )
        fresh = [o for o in observations if o.at > latest.checked_until]
        if not fresh:
            continue
        current = _prices_of(latest)
        off = [o for o in fresh if o.cost_usd >= _TINY_USD and not _fits(current, [o])]
        # Turns before the first non-reproduced one are verified.
        confirmed = [o for o in fresh if not off or o.at < off[0].at]
        if confirmed:
            latest.observations += len(confirmed)
            latest.checked_until = confirmed[-1].at
        if not off:
            continue
        # From the first non-reproduced turn: is it a new list? A new one is read off only when the turns from then
        # on determine it unambiguously (at least four independent, all reproducing it) — one bad record never does.
        since = [o for o in observations if o.at >= off[0].at]
        prices = fit_prices(since)
        if prices is None or prices == current:
            continue
        logger.warning("model_pricing: cena modelu %s sa zmenila od %s", model, off[0].at.isoformat())
        row = _new_row(model, prices, since, rate_once())
        row.web_search_usd = search_price(prices, [o for o in everything if o.at >= off[0].at])
        db.add(row)
        created.append(model)
    for row in (r for rows in existing.values() for r in rows if r.eur_usd is None):
        rate = rate_once()
        if rate is None:
            break
        row.eur_usd, row.rate_date, row.rate_source = rate.eur_usd, rate.on, rate.source
    try:
        db.commit()
    except IntegrityError:
        # The same price list was just written by a concurrent check — its write stands.
        db.rollback()
        return []
    return created


# ── pricing ─────────────────────────────────────────────────────────────────


def row_for(rows: dict[str, list[ModelPrice]], model: Optional[str], at: Optional[datetime]) -> Optional[ModelPrice]:
    """The price list used to price a model's spend from the given time: the latest one already valid then. Spend
    older than the model's first price list is priced by the first — the model did not change in that time, there was
    just nothing yet to read a price list from."""
    candidates = rows.get(model or "", [])
    if not candidates:
        return None
    if at is None:
        return candidates[-1]
    valid = [r for r in candidates if r.valid_from <= at]
    return valid[-1] if valid else candidates[0]


def family_row(rows: dict[str, list[ModelPrice]], family_or_model: Optional[str]) -> Optional[ModelPrice]:
    """The price list for a manually entered cost that names a whole model or only a family (``opus``): the exact model,
    otherwise the newest price list of that family."""
    if not family_or_model:
        return None
    if family_or_model in rows:
        return rows[family_or_model][-1]
    key = family_or_model.lower()
    matching = [rs[-1] for m, rs in rows.items() if key in m.lower()]
    return max(matching, key=lambda r: r.valid_from) if matching else None


def part_cost_usd(row: ModelPrice, part: UsagePart) -> float:
    """Token price of a part of the spend — web searches separately (:func:`search_cost_usd`)."""
    return _prices_of(row).cost_usd(_tokens(part))


def search_cost_usd(row: ModelPrice, part: UsagePart) -> Optional[float]:
    """Price of web searches in a part of the spend; ``None`` when it has them but the price list lacks their price."""
    if not part.web_search_requests:
        return 0.0
    if row.web_search_usd is None:
        return None
    return row.web_search_usd * part.web_search_requests
