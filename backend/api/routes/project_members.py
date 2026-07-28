"""REST router for ProjectMember — Shuhari RBAC project assignments.

M2.B milestone (2026-05-07). Mount prefix: ``/api/v1/project-members``.

Auth surface (v4.0.43 — ownership-scoped via ``backend.core.authz``):
* ``GET`` — any authenticated user; a Junior (``shu``) is scoped to projects they OWN, ri/ha see all.
* ``POST`` / ``PATCH`` / ``DELETE`` — owner-OR-``ri`` of the member's project (``ha`` does not gain member
  management on projects it doesn't own).

Note: NEX Studio aktuálne má 1-3 aktívnych používateľov. Project
membership becomes operationally relevant až keď ``shu`` users (Nazar)
začnú reálne pracovať na konkrétnych projektoch. Foundation existuje
od dnes — používatelia sa pridajú podľa potreby cez tieto endpointy.

What a membership row actually does: :mod:`backend.utils.kb_access` appends ``projects/<slug>/`` to a
``shu`` user's allowed KB categories for every project they are a member of, which is what opens that
project's folder in the Znalostná báza (``/knowledge``) and in RAG search (``/rag``). Without a row a
Junior sees only the ``kb_access_shu`` baseline (``icc/``, ``shuhari/``). It does NOT affect
:mod:`backend.core.authz` — projects, versions, deploy and ``/project-specs`` authorize on
``Project.created_by`` and never read this table.

There is still NO screen for these endpoints anywhere in the frontend, so the one lever that opens a
Junior's project documentation can only be pulled by calling the API by hand. Building it is blocked
on a permission decision, not on effort: a member picker needs the user directory, and ``GET /users``
is ``ri``-only while member management here is owner-OR-``ri`` — so either the screen is ``ri``-only
(and a Junior owner still cannot manage his own project) or the directory is widened. That is a
Director call; see the audit note rather than guessing one here.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.core import authz
from backend.core.security import get_current_user, require_shu_or_above
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
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ProjectMemberRead]:
    """List project memberships, optionally filtered by project or user.

    v4.0.43: a Junior (``shu``) MUST scope to a project they OWN (``project_id`` required + owner-checked);
    ri/ha may list across all projects.
    """
    if current_user.role not in authz.PRIVILEGED_ROLES:
        if project_id is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="project_id je povinný — nemôžeš vypísať členov naprieč projektami.",
            )
        authz.assert_project_id_access(db, current_user, project_id)
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
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ProjectMemberRead:
    member = db.get(ProjectMember, member_id)
    if member is None:
        raise HTTPException(status_code=404, detail="ProjectMember not found")
    authz.assert_project_id_access(db, current_user, member.project_id)
    return ProjectMemberRead.model_validate(member)


@router.post("", response_model=ProjectMemberRead, status_code=status.HTTP_201_CREATED)
def create_project_member(
    payload: ProjectMemberCreate,
    current_user: User = Depends(require_shu_or_above),
    db: Session = Depends(get_db),
) -> ProjectMemberRead:
    # v4.0.43: owner-or-ri may add members to a project they own.
    authz.assert_project_id_access(db, current_user, payload.project_id, ri_only=True)
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
    current_user: User = Depends(require_shu_or_above),
    db: Session = Depends(get_db),
) -> ProjectMemberRead:
    member = db.get(ProjectMember, member_id)
    if member is None:
        raise HTTPException(status_code=404, detail="ProjectMember not found")
    authz.assert_project_id_access(db, current_user, member.project_id, ri_only=True)
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
    current_user: User = Depends(require_shu_or_above),
    db: Session = Depends(get_db),
) -> Response:
    member = db.get(ProjectMember, member_id)
    if member is None:
        raise HTTPException(status_code=404, detail="ProjectMember not found")
    authz.assert_project_id_access(db, current_user, member.project_id, ri_only=True)
    db.delete(member)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
