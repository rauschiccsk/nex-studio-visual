"""Pydantic schemas for the per-phase metrics / ROI page (E5; v2 metrics per-phase basis, CR-V2-029).

A computed read-only aggregate over the WS-D capture (`PipelineMessage.payload.usage`/`.timing`,
grouped by the per-turn `phase` stamp) + Manažér-wait accumulation + per-phase rates/wages + per-model
pricing. HONEST by construction: every figure that depends on an unconfigured input (price / rate /
wage) is ``None`` — never a fabricated number; a ratio is ``None`` whenever EITHER side is ``None``.

Single reproducible base = all tokens (IN+OUT, incl. retries/failed) per PHASE per version. From it:
the agent side (tokens × per-model API price), the human side (tokens × per-phase rate × per-phase
wage), the idle split (real wall-clock), the Manažér overhead (measured wait, info-only), and the
headline ROI (N× faster, M× cheaper, EUR saved) per version + cumulative. (Replaces the v1 per-role
model; per-customer deploy is a separate ops cost, not part of the build-pipeline ROI here.)
"""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from pydantic import BaseModel


class ModelTokensRead(BaseModel):
    """Token usage attributed to one model family/id within a phase's usage."""

    input_tokens: int
    output_tokens: int


class UsageTotalsRead(BaseModel):
    """Summed token usage + active-compute time for a scope (phase / version / project)."""

    input_tokens: int
    output_tokens: int
    duration_seconds: float
    messages: int


class PhaseMetricRead(BaseModel):
    """One build phase's agent side (measured) vs human side (token-derived) within a scope."""

    phase: str
    # AGENT (measured)
    active_seconds: float
    #: inter-turn idle is not attributable to a single phase → always None (the real internal idle is a
    #: version-level wall-clock figure on VersionMetricsRead). Field kept for table-layout symmetry.
    internal_idle_seconds: Optional[float]
    input_tokens: int
    output_tokens: int
    parse_attempts: int
    #: tokens × per-model API price; None if ANY present model is unpriced after the fallback chain.
    agent_cost: Optional[float]
    agent_value_in: Optional[float]
    agent_value_out: Optional[float]
    by_model: dict[str, ModelTokensRead]
    #: model keys with no resolvable price (drives the per-row "AI cena chýba: model X" badge).
    unpriced_model_keys: list[str]
    # HUMAN (token-derived)
    #: tokens × per-phase rate (minutes per 1M tokens) — None when the rate is unset.
    human_minutes: Optional[float]
    #: human_minutes × per-phase wage — None when either input is unset.
    human_cost: Optional[float]
    # RATIOS — None whenever EITHER side is None (honest coherence).
    x_faster: Optional[float]
    m_cheaper: Optional[float]
    eur_saved: Optional[float]


class SystemOverheadRead(BaseModel):
    """Un-phased engine (``system`` author with no phase stamp) tokens — info-only; foots the per-phase
    table but never enters the headline ROI. (In the v2 engine virtually every message carries a phase
    stamp, so this row is usually empty; kept for completeness + back-compat.)"""

    input_tokens: int
    output_tokens: int
    active_seconds: float
    agent_cost: Optional[float]


class ManagerOverheadRead(BaseModel):
    """Manažér (human-in-the-loop) overhead: count of interventions + measured wait time (info-only).

    The wait is measured (real seconds the build sat at a schvaľovací bod / blocked). It is shown as a
    pure overhead figure — the v1 per-role Director wage/rate model is retired (CR-V2-029), so this row
    carries no priced cost: the agent-vs-human comparison is purely per-phase now."""

    interventions: int
    #: measured Manažér-wait (idle-a), seconds.
    wait_seconds: float


class RoiHeadlineRead(BaseModel):
    """Headline ROI over the comparison phases (NOT incl. the ``system`` row)."""

    agent_active_minutes: float
    human_minutes_total: Optional[float]
    #: Σ comparison-phase agent cost — None when pricing is incomplete.
    agent_cost_total: Optional[float]
    human_cost_total: Optional[float]
    #: human-time vs agent ACTIVE time.
    x_faster: Optional[float]
    m_cheaper: Optional[float]
    eur_saved: Optional[float]
    #: share of tokens whose model the envelope didn't name (priced flat) — model-drift visibility.
    unknown_model_token_pct: float
    #: ROI-angle label: we pay a flat Claude MAX subscription → marginal cost ~0.
    flat_subscription: bool
    marginal_cost_eur: float
    #: pricing AND rates AND wages all configured (the headline is fully meaningful).
    configured: bool
    pricing_configured: bool
    rates_configured: bool
    wages_configured: bool
    #: cumulative coverage: figures summed over `covered_versions` of `total_versions` (None-safe).
    covered_versions: int
    total_versions: int


class VersionMetricsRead(BaseModel):
    version_id: UUID
    version_number: str
    status: str
    #: version grand total (all phases + manager + system).
    usage: UsageTotalsRead
    #: the 4 comparison (agent) phases.
    by_phase: list[PhaseMetricRead]
    system_overhead: SystemOverheadRead
    manager: ManagerOverheadRead
    #: accumulated Manažér-wait + any live open wait (idle-a), seconds.
    manager_wait_seconds: float
    #: total wall-clock − active − manager-wait (idle-b); None when the message span is 0/unknown.
    internal_idle_seconds: Optional[float]
    #: real wall-clock span from min/max(message created_at); release_date fallback; None if unknowable.
    total_time_seconds: Optional[float]
    roi: RoiHeadlineRead


class ProjectMetricsRead(BaseModel):
    project_id: UUID
    slug: str
    #: cumulative grand total across all versions.
    usage: UsageTotalsRead
    #: cumulative per comparison phase.
    by_phase: list[PhaseMetricRead]
    system_overhead: SystemOverheadRead
    manager: ManagerOverheadRead
    by_version: list[VersionMetricsRead]
    #: cumulative headline (None-safe coverage across versions).
    roi: RoiHeadlineRead
