"""DEV-160 — ``/api/v1/projects/{project_id}/web-switch``: the frontends of a project, and adding the web frontend
(administration + public web) with a preview first — DEV-522: by the admin account, before the first PROD deploy, with a
written reason."""

from __future__ import annotations

from dataclasses import asdict
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.core import authz
from backend.core.security import get_current_user
from backend.db.models.foundation import User
from backend.db.models.projects import Project
from backend.db.session import get_db
from backend.schemas.project_surfaces import SurfaceRead, WebAddRequest, WebSwitchRead
from backend.services import project_surfaces, web_workspace
from backend.services.workspace_owner import chown_workspace

router = APIRouter(tags=["Project Surfaces"])


def _read(found: project_surfaces.WebSwitch) -> WebSwitchRead:
    return WebSwitchRead(
        allowed=found.allowed,
        refusal=found.refusal,
        web_port=found.web_port,
        surfaces=[SurfaceRead(**asdict(surface)) for surface in found.surfaces],
        after=[SurfaceRead(**asdict(surface)) for surface in found.after],
    )


@router.get("/projects/{project_id}/web-switch", response_model=WebSwitchRead)
def preview_web_switch(
    project_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> WebSwitchRead:
    """The frontends the project has, and what adding the web frontend would give it or why it cannot — nothing
    changes. A host that cannot be asked about the web's port is a refusal, never a guess (port_registry answers
    ``unknown``)."""
    project = authz.assert_project_id_access(db, current_user, project_id)
    return _read(project_surfaces.web_switch(db, project, current_user))


@router.post("/projects/{project_id}/web-switch", response_model=WebSwitchRead)
def switch_to_web(
    project_id: UUID,
    payload: Optional[WebAddRequest] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> WebSwitchRead:
    """Add the web frontend — accepted exactly when the preview allows it and a reason is written; the change is kept
    with who and why."""
    project = authz.assert_project_id_access(db, current_user, project_id)
    # DEV-161: one switch per project at a time — a second click waits here and then reads „už je Web“.
    db.execute(select(Project).where(Project.id == project.id).with_for_update())
    # The table is switched (inside a savepoint, not yet committed) before the workspace gets its web: a web that
    # cannot be built rolls the switch back, and a web that was built is never left behind a project the table still
    # calls Štandardný.
    switch = db.begin_nested()
    try:
        done = project_surfaces.switch_to_web(
            db, project, user=current_user, reason=payload.reason if payload else None
        )
        warnings = web_workspace.add_web(db, project)
    except (ValueError, web_workspace.WebWorkspaceError) as exc:
        switch.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    switch.commit()
    db.commit()
    # The backend runs as root; the new web/ joins the workspace convention like everything a founding writes.
    chown_workspace(project.source_path)
    return _read(done).model_copy(update={"warnings": warnings})
