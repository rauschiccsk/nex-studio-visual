"""REST router for the project cost page — *Náklady* (E5; v2 per-phase basis, CR-V2-029/CR-V2-063).

* ``GET /api/v1/projects/{slug}/metrics`` → the per-phase cost shape (cumulative + per-version cost
  rows incl. the hand-entered external row + totals with the measured/entered split + Manažér overhead
  + idle split + the assumption block). The route path is kept — renaming paths is churn.

Read-only (no pipeline mutation). ``require_shu_or_above`` — any authenticated user. The router is
mounted under the bare ``/api/v1`` prefix in ``backend/main.py`` (the path is ``/projects/{slug}/…``,
like the versions router).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.core import authz
from backend.core.security import require_shu_or_above
from backend.db.models.foundation import User
from backend.db.session import get_db
from backend.schemas.metrics import ProjectCostsRead
from backend.services import metrics as metrics_service

router = APIRouter(tags=["Metrics"])


@router.get(
    "/projects/{slug}/metrics",
    response_model=ProjectCostsRead,
)
def get_project_metrics(
    slug: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_shu_or_above),
) -> ProjectCostsRead:
    """Return what the project cost — per phase, per version, cumulative (E5)."""
    # v4.0.35: owner-or-privileged — a Junior may read metrics only for their OWN project.
    project = authz.assert_project_slug_access(db, current_user, slug)
    return metrics_service.compute_project_metrics(db, project)
