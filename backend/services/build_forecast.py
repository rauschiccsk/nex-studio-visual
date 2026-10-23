"""How long the agent will work on a version's tasks — forecast from what the cockpit measured (DEV-210).

The task plan used to carry the agent's own guess: ``estimated_minutes``, asked for as "a realistic estimate for a
capable HUMAN developer". Its sum was shown as the plan's „odhad ~3260 min (~54.3 h)“ — NEX Manager 1.4.0, whose
tasks then took the agent about an hour. Measured 23.10.2026 on the twelve latest versions, the agent worked 4–39×
faster than that number; it was asked for when work was to be billed by the hour, and billing went to tokens. The
Director: „Ja potrebujem odhad, že agent približne koľko to bude robiť.“

So the forecast is the cockpit's, from its own measurements: the time the agent actually worked on each finished task
(its turns, :func:`pipeline_metrics.aggregate_usage_by_task` — waiting for the Manažér is not in it). Before the first
task the pace is the history's — the project's own last builds, else the latest builds of any project; as tasks
finish, the build's own pace takes over. The ± is measured too: the same forecast replayed on finished builds at the
same point of progress. Chosen by backtest 23.10.2026 on 20 finished versions — median error 32 % before the first
task, 19 % after a quarter, 11 % after half.
"""

from __future__ import annotations

import statistics
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.config.settings import settings
from backend.db.models.tasks import Epic, Feat, Task
from backend.db.models.versions import Version
from backend.services import pipeline_metrics

#: How many tasks' worth of the historical pace stays mixed into a build's own: the pace is
#: ``(Σ finished + HISTORY_WEIGHT × history) / (finished + HISTORY_WEIGHT)``. Backtested against 3 — equal median
#: error, smaller worst case.
HISTORY_WEIGHT = 6
#: A project's tasks are like its earlier tasks: its last builds set its pace.
OWN_BUILDS = 2
#: Without a history of its own, the latest builds of any project, pooled, when they hold enough tasks.
RECENT_BUILDS = 6
MIN_POOLED_TASKS = 20
#: A build counts for the history once this many of its tasks were measured.
MIN_MEASURED_TASKS = 3
#: The ± is replayed on the latest finished builds with at least this many tasks, and needs at least that many builds.
ERROR_BUILDS = 12
MIN_TASKS_FOR_ERROR = 8
MIN_BUILDS_FOR_ERROR = 5


@dataclass(frozen=True)
class MeasuredBuild:
    """One version's measured work: minutes the agent worked on each finished task, in plan order."""

    version_id: uuid.UUID
    project_id: uuid.UUID
    created_at: datetime
    minutes: tuple[float, ...]
    #: every task of its plan is done — only such a build can say how far off a forecast was
    finished: bool


@dataclass(frozen=True)
class BuildForecast:
    minutes: float
    spent: float
    #: the typical relative error at this point of the build (0.3 = ±30 %); None when too few builds were measured
    error: Optional[float]
    tasks_done: int
    tasks_total: int

    @property
    def remaining(self) -> float:
        return max(0.0, self.minutes - self.spent)

    @property
    def text(self) -> str:
        """„asi 1 h 10 min ± 30 %“ — or, once every task is done, what it measured."""
        if self.tasks_done >= self.tasks_total:
            return f"{duration_words(self.spent)} (namerané)"
        spread = f" ± {percent_words(self.error)}" if self.error is not None else ""
        return f"asi {duration_words(self.minutes)}{spread}"

    @property
    def remaining_text(self) -> Optional[str]:
        """„zostáva asi 40 min“ while the build is under way; None before its first task and after its last."""
        if self.tasks_done == 0 or self.tasks_done >= self.tasks_total:
            return None
        return f"zostáva asi {duration_words(self.remaining)}"


def duration_words(minutes: float) -> str:
    """Minutes as the screen says them: „4 min“, „35 min“, „1 h 10 min“, „2 h“ — rounded to what a forecast can mean."""
    if minutes < 1:
        return "menej ako minúta"
    if minutes < 10:
        return f"{round(minutes)} min"
    if minutes < 60:
        return f"{max(10, 5 * round(minutes / 5))} min"
    rounded = 10 * round(minutes / 10)
    hours, rest = divmod(rounded, 60)
    return f"{hours} h {rest} min" if rest else f"{hours} h"


def percent_words(error: float) -> str:
    return f"{max(5, 5 * round(error * 100 / 5))} %"


# --- the rule, pure ----------------------------------------------------------------------------------------------


def history_pace(project_id: uuid.UUID, earlier: list[MeasuredBuild]) -> Optional[float]:
    """Minutes per task from the builds before this one: the project's own last ones, else the latest of any project."""
    own = [b for b in earlier if b.project_id == project_id][-OWN_BUILDS:]
    if own:
        return statistics.median([m for b in own for m in b.minutes])
    pooled = [m for b in earlier[-RECENT_BUILDS:] for m in b.minutes]
    return statistics.median(pooled) if len(pooled) >= MIN_POOLED_TASKS else None


def estimate(done: list[float], remaining_tasks: int, pace: Optional[float]) -> Optional[float]:
    """The whole build's minutes: what its finished tasks took plus its remaining tasks at the blended pace."""
    if pace is None:
        if len(done) < MIN_MEASURED_TASKS:
            return None
        blended = statistics.fmean(done)
    else:
        blended = (sum(done) + HISTORY_WEIGHT * pace) / (len(done) + HISTORY_WEIGHT)
    return sum(done) + remaining_tasks * blended


def typical_error(builds: list[MeasuredBuild], progress: float) -> Optional[float]:
    """The median relative error of :func:`estimate` replayed on the latest finished builds at ``progress`` (0–1)."""
    errors: list[float] = []
    candidates = [b for b in builds if b.finished and len(b.minutes) >= MIN_TASKS_FOR_ERROR]
    for build in candidates[-ERROR_BUILDS:]:
        earlier = [b for b in builds if b.created_at < build.created_at]
        known = int(len(build.minutes) * progress)
        predicted = estimate(
            list(build.minutes[:known]), len(build.minutes) - known, history_pace(build.project_id, earlier)
        )
        actual = sum(build.minutes)
        if predicted is not None and actual > 0:
            errors.append(abs(predicted - actual) / actual)
    return statistics.median(errors) if len(errors) >= MIN_BUILDS_FOR_ERROR else None


# --- reading the measurements ------------------------------------------------------------------------------------


def _plan(db: Session, version_id: uuid.UUID) -> list[tuple[uuid.UUID, str]]:
    """``(task id, status)`` of the version's plan, in the order the tasks are worked."""
    return list(
        db.execute(
            select(Task.id, Task.status)
            .join(Feat, Feat.id == Task.feat_id)
            .join(Epic, Epic.id == Feat.epic_id)
            .where(Epic.version_id == version_id)
            .order_by(Epic.number, Feat.number, Task.number)
        ).all()
    )


def _done_minutes(plan: list[tuple[uuid.UUID, str]], by_task: dict[str, pipeline_metrics.UsageTotals]) -> list[float]:
    worked = (by_task.get(str(task_id)) for task_id, status in plan if status == "done")
    return [t.duration_seconds / 60 for t in worked if t is not None and t.duration_seconds > 0]


_cache: dict[str, tuple[float, list[MeasuredBuild]]] = {}


def measured_builds(db: Session) -> list[MeasuredBuild]:
    """Every version with enough measured tasks, oldest first. Held for ``build_forecast_history_ttl_seconds`` — the
    plan screen asks often and the history moves only when a build does."""
    cached = _cache.get("builds")
    if cached and time.monotonic() - cached[0] < settings.build_forecast_history_ttl_seconds:
        return cached[1]
    builds = []
    for version_id, project_id, created_at in db.execute(
        select(Version.id, Version.project_id, Version.created_at).order_by(Version.created_at)
    ).all():
        plan = _plan(db, version_id)
        minutes = _done_minutes(plan, pipeline_metrics.aggregate_usage_by_task(db, version_id))
        if len(minutes) >= MIN_MEASURED_TASKS:
            finished = all(status == "done" for _id, status in plan)
            builds.append(MeasuredBuild(version_id, project_id, created_at, tuple(minutes), finished))
    _cache["builds"] = (time.monotonic(), builds)
    return builds


def forecast(
    db: Session,
    version_id: uuid.UUID,
    *,
    by_task: Optional[dict[str, pipeline_metrics.UsageTotals]] = None,
) -> Optional[BuildForecast]:
    """The forecast of the version's task work, or None when there is no plan or nothing measured to go by yet.

    ``by_task`` is the caller's :func:`pipeline_metrics.aggregate_usage_by_task` of this version, when it has it."""
    version = db.get(Version, version_id)
    plan = _plan(db, version_id)
    if version is None or not plan:
        return None
    done = _done_minutes(
        plan, by_task if by_task is not None else pipeline_metrics.aggregate_usage_by_task(db, version_id)
    )
    tasks_done = sum(1 for _id, status in plan if status == "done")
    others = [b for b in measured_builds(db) if b.version_id != version_id]
    earlier = [b for b in others if b.created_at < version.created_at]
    minutes = estimate(done, len(plan) - tasks_done, history_pace(version.project_id, earlier))
    if minutes is None:
        return None
    return BuildForecast(
        minutes=minutes,
        spent=sum(done),
        error=typical_error(others, tasks_done / len(plan)),
        tasks_done=tasks_done,
        tasks_total=len(plan),
    )
