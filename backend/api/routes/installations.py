"""DEV-378 — the „Inštalácie“ page: every installation across projects, and whether one lives."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from backend.core.security import get_current_user
from backend.db.models.foundation import User
from backend.db.session import get_db
from backend.schemas.installation import InstallationCustomer, InstallationStatus
from backend.services import installations as installations_service

router = APIRouter(prefix="/installations", tags=["Installations"])


@router.get("", response_model=list[InstallationCustomer])
def list_installations(
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> list[InstallationCustomer]:
    """Every customer with an installation the person may see — the owner's projects, the admin's all."""
    return [InstallationCustomer(**group) for group in installations_service.list_installations(db, current_user)]


@router.get("/{customer_id}/{environment}/status", response_model=InstallationStatus)
async def installation_status(
    customer_id: UUID,
    environment: Literal["uat", "prod"],
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> InstallationStatus:
    """Whether the installation lives — the address is the cockpit's, the answer shared for a while."""
    try:
        answer = await installations_service.status(db, current_user, customer_id, environment)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return InstallationStatus(state=answer.state, version=answer.version, detail=answer.detail)
