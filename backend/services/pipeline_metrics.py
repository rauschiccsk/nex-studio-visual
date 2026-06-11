"""Pipeline token-usage + dispatch-time aggregation (WS-D, CR-NS-036).

Rolls up the per-turn metrics that :func:`orchestrator.invoke_agent` captures into each
``PipelineMessage.payload`` (``usage`` ``{input_tokens, output_tokens, model}`` + ``timing``
``{duration_seconds, parse_attempts}``) — summed per TASK → FEAT → EPIC and a version grand total.

This is the **data layer** for the future metrics page (Phase 3 / E5); there is intentionally no
cost calculation or UI here (the developer-rate / API-price settings live in ``config.settings`` for
that later comparison). Pure read — no live ``claude`` call, no mutation.

Attribution: build-phase messages carry ``payload.task_id`` (orchestrator writes it per task), so
they roll up TASK → FEAT (``Task.feat_id``) → EPIC (``Feat.epic_id``). Gate-phase / system messages
have no ``task_id`` and contribute to the **version** total only (they are pipeline overhead not
owned by any one task) — never silently dropped.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db.models.pipeline import PipelineMessage
from backend.db.models.tasks import Epic, Feat, Task


@dataclass
class UsageTotals:
    """Summed token usage + wall-clock for one scope (a task / feat / epic / the whole version)."""

    input_tokens: int = 0
    output_tokens: int = 0
    duration_seconds: float = 0.0
    messages: int = 0

    def add(self, *, input_tokens: int, output_tokens: int, duration_seconds: float) -> None:
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.duration_seconds += duration_seconds
        self.messages += 1


@dataclass
class PipelineUsageAggregate:
    """Per-scope usage roll-up for one version (WS-D). Keys are the entity UUIDs."""

    version: UsageTotals = field(default_factory=UsageTotals)
    by_task: dict[uuid.UUID, UsageTotals] = field(default_factory=dict)
    by_feat: dict[uuid.UUID, UsageTotals] = field(default_factory=dict)
    by_epic: dict[uuid.UUID, UsageTotals] = field(default_factory=dict)


def _coerce_task_id(raw: object) -> uuid.UUID | None:
    """Parse a ``payload.task_id`` (stored as a str) to a UUID, tolerating absence / bad data."""
    if not raw:
        return None
    if isinstance(raw, uuid.UUID):
        return raw
    try:
        return uuid.UUID(str(raw))
    except (ValueError, AttributeError, TypeError):
        return None


def aggregate_pipeline_usage(db: Session, version_id: uuid.UUID) -> PipelineUsageAggregate:
    """Sum token usage + dispatch time across a version's pipeline, rolled up TASK → FEAT → EPIC.

    Reads every ``PipelineMessage`` for the version whose payload carries WS-D metrics (``usage`` or
    ``timing``); messages without either (plain system notes) are skipped. ``usage`` may be ``None``
    in a payload (no envelope carried it) — that contributes 0 tokens but its ``timing`` still counts.
    """
    agg = PipelineUsageAggregate()

    # task_id → (feat_id, epic_id) for this version's plan, so a build message rolls up its parents.
    task_parents: dict[uuid.UUID, tuple[uuid.UUID, uuid.UUID]] = {
        row[0]: (row[1], row[2])
        for row in db.execute(
            select(Task.id, Feat.id, Epic.id)
            .join(Feat, Task.feat_id == Feat.id)
            .join(Epic, Feat.epic_id == Epic.id)
            .where(Epic.version_id == version_id)
        ).all()
    }

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
        input_tokens = int(usage.get("input_tokens") or 0)
        output_tokens = int(usage.get("output_tokens") or 0)
        duration_seconds = float(timing.get("duration_seconds") or 0.0)

        agg.version.add(input_tokens=input_tokens, output_tokens=output_tokens, duration_seconds=duration_seconds)

        task_id = _coerce_task_id(payload.get("task_id"))
        if task_id is None or task_id not in task_parents:
            continue  # gate/system overhead — counted at the version level only
        feat_id, epic_id = task_parents[task_id]
        for scope_map, key in (
            (agg.by_task, task_id),
            (agg.by_feat, feat_id),
            (agg.by_epic, epic_id),
        ):
            scope_map.setdefault(key, UsageTotals()).add(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                duration_seconds=duration_seconds,
            )

    return agg
