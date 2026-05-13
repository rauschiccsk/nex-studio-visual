"""REST router for :class:`~backend.db.models.foundation.User`.

Exposes the standard CRUD surface for users:

* ``GET    /``            → paginated list (filter by ``role`` and
  ``is_active``).
* ``GET    /{user_id}``   → single user by primary key.
* ``POST   /``            → create a new user.
* ``PATCH  /{user_id}``   → partial update of the mutable fields.
* ``DELETE /{user_id}``   → soft-delete (deactivate) a user (HTTP 204).
* ``POST   /{user_id}/change-password`` → change a user's password
  (ri may change any; ha/shu only own).

All endpoints are synchronous ``def`` — pg8000 is a synchronous driver and
FastAPI dispatches sync endpoints to a thread pool automatically. The
router delegates every persistence operation to
:mod:`backend.services.user` and handles commit/rollback itself so the
service layer remains transaction-agnostic.

The router is prefix-less; the mount prefix (``/api/v1/users``) is
applied in ``backend/main.py`` via ``app.include_router``.
"""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from backend.core.security import get_current_user, require_ri_role
from backend.db.models.foundation import User
from backend.db.session import get_db
from backend.schemas.pagination import PaginatedResponse
from backend.schemas.user import ChangePasswordRequest, UserCreate, UserRead, UserRole, UserUpdate
from backend.services import user as user_service

router = APIRouter(tags=["Users"])


def _map_value_error(exc: ValueError) -> HTTPException:
    """Translate a service-layer ``ValueError`` into an HTTP exception.

    Mirrors the ICC error-handling pattern: ``not found`` → 404,
    duplicates/conflicts → 409, everything else (constraint / FK /
    validation failures such as "cannot delete ... referenced by ...") →
    422.
    """
    message = str(exc)
    lowered = message.lower()
    if "not found" in lowered:
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=message)
    if "already exists" in lowered or "duplicate" in lowered or "conflict" in lowered:
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=message)
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=message)


@router.get("", response_model=PaginatedResponse[UserRead])
def list_users(
    role: Optional[UserRole] = Query(
        default=None,
        description="Filter by role (ri | ha | shu).",
    ),
    is_active: Optional[bool] = Query(
        default=None,
        description="Filter by the soft-disable flag.",
    ),
    skip: int = Query(default=0, ge=0, description="Number of rows to skip."),
    limit: int = Query(default=50, ge=1, le=100, description="Maximum rows to return."),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_ri_role),
) -> PaginatedResponse[UserRead]:
    """Return a paginated list of users."""
    try:
        rows = user_service.list_users(
            db,
            role=role,
            is_active=is_active,
            limit=limit,
            offset=skip,
        )
        total = user_service.count_users(
            db,
            role=role,
            is_active=is_active,
        )
    except ValueError as exc:
        raise _map_value_error(exc) from exc

    return PaginatedResponse[UserRead](
        items=[UserRead.model_validate(row) for row in rows],
        total=total,
        skip=skip,
        limit=limit,
    )


@router.get("/{user_id}", response_model=UserRead)
def get_user(
    user_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_ri_role),
) -> UserRead:
    """Return a single user by primary key."""
    try:
        user = user_service.get_by_id(db, user_id)
    except ValueError as exc:
        raise _map_value_error(exc) from exc
    return UserRead.model_validate(user)


@router.post(
    "",
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
)
def create_user(
    payload: UserCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_ri_role),
) -> UserRead:
    """Create a new user (ri role only).

    The service layer hashes the plaintext password with bcrypt and creates
    a :class:`UserSession` with ``token_version=0`` for the new user.
    """
    try:
        user = user_service.create(db, payload)
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise _map_value_error(exc) from exc
    db.refresh(user)
    return UserRead.model_validate(user)


@router.patch("/{user_id}", response_model=UserRead)
def update_user(
    user_id: UUID,
    payload: UserUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_ri_role),
) -> UserRead:
    """Partially update a user's mutable fields (ri only).

    An ``ri`` user cannot set ``is_active=False`` on their own account —
    this prevents accidental self-lockout.
    """
    if payload.is_active is False and user_id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot deactivate your own account",
        )
    try:
        user = user_service.update(db, user_id, payload)
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise _map_value_error(exc) from exc
    db.refresh(user)
    return UserRead.model_validate(user)


@router.delete(
    "/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
def delete_user(
    user_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_ri_role),
) -> Response:
    """Hard-delete a user (ri only).

    The ``user_service.delete`` helper proactively checks every inbound
    ``ondelete='RESTRICT'`` foreign key (projects, bugs, architect_sessions,
    raw_specifications, professional_specifications, design_documents)
    before issuing the DELETE — if any row still references the user,
    a clean :class:`ValueError` propagates up to a 409 Conflict here
    instead of a raw integrity error.

    ``ri`` users cannot delete their own account — prevents accidental
    self-lockout. For routine soft-disable, the operator should use
    ``PATCH /users/{id}`` with ``is_active=false`` (rendered in the UI
    as the separate "Deaktivovať" button).

    History: until 2026-05-13 this endpoint was a soft-delete
    (``update(is_active=False)``). The semantic mismatch — UI "trash" icon
    suggesting a real delete while the row stayed in the DB blocking new
    users with the same email/username — was the bug Director hit when
    recreating "tibi". The endpoint now does what the verb says.
    """
    if user_id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete your own account",
        )
    try:
        user_service.delete(db, user_id)
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise _map_value_error(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{user_id}/change-password", response_model=UserRead)
def change_password(
    user_id: UUID,
    payload: ChangePasswordRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> UserRead:
    """Change a user's password (ri role only).

    The service layer hashes the new password with bcrypt and bumps
    ``token_version`` to invalidate all existing JWTs for the target user.
    """
    try:
        user = user_service.change_password(
            db,
            user_id=user_id,
            new_password=payload.new_password,
            current_user=current_user,
        )
        db.commit()
    except ValueError as exc:
        db.rollback()
        message = str(exc)
        if "permissions" in message.lower():
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=message,
            ) from exc
        raise _map_value_error(exc) from exc
    db.refresh(user)
    return UserRead.model_validate(user)
