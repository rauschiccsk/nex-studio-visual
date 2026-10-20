"""DEV-160 — ``/api/v1/projects/{project_id}/surfaces`` and ``…/web-switch``: a project's frontends, and switching a
Štandardný project to Web (administration + public web) with a preview first."""

from __future__ import annotations

from dataclasses import asdict
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from backend.core import authz
from backend.core.security import get_current_user
from backend.db.models.foundation import User
from backend.db.session import get_db
from backend.schemas.project_surfaces import SurfaceRead, WebSwitchRead
from backend.services import project_surfaces

router = APIRouter(tags=["Project Surfaces"])


def _read(found: project_surfaces.WebSwitch) -> WebSwitchRead:
    return WebSwitchRead(
        allowed=found.allowed,
        refusal=found.refusal,
        web_port=found.web_port,
        surfaces=[SurfaceRead(**asdict(surface)) for surface in found.surfaces],
    )


@router.get("/projects/{project_id}/surfaces", response_model=list[SurfaceRead])
def get_project_surfaces(
    project_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[SurfaceRead]:
    """The project's frontends — folder, service, port, login and address of each."""
    project = authz.assert_project_id_access(db, current_user, project_id)
    return [SurfaceRead(**asdict(surface)) for surface in project_surfaces.surfaces_of(project)]


@router.get("/projects/{project_id}/web-switch", response_model=WebSwitchRead)
def preview_web_switch(
    project_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> WebSwitchRead:
    """What switching the project to Web would give it, or why it cannot — nothing changes. A host that cannot be
    asked about the web's port is a refusal, never a guess (port_registry answers ``unknown``)."""
    project = authz.assert_project_id_access(db, current_user, project_id)
    return _read(project_surfaces.web_switch(db, project))


@router.post("/projects/{project_id}/web-switch", response_model=WebSwitchRead)
def switch_to_web(
    project_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> WebSwitchRead:
    """Switch the project to Web — accepted exactly when the preview allows it; the change is kept."""
    project = authz.assert_project_id_access(db, current_user, project_id)
    try:
        done = project_surfaces.switch_to_web(db, project, changed_by=current_user.id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    db.commit()
    return _read(done)
