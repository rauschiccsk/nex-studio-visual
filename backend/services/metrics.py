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
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.config.settings import settings
from backend.db.models.external_cost import ExternalCost
from backend.db.models.pipeline import STAGE_VALUES, PipelineMessage, PipelineState
from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.schemas.metrics import (
    CostRowRead,
    CostTotalsRead,
    ManagerOverheadRead,
    ProjectCostsRead,
    UsageTotalsRead,
    VersionCostsRead,
)
from backend.services import system_setting
from backend.services.pipeline_metrics import (
    UsageTotals,
    aggregate_pipeline_usage,
    aggregate_usage_by_phase,
)

logger = logging.getLogger(__name__)

#: The build phases compared against a human, derived from the canonical stage tuple minus the
#: terminal ``done`` (no work/tokens attribute to ``done`` — it is the post-build terminal phase), so
#: the list stays in lock-step with ``STAGE_VALUES`` (5 phases today) and a phase rename can't fall out
#: silently. The Manažér overhead (human-in-the-loop wait) is handled separately as an info-only row;
#: ``system`` is engine-only (a message with no phase stamp + a ``system`` author) and lands in the
#: agent-only ``system`` row.
TERMINAL_PHASE = "done"
COMPARISON_PHASES: tuple[str, ...] = tuple(s for s in STAGE_VALUES if s != TERMINAL_PHASE)
_PRICE_FAMILIES: tuple[str, ...] = ("opus", "sonnet", "haiku")

#: Row keys outside the build phases. ``externe`` is hand-entered spend (its wage key is
#: ``metrics_hourly_wage_externe``); ``system`` is un-phased engine spend and is AGENT-ONLY — it has no
#: wage key and never carries human figures.
EXTERNAL_ROW_KEY = "externe"
SYSTEM_ROW_KEY = "system"

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
    """tokens → minutes via the single conversion coefficient (minutes per 1M total tokens). None when
    unset (0) — the human columns then disappear rather than showing a fabricated figure."""
    if conv_rate <= 0:
        return None
    return (t.input_tokens + t.output_tokens) / 1_000_000.0 * conv_rate


def _human_cost(human_minutes: Optional[float], wage: float) -> Optional[float]:
    if human_minutes is None or wage <= 0:
        return None
    return human_minutes / 60.0 * wage


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
    return bool(t.input_tokens or t.output_tokens or t.duration_seconds or t.parse_attempts)


def _phase_row(db: Session, phase: str, t: UsageTotals, flat_in: float, flat_out: float) -> CostRowRead:
    """One ``kind="phase"`` row. ``share_pct`` is filled in per scope afterwards (see
    :func:`_fill_share_pct`) — it needs every row of the scope to exist first."""
    human_minutes = _human_minutes_for_phase(t, _coefficient(db))
    agent_cost, _, _, unpriced = _agent_cost_split(db, t.by_model, flat_in, flat_out)
    return CostRowRead(
        key=phase,
        kind="phase",
        turns=t.messages,
        input_tokens=t.input_tokens,
        output_tokens=t.output_tokens,
        share_pct=0.0,
        agent_cost=agent_cost,
        unpriced_model_keys=unpriced,
        human_minutes=human_minutes,
        human_cost=_human_cost(human_minutes, _phase_wage(db, phase)),
        active_seconds=t.duration_seconds,
    )


def _build_phases(db: Session, by_phase: dict[str, UsageTotals], flat_in: float, flat_out: float) -> list[CostRowRead]:
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
        rows.append(_phase_row(db, phase, t, flat_in, flat_out))
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


def _system_rows(db: Session, t: UsageTotals, flat_in: float, flat_out: float) -> list[CostRowRead]:
    """The ``kind="system"`` row (0 or 1) — un-phased engine spend, AGENT-ONLY.

    ``human_minutes``/``human_cost`` are ALWAYS ``None`` and there is no ``metrics_hourly_wage_system``
    key: engine tokens nobody stamped with a phase have no human equivalent by definition. Its
    ``agent_cost`` DOES belong to ``agent_cost_measured`` (it is metered spend) but it must never drag
    the human totals to ``None`` — hence the None-propagation in :func:`_cost_totals` is scoped to the
    phase rows."""
    if not _has_activity(t):
        return []
    agent_cost, _, _, unpriced = _agent_cost_split(db, t.by_model, flat_in, flat_out)
    return [
        CostRowRead(
            key=SYSTEM_ROW_KEY,
            kind="system",
            turns=t.messages,
            input_tokens=t.input_tokens,
            output_tokens=t.output_tokens,
            share_pct=0.0,
            agent_cost=agent_cost,
            unpriced_model_keys=unpriced,
            human_minutes=None,
            human_cost=None,
            active_seconds=t.duration_seconds,
        )
    ]


def _external_rows(
    db: Session,
    project_id: uuid.UUID,
    version_id: Optional[uuid.UUID],
    flat_in: float,
    flat_out: float,
) -> list[CostRowRead]:
    """The ``kind="external"`` row (0 or 1) aggregating the scope's hand-entered ``external_cost`` rows.

    Scope: ``version_id`` given → that version's entries only; ``None`` (project level) → ALL the
    project's entries, version-bound and version-less alike (a version-less entry belongs to the project
    total and to no version).

    Priced through the SAME ``_agent_cost_split`` chain (the entries' ``model`` field builds the
    ``by_model`` split) and converted with the SAME coefficient as measured work, with the
    ``metrics_hourly_wage_externe`` wage. ``active_seconds`` is 0.0 (nothing was metered here) and
    ``turns`` is the number of entries."""
    stmt = select(ExternalCost).where(ExternalCost.project_id == project_id)
    if version_id is not None:
        stmt = stmt.where(ExternalCost.version_id == version_id)
    entries = db.execute(stmt).scalars().all()
    if not entries:
        return []

    t = UsageTotals()
    for entry in entries:
        t.add(
            input_tokens=int(entry.input_tokens or 0),
            output_tokens=int(entry.output_tokens or 0),
            duration_seconds=0.0,
            model=entry.model,
        )

    human_minutes = _human_minutes_for_phase(t, _coefficient(db))
    agent_cost, _, _, unpriced = _agent_cost_split(db, t.by_model, flat_in, flat_out)
    return [
        CostRowRead(
            key=EXTERNAL_ROW_KEY,
            kind="external",
            turns=t.messages,
            input_tokens=t.input_tokens,
            output_tokens=t.output_tokens,
            share_pct=0.0,
            agent_cost=agent_cost,
            unpriced_model_keys=unpriced,
            human_minutes=human_minutes,
            human_cost=_human_cost(human_minutes, _phase_wage(db, EXTERNAL_ROW_KEY)),
            active_seconds=0.0,
        )
    ]


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
    by_phase: dict[str, UsageTotals],
    project_id: uuid.UUID,
    version_id: Optional[uuid.UUID],
    flat_in: float,
    flat_out: float,
) -> list[CostRowRead]:
    """A scope's rows in PAYLOAD ORDER — the screen renders them as they arrive, so the order is a
    backend contract: phase rows in canonical ``COMPARISON_PHASES`` order, then the ``external`` row,
    then the ``system`` row last (it foots the table). A row absent from the scope is not emitted."""
    rows = _build_phases(db, by_phase, flat_in, flat_out)
    rows += _external_rows(db, project_id, version_id, flat_in, flat_out)
    rows += _system_rows(db, _overhead_totals(by_phase), flat_in, flat_out)
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


def _cost_totals(rows: list[CostRowRead]) -> CostTotalsRead:
    """Scope totals with the measured/entered split — summed but NEVER merged.

    **None propagation, scoped deliberately.** A ``…_measured`` figure is ``None`` iff some MEASURED row
    that carries tokens has that figure unconfigured. For the human side that scope is the
    ``kind="phase"`` rows ONLY (the pre-CR ``_cost_totals`` scope, kept): the agent-only ``system`` row
    carries ``None`` human figures by definition and must not drag the human totals to ``None``. For the
    agent side the ``system`` row COUNTS — it is metered spend, so an unpriced system row does make
    ``agent_cost_measured`` ``None``.

    A scope with no external entries reports ``0.0`` externals — a REAL zero (nothing was entered), not
    ``None``. A ``…_total`` is ``None`` when either half is ``None``.

    **Only a token-bearing row can make a figure incomplete** (0 tokens cost 0 whatever the price/wage),
    and on the human side at least ONE such phase row must EXIST: with none, ``all(...)`` over the empty
    filtered sequence would be vacuously True and Σ of an all-``None`` list would render a fabricated
    ``0`` — exactly what a scope whose whole metered spend sits in the agent-only ``system`` row would
    show ("Cena ľudskej práce 0,00 €" under a table of ``—``). No phase tokens → no human figure."""
    phase_rows = [r for r in rows if r.kind == "phase"]
    measured_rows = [r for r in rows if r.kind in ("phase", "system")]
    external_row = next((r for r in rows if r.kind == "external"), None)

    token_measured_rows = [r for r in measured_rows if r.input_tokens or r.output_tokens]
    token_phase_rows = [r for r in phase_rows if r.input_tokens or r.output_tokens]

    agent_ok = all(r.agent_cost is not None for r in token_measured_rows)
    human_ok = bool(token_phase_rows) and all(r.human_minutes is not None for r in token_phase_rows)
    human_cost_ok = bool(token_phase_rows) and all(r.human_cost is not None for r in token_phase_rows)

    agent_measured = _sum_or_none([r.agent_cost for r in measured_rows], complete=agent_ok)
    human_minutes_measured = _sum_or_none([r.human_minutes for r in phase_rows], complete=human_ok)
    human_cost_measured = _sum_or_none([r.human_cost for r in phase_rows], complete=human_cost_ok)

    # No external entry at all → a real 0 on every external figure (nothing was entered). An entry that
    # exists carries its own figures, ``None`` included (unpriced model / unset wage). The VOLUME figures
    # split the same way as the money ones — the screen's "z toho namerané / z toho ručne zadané" rows
    # must be able to cover every column, not just money.
    agent_external: Optional[float] = external_row.agent_cost if external_row else 0.0
    human_minutes_external: Optional[float] = external_row.human_minutes if external_row else 0.0
    human_cost_external: Optional[float] = external_row.human_cost if external_row else 0.0

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
        agent_cost_measured=agent_measured,
        agent_cost_external=agent_external,
        agent_cost_total=_total_of(agent_measured, agent_external),
        human_minutes_measured=human_minutes_measured,
        human_minutes_external=human_minutes_external,
        human_minutes_total=_total_of(human_minutes_measured, human_minutes_external),
        human_cost_measured=human_cost_measured,
        human_cost_external=human_cost_external,
        human_cost_total=_total_of(human_cost_measured, human_cost_external),
    )


# ── config flags / assumptions ────────────────────────────────────────────────


def _config_flags(db: Session, flat_in: float, flat_out: float) -> tuple[bool, bool, bool]:
    """``(pricing, coefficient, wages)`` — the per-dimension config booleans the screen shows in its
    assumption strip (an unconfigured dimension hides its columns instead of faking them)."""
    pricing_configured = (flat_in > 0 and flat_out > 0) or any(
        _effective_price(db, f"api_price_input_per_mtok_{fam}", 0.0) > 0
        and _effective_price(db, f"api_price_output_per_mtok_{fam}", 0.0) > 0
        for fam in _PRICE_FAMILIES
    )
    coefficient_configured = _coefficient(db) > 0
    # Every wage-bearing row counts — ``externe`` included: its wage produces a real human-cost figure
    # on screen, so a scope priced through it alone must never be labelled "Mzdy nenastavené".
    wages_configured = any(_phase_wage(db, key) > 0 for key in WAGE_ROW_KEYS)
    return pricing_configured, coefficient_configured, wages_configured


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
    """Aggregate the project's cost per phase / version / project — measured + hand-entered, split."""
    flat_in = _effective_price(db, "api_price_input_per_mtok", settings.api_price_input_per_mtok)
    flat_out = _effective_price(db, "api_price_output_per_mtok", settings.api_price_output_per_mtok)
    pricing_configured, coefficient_configured, wages_configured = _config_flags(db, flat_in, flat_out)

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

    for version in versions:
        grand = aggregate_pipeline_usage(db, version.id).version
        by_phase_totals = aggregate_usage_by_phase(db, version.id)
        v_wait = _manager_wait_seconds(db, version.id)
        interventions = _manager_interventions(db, version.id)

        rows = _scope_rows(db, by_phase_totals, project.id, version.id, flat_in, flat_out)
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
    cum_rows = _scope_rows(db, cumulative_by_phase, project.id, None, flat_in, flat_out)

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
        pricing_configured=pricing_configured,
        coefficient_configured=coefficient_configured,
        wages_configured=wages_configured,
    )
