"""Per-phase project metrics / ROI computation (E5; v2 metrics per-phase basis, CR-V2-029).

Read-only aggregation over the live WS-D capture (per-dispatch ``PipelineMessage.payload.usage``/
``.timing``, grouped by the per-turn ``phase`` stamp by :func:`pipeline_metrics.aggregate_usage_by_phase`)
+ Manažér-wait accumulation (``PipelineState.total_director_wait_seconds`` — the column name is kept;
it now meters Manažér-wait, CR-V2-004) + per-phase rates/wages + per-model pricing (``system_settings``,
env fallback for the flat pair).

Single reproducible base = all tokens (IN+OUT, incl. retries/failed) per PHASE per version. From it:
the **agent** side (tokens × per-model API price; active = Σ timing duration), the **human** side
(tokens × per-phase minutes-per-Mtok rate × per-phase wage), the **idle** split (real wall-clock, never
folded into agent time), the **Manažér** overhead (measured wait, info-only — the v1 priced Director
overhead is retired), and the headline ROI (N× faster, M× cheaper, EUR saved) per version + cumulative.
Per-customer deploy is a separate ops cost, not part of this build-pipeline ROI.

**Honest by construction:** any figure depending on an unconfigured input (price / rate / wage) is
``None`` — never fabricated; a ratio is ``None`` whenever EITHER side is ``None``. No pipeline
mutation, no live ``claude`` call.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.config.settings import settings
from backend.db.models.pipeline import STAGE_VALUES, PipelineMessage, PipelineState
from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.schemas.metrics import (
    ManagerOverheadRead,
    ModelTokensRead,
    PhaseMetricRead,
    ProjectMetricsRead,
    RoiHeadlineRead,
    SystemOverheadRead,
    UsageTotalsRead,
    VersionMetricsRead,
)
from backend.services import system_setting
from backend.services.pipeline_metrics import (
    UsageTotals,
    aggregate_pipeline_usage,
    aggregate_usage_by_phase,
)

logger = logging.getLogger(__name__)

#: The 4 build phases compared against a human, derived from the canonical stage tuple minus the
#: terminal ``done`` (no work/tokens attribute to ``done`` — it is the post-build terminal phase). A
#: phase rename can't fall out silently (it stays in lock-step with ``STAGE_VALUES``). The Manažér
#: overhead (human-in-the-loop wait) is handled separately as an info-only row; ``system`` is
#: engine-only (a message with no phase stamp + a ``system`` author — excluded from both sides, shown
#: as an info-only overhead row).
TERMINAL_PHASE = "done"
COMPARISON_PHASES: tuple[str, ...] = tuple(s for s in STAGE_VALUES if s != TERMINAL_PHASE)
_PRICE_FAMILIES: tuple[str, ...] = ("opus", "sonnet", "haiku")


# ── small reads / pricing primitives ──────────────────────────────────────────


def _totals_read(t: UsageTotals) -> UsageTotalsRead:
    return UsageTotalsRead(
        input_tokens=t.input_tokens,
        output_tokens=t.output_tokens,
        duration_seconds=t.duration_seconds,
        messages=t.messages,
    )


def _effective_price(db: Session, key: str, env_fallback: float) -> float:
    """system_settings value, else the env value (config.settings) — 0.0 means unset."""
    return system_setting.get_float(db, key) or env_fallback


def _model_family(model_id: Optional[str]) -> str:
    """Map a full model id (``claude-opus-4-8`` …) to a price family. The ``"_unknown"`` sentinel (no
    model named in the envelope) maps to ``"_unknown"`` silently; a *named* id that matches no family
    logs a warning so a model roll that changes the family token surfaces, never silently mis-priced."""
    if not model_id or model_id == "_unknown":
        return "_unknown"
    mid = model_id.lower()
    for fam in _PRICE_FAMILIES:
        if fam in mid:
            return fam
    logger.warning("metrics: unrecognized model id %r → _unknown bucket", model_id)
    return "_unknown"


def _resolve_price(db: Session, family: str, flat_in: float, flat_out: float) -> tuple[float, float]:
    """Ordered fallback: per-family key (when both > 0) → flat pair (system_settings → env). The flat
    pair covers ``_unknown`` + any family left unkeyed."""
    if family != "_unknown":
        pin = _effective_price(db, f"api_price_input_per_mtok_{family}", 0.0)
        pout = _effective_price(db, f"api_price_output_per_mtok_{family}", 0.0)
        if pin > 0 and pout > 0:
            return pin, pout
    return flat_in, flat_out


def _agent_cost_split(
    db: Session, by_model: dict, flat_in: float, flat_out: float
) -> tuple[Optional[float], Optional[float], Optional[float], list[str]]:
    """``(total_cost, value_in, value_out, unpriced_model_keys)`` for a phase's per-model token split.

    The three costs are ``None`` if ANY token-bearing model resolves to no price after the full
    fallback chain (a fully-priced set with ``_unknown`` mass costed at the flat pair is LEGITIMATE —
    the "paid for compute, envelope didn't name the model" case). A model carrying 0 tokens never
    triggers an unpriced gap (0 tokens cost 0 regardless of price); a phase with no tokens at all costs
    a real 0 (it did no work), not ``None``."""
    value_in = 0.0
    value_out = 0.0
    unpriced: list[str] = []
    for model_key, mt in by_model.items():
        if mt.input_tokens == 0 and mt.output_tokens == 0:
            continue
        pin, pout = _resolve_price(db, _model_family(model_key), flat_in, flat_out)
        if pin <= 0 or pout <= 0:
            unpriced.append(model_key)
            continue
        value_in += mt.input_tokens * pin
        value_out += mt.output_tokens * pout
    if unpriced:
        return None, None, None, unpriced
    return (value_in + value_out) / 1_000_000.0, value_in / 1_000_000.0, value_out / 1_000_000.0, []


# ── human side ────────────────────────────────────────────────────────────────


def _human_minutes_for_phase(t: UsageTotals, conv_rate: float) -> Optional[float]:
    """tokens → minutes via the per-phase conversion (minutes per 1M total tokens). None when unset (0)."""
    if conv_rate <= 0:
        return None
    return (t.input_tokens + t.output_tokens) / 1_000_000.0 * conv_rate


def _human_cost(human_minutes: Optional[float], wage: float) -> Optional[float]:
    if human_minutes is None or wage <= 0:
        return None
    return human_minutes / 60.0 * wage


def _phase_wage(db: Session, phase: str) -> float:
    """Per-phase hourly wage (currency-agnostic) for the human-cost side. 0 = unset → null."""
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


# ── per-phase + scope assembly ────────────────────────────────────────────────


def _phase_metric(db: Session, phase: str, t: UsageTotals, flat_in: float, flat_out: float) -> PhaseMetricRead:
    conv_rate = system_setting.get_float(db, f"metrics_minutes_per_mtok_{phase}")
    wage = _phase_wage(db, phase)
    agent_cost, value_in, value_out, unpriced = _agent_cost_split(db, t.by_model, flat_in, flat_out)
    human_minutes = _human_minutes_for_phase(t, conv_rate)
    human_cost = _human_cost(human_minutes, wage)
    active_minutes = t.duration_seconds / 60.0
    # speed: token-derived human-time vs agent ACTIVE time; ratios are None whenever EITHER side is None.
    x_faster = (human_minutes / active_minutes) if (human_minutes is not None and active_minutes > 0) else None
    m_cheaper = (
        (human_cost / agent_cost) if (human_cost is not None and agent_cost is not None and agent_cost > 0) else None
    )
    eur_saved = (human_cost - agent_cost) if (human_cost is not None and agent_cost is not None) else None
    return PhaseMetricRead(
        phase=phase,
        active_seconds=t.duration_seconds,
        internal_idle_seconds=None,  # inter-turn idle is not phase-attributable (kept for layout symmetry)
        input_tokens=t.input_tokens,
        output_tokens=t.output_tokens,
        parse_attempts=t.parse_attempts,
        agent_cost=agent_cost,
        agent_value_in=value_in,
        agent_value_out=value_out,
        by_model={
            k: ModelTokensRead(input_tokens=mt.input_tokens, output_tokens=mt.output_tokens)
            for k, mt in t.by_model.items()
        },
        unpriced_model_keys=unpriced,
        human_minutes=human_minutes,
        human_cost=human_cost,
        x_faster=x_faster,
        m_cheaper=m_cheaper,
        eur_saved=eur_saved,
    )


def _build_phases(
    db: Session, by_phase: dict[str, UsageTotals], flat_in: float, flat_out: float
) -> list[PhaseMetricRead]:
    """The comparison-phase rows for the phases that ACTUALLY did work — emit only a phase with SOME metered
    activity (tokens OR wall-clock OR parse-attempts), in canonical ``COMPARISON_PHASES`` order
    (metrics-v3-three-phases.md Part 2; drop predicate widened in metrics-v3-followup.md C2). A phase with NO
    metered activity is DROPPED rather than rendered as a phantom empty row: a v3 conversation project shows
    Návrh / Programovanie / Verifikácia (the three phases the collapsed one-partner flow stamps), a legacy
    v1/v2 project shows whatever phases it truly used — never a permanent empty row.

    The predicate is metered ACTIVITY, not tokens alone: a failed turn whose envelope carried ``timing`` but
    no ``usage`` lands with 0 tokens + real ``duration_seconds`` (metrics-v3-followup.md C1). Dropping it on a
    tokens-only test would (a) inflate the headline ``x_faster`` (its ``active_seconds`` vanish from the
    denominator — a one-sided bias flattering the agent) and (b) hide its time entirely (``_overhead_totals``
    excludes ``COMPARISON_PHASES``, so the wall-clock would foot nowhere). ``parse_attempts`` (rework with no
    surviving usage) is likewise real activity.

    Footing is preserved: a dropped phase contributed no tokens AND no time, and ``_overhead_totals`` still
    folds every non-comparison bucket, so the per-phase table + the system-overhead row still foot to the
    grand total. Applied to BOTH the per-version and the cumulative ``by_phase`` (both callers route here)."""
    rows: list[PhaseMetricRead] = []
    for phase in COMPARISON_PHASES:
        t = by_phase.get(phase)
        if t is None or not (t.input_tokens or t.output_tokens or t.duration_seconds or t.parse_attempts):
            continue
        rows.append(_phase_metric(db, phase, t, flat_in, flat_out))
    return rows


def _overhead_totals(by_phase: dict[str, UsageTotals]) -> UsageTotals:
    """Fold every bucket that is NOT one of the 4 comparison phases (``system``, the terminal ``done``,
    or any unexpected key) into a single info-only overhead bucket, so the per-phase table always foots
    to the grand total — no metered tokens silently vanish (honest by construction)."""
    overhead = UsageTotals()
    for phase, t in by_phase.items():
        if phase not in COMPARISON_PHASES:
            overhead.merge(t)
    return overhead


def _system_overhead(db: Session, t: UsageTotals, flat_in: float, flat_out: float) -> SystemOverheadRead:
    agent_cost, _, _, _ = _agent_cost_split(db, t.by_model, flat_in, flat_out)
    return SystemOverheadRead(
        input_tokens=t.input_tokens,
        output_tokens=t.output_tokens,
        active_seconds=t.duration_seconds,
        agent_cost=agent_cost,
    )


def _cost_totals(phase_metrics: list[PhaseMetricRead]) -> tuple[Optional[float], Optional[float]]:
    """Headline agent/human cost totals over the comparison phases. Each total is None ONLY when a phase
    that DID work is unconfigured (agent unpriced / human rate-or-wage unset) — so the headline is a
    complete phase comparison, not a partial. (The Manažér overhead is info-only in v2 — no priced
    Director cost folds into the headline any more, CR-V2-029.)"""
    agent_total = 0.0
    agent_ok = True
    human_total = 0.0
    human_ok = True
    for pm in phase_metrics:
        if pm.input_tokens or pm.output_tokens:
            if pm.agent_cost is None:
                agent_ok = False
            else:
                agent_total += pm.agent_cost
            if pm.human_cost is None:
                human_ok = False
            else:
                human_total += pm.human_cost
    return (agent_total if agent_ok else None), (human_total if human_ok else None)


def _config_flags(db: Session, flat_in: float, flat_out: float) -> tuple[bool, bool, bool, bool]:
    """``(pricing, rates, wages, configured)`` — per-dimension config booleans for the banner +
    headline ``configured = pricing AND rates AND wages``."""
    pricing_configured = (flat_in > 0 and flat_out > 0) or any(
        _effective_price(db, f"api_price_input_per_mtok_{fam}", 0.0) > 0
        and _effective_price(db, f"api_price_output_per_mtok_{fam}", 0.0) > 0
        for fam in _PRICE_FAMILIES
    )
    rates_configured = any(
        system_setting.get_float(db, f"metrics_minutes_per_mtok_{phase}") > 0 for phase in COMPARISON_PHASES
    )
    wages_configured = any(_phase_wage(db, phase) > 0 for phase in COMPARISON_PHASES)
    return (
        pricing_configured,
        rates_configured,
        wages_configured,
        (pricing_configured and rates_configured and wages_configured),
    )


def _compute_headline(
    phase_metrics: list[PhaseMetricRead],
    agent_cost_total: Optional[float],
    human_cost_total: Optional[float],
    flags: tuple[bool, bool, bool, bool],
    *,
    covered: int,
    total: int,
) -> RoiHeadlineRead:
    pricing_configured, rates_configured, wages_configured, configured = flags
    agent_active_minutes = sum(pm.active_seconds for pm in phase_metrics) / 60.0
    phase_hm = [pm.human_minutes for pm in phase_metrics if pm.human_minutes is not None]
    human_minutes_total = sum(phase_hm) if phase_hm else None
    x_faster = (
        (human_minutes_total / agent_active_minutes)
        if (human_minutes_total is not None and agent_active_minutes > 0)
        else None
    )
    m_cheaper = (
        (human_cost_total / agent_cost_total)
        if (human_cost_total is not None and agent_cost_total is not None and agent_cost_total > 0)
        else None
    )
    eur_saved = (
        (human_cost_total - agent_cost_total)
        if (human_cost_total is not None and agent_cost_total is not None)
        else None
    )
    total_tok = 0
    unknown_tok = 0
    for pm in phase_metrics:
        for k, mt in pm.by_model.items():
            tok = mt.input_tokens + mt.output_tokens
            total_tok += tok
            if _model_family(k) == "_unknown":
                unknown_tok += tok
    unknown_pct = (unknown_tok / total_tok * 100.0) if total_tok > 0 else 0.0
    return RoiHeadlineRead(
        agent_active_minutes=agent_active_minutes,
        human_minutes_total=human_minutes_total,
        agent_cost_total=agent_cost_total,
        human_cost_total=human_cost_total,
        x_faster=x_faster,
        m_cheaper=m_cheaper,
        eur_saved=eur_saved,
        unknown_model_token_pct=unknown_pct,
        flat_subscription=True,
        marginal_cost_eur=0.0,
        configured=configured,
        pricing_configured=pricing_configured,
        rates_configured=rates_configured,
        wages_configured=wages_configured,
        covered_versions=covered,
        total_versions=total,
    )


def _manager_overhead(interventions: int, wait_seconds: float) -> ManagerOverheadRead:
    """The Manažér (human-in-the-loop) overhead row — measured wait + intervention count, info-only.
    (The v1 priced Director overhead — agent-side wait × wage + symmetric human-side — is retired in
    CR-V2-029 with the per-role Director wage/rate keys; the comparison is purely per-phase now.)"""
    return ManagerOverheadRead(interventions=interventions, wait_seconds=wait_seconds)


def compute_project_metrics(db: Session, project: Project) -> ProjectMetricsRead:
    """Aggregate the project's per-phase agent-vs-human effort + cost + ROI, per version + cumulative."""
    flat_in = _effective_price(db, "api_price_input_per_mtok", settings.api_price_input_per_mtok)
    flat_out = _effective_price(db, "api_price_output_per_mtok", settings.api_price_output_per_mtok)
    flags = _config_flags(db, flat_in, flat_out)

    versions = (
        db.execute(select(Version).where(Version.project_id == project.id).order_by(Version.version_number.asc()))
        .scalars()
        .all()
    )

    cumulative_grand = UsageTotals()
    cumulative_by_phase: dict[str, UsageTotals] = {}
    cum_manager_wait = 0.0
    cum_interventions = 0
    cum_agent_cost = 0.0
    cum_human_cost = 0.0
    covered = 0
    by_version: list[VersionMetricsRead] = []

    for version in versions:
        grand = aggregate_pipeline_usage(db, version.id).version
        by_phase_totals = aggregate_usage_by_phase(db, version.id)
        v_wait = _manager_wait_seconds(db, version.id)
        interventions = _manager_interventions(db, version.id)

        phase_metrics = _build_phases(db, by_phase_totals, flat_in, flat_out)
        sys_overhead = _system_overhead(db, _overhead_totals(by_phase_totals), flat_in, flat_out)
        manager = _manager_overhead(interventions, v_wait)
        agent_ct, human_ct = _cost_totals(phase_metrics)
        total_time = _total_time_seconds(db, version)
        internal_idle = _internal_idle_seconds(total_time, grand.duration_seconds, v_wait)

        roi = _compute_headline(
            phase_metrics,
            agent_ct,
            human_ct,
            flags,
            covered=(1 if (agent_ct is not None and human_ct is not None) else 0),
            total=1,
        )

        by_version.append(
            VersionMetricsRead(
                version_id=version.id,
                version_number=version.version_number,
                status=version.status,
                usage=_totals_read(grand),
                by_phase=phase_metrics,
                system_overhead=sys_overhead,
                manager=manager,
                manager_wait_seconds=v_wait,
                internal_idle_seconds=internal_idle,
                total_time_seconds=total_time,
                roi=roi,
            )
        )

        # cumulative accumulation
        cumulative_grand.merge(grand)
        for phase, t in by_phase_totals.items():
            cumulative_by_phase.setdefault(phase, UsageTotals()).merge(t)
        cum_manager_wait += v_wait
        cum_interventions += interventions
        if agent_ct is not None and human_ct is not None:  # None-safe cumulative coverage
            covered += 1
            cum_agent_cost += agent_ct
            cum_human_cost += human_ct

    cum_phase_metrics = _build_phases(db, cumulative_by_phase, flat_in, flat_out)
    cum_system_overhead = _system_overhead(db, _overhead_totals(cumulative_by_phase), flat_in, flat_out)
    cum_manager = _manager_overhead(cum_interventions, cum_manager_wait)
    cum_roi = _compute_headline(
        cum_phase_metrics,
        cum_agent_cost if covered else None,
        cum_human_cost if covered else None,
        flags,
        covered=covered,
        total=len(versions),
    )

    return ProjectMetricsRead(
        project_id=project.id,
        slug=project.slug,
        usage=_totals_read(cumulative_grand),
        by_phase=cum_phase_metrics,
        system_overhead=cum_system_overhead,
        manager=cum_manager,
        by_version=by_version,
        roi=cum_roi,
    )
