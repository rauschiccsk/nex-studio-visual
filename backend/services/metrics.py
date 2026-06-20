"""Role-based project metrics / ROI computation (E5; metrics redesign).

Read-only aggregation over the live WS-D capture (per-dispatch ``PipelineMessage.payload.usage``/
``.timing``, grouped by ROLE-OF-ORIGIN by :func:`pipeline_metrics.aggregate_usage_by_role`) +
Director-wait accumulation (``PipelineState.total_director_wait_seconds``) + per-role rates/wages +
per-model pricing (``system_settings``, env fallback for the flat pair).

Single reproducible base = all tokens (IN+OUT, incl. retries/failed) per role per version. From it:
the **agent** side (tokens × per-model API price; active = Σ timing duration), the **human** side
(tokens × per-role minutes-per-Mtok rate × per-role wage), the **idle** split (real wall-clock, never
folded into agent time), the **Director** overhead (measured agent-side wait + symmetric human-side),
and the headline ROI (N× faster, M× cheaper, EUR saved) per version + cumulative.

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
from backend.db.models.pipeline import ACTOR_VALUES, PipelineMessage, PipelineState
from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.schemas.metrics import (
    DirectorMetricRead,
    ModelTokensRead,
    ProjectMetricsRead,
    RoiHeadlineRead,
    RoleMetricRead,
    SystemOverheadRead,
    UsageTotalsRead,
    VersionMetricsRead,
)
from backend.services import system_setting
from backend.services.pipeline_metrics import (
    UsageTotals,
    aggregate_pipeline_usage,
    aggregate_usage_by_role,
)

logger = logging.getLogger(__name__)

#: The 5 agent roles compared against a human (derived from the canonical actor tuple so a new actor
#: can't fall out silently — review #8). ``director`` is human overhead handled separately; ``system``
#: is engine-only (excluded from both sides, shown as an info-only overhead row — §1.4).
COMPARISON_ROLES: tuple[str, ...] = tuple(r for r in ACTOR_VALUES if r != "director")
DIRECTOR_ROLE = "director"
SYSTEM_ROLE = "system"
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
    """``(total_cost, value_in, value_out, unpriced_model_keys)`` for a role's per-model token split.

    The three costs are ``None`` if ANY token-bearing model resolves to no price after the full
    fallback chain (a fully-priced set with ``_unknown`` mass costed at the flat pair is LEGITIMATE —
    the "paid for compute, envelope didn't name the model" case). A model carrying 0 tokens never
    triggers an unpriced gap (0 tokens cost 0 regardless of price); a role with no tokens at all costs
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


def _human_minutes_for_role(t: UsageTotals, conv_rate: float) -> Optional[float]:
    """tokens → minutes via the per-role conversion (minutes per 1M total tokens). None when unset (0)."""
    if conv_rate <= 0:
        return None
    return (t.input_tokens + t.output_tokens) / 1_000_000.0 * conv_rate


def _human_cost(human_minutes: Optional[float], wage: float) -> Optional[float]:
    if human_minutes is None or wage <= 0:
        return None
    return human_minutes / 60.0 * wage


def _role_wage(db: Session, role: str) -> float:
    """Per-role hourly wage. Implementer SUPERSEDES ``developer_hourly_rate`` (read as fallback ONLY
    when the new key has no row; an explicit stored 0 is honored as unset — §3.3)."""
    if role == "implementer":
        w = system_setting.get_float_or_none(db, "metrics_hourly_wage_implementer")
        if w is None:
            return _effective_price(db, "developer_hourly_rate", settings.developer_hourly_rate)
        return w
    return system_setting.get_float(db, f"metrics_hourly_wage_{role}")


# ── idle / time / director count ──────────────────────────────────────────────


def _director_wait_seconds(db: Session, version_id: uuid.UUID) -> float:
    """Accumulated Director-wait + any live open wait for a version (idle-a)."""
    state = db.execute(select(PipelineState).where(PipelineState.version_id == version_id)).scalar_one_or_none()
    if state is None:
        return 0.0
    wait = float(state.total_director_wait_seconds or 0.0)
    if state.awaiting_director_since is not None:
        wait += (datetime.now(timezone.utc) - state.awaiting_director_since).total_seconds()
    return wait


def _director_interventions(db: Session, version_id: uuid.UUID) -> int:
    """Count of Director-authored pipeline messages for a version."""
    return (
        db.execute(
            select(func.count())
            .select_from(PipelineMessage)
            .where(PipelineMessage.version_id == version_id, PipelineMessage.author == "director")
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


def _internal_idle_seconds(total: Optional[float], active: float, director_wait: float) -> Optional[float]:
    """total wall-clock − active compute − director-wait (idle-b). None (never fabricated 0) when the
    span is 0/unknown."""
    if total is None or total <= 0:
        return None
    return max(total - active - director_wait, 0.0)


# ── per-role + scope assembly ─────────────────────────────────────────────────


def _role_metric(db: Session, role: str, t: UsageTotals, flat_in: float, flat_out: float) -> RoleMetricRead:
    conv_rate = system_setting.get_float(db, f"metrics_minutes_per_mtok_{role}")
    wage = _role_wage(db, role)
    agent_cost, value_in, value_out, unpriced = _agent_cost_split(db, t.by_model, flat_in, flat_out)
    human_minutes = _human_minutes_for_role(t, conv_rate)
    human_cost = _human_cost(human_minutes, wage)
    active_minutes = t.duration_seconds / 60.0
    # speed: token-derived human-time vs agent ACTIVE time; ratios are None whenever EITHER side is None.
    x_faster = (human_minutes / active_minutes) if (human_minutes is not None and active_minutes > 0) else None
    m_cheaper = (
        (human_cost / agent_cost) if (human_cost is not None and agent_cost is not None and agent_cost > 0) else None
    )
    eur_saved = (human_cost - agent_cost) if (human_cost is not None and agent_cost is not None) else None
    return RoleMetricRead(
        role=role,
        active_seconds=t.duration_seconds,
        internal_idle_seconds=None,  # inter-turn idle is not role-attributable (kept for layout symmetry)
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


def _build_roles(db: Session, by_role: dict[str, UsageTotals], flat_in: float, flat_out: float) -> list[RoleMetricRead]:
    """The 5 comparison-role rows (always all 5, in canonical order — a non-participating role costs a
    real 0, so the table is stable across versions)."""
    return [_role_metric(db, role, by_role.get(role, UsageTotals()), flat_in, flat_out) for role in COMPARISON_ROLES]


def _system_overhead(db: Session, t: UsageTotals, flat_in: float, flat_out: float) -> SystemOverheadRead:
    agent_cost, _, _, _ = _agent_cost_split(db, t.by_model, flat_in, flat_out)
    return SystemOverheadRead(
        input_tokens=t.input_tokens,
        output_tokens=t.output_tokens,
        active_seconds=t.duration_seconds,
        agent_cost=agent_cost,
    )


def _compute_director(
    role_metrics: list[RoleMetricRead],
    v_wait: float,
    interventions: int,
    director_wage: float,
    director_rate: float,
) -> DirectorMetricRead:
    """Director overhead: measured agent-side wait cost + symmetric human-side director cost (both via
    the same per-hour wage; the human side via the ``director_minutes_per_human_role_hour`` rate — §2.5).
    Default rate 0 → human-side director None (not modeled)."""
    role_hm = [rm.human_minutes for rm in role_metrics if rm.human_minutes is not None]
    human_minutes_total = sum(role_hm) if role_hm else None
    agent_director_cost = (v_wait / 3600.0 * director_wage) if director_wage > 0 else None
    human_director_minutes = (
        (human_minutes_total / 60.0 * director_rate)
        if (human_minutes_total is not None and director_rate > 0)
        else None
    )
    human_director_cost = _human_cost(human_director_minutes, director_wage)
    return DirectorMetricRead(
        interventions=interventions,
        agent_wait_seconds=v_wait,
        agent_director_cost=agent_director_cost,
        human_director_minutes=human_director_minutes,
        human_director_cost=human_director_cost,
    )


def _cost_totals(
    role_metrics: list[RoleMetricRead],
    director: DirectorMetricRead,
) -> tuple[Optional[float], Optional[float]]:
    """Headline agent/human cost totals over the comparison roles + Director (§1.4). Each total is
    None ONLY when a role that DID work is unconfigured (agent unpriced / human rate-or-wage unset) —
    so the headline is a complete role comparison, not a partial. The Director contributes WHEN priced
    (measured agent-side wait × wage; symmetric human-side via the rate model) and is otherwise shown
    in its own row; it never nullifies the headline. When the Director is unconfigured on both sides
    (default rate 0 + wage unset) the totals are a clean symmetric 5-role comparison."""
    agent_total = 0.0
    agent_ok = True
    human_total = 0.0
    human_ok = True
    for rm in role_metrics:
        if rm.input_tokens or rm.output_tokens:
            if rm.agent_cost is None:
                agent_ok = False
            else:
                agent_total += rm.agent_cost
            if rm.human_cost is None:
                human_ok = False
            else:
                human_total += rm.human_cost
    if director.agent_director_cost is not None:
        agent_total += director.agent_director_cost
    if director.human_director_cost is not None:
        human_total += director.human_director_cost
    return (agent_total if agent_ok else None), (human_total if human_ok else None)


def _config_flags(db: Session, flat_in: float, flat_out: float) -> tuple[bool, bool, bool, bool]:
    """``(pricing, rates, wages, configured)`` — per-dimension config booleans for the banner +
    headline ``configured = pricing AND rates AND wages`` (review #11)."""
    pricing_configured = (flat_in > 0 and flat_out > 0) or any(
        _effective_price(db, f"api_price_input_per_mtok_{fam}", 0.0) > 0
        and _effective_price(db, f"api_price_output_per_mtok_{fam}", 0.0) > 0
        for fam in _PRICE_FAMILIES
    )
    rates_configured = any(
        system_setting.get_float(db, f"metrics_minutes_per_mtok_{role}") > 0 for role in COMPARISON_ROLES
    )
    wages_configured = any(_role_wage(db, role) > 0 for role in COMPARISON_ROLES)
    return (
        pricing_configured,
        rates_configured,
        wages_configured,
        (pricing_configured and rates_configured and wages_configured),
    )


def _compute_headline(
    role_metrics: list[RoleMetricRead],
    agent_cost_total: Optional[float],
    human_cost_total: Optional[float],
    flags: tuple[bool, bool, bool, bool],
    *,
    covered: int,
    total: int,
) -> RoiHeadlineRead:
    pricing_configured, rates_configured, wages_configured, configured = flags
    agent_active_minutes = sum(rm.active_seconds for rm in role_metrics) / 60.0
    role_hm = [rm.human_minutes for rm in role_metrics if rm.human_minutes is not None]
    human_minutes_total = sum(role_hm) if role_hm else None
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
    for rm in role_metrics:
        for k, mt in rm.by_model.items():
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


def compute_project_metrics(db: Session, project: Project) -> ProjectMetricsRead:
    """Aggregate the project's per-role agent-vs-human effort + cost + ROI, per version + cumulative."""
    flat_in = _effective_price(db, "api_price_input_per_mtok", settings.api_price_input_per_mtok)
    flat_out = _effective_price(db, "api_price_output_per_mtok", settings.api_price_output_per_mtok)
    director_wage = system_setting.get_float(db, "metrics_hourly_wage_director")
    director_rate = system_setting.get_float(db, "metrics_director_minutes_per_human_role_hour")
    flags = _config_flags(db, flat_in, flat_out)

    versions = (
        db.execute(select(Version).where(Version.project_id == project.id).order_by(Version.version_number.asc()))
        .scalars()
        .all()
    )

    cumulative_grand = UsageTotals()
    cumulative_by_role: dict[str, UsageTotals] = {}
    cum_director_wait = 0.0
    cum_interventions = 0
    cum_agent_cost = 0.0
    cum_human_cost = 0.0
    covered = 0
    by_version: list[VersionMetricsRead] = []

    for version in versions:
        grand = aggregate_pipeline_usage(db, version.id).version
        by_role_totals = aggregate_usage_by_role(db, version.id)
        v_wait = _director_wait_seconds(db, version.id)
        interventions = _director_interventions(db, version.id)

        role_metrics = _build_roles(db, by_role_totals, flat_in, flat_out)
        sys_overhead = _system_overhead(db, by_role_totals.get(SYSTEM_ROLE, UsageTotals()), flat_in, flat_out)
        director = _compute_director(role_metrics, v_wait, interventions, director_wage, director_rate)
        agent_ct, human_ct = _cost_totals(role_metrics, director)
        total_time = _total_time_seconds(db, version)
        internal_idle = _internal_idle_seconds(total_time, grand.duration_seconds, v_wait)

        roi = _compute_headline(
            role_metrics,
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
                by_role=role_metrics,
                system_overhead=sys_overhead,
                director=director,
                director_wait_seconds=v_wait,
                internal_idle_seconds=internal_idle,
                total_time_seconds=total_time,
                roi=roi,
            )
        )

        # cumulative accumulation
        cumulative_grand.merge(grand)
        for role, t in by_role_totals.items():
            cumulative_by_role.setdefault(role, UsageTotals()).merge(t)
        cum_director_wait += v_wait
        cum_interventions += interventions
        if agent_ct is not None and human_ct is not None:  # None-safe cumulative coverage (§1.3)
            covered += 1
            cum_agent_cost += agent_ct
            cum_human_cost += human_ct

    cum_role_metrics = _build_roles(db, cumulative_by_role, flat_in, flat_out)
    cum_system_overhead = _system_overhead(db, cumulative_by_role.get(SYSTEM_ROLE, UsageTotals()), flat_in, flat_out)
    cum_director = _compute_director(
        cum_role_metrics, cum_director_wait, cum_interventions, director_wage, director_rate
    )
    cum_roi = _compute_headline(
        cum_role_metrics,
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
        by_role=cum_role_metrics,
        system_overhead=cum_system_overhead,
        director=cum_director,
        by_version=by_version,
        roi=cum_roi,
    )
