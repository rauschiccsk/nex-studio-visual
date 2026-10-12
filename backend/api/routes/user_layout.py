"""DEV-82 — the caller's own screen layout: the widths of the side panels and the collapsed sidebar.

* ``GET  /`` → the caller's layout (defaults when nothing was chosen) with the bounds of every width.
* ``PUT  /`` → change what the body names; a width outside its bounds is refused (422).

Scoped to ``current_user`` — no user id in the path, so nobody can read or change another person's layout.
The mount prefix (``/api/v1/user-layout``) is applied in :mod:`backend.main`.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from backend.core.security import get_current_user
from backend.db.models.foundation import User
from backend.db.session import get_db
from backend.schemas.user_layout import LayoutLimitRead, UserLayoutRead, UserLayoutWrite
from backend.services import user_layout as service

router = APIRouter(tags=["User Layout"])


def _read(row) -> UserLayoutRead:
    return UserLayoutRead(
        sidebar_width=row.sidebar_width if row else None,
        sidebar_collapsed=bool(row.sidebar_collapsed) if row else False,
        plan_width=row.plan_width if row else None,
        limits={key: LayoutLimitRead(**limit.__dict__) for key, limit in service.LIMITS.items()},
    )


@router.get("", response_model=UserLayoutRead)
def get_my_layout(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> UserLayoutRead:
    return _read(service.get(db, current_user.id))


@router.put("", response_model=UserLayoutRead)
def set_my_layout(
    payload: UserLayoutWrite,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> UserLayoutRead:
    changes = payload.model_dump(include=payload.model_fields_set)
    if changes.get("sidebar_collapsed", False) is None:
        changes.pop("sidebar_collapsed")
    try:
        row = service.update(db, current_user.id, changes)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    db.commit()
    return _read(row)
