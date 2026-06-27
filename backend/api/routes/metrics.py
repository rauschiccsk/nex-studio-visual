"""REST router for the project metrics / ROI page (E5; v2 per-phase basis, CR-V2-029).

* ``GET /api/v1/projects/{slug}/metrics`` → the per-phase metrics shape (cumulative + per-version,
  per-phase agent-vs-human + system overhead + Manažér overhead + idle split + ROI).

Read-only (no pipeline mutation). ``require_shu_or_above`` — any authenticated user. The router is
mounted under the bare ``/api/v1`` prefix in ``backend/main.py`` (the path is ``/projects/{slug}/…``,
like the versions router).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.core.security import require_shu_or_above
from backend.db.models.projects import Project
from backend.db.session import get_db
from backend.schemas.metrics import ProjectMetricsRead
from backend.services import metrics as metrics_service

router = APIRouter(tags=["Metrics"])


@router.get(
    "/projects/{slug}/metrics",
    response_model=ProjectMetricsRead,
    dependencies=[Depends(require_shu_or_above)],
)
def get_project_metrics(slug: str, db: Session = Depends(get_db)) -> ProjectMetricsRead:
    """Return the project's measured AI effort + cost + human-baseline ROI, per phase (E5)."""
    project = db.execute(select(Project).where(Project.slug == slug)).scalar_one_or_none()
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Project not found: {slug}")
    return metrics_service.compute_project_metrics(db, project)
