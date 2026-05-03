"""REST router for :class:`~backend.db.models.system_settings.SystemSetting`.

* ``GET    /``         → list every known setting (defaults + stored overrides).
* ``GET    /{key}``    → single setting by key.
* ``PATCH  /{key}``    → upsert the value. Requires ``ri`` role.

The router is prefix-less; the mount prefix (``/api/v1/system-settings``)
is applied in :mod:`backend.main`.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from backend.core.security import get_current_user, require_ri_role
from backend.db.models.foundation import User
from backend.db.session import get_db
from backend.schemas.system_setting import (
    SystemSettingRead,
    SystemSettingUpdate,
)
from backend.services import system_setting as service

router = APIRouter(tags=["System Settings"])


@router.get("", response_model=list[SystemSettingRead])
def list_system_settings(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[SystemSettingRead]:
    """Return every known setting — defaults where no override exists."""
    return service.list_all(db)


@router.get("/{key}", response_model=SystemSettingRead)
def get_system_setting(
    key: str,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> SystemSettingRead:
    """Return one setting by key. 404 when the key is unknown."""
    try:
        return service.get_by_key(db, key)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.patch("/{key}", response_model=SystemSettingRead)
def update_system_setting(
    key: str,
    payload: SystemSettingUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_ri_role),
) -> SystemSettingRead:
    """Upsert the value for a known setting. ``ri`` role only.

    Returns the stored row. Unknown keys → 404 (the Settings UI is
    expected to only PATCH registered keys).
    """
    try:
        result = service.upsert(db, key, payload.value, updated_by=current_user.id)
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return result
