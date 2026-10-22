"""Orientačná cena verzie — from the number of tasks in its plan, measured on the finished versions (DEV-154).

Director 20.10.2026: „Jedná sa skutočne len o odhad, aby zákazník vedel sa rozhodnúť, či objedná daný projekt.“ Measured
22.10.2026 on 15 finished versions: tokens delivered per task of the plan stray 26 % from their mean on the versions
after a project's first (each estimated from the others: 22 % off on average), the size of the Špecifikácia 35 %
(39 %, worst 126 %). Director 22.10.2026: „Súhlasím“ — the estimate comes after Návrh, from the plan.

* A finished version becomes a measurement once (:func:`refresh_samples`): the tasks of its plan and the tokens its
  statement counts — code and tests, documentation (:func:`delivery_statement.preview`).
* A project's first version carries the skeleton of the whole application; it is measured apart from the later ones.
* The estimate = tasks × mean tokens per task of the like versions × the version's rates. The spread is how far those
  versions strayed from their mean; fewer than ``settings.price_estimate_min_samples`` of them is said, not hidden.
* A fix of our own error is not billed and gets no estimate; a version without a plan has none yet.
* It is kept at the version (:class:`PriceEstimate`) — no invoice: the statement after the delivery says what is
  billed, and the billing tab shows the two side by side.
"""

from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.config.settings import settings
from backend.db.models.pipeline import PipelineState
from backend.db.models.price_estimate import PriceEstimate, PriceSample
from backend.db.models.projects import Project
from backend.db.models.tasks import Epic, Feat, Task
from backend.db.models.versions import Version
from backend.services import delivered_tokens, delivery_statement

logger = logging.getLogger(__name__)

_CENT = Decimal("0.01")
_THOUSAND = Decimal(1000)


@dataclass(frozen=True)
class Figures:
    tasks: int
    first_version: bool
    code_tokens: int
    docs_tokens: int
    rate_code: Decimal
    rate_docs: Decimal
    amount_eur: Decimal
    spread_pct: Optional[int]
    samples: int


def tasks_of(db: Session, version_id) -> int:
    """The tasks of the version's plan."""
    return db.execute(
        select(func.count(Task.id))
        .join(Feat, Feat.id == Task.feat_id)
        .join(Epic, Epic.id == Feat.epic_id)
        .where(Epic.version_id == version_id)
    ).scalar_one()


def _finished(db: Session, project_id) -> list[Version]:
    return list(
        db.execute(
            select(Version)
            .join(PipelineState, PipelineState.version_id == Version.id)
            .where(
                Version.project_id == project_id,
                PipelineState.status == "done",
                PipelineState.flow_type == "new_version",
            )
            .order_by(Version.created_at)
        ).scalars()
    )


def is_first(db: Session, version: Version) -> bool:
    """No version of the project finished before this one was founded — it builds the application from nothing."""
    return not [
        v for v in _finished(db, version.project_id) if v.id != version.id and v.created_at < version.created_at
    ]


def refresh_samples(db: Session, projects_root: Path) -> int:
    """Every finished version not yet measured becomes a measurement; returns how many were added.

    A version whose statement cannot be counted (no delivered commit, an unreadable repository) is left out this time
    and tried again on the next estimate."""
    measured = set(db.execute(select(PriceSample.version_id)).scalars())
    added = 0
    for project in db.execute(select(Project)).scalars():
        for version in _finished(db, project.id):
            if version.id in measured:
                continue
            tasks = tasks_of(db, version.id)
            if not tasks:
                continue
            preview = delivery_statement.preview(db, version, Path(projects_root) / project.slug)
            if preview.count is None:
                continue
            db.add(
                PriceSample(
                    version_id=version.id,
                    first_version=is_first(db, version),
                    tasks=tasks,
                    code_tokens=preview.count.tokens(delivered_tokens.KIND_CODE)
                    + preview.count.tokens(delivered_tokens.KIND_TESTS),
                    docs_tokens=preview.count.tokens(delivered_tokens.KIND_DOCS),
                )
            )
            added += 1
    if added:
        db.commit()
    return added


def figures(db: Session, version: Version) -> Optional[Figures]:
    """The estimate for ``version`` from the measurements — ``None`` for a fix of our error, a version without a plan
    or when nothing like it was measured yet."""
    if delivery_statement.work_kind(db, version) == delivery_statement.WORK_FIX:
        return None
    tasks = tasks_of(db, version.id)
    if not tasks:
        return None
    first = is_first(db, version)
    like = list(
        db.execute(
            select(PriceSample).where(PriceSample.first_version == first, PriceSample.version_id != version.id)
        ).scalars()
    )
    if not like:
        return None
    code_per_task = statistics.mean(s.code_tokens / s.tasks for s in like)
    docs_per_task = statistics.mean(s.docs_tokens / s.tasks for s in like)
    per_task = [(s.code_tokens + s.docs_tokens) / s.tasks for s in like]
    spread = round(statistics.stdev(per_task) / statistics.mean(per_task) * 100) if len(per_task) >= 2 else None
    code_tokens, docs_tokens = round(code_per_task * tasks), round(docs_per_task * tasks)
    rate_code, rate_docs = delivery_statement.version_rates(db, version)
    amount = (Decimal(code_tokens) / _THOUSAND * rate_code + Decimal(docs_tokens) / _THOUSAND * rate_docs).quantize(
        _CENT, rounding=ROUND_HALF_UP
    )
    return Figures(tasks, first, code_tokens, docs_tokens, rate_code, rate_docs, amount, spread, len(like))


def stored(db: Session, version_id) -> Optional[PriceEstimate]:
    return db.execute(select(PriceEstimate).where(PriceEstimate.version_id == version_id)).scalar_one_or_none()


def current(db: Session, version: Version, projects_root: Path) -> Optional[PriceEstimate]:
    """The version's estimate, measured anew when its plan or the measurements changed; kept at the version."""
    refresh_samples(db, projects_root)
    found = figures(db, version)
    kept = stored(db, version.id)
    if found is None:
        if kept is not None:
            db.delete(kept)
            db.commit()
        return None
    if kept is not None and (kept.tasks, kept.samples, kept.amount_eur) == (
        found.tasks,
        found.samples,
        found.amount_eur,
    ):
        return kept
    if kept is None:
        kept = PriceEstimate(version_id=version.id)
        db.add(kept)
    for field in Figures.__dataclass_fields__:
        setattr(kept, field, getattr(found, field))
    kept.created_at = func.clock_timestamp()
    db.commit()
    db.refresh(kept)
    return kept


def enough(estimate: PriceEstimate) -> bool:
    return estimate.samples >= settings.price_estimate_min_samples
