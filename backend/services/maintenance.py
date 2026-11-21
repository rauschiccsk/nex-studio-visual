"""Periodic maintenance that survives restarts (DEV-484).

A job remembers when it last ran (``maintenance_runs``). After a start the loop waits only for what is left of the
interval — and when the job is due (never ran, or the interval passed), a short grace period, so a deploy's own checks
are not disturbed. 21.11.2026: the daily pruning of the CI runners' Docker (DEV-435) slept a whole day after every start
and the cockpit was deployed several times a day; in 72 hours it never ran.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from backend.db.models.maintenance_run import MaintenanceRun

#: The job that prunes the CI runners' Docker of old build cache and images (DEV-435).
CI_RUNNER_PRUNE = "ci_runner_cache_prune"


def last_run(db: Session, job: str) -> Optional[datetime]:
    row = db.get(MaintenanceRun, job)
    return row.last_run_at if row is not None else None


def seconds_until_due(db: Session, job: str, interval: float, grace: float, now: Optional[datetime] = None) -> float:
    """How long the loop waits before the job runs: the rest of the interval, never less than ``grace``."""
    now = now or datetime.now(timezone.utc)
    last = last_run(db, job)
    if last is None:
        return grace
    left = interval - (now - last).total_seconds()
    return max(left, grace)


def mark_run(db: Session, job: str, at: Optional[datetime] = None) -> None:
    at = at or datetime.now(timezone.utc)
    row = db.get(MaintenanceRun, job)
    if row is None:
        db.add(MaintenanceRun(job=job, last_run_at=at))
    else:
        row.last_run_at = at
    db.commit()
