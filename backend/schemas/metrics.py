"""Pydantic schemas for the role-based metrics / ROI page (E5; metrics redesign).

A computed read-only aggregate over the WS-D capture (`PipelineMessage.payload.usage`/`.timing`) +
Director-wait accumulation + per-role rates/wages + per-model pricing. HONEST by construction: every
figure that depends on an unconfigured input (price / rate / wage) is ``None`` — never a fabricated
number; a ratio is ``None`` whenever EITHER side is ``None``.

Single reproducible base = all tokens (IN+OUT, incl. retries/failed) per ROLE-OF-ORIGIN per version.
From it: the agent side (tokens × per-model API price), the human side (tokens × per-role rate × per-
role wage), the idle split (real wall-clock), the Director overhead (measured agent-side + symmetric
human-side), and the headline ROI (N× faster, M× cheaper, EUR saved) per version + cumulative.
"""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from pydantic import BaseModel


class ModelTokensRead(BaseModel):
    """Token usage attributed to one model family/id within a role's usage."""

    input_tokens: int
    output_tokens: int


class UsageTotalsRead(BaseModel):
    """Summed token usage + active-compute time for a scope (role / version / project)."""

    input_tokens: int
    output_tokens: int
    duration_seconds: float
    messages: int


class RoleMetricRead(BaseModel):
    """One comparison role's agent side (measured) vs human side (token-derived) within a scope."""

    role: str
    # AGENT (measured)
    active_seconds: float
    #: inter-turn idle is not attributable to a single role → always None (the real internal idle is a
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
    #: tokens × per-role rate (minutes per 1M tokens) — None when the rate is unset.
    human_minutes: Optional[float]
    #: human_minutes × per-role wage — None when either input is unset.
    human_cost: Optional[float]
    # RATIOS — None whenever EITHER side is None (honest coherence, metrics redesign §2.7).
    x_faster: Optional[float]
    m_cheaper: Optional[float]
    eur_saved: Optional[float]


class SystemOverheadRead(BaseModel):
    """Un-compared engine (``system``) tokens — info-only; foots the per-role table but never enters
    the headline ROI (metrics redesign §1.4)."""

    input_tokens: int
    output_tokens: int
    active_seconds: float
    agent_cost: Optional[float]


class DirectorMetricRead(BaseModel):
    """Director overhead: count + measured agent-side wait cost + symmetric human-side director cost."""

    interventions: int
    #: measured Director-wait (idle-a), seconds.
    agent_wait_seconds: float
    #: agent_wait × director wage — empirical (measured) agent-side director cost; None when wage unset.
    agent_director_cost: Optional[float]
    #: human-side director minutes (Σ human role-minutes × director_minutes_per_human_role_hour).
    human_director_minutes: Optional[float]
    #: human_director_minutes × director wage — same rate model as the agent side (metrics redesign §2.5).
    human_director_cost: Optional[float]


class RoiHeadlineRead(BaseModel):
    """Headline ROI over the comparison roles + Director (NOT incl. the ``system`` row)."""

    agent_active_minutes: float
    human_minutes_total: Optional[float]
    #: Σ comparison-role agent cost + Director agent cost — None when pricing is incomplete.
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
    #: version grand total (all roles + director + system).
    usage: UsageTotalsRead
    #: the 5 comparison (agent) roles.
    by_role: list[RoleMetricRead]
    system_overhead: SystemOverheadRead
    director: DirectorMetricRead
    #: accumulated Director-wait + any live open wait (idle-a), seconds.
    director_wait_seconds: float
    #: total wall-clock − active − director-wait (idle-b); None when the message span is 0/unknown.
    internal_idle_seconds: Optional[float]
    #: real wall-clock span from min/max(message created_at); release_date fallback; None if unknowable.
    total_time_seconds: Optional[float]
    roi: RoiHeadlineRead


class ProjectMetricsRead(BaseModel):
    project_id: UUID
    slug: str
    #: cumulative grand total across all versions.
    usage: UsageTotalsRead
    #: cumulative per comparison role.
    by_role: list[RoleMetricRead]
    system_overhead: SystemOverheadRead
    director: DirectorMetricRead
    by_version: list[VersionMetricsRead]
    #: cumulative headline (None-safe coverage across versions).
    roi: RoiHeadlineRead
