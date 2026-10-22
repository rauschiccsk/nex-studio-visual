"""REST router for :class:`~backend.db.models.foundation.UserSession` — the Relácie tab of Nastavenia.

Sessions are made by the login (``backend/services/auth.py``, ``backend/services/user.py``); this router lists them
and revokes one:

* ``GET    /``                    → paginated list (filter by ``user_id``), each row naming its owner.
* ``DELETE /{user_session_id}``   → hard-delete a session (HTTP 204) — the „Odvolať" button; every JWT issued
  against it is rejected on the next verification.

DEV-155 (22.10.2026) removed the create, read-one and patch routes, which no screen called. The router is prefix-less;
``backend/main.py`` mounts it at ``/api/v1/user-sessions``. Sync ``def`` endpoints (pg8000 is synchronous); the router
owns commit / rollback, the service only flushes.
"""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.core.security import require_ha_or_above
from backend.db.models.foundation import User
from backend.db.session import get_db
from backend.schemas.pagination import PaginatedResponse
from backend.schemas.user_session import (
    UserSessionRead,
)
from backend.services import user_session as user_session_service

router = APIRouter(
    tags=["User Sessions"],
    dependencies=[Depends(require_ha_or_above)],
)


def _map_value_error(exc: ValueError) -> HTTPException:
    """Translate a service-layer ``ValueError`` into an HTTP exception.

    Mirrors the ICC error-handling pattern: ``not found`` → 404,
    duplicates / conflicts → 409, everything else (constraint / FK /
    validation failures) → 422.
    """
    message = str(exc)
    lowered = message.lower()
    if "not found" in lowered:
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=message)
    if "already exists" in lowered or "duplicate" in lowered or "conflict" in lowered:
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=message)
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=message)


@router.get("", response_model=PaginatedResponse[UserSessionRead])
def list_user_sessions(
    user_id: Optional[UUID] = Query(
        default=None,
        description=(
            "Filter by the user this session belongs to — the core "
            '"show every active session for this user" query that drives '
            "the settings page / admin tooling. Hits the "
            "``ix_user_sessions_user_id`` index."
        ),
    ),
    skip: int = Query(default=0, ge=0, description="Number of rows to skip."),
    limit: int = Query(default=50, ge=1, le=100, description="Maximum rows to return."),
    db: Session = Depends(get_db),
) -> PaginatedResponse[UserSessionRead]:
    """Return a paginated list of user sessions.

    Results are ordered by ``created_at DESC`` so the most recently
    opened sessions appear first — matching the "Active sessions" UI
    convention on the settings page.

    Each row carries the owner's ``username``. This endpoint is ``ha``+, but the user directory
    (``GET /users``) is ``ri``-only, so the Relácie tab could not resolve the ids on its own: a Medior
    saw a column of raw UUIDs next to a per-row "Odvolať" and had to decide whether to cut a session
    without knowing whose it was. One extra query for the page's user ids, not a join per row.
    """
    try:
        rows = user_session_service.list_user_sessions(
            db,
            user_id=user_id,
            limit=limit,
            offset=skip,
        )
        total = user_session_service.count_user_sessions(
            db,
            user_id=user_id,
        )
    except ValueError as exc:
        raise _map_value_error(exc) from exc

    owner_ids = {row.user_id for row in rows}
    usernames: dict[UUID, str] = (
        {uid: name for uid, name in db.execute(select(User.id, User.username).where(User.id.in_(owner_ids))).all()}
        if owner_ids
        else {}
    )

    items = []
    for row in rows:
        item = UserSessionRead.model_validate(row)
        # None when the owner row is gone — the panel then falls back to the id rather than inventing a name.
        item.username = usernames.get(row.user_id)
        items.append(item)

    return PaginatedResponse[UserSessionRead](
        items=items,
        total=total,
        skip=skip,
        limit=limit,
    )


@router.delete(
    "/{user_session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
def delete_user_session(
    user_session_id: UUID,
    db: Session = Depends(get_db),
) -> Response:
    """Hard-delete a user session by primary key.

    ``user_sessions`` has no inbound FKs, so no RESTRICT dependency
    check is required — simply drop the row. This is the canonical
    "logout" / "session expired" cleanup path; deleting the parent
    :class:`~backend.db.models.foundation.User` cascades automatically
    via ``ON DELETE CASCADE`` on ``user_id`` and is the usual "wipe all
    sessions on account removal" path.
    """
    try:
        user_session_service.delete(db, user_session_id)
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise _map_value_error(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
