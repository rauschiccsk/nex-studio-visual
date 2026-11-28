"""DEV-536 — ``/api/v1/projects/{project_id}/guide``: Sprievodca of the project's app — whether it is on, and the switch
that turns it off for a special app (on is the template's default)."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.core import authz
from backend.core.security import get_current_user
from backend.db.models.foundation import User
from backend.db.models.projects import Project
from backend.db.session import get_db
from backend.schemas.project_surfaces import GuideSwitchRead, GuideSwitchRequest
from backend.services import app_guide
from backend.services.workspace_owner import chown_workspace

router = APIRouter(tags=["Project Guide"])


def _read(found: app_guide.GuideSwitch, warnings: list[str] | None = None) -> GuideSwitchRead:
    return GuideSwitchRead(
        applies=found.applies,
        on=found.on,
        refusal=found.refusal,
        kit_version=found.kit_version,
        needed=app_guide.GUIDE_SINCE,
        warnings=warnings or [],
    )


@router.get("/projects/{project_id}/guide", response_model=GuideSwitchRead)
def read_guide(
    project_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> GuideSwitchRead:
    """Whether the app has Sprievodca on and whether it can be switched now — nothing changes."""
    project = authz.assert_project_id_access(db, current_user, project_id)
    return _read(app_guide.guide_switch(project))


@router.put("/projects/{project_id}/guide", response_model=GuideSwitchRead)
def switch_guide(
    project_id: UUID,
    payload: GuideSwitchRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> GuideSwitchRead:
    """Turn Sprievodca on or off — the manifest is written and committed; 409 with the reason when it cannot be now."""
    project = authz.assert_project_id_access(db, current_user, project_id)
    # One switch per project at a time — a second click waits here and then reads the manifest the first one wrote.
    db.execute(select(Project).where(Project.id == project.id).with_for_update())
    try:
        found, warnings = app_guide.set_guide(project, payload.on)
    except app_guide.GuideSwitchError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    finally:
        db.commit()
    # The backend runs as root; the manifest it rewrote stays the workspace's like everything else it writes.
    chown_workspace(project.source_path)
    return _read(found, warnings)
