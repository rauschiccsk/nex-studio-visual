"""REST router for the Credentials registry.

Per CLAUDE.md §13 every endpoint is gated by
:func:`backend.core.security.require_ri_role`. CC has no user account
(§13) so any request from CC reaching the backend over HTTP receives
HTTP 401 / 403 — defense-in-depth, regardless of how the file_path
gets into the registry.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from backend.core.security import require_ri_role
from backend.db.models.foundation import User
from backend.db.session import get_db
from backend.schemas.credentials import (
    CredentialContent,
    CredentialContentUpdate,
    CredentialCreate,
    CredentialRead,
)
from backend.services import credentials as credentials_service

router = APIRouter(tags=["Credentials"])


def _map_value_error(exc: ValueError) -> HTTPException:
    message = str(exc)
    lowered = message.lower()
    if "not found" in lowered:
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=message)
    if "already exists" in lowered or "duplicate" in lowered or "conflict" in lowered:
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=message)
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=message)


@router.get("", response_model=list[CredentialRead])
def list_credentials(
    _ri: User = Depends(require_ri_role),
    db: Session = Depends(get_db),
) -> list[CredentialRead]:
    """List every credentials registry row (newest first)."""
    return [CredentialRead.model_validate(c) for c in credentials_service.list_credentials(db)]


@router.get("/{credential_id}/content", response_model=CredentialContent)
def get_credential_content(
    credential_id: UUID,
    _ri: User = Depends(require_ri_role),
    db: Session = Depends(get_db),
) -> CredentialContent:
    try:
        return credentials_service.read_content(db, credential_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise _map_value_error(exc) from exc


@router.put("/{credential_id}/content", response_model=CredentialContent)
def put_credential_content(
    credential_id: UUID,
    payload: CredentialContentUpdate,
    _ri: User = Depends(require_ri_role),
    db: Session = Depends(get_db),
) -> CredentialContent:
    try:
        result = credentials_service.write_content(db, credential_id, payload.content)
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise _map_value_error(exc) from exc
    return result


@router.post("", response_model=CredentialRead, status_code=status.HTTP_201_CREATED)
def create_credential(
    payload: CredentialCreate,
    _ri: User = Depends(require_ri_role),
    db: Session = Depends(get_db),
) -> CredentialRead:
    try:
        cred = credentials_service.create(db, payload)
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise _map_value_error(exc) from exc
    db.refresh(cred)
    return CredentialRead.model_validate(cred)


@router.delete("/{credential_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
def delete_credential(
    credential_id: UUID,
    _ri: User = Depends(require_ri_role),
    db: Session = Depends(get_db),
) -> Response:
    try:
        credentials_service.delete(db, credential_id)
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise _map_value_error(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
