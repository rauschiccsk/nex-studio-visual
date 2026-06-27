"""Pipeline token-usage + dispatch-time aggregation (WS-D, CR-NS-036; v2 metrics per-phase basis).

Rolls up the per-turn metrics that :func:`orchestrator.invoke_agent` captures into each
``PipelineMessage.payload`` (``usage`` ``{input_tokens, output_tokens, model}`` + ``timing``
``{duration_seconds, parse_attempts}``) into a version grand total (:func:`aggregate_pipeline_usage`)
and a per-PHASE split (:func:`aggregate_usage_by_phase`) — the single reproducible base for the
per-phase agent-vs-human metrics page.

This is the **data layer** for the metrics page (E5; per-phase recompute in CR-V2-029); there is
intentionally no cost calculation or UI here (the per-phase rate/wage + per-model price settings live
in ``system_settings`` for that later comparison in ``services.metrics``). Pure read — no live
``claude`` call, no mutation.

Attribution (CR-V2-029): messages are grouped by PHASE = ``payload.phase`` (the per-turn phase stamp
the engine writes on every fold/seed message in CR-V2-009 — AI Agent → its current phase, helpers →
their spawning phase, Auditor → ``verifikacia``) ELSE ``msg.stage`` (the message's DB stage, which is
itself the canonical 4-phase value). Both resolve to one of the four phases (``priprava`` / ``navrh`` /
``programovanie`` / ``verifikacia``), so summing the returned buckets reproduces the version grand
total. (This replaces the v1 ``payload.metrics_role`` role-of-origin grouping — there are no fixed
roles left to attribute to in the v2 AI-Agent + Auditor engine.)
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db.models.pipeline import PipelineMessage


@dataclass
class ModelTokens:
    """Token usage attributed to one model family/id within a :class:`UsageTotals`."""

    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class UsageTotals:
    """Summed token usage + wall-clock for one scope (a phase / the whole version).

    ``by_model`` splits the tokens per ``payload.usage.model`` (full model id, or ``"_unknown"`` when
    the envelope carried usage but no model) so the cost layer can price each family separately;
    ``parse_attempts`` sums the per-turn re-emit count (rework evidence — metrics redesign §1.1/§1.2)."""

    input_tokens: int = 0
    output_tokens: int = 0
    duration_seconds: float = 0.0
    messages: int = 0
    parse_attempts: int = 0
    by_model: dict[str, ModelTokens] = field(default_factory=dict)

    def add(
        self,
        *,
        input_tokens: int,
        output_tokens: int,
        duration_seconds: float,
        messages: int = 1,
        parse_attempts: int = 0,
        model: str | None = None,
    ) -> None:
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.duration_seconds += duration_seconds
        self.messages += messages
        self.parse_attempts += parse_attempts
        key = model or "_unknown"
        mt = self.by_model.setdefault(key, ModelTokens())
        mt.input_tokens += input_tokens
        mt.output_tokens += output_tokens

    def merge(self, other: "UsageTotals") -> None:
        """Fold another :class:`UsageTotals` in (cumulative-across-versions), including ``by_model``."""
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.duration_seconds += other.duration_seconds
        self.messages += other.messages
        self.parse_attempts += other.parse_attempts
        for m, mt in other.by_model.items():
            dst = self.by_model.setdefault(m, ModelTokens())
            dst.input_tokens += mt.input_tokens
            dst.output_tokens += mt.output_tokens


@dataclass
class PipelineUsageAggregate:
    """Version grand-total usage roll-up for one version (WS-D; scope roll-up retired in the metrics
    redesign — per-EPIC/FEAT/TASK metrics were dropped in favour of the per-role model)."""

    version: UsageTotals = field(default_factory=UsageTotals)


def aggregate_pipeline_usage(db: Session, version_id: uuid.UUID) -> PipelineUsageAggregate:
    """Sum token usage + dispatch time across a version's pipeline into the version grand total.

    Reads every ``PipelineMessage`` for the version whose payload carries WS-D metrics (``usage`` or
    ``timing``); messages without either (plain system notes) are skipped. ``usage`` may be ``None``
    in a payload (no envelope carried it) — that contributes 0 tokens but its ``timing`` still counts,
    so retries/failed turns are in the base by construction.
    """
    agg = PipelineUsageAggregate()

    messages = (
        db.execute(
            select(PipelineMessage).where(PipelineMessage.version_id == version_id).order_by(PipelineMessage.seq.asc())
        )
        .scalars()
        .all()
    )

    for msg in messages:
        payload = msg.payload or {}
        if "usage" not in payload and "timing" not in payload:
            continue  # not a metered dispatch (e.g. a plain system notification)
        usage = payload.get("usage") or {}
        timing = payload.get("timing") or {}
        model = usage.get("model")
        agg.version.add(
            input_tokens=int(usage.get("input_tokens") or 0),
            output_tokens=int(usage.get("output_tokens") or 0),
            duration_seconds=float(timing.get("duration_seconds") or 0.0),
            parse_attempts=int(timing.get("parse_attempts") or 0),
            model=model if isinstance(model, str) else None,
        )

    return agg


def aggregate_usage_by_phase(db: Session, version_id: uuid.UUID) -> dict[str, UsageTotals]:
    """Single reproducible base: all metered messages grouped by PHASE, tokens split by model (CR-V2-029).

    Phase = ``payload.phase`` (the per-turn phase stamp the engine writes on every fold/seed message,
    CR-V2-009) ELSE ``msg.stage`` (the message's DB stage, itself the canonical 4-phase value). Both
    resolve to one of ``priprava`` / ``navrh`` / ``programovanie`` / ``verifikacia``. The scan rule is
    byte-identical to :func:`aggregate_pipeline_usage` (counts any payload bearing ``usage`` OR
    ``timing`` — including 0-token/real-wall-clock failed turns), so summing the returned buckets
    reproduces the version grand total and retries/failed attempts are in the base by construction.

    (Replaces the v1 ``aggregate_usage_by_role`` role-of-origin grouping — the v2 engine has no fixed
    roles, only the four visible phases.)
    """
    by_phase: dict[str, UsageTotals] = {}
    messages = (
        db.execute(
            select(PipelineMessage).where(PipelineMessage.version_id == version_id).order_by(PipelineMessage.seq.asc())
        )
        .scalars()
        .all()
    )
    for msg in messages:
        payload = msg.payload or {}
        if "usage" not in payload and "timing" not in payload:
            continue
        usage = payload.get("usage") or {}
        timing = payload.get("timing") or {}
        phase_stamp = payload.get("phase")
        phase = phase_stamp if isinstance(phase_stamp, str) else msg.stage
        model = usage.get("model")
        by_phase.setdefault(phase, UsageTotals()).add(
            input_tokens=int(usage.get("input_tokens") or 0),
            output_tokens=int(usage.get("output_tokens") or 0),
            duration_seconds=float(timing.get("duration_seconds") or 0.0),
            parse_attempts=int(timing.get("parse_attempts") or 0),
            model=model if isinstance(model, str) else None,
        )
    return by_phase
