"""REST router for ProjectMember — Shuhari RBAC project assignments.

M2.B milestone (2026-05-07). Mount prefix: ``/api/v1/project-members``.

Auth surface:
* ``GET`` — ``Depends(require_ha_or_above)`` (read access for ``ri`` and ``ha``)
* ``POST`` / ``DELETE`` — ``Depends(require_ri_role)`` (assignment management
  is a Director-only operation; ``ha`` cannot assign other users to projects).

Note: NEX Studio aktuálne má 1-3 aktívnych používateľov. Project
membership becomes operationally relevant až keď ``shu`` users (Nazar)
začnú reálne pracovať na konkrétnych projektoch. Foundation existuje
od dnes — používatelia sa pridajú podľa potreby cez tieto endpointy.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.core.security import require_ha_or_above, require_ri_role
from backend.db.models.foundation import User
from backend.db.models.project_member import ProjectMember
from backend.db.session import get_db
from backend.schemas.project_member import (
    ProjectMemberCreate,
    ProjectMemberRead,
    ProjectMemberUpdate,
)

router = APIRouter(tags=["Project Members"])


@router.get("", response_model=list[ProjectMemberRead])
def list_project_members(
    project_id: UUID | None = Query(default=None, description="Filter by project."),
    user_id: UUID | None = Query(default=None, description="Filter by user."),
    _ha: User = Depends(require_ha_or_above),
    db: Session = Depends(get_db),
) -> list[ProjectMemberRead]:
    """List project memberships, optionally filtered by project or user."""
    stmt = select(ProjectMember).order_by(ProjectMember.created_at.desc())
    if project_id is not None:
        stmt = stmt.where(ProjectMember.project_id == project_id)
    if user_id is not None:
        stmt = stmt.where(ProjectMember.user_id == user_id)
    rows = db.execute(stmt).scalars().all()
    return [ProjectMemberRead.model_validate(r) for r in rows]


@router.get("/{member_id}", response_model=ProjectMemberRead)
def get_project_member(
    member_id: UUID,
    _ha: User = Depends(require_ha_or_above),
    db: Session = Depends(get_db),
) -> ProjectMemberRead:
    member = db.get(ProjectMember, member_id)
    if member is None:
        raise HTTPException(status_code=404, detail="ProjectMember not found")
    return ProjectMemberRead.model_validate(member)


@router.post("", response_model=ProjectMemberRead, status_code=status.HTTP_201_CREATED)
def create_project_member(
    payload: ProjectMemberCreate,
    _ri: User = Depends(require_ri_role),
    db: Session = Depends(get_db),
) -> ProjectMemberRead:
    member = ProjectMember(
        project_id=payload.project_id,
        user_id=payload.user_id,
        role=payload.role,
    )
    db.add(member)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        message = str(exc.orig)
        if "uq_project_members_project_user" in message:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="User is already a member of this project",
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid project_id or user_id (foreign key violation)",
        ) from exc
    db.refresh(member)
    return ProjectMemberRead.model_validate(member)


@router.patch("/{member_id}", response_model=ProjectMemberRead)
def update_project_member(
    member_id: UUID,
    payload: ProjectMemberUpdate,
    _ri: User = Depends(require_ri_role),
    db: Session = Depends(get_db),
) -> ProjectMemberRead:
    member = db.get(ProjectMember, member_id)
    if member is None:
        raise HTTPException(status_code=404, detail="ProjectMember not found")
    if payload.role is not None:
        member.role = payload.role
    db.commit()
    db.refresh(member)
    return ProjectMemberRead.model_validate(member)


@router.delete(
    "/{member_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
def delete_project_member(
    member_id: UUID,
    _ri: User = Depends(require_ri_role),
    db: Session = Depends(get_db),
) -> Response:
    member = db.get(ProjectMember, member_id)
    if member is None:
        raise HTTPException(status_code=404, detail="ProjectMember not found")
    db.delete(member)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
