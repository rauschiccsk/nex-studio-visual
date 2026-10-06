"""Cenník modelov Anthropic — vyčítaný z ťahov, ktoré zaplatil Claude Code, nie zadaný ručne (ICCINT-168).

**Odkiaľ ceny.** Každý ťah, ktorý Claude Code dokončil, nesie po modeloch tokeny štyroch druhov (vstup, výstup,
čítanie a zápis vyrovnávacej pamäte) a cenu, ktorú za ne Claude Code vypočítal (:mod:`usage_ledger`). Cena je
lineárna v tokenoch, takže zo štyroch nezávislých ťahov toho istého modelu vyjdú štyri ceny presne; z viacerých
ich vyrovnáva metóda najmenších štvorcov. Pevný pomer (napr. čítanie = 10 % vstupu) sa NEPREDPOKLADÁ — Opus 5.5
číta pamäť za 5 % vstupu, Haiku a Sonnet za 10 % (zmerané 06.10.2026).

**Zaokrúhlenie na cenník.** Vstupných tokenov mimo pamäte býva v ťahu len pár, takže ich cena vyjde z rovníc
nepresne (Dedo Home: 4,9938 namiesto 5). Každá cena sa preto zaokrúhli na najhrubší krok (1, 0,5, 0,25, 0,1 …),
pri ktorom cenník stále zopakuje ceny Claude Code v tolerancii — z toho vyjde cenník, ako ho Anthropic uvádza.

**Overenie.** Cenník platí, len keď zopakuje súčet cien Claude Code najviac s odchýlkou :data:`TOTAL_TOLERANCE`
a žiadny ťah sa neodchýli o viac než :data:`TURN_TOLERANCE`. Novšie ťahy sa s ním porovnávajú priebežne; keď ich
cenník prestane zopakovať (Anthropic zmenil cenu), vznikne nový od prvého takého ťahu a starý ostáva pre staršie.

**Kurz.** Referenčný kurz Európskej centrálnej banky sa stiahne raz, keď cenník vznikne, a uloží sa k nemu
s dátumom (Director 06.10.2026: „kurz zistiť z verejne dostupného zdroja len pri zmene ceny Antropic modelov").
Keď sa nedá stiahnuť, cenník ostane bez kurzu, ceny v eurách sa nevyčíslia a pri ďalšej kontrole sa skúsi znova.
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

#: Súčet cien Claude Code na ťahoch, ktorými sa cenník overuje, musí cenník zopakovať aspoň takto presne.
TOTAL_TOLERANCE = 0.005
#: … a žiadny jednotlivý ťah sa nesmie odchýliť viac. Drobné ťahy (pod :data:`_TINY_USD`) sa do tejto
#: kontroly nerátajú — pri nich je relatívna odchýlka zaokrúhlenie, nie iný cenník.
TURN_TOLERANCE = 0.02
_TINY_USD = 0.001
#: Kroky zaokrúhlenia cien, od najhrubšieho.
_STEPS = (1.0, 0.5, 0.25, 0.1, 0.05, 0.01, 0.005, 0.001, 0.0001)
#: Cena vyhľadávania sa uzná, keď ju každý ťah s vyhľadávaním zopakuje aspoň takto presne.
_SEARCH_TOLERANCE = 0.05

ECB_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
ECB_SOURCE = "Európska centrálna banka — referenčný kurz"
#: Po neúspešnom stiahnutí kurzu sa ďalší pokus odloží — nedostupná ECB nesmie brzdiť každé otvorenie Nákladov.
_ECB_RETRY_SECONDS = 600
_ecb_failed_at: Optional[float] = None


@dataclass(frozen=True)
class Observation:
    """Jeden ťah jedného modelu, ktorý zaplatil Claude Code."""

    at: datetime
    tokens: tuple[int, int, int, int]  # vstup, výstup, čítanie pamäte, zápis do pamäte
    cost_usd: float
    #: Vyhľadávania na webe — v ``cost_usd`` sú, ale nie sú tokeny. Ťah s nimi cenník tokenov neučí (vstup by
    #: ich cenu pohltil: Haiku vyšiel 1,55 $ namiesto 1 $); z neho sa vyčíta cena jedného vyhľadávania.
    searches: int = 0


@dataclass(frozen=True)
class Prices:
    """Ceny v dolároch za milión tokenov."""

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


# ── vyčítanie cien ─────────────────────────────────────────────────────────────


def _solve(matrix: list[list[float]], rhs: list[float]) -> Optional[list[float]]:
    """Gaussova eliminácia s výberom pivota; ``None`` pri singulárnej sústave (ťahy nie sú nezávislé)."""
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
    """Ceny za milión tokenov metódou najmenších štvorcov. Stĺpce sa najprv zmerajú na rovnakú veľkosť —
    čítaní z pamäte sú milióny, vstupov jednotky, a bez toho by sústava bola numericky slepá."""
    rows = [[t / 1_000_000.0 for t in o.tokens] for o in observations]
    scale = [max((abs(r[j]) for r in rows), default=0.0) for j in range(4)]
    if any(s <= 0 for s in scale):
        return None  # niektorý druh tokenov sa ani raz nevyskytol — jeho cena sa nedá zistiť
    rows = [[r[j] / scale[j] for j in range(4)] for r in rows]
    ys = [o.cost_usd for o in observations]
    normal = [[sum(r[i] * r[j] for r in rows) for j in range(4)] for i in range(4)]
    rhs = [sum(r[i] * y for r, y in zip(rows, ys)) for i in range(4)]
    solved = _solve(normal, rhs)
    if solved is None:
        return None
    return [solved[j] / scale[j] for j in range(4)]


def deviations(prices: Prices, observations: list[Observation]) -> tuple[float, float]:
    """``(odchýlka súčtu, najväčšia odchýlka ťahu)`` cenníka od cien Claude Code — relatívne."""
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
    """Cenník, ktorý zopakuje ceny Claude Code na týchto ťahoch — alebo ``None``, keď sa z nich zistiť nedá
    (menej než štyri nezávislé ťahy, záporná cena, alebo ich jeden cenník nezopakuje)."""
    if len(observations) < 4:
        return None
    raw = _least_squares(observations)
    if raw is None or any(p < -1e-9 for p in raw):
        return None
    current = [max(p, 0.0) for p in raw]
    if not _fits(Prices(*current), observations):
        return None
    # Cena s najväčším podielom na súčte sa zaokrúhľuje prvá — tá, čo takmer nič nestojí, potom už nepohne.
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
    """Referenčný kurz eura k doláru z ECB; ``None``, keď sa nedá stiahnuť alebo prečítať — a potom
    :data:`_ECB_RETRY_SECONDS` ani nepokúša. (Skúšky túto funkciu nahrádzajú, aby nesiahli na internet; samotné
    čítanie odpovede ECB a odklad po neúspechu skúšajú cez :func:`_throttled_ecb_rate`.)"""
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
        with urllib.request.urlopen(ECB_URL, timeout=timeout) as response:  # noqa: S310 — pevná https adresa
            body = response.read().decode("utf-8", errors="replace")
        day = re.search(r"time=['\"](\d{4}-\d{2}-\d{2})['\"]", body)
        usd = re.search(r"currency=['\"]USD['\"]\s+rate=['\"]([0-9.]+)['\"]", body)
        if not day or not usd:
            logger.warning("model_pricing: odpoveď ECB nemá kurz USD")
            return None
        return Rate(eur_usd=float(usd.group(1)), on=date.fromisoformat(day.group(1)), source=ECB_SOURCE)
    except Exception:  # noqa: BLE001 — bez kurzu sa len nevyčíslia eurá, Náklady nesmú spadnúť
        logger.warning("model_pricing: kurz ECB sa nepodarilo stiahnuť", exc_info=True)
        return None


# ── údaje ──────────────────────────────────────────────────────────────────────


def _usage_rows(db: Session) -> Iterable[tuple[datetime, object]]:
    """Všetky uložené spotreby — ťahy stavby aj odpovede Poradcu — s časom zápisu."""
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
    """Ťahy, ktoré zaplatil Claude Code, po modeloch, od najstaršieho."""
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
    """Cenníky po modeloch, od najstaršieho."""
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
    """Cena jedného vyhľadávania na webe: čo z ceny ťahu ostane po tokenoch, delené počtom vyhľadávaní —
    zaokrúhlené na najhrubší krok, ktorý každý taký ťah zopakuje; ``None``, keď taký ťah nie je alebo nesedí."""
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
    """Doplní a overí cenníky podľa ťahov zaplatených od poslednej kontroly; vráti modely, ktorým pribudol cenník.

    * model bez cenníka → vyčíta sa zo všetkých jeho ťahov (keď sa dá) a stiahne sa kurz;
    * model s cenníkom → nové ťahy sa s ním porovnajú; keď ich cenník prestane zopakovať a ťahy od toho okamihu
      určia iný cenník, vznikne nový od prvého z nich (Anthropic zmenil cenu) — starý ostáva pre staršie ťahy;
    * cenník bez kurzu → kurz sa skúsi stiahnuť znova.

    Kurz sa hľadá až pri volaní (``ecb_rate`` z modulu), nie pri definícii — skúšky ho tak vedia nahradiť
    a nikdy nesiahnu na internet."""
    fetcher = fetch_rate or ecb_rate
    fetched: list[Optional[Rate]] = []

    def rate_once() -> Optional[Rate]:
        """Kurz najviac raz za kontrolu — rovnaký pre všetky cenníky, ktoré pri nej vzniknú."""
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
        # Ťahy pred prvým nezopakovaným sú overené.
        confirmed = [o for o in fresh if not off or o.at < off[0].at]
        if confirmed:
            latest.observations += len(confirmed)
            latest.checked_until = confirmed[-1].at
        if not off:
            continue
        # Od prvého nezopakovaného: je to nový cenník? Nový sa vyčíta, až keď ho ťahy od toho okamihu jednoznačne
        # určia (aspoň štyri nezávislé a všetky ho zopakujú) — jeden zlý zápis medzi správnymi ho neurčí nikdy.
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
        # Ten istý cenník práve zapísala súbežná kontrola — jej zápis platí.
        db.rollback()
        return []
    return created


# ── oceňovanie ─────────────────────────────────────────────────────────────────


def row_for(rows: dict[str, list[ModelPrice]], model: Optional[str], at: Optional[datetime]) -> Optional[ModelPrice]:
    """Cenník, ktorým sa ocení spotreba modelu z daného času: posledný, ktorý v tom čase už platil. Spotreba
    staršia než prvý cenník modelu sa ocení prvým — model sa za ten čas nezmenil, len ešte nebolo z čoho
    cenník vyčítať."""
    candidates = rows.get(model or "", [])
    if not candidates:
        return None
    if at is None:
        return candidates[-1]
    valid = [r for r in candidates if r.valid_from <= at]
    return valid[-1] if valid else candidates[0]


def family_row(rows: dict[str, list[ModelPrice]], family_or_model: Optional[str]) -> Optional[ModelPrice]:
    """Cenník pre ručne zadaný náklad, ktorý menuje celý model alebo len rodinu (``opus``): presný model,
    inak najnovší cenník modelu tej rodiny."""
    if not family_or_model:
        return None
    if family_or_model in rows:
        return rows[family_or_model][-1]
    key = family_or_model.lower()
    matching = [rs[-1] for m, rs in rows.items() if key in m.lower()]
    return max(matching, key=lambda r: r.valid_from) if matching else None


def part_cost_usd(row: ModelPrice, part: UsagePart) -> float:
    """Cena tokenov časti spotreby — vyhľadávania na webe zvlášť (:func:`search_cost_usd`)."""
    return _prices_of(row).cost_usd(_tokens(part))


def search_cost_usd(row: ModelPrice, part: UsagePart) -> Optional[float]:
    """Cena vyhľadávaní na webe v časti spotreby; ``None``, keď ich má, ale cenník ich cenu ešte nepozná."""
    if not part.web_search_requests:
        return 0.0
    if row.web_search_usd is None:
        return None
    return row.web_search_usd * part.web_search_requests
