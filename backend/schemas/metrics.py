"""Pydantic schemas for the per-phase cost page — *Náklady* (E5; CR-V2-029, reshaped in CR-V2-063).

A computed read-only aggregate over the WS-D capture (`PipelineMessage.payload.usage`/`.timing`,
grouped by the per-turn `phase` stamp) + Manažér-wait accumulation + the human-work coefficient + the
per-phase wages + per-model pricing, PLUS the hand-entered ``external_cost`` entries.

HONEST by construction, two rules that govern every field below:

* **A missing input stays ``None``** — every figure that depends on an unconfigured input (price /
  coefficient / wage) is ``None``, never a fabricated ``0``.
* **Measured and entered figures are summed but never merged** — ``kind`` keeps them distinguishable
  per row, and every total carries its ``…_measured`` / ``…_external`` split.

The question this shape answers is *what it cost* (per phase, per version, per project), not the v1
*are we better than a human?* — the ROI headline (N× faster, M× cheaper, EUR saved) is retired with
CR-V2-063 together with its three confirmed defects.
"""

from __future__ import annotations

from typing import Literal, Optional
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


class CostRowRead(BaseModel):
    """One cost row within a scope. `kind` keeps measured and entered figures distinguishable all
    the way to the screen — a renderer must never present them as the same class of number."""

    key: str  # phase key, or "externe", or "system"
    kind: Literal["phase", "external", "system"]
    turns: int  # metered messages (UsageTotals.messages) — NOT parse_attempts
    input_tokens: int
    output_tokens: int
    #: this row's tokens ÷ the scope's total tokens × 100. Always computable (never None), which is
    #: why it is token share and not cost share — cost is None whenever a model is unpriced.
    share_pct: float
    agent_cost: Optional[float]
    unpriced_model_keys: list[str]
    human_minutes: Optional[float]
    human_cost: Optional[float]
    active_seconds: float  # kept: real measured compute time (0.0 for external rows)


class CostTotalsRead(BaseModel):
    """Scope totals with the mandatory measured/entered split — on the VOLUME figures too.

    ``turns`` / ``input_tokens`` / ``output_tokens`` are the combined totals; their ``…_measured``
    (the ``phase`` + ``system`` rows) and ``…_external`` (the ``external`` row) halves ship alongside so
    the screen's "z toho namerané / z toho ručne zadané" split covers EVERY column, not only money — a
    merged turn/token count would be exactly the merge rule 2 above forbids."""

    turns: int
    turns_measured: int
    turns_external: int
    input_tokens: int
    input_tokens_measured: int
    input_tokens_external: int
    output_tokens: int
    output_tokens_measured: int
    output_tokens_external: int
    agent_cost_measured: Optional[float]
    agent_cost_external: Optional[float]
    agent_cost_total: Optional[float]
    human_minutes_measured: Optional[float]
    human_minutes_external: Optional[float]
    human_minutes_total: Optional[float]
    human_cost_measured: Optional[float]
    human_cost_external: Optional[float]
    human_cost_total: Optional[float]


class ManagerOverheadRead(BaseModel):
    """Manažér (human-in-the-loop) overhead: count of interventions + measured wait time (info-only).

    The wait is measured (real seconds the build sat at a schvaľovací bod / blocked). It is shown as a
    pure overhead figure — the v1 per-role Director wage/rate model is retired (CR-V2-029), so this row
    carries no priced cost: the agent-vs-human comparison is purely per-phase now."""

    interventions: int
    #: measured Manažér-wait (idle-a), seconds.
    wait_seconds: float


class VersionCostsRead(BaseModel):
    version_id: UUID
    version_number: str
    status: str
    #: version grand total (all phases + manager + system) — measured only, external is never metered.
    usage: UsageTotalsRead
    #: ORDER IS A CONTRACT (the screen renders `rows` as they arrive): the comparison phases in
    #: canonical order (the list derives from ``STAGE_VALUES`` — 5 today) → the ``external`` row →
    #: the ``system`` row last, it foots the table. A row absent from the scope is simply not emitted.
    rows: list[CostRowRead]
    totals: CostTotalsRead
    manager: ManagerOverheadRead
    #: accumulated Manažér-wait + any live open wait (idle-a), seconds.
    manager_wait_seconds: float
    #: total wall-clock − active − manager-wait (idle-b); None when the message span is 0/unknown.
    internal_idle_seconds: Optional[float]
    #: real wall-clock span from min/max(message created_at); release_date fallback; None if unknowable.
    total_time_seconds: Optional[float]


class ProjectCostsRead(BaseModel):
    project_id: UUID
    slug: str
    #: cumulative grand total across all versions.
    usage: UsageTotalsRead
    #: cumulative rows, same order contract as a version's.
    rows: list[CostRowRead]
    totals: CostTotalsRead
    by_version: list[VersionCostsRead]
    manager: ManagerOverheadRead
    # ── the assumption block the screen MUST display (human work is a conversion, not a measurement) ──
    #: the single human-work coefficient (min / 1M tokens); None when 0/unset.
    coefficient_minutes_per_mtok: Optional[float]
    #: row key -> hourly wage, None when unset. No ``"system"`` entry — un-phased engine tokens have no
    #: human equivalent by definition (that row is agent-only).
    wages: dict[str, Optional[float]]
    currency: str = "EUR"
    pricing_configured: bool
    coefficient_configured: bool
    wages_configured: bool
