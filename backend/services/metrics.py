"""Per-phase project cost computation — the *Náklady* screen (E5; CR-V2-029, reshaped in CR-V2-063).

Read-only aggregation over the live WS-D capture (per-dispatch ``PipelineMessage.payload.usage``/
``.timing``, grouped by the per-turn ``phase`` stamp by :func:`pipeline_metrics.aggregate_usage_by_phase`)
+ Manažér-wait accumulation (``PipelineState.total_director_wait_seconds`` — the column name is kept;
it now meters Manažér-wait, CR-V2-004) + the human-work coefficient/wages + per-model pricing
(``system_settings``, env fallback for the flat pair) + the hand-entered ``external_cost`` entries.

Single reproducible base = all tokens (IN+OUT, incl. retries/failed) per PHASE per version. From it:
the **agent** side (tokens × per-model API price; active = Σ timing duration), the **human** side
(tokens × the single minutes-per-Mtok coefficient × the row's wage), the **idle** split (real
wall-clock, never folded into agent time) and the **Manažér** overhead (measured wait, info-only).
The cockpit meters only its OWN builds, so work done outside one (Dedo in the terminal, a developer
working directly) enters through ``external_cost`` as a SEPARATE row and a separate ``…_external``
total. Per-customer deploy is a separate ops cost, not part of this build-pipeline figure.

**Honest by construction:** any figure depending on an unconfigured input (price / coefficient / wage)
is ``None`` — never fabricated; measured and entered figures are summed but never merged. No pipeline
mutation, no live ``claude`` call. (The v1 ROI headline — N× faster, M× cheaper, EUR saved — is retired
with CR-V2-063: the Manažér needs what it cost, not whether we beat a human.)
"""

from __future__ import annotations

import logging
import math
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.db.models.external_cost import ExternalCost
from backend.db.models.model_price import ModelPrice
from backend.db.models.pipeline import STAGE_VALUES, PipelineMessage, PipelineState
from backend.db.models.poradca import AUTHOR_PORADCA, PoradcaConversation, PoradcaMessage
from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.schemas.metrics import (
    CostRowRead,
    CostTotalsRead,
    ManagerOverheadRead,
    PriceListRead,
    ProjectCostsRead,
    UnpricedRead,
    UsageTotalsRead,
    VersionCostsRead,
)
from backend.services import model_pricing, system_setting
from backend.services.pipeline_metrics import (
    UsageTotals,
    aggregate_pipeline_usage,
    aggregate_usage_by_phase,
)
from backend.services.usage_ledger import UsagePart, parts_from_usage_payload

logger = logging.getLogger(__name__)

#: The build phases compared against a human, derived from the canonical stage tuple minus the
#: terminal ``done`` (no work/tokens attribute to ``done`` — it is the post-build terminal phase), so
#: the list stays in lock-step with ``STAGE_VALUES`` (5 phases today) and a phase rename can't fall out
#: silently. The Manažér overhead (human-in-the-loop wait) is handled separately as an info-only row;
#: ``system`` is engine-only (a message with no phase stamp + a ``system`` author) and lands in the
#: agent-only ``system`` row.
TERMINAL_PHASE = "done"
COMPARISON_PHASES: tuple[str, ...] = tuple(s for s in STAGE_VALUES if s != TERMINAL_PHASE)
#: Row keys outside the build phases. ``externe`` is hand-entered spend (its wage key is
#: ``metrics_hourly_wage_externe``); ``system`` is un-phased engine spend and is AGENT-ONLY — it has no
#: wage key and never carries human figures.
EXTERNAL_ROW_KEY = "externe"
SYSTEM_ROW_KEY = "system"
#: ICCINT-167: spotreba Poradcu — riadok zvlášť od fáz stavby, len strana agenta (ako ``system``).
PORADCA_ROW_KEY = "poradca"

#: The single human-work coefficient (minutes of human work per 1M tokens) — one key for every phase
#: AND for the external row (CR-V2-063 collapsed the five per-phase copies that only drifted apart).
COEFFICIENT_KEY = "metrics_minutes_per_mtok"

#: Every row key that HAS a human side, i.e. that owns a ``metrics_hourly_wage_*`` key: the comparison
#: phases + ``externe``. The agent-only ``system`` row is deliberately absent (no wage key exists, nor
#: will one). Single source for both :func:`_wages` and the ``wages_configured`` flag, so a wage the
#: screen offers can never fail to satisfy the flag.
WAGE_ROW_KEYS: tuple[str, ...] = (*COMPARISON_PHASES, EXTERNAL_ROW_KEY)


# ── small reads / pricing primitives ──────────────────────────────────────────


def _totals_read(t: UsageTotals) -> UsageTotalsRead:
    return UsageTotalsRead(
        input_tokens=t.input_tokens,
        output_tokens=t.output_tokens,
        duration_seconds=t.duration_seconds,
        messages=t.messages,
    )


#: Why spend could not be priced — sentences the screen shows next to the figure (ICCINT-168).
UNRECORDED_REASON = "záznam sedenia sa nezachoval — ťah spred v4.43.0 má len vstup a výstup, bez vyrovnávacej pamäte"
NO_PRICE_REASON = "cenník modelu {model} sa zatiaľ nedá zistiť — málo ťahov, ktoré zaplatil Claude Code"
NO_RATE_REASON = "kurz eura sa nepodarilo stiahnuť z ECB"
NO_MODEL_REASON = "spotreba neuvádza model"
NO_SEARCH_PRICE_REASON = "vyhľadávanie na webe — cenník modelu {model} jeho cenu zatiaľ nepozná"
UNKNOWN_SPEND_REASON = (
    "ťah, ktorého spotreba sa nezaznamenala (zlyhal pred v4.43.0, alebo sa záznam sedenia nedal prečítať)"
)


@dataclass
class _Pricing:
    """The price lists (by model) and the ones a scope actually used — those are what the screen shows."""

    lists: dict[str, list[ModelPrice]]
    used: dict[uuid.UUID, ModelPrice] = field(default_factory=dict)
    #: row key → (exact agent euros, exact human euros or None) — before rounding UP, so the project scope can be
    #: reconciled with its versions (:func:`_reconcile_with_versions`).
    exact: dict[str, tuple[float, Optional[float]]] = field(default_factory=dict)

    def for_scope(self) -> "_Pricing":
        return _Pricing(self.lists)


@dataclass
class _Cost:
    eur: float = 0.0
    priced: bool = False
    #: reason → [parts, tokens]
    unpriced: dict[str, list[int]] = field(default_factory=dict)

    def miss(self, reason: str, count: int, tokens: int) -> None:
        acc = self.unpriced.setdefault(reason, [0, 0])
        acc[0] += count
        acc[1] += tokens

    def whole_euros(self, has_spend: bool) -> Optional[int]:
        """Rounded UP to whole euros (Director 06.10.2026: rather a little more than less). ``None`` when there
        was spend and none of it could be priced; a real ``0`` when there was none."""
        if self.priced:
            return _ceil_euros(self.eur)
        return None if has_spend else 0

    def unpriced_read(self) -> list[UnpricedRead]:
        return [UnpricedRead(reason=r, turns=c, tokens=t) for r, (c, t) in sorted(self.unpriced.items())]


def _price_part(pricing: _Pricing, cost: _Cost, part: UsagePart, at: Optional[datetime]) -> None:
    if not part.model:
        cost.miss(NO_MODEL_REASON, 1, part.tokens)
        return
    row = model_pricing.row_for(pricing.lists, part.model, at)
    if row is None:
        cost.miss(NO_PRICE_REASON.format(model=part.model), 1, part.tokens)
        return
    if not row.eur_usd:
        cost.miss(NO_RATE_REASON, 1, part.tokens)
        return
    cost.eur += model_pricing.part_cost_usd(row, part) / row.eur_usd
    cost.priced = True
    pricing.used[row.id] = row
    searches = model_pricing.search_cost_usd(row, part)
    if searches is None:
        cost.miss(NO_SEARCH_PRICE_REASON.format(model=part.model), part.web_search_requests, 0)
    else:
        cost.eur += searches / row.eur_usd


def _price_totals(pricing: _Pricing, t: UsageTotals) -> _Cost:
    """Tokens × the price list valid when they were spent × that list's rate — one formula for every row."""
    cost = _Cost()
    for at, part in t.parts:
        _price_part(pricing, cost, part, at)
    if t.unrecorded_turns:
        cost.miss(UNRECORDED_REASON, t.unrecorded_turns, t.unrecorded_tokens)
    if t.unknown_turns:
        cost.miss(UNKNOWN_SPEND_REASON, t.unknown_turns, 0)
    return cost


def _has_spend(t: UsageTotals) -> bool:
    return bool(t.parts or t.unrecorded_turns or t.unknown_turns)


def _price_list_read(row: ModelPrice) -> PriceListRead:
    def eur(usd: float) -> Optional[float]:
        return round(usd / row.eur_usd, 4) if row.eur_usd else None

    return PriceListRead(
        model=row.model,
        valid_from=row.valid_from,
        input_usd=row.input_usd,
        output_usd=row.output_usd,
        cache_read_usd=row.cache_read_usd,
        cache_write_usd=row.cache_write_usd,
        web_search_usd=row.web_search_usd,
        eur_usd=row.eur_usd,
        rate_date=row.rate_date,
        rate_source=row.rate_source,
        input_eur=eur(row.input_usd),
        output_eur=eur(row.output_usd),
        cache_read_eur=eur(row.cache_read_usd),
        cache_write_eur=eur(row.cache_write_usd),
        web_search_eur=eur(row.web_search_usd) if row.web_search_usd is not None else None,
        observations=row.observations,
        max_deviation=row.max_deviation,
    )


def _price_list(pricing: _Pricing) -> list[PriceListRead]:
    return [_price_list_read(r) for r in sorted(pricing.used.values(), key=lambda r: (r.model, r.valid_from))]


def usage_cost(db: Session, usage: object, at: Optional[datetime]) -> Optional[float]:
    """Cena jednej odpovede Poradcu v eurách — tým istým cenníkom ako Náklady (ICCINT-168), zaokrúhlená hore
    na celé centy. ``None``, keď sa celá oceniť nedá (stará odpoveď bez záznamu, model bez cenníka, bez kurzu)."""
    parts = parts_from_usage_payload(usage)
    if not parts:
        return None
    pricing = _Pricing(model_pricing.price_rows(db))
    cost = _Cost()
    for part in parts:
        _price_part(pricing, cost, part, at)
    if cost.unpriced or not cost.priced:
        return None
    return math.ceil(round(cost.eur * 100, 6)) / 100


# ── human side ────────────────────────────────────────────────────────────────


def _human_minutes_for_phase(t: UsageTotals, conv_rate: float) -> Optional[float]:
    """tokens → minutes via the single conversion coefficient (minutes per 1M total tokens). None when
    unset (0) — the human columns then disappear rather than showing a fabricated figure."""
    if conv_rate <= 0:
        return None
    return (t.input_tokens + t.output_tokens) / 1_000_000.0 * conv_rate


def _human_exact(human_minutes: Optional[float], wage: float) -> Optional[float]:
    if human_minutes is None or wage <= 0:
        return None
    return human_minutes / 60.0 * wage


def _human_cost(human_minutes: Optional[float], wage: float) -> Optional[int]:
    """Whole euros rounded up, like every euro figure on the screen (ICCINT-168)."""
    exact = _human_exact(human_minutes, wage)
    return None if exact is None else _ceil_euros(exact)


def _ceil_euros(exact: float) -> int:
    return math.ceil(round(exact, 6))


def _coefficient(db: Session) -> float:
    """The single minutes-per-Mtok coefficient — the same one for every phase and for external cost."""
    return system_setting.get_float(db, COEFFICIENT_KEY)


def _phase_wage(db: Session, phase: str) -> float:
    """Hourly wage (currency-agnostic) for a row's human side — 0 = unset → null. Also serves the
    ``externe`` row (``metrics_hourly_wage_externe``); the ``system`` row has no wage by design."""
    return system_setting.get_float(db, f"metrics_hourly_wage_{phase}")


# ── idle / time / manazer count ────────────────────────────────────────────────


def _manager_wait_seconds(db: Session, version_id: uuid.UUID) -> float:
    """Accumulated Manažér-wait + any live open wait for a version (idle-a).

    Reads ``PipelineState.total_director_wait_seconds`` / ``awaiting_director_since`` — the COLUMN names
    are unchanged (no DDL rename, CR-V2-004) but the VALUE now meters Manažér-wait."""
    state = db.execute(select(PipelineState).where(PipelineState.version_id == version_id)).scalar_one_or_none()
    if state is None:
        return 0.0
    wait = float(state.total_director_wait_seconds or 0.0)
    if state.awaiting_director_since is not None:
        wait += (datetime.now(timezone.utc) - state.awaiting_director_since).total_seconds()
    return wait


def _manager_interventions(db: Session, version_id: uuid.UUID) -> int:
    """Count of Manažér-authored pipeline messages for a version."""
    return (
        db.execute(
            select(func.count())
            .select_from(PipelineMessage)
            .where(PipelineMessage.version_id == version_id, PipelineMessage.author == "manazer")
        ).scalar()
        or 0
    )


def _total_time_seconds(db: Session, version: Version) -> Optional[float]:
    """Real wall-clock span from min/max(message ``created_at``) FIRST (real timestamps, not integer
    days — §2.4); the released ``release_date`` is a fallback only when no message span exists. None
    when unknowable (so internal idle is never fabricated as 0)."""
    first, last = db.execute(
        select(func.min(PipelineMessage.created_at), func.max(PipelineMessage.created_at)).where(
            PipelineMessage.version_id == version.id
        )
    ).one()
    if first is not None and last is not None and last > first:
        return (last - first).total_seconds()
    if version.release_date is not None:
        return float(max((version.release_date - version.created_at.date()).days, 0) * 86400) or None
    return None


def _internal_idle_seconds(total: Optional[float], active: float, manager_wait: float) -> Optional[float]:
    """total wall-clock − active compute − manager-wait (idle-b). None (never fabricated 0) when the
    span is 0/unknown."""
    if total is None or total <= 0:
        return None
    return max(total - active - manager_wait, 0.0)


# ── rows ──────────────────────────────────────────────────────────────────────


def _has_activity(t: UsageTotals) -> bool:
    """Did this bucket meter ANY activity? Tokens OR wall-clock OR parse-attempts — a failed turn whose
    envelope carried ``timing`` but no ``usage`` is real work (0 tokens + real seconds) and must not be
    dropped, or its time would foot nowhere (metrics-v3-followup.md C1)."""
    return bool(t.input_tokens or t.output_tokens or t.duration_seconds or t.parse_attempts or t.parts)


def _measured_row(
    db: Session,
    pricing: _Pricing,
    *,
    key: str,
    kind: str,
    t: UsageTotals,
    with_human: bool,
) -> CostRowRead:
    """One measured row (a phase, Poradca or the un-phased system spend). ``share_pct`` is filled in per scope
    afterwards (see :func:`_fill_share_pct`) — it needs every row of the scope to exist first. Only a phase row
    has a human side: Poradca answers questions and the system row is engine overhead."""
    cost = _price_totals(pricing, t)
    human_minutes = _human_minutes_for_phase(t, _coefficient(db)) if with_human else None
    wage = _phase_wage(db, key) if with_human else 0.0
    pricing.exact[key] = (cost.eur, _human_exact(human_minutes, wage))
    return CostRowRead(
        key=key,
        kind=kind,
        turns=t.messages,
        input_tokens=t.input_tokens,
        output_tokens=t.output_tokens,
        cache_read_tokens=t.cache_read_tokens,
        cache_write_tokens=t.cache_write_tokens,
        share_pct=0.0,
        agent_cost=cost.whole_euros(_has_spend(t)),
        unpriced=cost.unpriced_read(),
        human_minutes=human_minutes,
        human_cost=_human_cost(human_minutes, _phase_wage(db, key)) if with_human else None,
        active_seconds=t.duration_seconds,
    )


def _build_phases(db: Session, pricing: _Pricing, by_phase: dict[str, UsageTotals]) -> list[CostRowRead]:
    """The comparison-phase rows for the phases that ACTUALLY did work — emit only a phase with SOME metered
    activity (tokens OR wall-clock OR parse-attempts), in canonical ``COMPARISON_PHASES`` order
    (metrics-v3-three-phases.md Part 2; drop predicate widened in metrics-v3-followup.md C2). A phase with NO
    metered activity is DROPPED rather than rendered as a phantom empty row: a v3 conversation project shows
    Návrh / Programovanie / Verifikácia (the three phases the collapsed one-partner flow stamps), a legacy
    v1/v2 project shows whatever phases it truly used — never a permanent empty row.

    Footing is preserved: a dropped phase contributed no tokens AND no time, and ``_overhead_totals`` still
    folds every non-comparison bucket into the ``system`` row, so the table still foots to the grand total.
    Applied to BOTH the per-version and the cumulative ``by_phase`` (both callers route here)."""
    rows: list[CostRowRead] = []
    for phase in COMPARISON_PHASES:
        t = by_phase.get(phase)
        if t is None or not _has_activity(t):
            continue
        rows.append(_measured_row(db, pricing, key=phase, kind="phase", t=t, with_human=True))
    return rows


def _overhead_totals(by_phase: dict[str, UsageTotals]) -> UsageTotals:
    """Fold every bucket that is NOT a comparison phase (``system``, the terminal ``done``, or any
    unexpected key) into a single info-only overhead bucket, so the table always foots to the grand
    total — no metered tokens silently vanish (honest by construction)."""
    overhead = UsageTotals()
    for phase, t in by_phase.items():
        if phase not in COMPARISON_PHASES:
            overhead.merge(t)
    return overhead


def _system_rows(db: Session, pricing: _Pricing, t: UsageTotals) -> list[CostRowRead]:
    """The ``kind="system"`` row (0 or 1) — un-phased engine spend, AGENT-ONLY.

    ``human_minutes``/``human_cost`` are ALWAYS ``None`` and there is no ``metrics_hourly_wage_system``
    key: engine tokens nobody stamped with a phase have no human equivalent by definition. Its
    ``agent_cost`` DOES belong to ``agent_cost_measured`` (it is metered spend) but it must never drag
    the human totals to ``None`` — hence the None-propagation in :func:`_cost_totals` is scoped to the
    phase rows."""
    if not _has_activity(t):
        return []
    return [_measured_row(db, pricing, key=SYSTEM_ROW_KEY, kind="system", t=t, with_human=False)]


def _external_rows(
    db: Session,
    pricing: _Pricing,
    project_id: uuid.UUID,
    version_id: Optional[uuid.UUID],
) -> list[CostRowRead]:
    """The ``kind="external"`` row (0 or 1) aggregating the scope's hand-entered ``external_cost`` rows.

    Scope: ``version_id`` given → that version's entries only; ``None`` (project level) → ALL the
    project's entries, version-bound and version-less alike (a version-less entry belongs to the project
    total and to no version).

    Priced with the SAME price lists as measured work (ICCINT-168): the entry names a model or just a family
    (``opus``) and carries input/output only — it is priced by that model's newest list. Converted with the
    SAME coefficient as measured work, with the ``metrics_hourly_wage_externe`` wage. ``active_seconds`` is
    0.0 (nothing was metered here) and ``turns`` is the number of entries."""
    stmt = select(ExternalCost).where(ExternalCost.project_id == project_id)
    if version_id is not None:
        stmt = stmt.where(ExternalCost.version_id == version_id)
    entries = db.execute(stmt).scalars().all()
    if not entries:
        return []

    t = UsageTotals()
    cost = _Cost()
    for entry in entries:
        input_tokens, output_tokens = int(entry.input_tokens or 0), int(entry.output_tokens or 0)
        t.add(input_tokens=input_tokens, output_tokens=output_tokens, duration_seconds=0.0, model=entry.model)
        row = model_pricing.family_row(pricing.lists, entry.model)
        model = row.model if row else entry.model
        part = UsagePart(model=model, input_tokens=input_tokens, output_tokens=output_tokens)
        _price_part(pricing, cost, part, None)

    human_minutes = _human_minutes_for_phase(t, _coefficient(db))
    pricing.exact[EXTERNAL_ROW_KEY] = (cost.eur, _human_exact(human_minutes, _phase_wage(db, EXTERNAL_ROW_KEY)))
    return [
        CostRowRead(
            key=EXTERNAL_ROW_KEY,
            kind="external",
            turns=t.messages,
            input_tokens=t.input_tokens,
            output_tokens=t.output_tokens,
            cache_read_tokens=0,
            cache_write_tokens=0,
            share_pct=0.0,
            agent_cost=cost.whole_euros(bool(t.input_tokens or t.output_tokens)),
            unpriced=cost.unpriced_read(),
            human_minutes=human_minutes,
            human_cost=_human_cost(human_minutes, _phase_wage(db, EXTERNAL_ROW_KEY)),
            active_seconds=0.0,
        )
    ]


def _poradca_rows(
    db: Session,
    pricing: _Pricing,
    project_id: uuid.UUID,
    version_id: Optional[uuid.UUID],
) -> list[CostRowRead]:
    """Riadok ``kind="poradca"`` (0 alebo 1) — odpovede Poradcu k projektu (ICCINT-167).

    Nameraná spotreba, ale mimo fáz stavby a bez ľudského ekvivalentu: Poradca nerobí prácu, ktorú by
    inak robil programátor, odpovedá na otázky. Rozsah: ``version_id`` → odpovede na otázky k tej verzii
    (verzia v čase otázky, nie súčasná voľba rozhovoru); ``None`` → všetky odpovede projektu.

    Vymazané rozhovory sa rátajú ZÁMERNE: vymazanie zahodí text, nie peniaze, ktoré odpovede stáli
    (``runner.delete_conversation`` spotrebu ponechá práve kvôli tomuto súčtu)."""
    stmt = (
        select(PoradcaMessage)
        .join(PoradcaConversation, PoradcaConversation.id == PoradcaMessage.conversation_id)
        .where(
            PoradcaConversation.project_id == project_id,
            PoradcaMessage.author == AUTHOR_PORADCA,
            PoradcaMessage.usage.is_not(None),
        )
    )
    if version_id is not None:
        stmt = stmt.where(PoradcaMessage.version_id == version_id)
    answers = db.execute(stmt).scalars().all()
    t = UsageTotals()
    for answer in answers:
        usage = answer.usage or {}
        t.add(
            input_tokens=int(usage.get("input_tokens") or 0),
            output_tokens=int(usage.get("output_tokens") or 0),
            duration_seconds=float(answer.duration_seconds or 0.0),
            model=usage.get("model"),
            parts=parts_from_usage_payload(usage),
            at=answer.created_at,
        )
    if not _has_activity(t):
        return []
    return [_measured_row(db, pricing, key=PORADCA_ROW_KEY, kind="poradca", t=t, with_human=False)]


def _reconcile_with_versions(
    rows: list[CostRowRead], pricing: _Pricing, versions: list[tuple[list[CostRowRead], _Pricing]]
) -> None:
    """The project scope never shows less than its versions (ICCINT-168 — Director: rather a little more).

    Each version rounds every row UP on its own, so rounding the merged project rows once would come out up to a
    euro per row and version LOWER than the sum of the versions. A project row is therefore the sum of its versions'
    rounded figures plus — rounded up — whatever belongs to no version (a Poradca answer about the whole project, a
    hand-entered cost without a version)."""
    for row in rows:
        p_agent, p_human = pricing.exact.get(row.key, (0.0, None))
        same = [(next((r for r in v_rows if r.key == row.key), None), v_pricing) for v_rows, v_pricing in versions]
        if row.agent_cost is not None:
            rounded = sum((vr.agent_cost or 0) for vr, _ in same if vr is not None)
            exact = sum(vp.exact.get(row.key, (0.0, None))[0] for _, vp in same)
            row.agent_cost = rounded + _remainder(p_agent - exact)
        if row.human_cost is not None and p_human is not None:
            rounded = sum((vr.human_cost or 0) for vr, _ in same if vr is not None)
            exact = sum((vp.exact.get(row.key, (0.0, None))[1] or 0.0) for _, vp in same)
            row.human_cost = rounded + _remainder(p_human - exact)


def _remainder(exact: float) -> int:
    return _ceil_euros(exact) if exact > 1e-6 else 0


def _fill_share_pct(rows: list[CostRowRead]) -> None:
    """Set each row's share of the SCOPE's tokens (in place, once every row of the scope exists).

    Token share — not cost share — precisely because it is always computable: a cost is ``None`` the
    moment a model is unpriced, and a column that vanishes for a pricing gap would be useless. A scope
    with no tokens leaves every row at ``0.0`` (nothing to divide, never a fabricated share)."""
    total = sum(r.input_tokens + r.output_tokens for r in rows)
    if total <= 0:
        return
    for row in rows:
        row.share_pct = (row.input_tokens + row.output_tokens) / total * 100.0


def _scope_rows(
    db: Session,
    pricing: _Pricing,
    by_phase: dict[str, UsageTotals],
    project_id: uuid.UUID,
    version_id: Optional[uuid.UUID],
) -> list[CostRowRead]:
    """A scope's rows in PAYLOAD ORDER — the screen renders them as they arrive, so the order is a
    backend contract: phase rows in canonical ``COMPARISON_PHASES`` order, then the ``external`` row,
    then the ``poradca`` row (ICCINT-167), then the ``system`` row last (it foots the table). A row absent
    from the scope is not emitted."""
    rows = _build_phases(db, pricing, by_phase)
    rows += _external_rows(db, pricing, project_id, version_id)
    rows += _poradca_rows(db, pricing, project_id, version_id)
    rows += _system_rows(db, pricing, _overhead_totals(by_phase))
    _fill_share_pct(rows)
    return rows


# ── totals ────────────────────────────────────────────────────────────────────


def _sum_or_none(values: list[Optional[float]], *, complete: bool) -> Optional[float]:
    """Σ of the present values, or ``None`` when *complete* is False (a required input was unset)."""
    if not complete:
        return None
    return sum(v for v in values if v is not None)


def _total_of(measured: Optional[float], external: Optional[float]) -> Optional[float]:
    """measured + entered — ``None`` when EITHER half is ``None`` (a partial sum would read as a total)."""
    if measured is None or external is None:
        return None
    return measured + external


def _agent_sum(rows: list[CostRowRead]) -> Optional[int]:
    """Σ of the rows' whole-euro agent figures — the table adds up because it sums what it shows. ``None``
    only when some row had spend and NO row could price anything (ICCINT-168: the unpriced part is named on
    the rows and by ``agent_cost_complete``, not hidden in a ``None`` that swallows the priced part)."""
    priced = [r.agent_cost for r in rows if r.agent_cost is not None]
    if priced:
        return sum(priced)
    return None if any(r.agent_cost is None for r in rows) else 0


def _cost_totals(rows: list[CostRowRead]) -> CostTotalsRead:
    """Scope totals with the measured/entered split — summed but NEVER merged.

    **Agent side (ICCINT-168).** Every figure is the sum of the rows' whole-euro figures; spend that could not
    be priced is named on its row (``unpriced``) and flips ``agent_cost_complete`` to ``False`` — the total is
    then the priced part and the screen says so.

    **Human side, None propagation scoped deliberately.** A ``…_measured`` figure is ``None`` iff some
    ``kind="phase"`` row that carries tokens has that figure unconfigured: the agent-only ``system`` and
    ``poradca`` rows carry ``None`` human figures by definition and must not drag the human totals to ``None``.
    **Only a token-bearing row can make a figure incomplete** (0 tokens cost 0 whatever the wage), and at
    least ONE such phase row must EXIST: with none, ``all(...)`` over the empty filtered sequence would be
    vacuously True and Σ of an all-``None`` list would render a fabricated ``0`` — exactly what a scope whose
    whole metered spend sits in the agent-only ``system`` row would show ("Cena ľudskej práce 0 €" under a
    table of ``—``). No phase tokens → no human figure.

    A scope with no external entries reports ``0`` externals — a REAL zero (nothing was entered), not
    ``None``. A human ``…_total`` is ``None`` when either half is ``None``."""
    phase_rows = [r for r in rows if r.kind == "phase"]
    measured_rows = [r for r in rows if r.kind in ("phase", "poradca", "system")]
    external_row = next((r for r in rows if r.kind == "external"), None)

    token_phase_rows = [r for r in phase_rows if r.input_tokens or r.output_tokens]
    human_ok = bool(token_phase_rows) and all(r.human_minutes is not None for r in token_phase_rows)
    human_cost_ok = bool(token_phase_rows) and all(r.human_cost is not None for r in token_phase_rows)

    agent_measured = _agent_sum(measured_rows)
    agent_external: Optional[int] = _agent_sum([external_row]) if external_row else 0
    # Straight from the rows, not from the halves: a measured half that priced nothing (``None``) next to a
    # real-zero external half must stay "unpriced", never add up to a fabricated 0 €.
    agent_total = _agent_sum(rows)
    human_minutes_measured = _sum_or_none([r.human_minutes for r in phase_rows], complete=human_ok)
    human_cost_measured = _sum_or_none([r.human_cost for r in phase_rows], complete=human_cost_ok)

    # No external entry at all → a real 0 on every external figure (nothing was entered). An entry that
    # exists carries its own figures, ``None`` included (unset wage). The VOLUME figures split the same way
    # as the money ones — the screen's "z toho namerané / z toho ručne zadané" rows must be able to cover
    # every column, not just money.
    human_minutes_external: Optional[float] = external_row.human_minutes if external_row else 0.0
    human_cost_external: Optional[int] = external_row.human_cost if external_row else 0

    return CostTotalsRead(
        turns=sum(r.turns for r in rows),
        turns_measured=sum(r.turns for r in measured_rows),
        turns_external=external_row.turns if external_row else 0,
        input_tokens=sum(r.input_tokens for r in rows),
        input_tokens_measured=sum(r.input_tokens for r in measured_rows),
        input_tokens_external=external_row.input_tokens if external_row else 0,
        output_tokens=sum(r.output_tokens for r in rows),
        output_tokens_measured=sum(r.output_tokens for r in measured_rows),
        output_tokens_external=external_row.output_tokens if external_row else 0,
        cache_read_tokens=sum(r.cache_read_tokens for r in rows),
        cache_write_tokens=sum(r.cache_write_tokens for r in rows),
        agent_cost_measured=agent_measured,
        agent_cost_external=agent_external,
        agent_cost_total=agent_total,
        agent_cost_complete=not any(r.unpriced for r in rows),
        human_minutes_measured=human_minutes_measured,
        human_minutes_external=human_minutes_external,
        human_minutes_total=_total_of(human_minutes_measured, human_minutes_external),
        human_cost_measured=human_cost_measured,
        human_cost_external=human_cost_external,
        human_cost_total=_total_of(human_cost_measured, human_cost_external),
    )


# ── config flags / assumptions ────────────────────────────────────────────────


def _wages_configured(db: Session) -> bool:
    """Every wage-bearing row counts — ``externe`` included: its wage produces a real human-cost figure on
    screen, so a scope priced through it alone must never be labelled "Mzdy nenastavené"."""
    return any(_phase_wage(db, key) > 0 for key in WAGE_ROW_KEYS)


def _wages(db: Session) -> dict[str, Optional[float]]:
    """Row key → hourly wage, ``None`` when unset. Covers the comparison phases + ``externe``; the
    ``system`` row is agent-only and deliberately has NO entry here (and no key to add)."""
    return {key: (_phase_wage(db, key) or None) for key in WAGE_ROW_KEYS}


def _manager_overhead(interventions: int, wait_seconds: float) -> ManagerOverheadRead:
    """The Manažér (human-in-the-loop) overhead row — measured wait + intervention count, info-only.
    (The v1 priced Director overhead — agent-side wait × wage + symmetric human-side — is retired in
    CR-V2-029 with the per-role Director wage/rate keys; the comparison is purely per-phase now.)"""
    return ManagerOverheadRead(interventions=interventions, wait_seconds=wait_seconds)


def compute_project_metrics(db: Session, project: Project) -> ProjectCostsRead:
    """Aggregate the project's cost per phase / version / project — measured + hand-entered, split.

    ICCINT-168: the price lists are brought up to date first (a model that has just collected enough
    Claude-Code-paid turns gets its list and its ECB rate here), then every scope is priced with them."""
    model_pricing.refresh(db)
    pricing = _Pricing(model_pricing.price_rows(db))
    # `coefficient_configured` is intentionally NOT shipped: the screen derives the same state from
    # `coefficient_minutes_per_mtok is None`, and two sources for one fact is how they drift apart.
    wages_configured = _wages_configured(db)

    versions = (
        db.execute(select(Version).where(Version.project_id == project.id).order_by(Version.version_number.asc()))
        .scalars()
        .all()
    )

    cumulative_grand = UsageTotals()
    cumulative_by_phase: dict[str, UsageTotals] = {}
    cum_manager_wait = 0.0
    cum_interventions = 0
    by_version: list[VersionCostsRead] = []
    version_scopes: list[tuple[list[CostRowRead], _Pricing]] = []

    for version in versions:
        grand = aggregate_pipeline_usage(db, version.id).version
        by_phase_totals = aggregate_usage_by_phase(db, version.id)
        v_wait = _manager_wait_seconds(db, version.id)
        interventions = _manager_interventions(db, version.id)

        v_pricing = pricing.for_scope()
        rows = _scope_rows(db, v_pricing, by_phase_totals, project.id, version.id)
        version_scopes.append((rows, v_pricing))
        total_time = _total_time_seconds(db, version)

        by_version.append(
            VersionCostsRead(
                version_id=version.id,
                version_number=version.version_number,
                status=version.status,
                usage=_totals_read(grand),
                rows=rows,
                totals=_cost_totals(rows),
                manager=_manager_overhead(interventions, v_wait),
                manager_wait_seconds=v_wait,
                internal_idle_seconds=_internal_idle_seconds(total_time, grand.duration_seconds, v_wait),
                total_time_seconds=total_time,
                price_list=_price_list(v_pricing),
            )
        )

        # cumulative accumulation
        cumulative_grand.merge(grand)
        for phase, t in by_phase_totals.items():
            cumulative_by_phase.setdefault(phase, UsageTotals()).merge(t)
        cum_manager_wait += v_wait
        cum_interventions += interventions

    # Project scope: the merged per-phase buckets + ALL the project's external entries (version-less
    # ones included — they belong to the project total and to no version).
    p_pricing = pricing.for_scope()
    cum_rows = _scope_rows(db, p_pricing, cumulative_by_phase, project.id, None)
    _reconcile_with_versions(cum_rows, p_pricing, version_scopes)

    return ProjectCostsRead(
        project_id=project.id,
        slug=project.slug,
        usage=_totals_read(cumulative_grand),
        rows=cum_rows,
        totals=_cost_totals(cum_rows),
        by_version=by_version,
        manager=_manager_overhead(cum_interventions, cum_manager_wait),
        coefficient_minutes_per_mtok=(_coefficient(db) or None),
        wages=_wages(db),
        currency="EUR",
        wages_configured=wages_configured,
        price_list=_price_list(p_pricing),
    )
