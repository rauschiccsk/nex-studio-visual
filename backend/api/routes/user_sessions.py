"""REST router for :class:`~backend.db.models.foundation.UserSession`.

Exposes the standard CRUD surface for user sessions (DESIGN.md §1.1
"Auth pattern" / ``user_sessions`` table) — the per-user JWT lifecycle
anchor that backs the ``authStore`` rotation logic and the "Active
sessions" UI on the settings page:

* ``GET    /``                    → paginated list (filter by
  ``user_id``).
* ``GET    /{user_session_id}``   → single user session by primary
  key.
* ``POST   /``                    → create a new user session.
* ``PATCH  /{user_session_id}``   → partial update of the mutable
  (``token_version`` / ``last_seen_at``) fields.
* ``DELETE /{user_session_id}``   → hard-delete a user session
  (HTTP 204).

All endpoints are synchronous ``def`` — pg8000 is a synchronous driver
and FastAPI dispatches sync endpoints to a thread pool automatically.
The router delegates every persistence operation to
:mod:`backend.services.user_session` and handles commit / rollback
itself so the service layer remains transaction-agnostic.

The router is prefix-less; the mount prefix
(``/api/v1/user-sessions``) is applied in ``backend/main.py`` via
``app.include_router`` (Task 4.27).

Design notes (per DESIGN.md §1.1 "Auth pattern", ``user_sessions``
table and §6 REST API Architecture):

* ``id``, ``created_at`` and ``updated_at`` are server-managed and
  therefore immutable. ``user_id`` is the session's parent reference —
  a session belongs to exactly one user for its lifetime, so the FK
  is immutable. ``user_id`` uses ``ON DELETE CASCADE`` at the DB
  level so every session is removed automatically when its parent
  :class:`~backend.db.models.foundation.User` is dropped.
  :class:`~backend.schemas.user_session.UserSessionUpdate` deliberately
  omits all immutable / server-managed fields, and the service's
  allow-list enforces that contract defensively.
* ``token_version`` is a monotonically increasing counter used by the
  ``tv`` JWT claim to invalidate all outstanding tokens on logout.
  ``PATCH`` bumps the counter on logout so every JWT issued against
  the session is rejected on the next verification.
* ``last_seen_at`` is refreshed on every authenticated request — the
  auth middleware will resolve the session, call ``PATCH`` with the
  new timestamp, and rely on the ``updated_at`` ``onupdate`` trigger
  to stamp the audit column. The column defaults to ``NOW()`` at
  insert time via the DB-level ``server_default``.
* ``user_sessions`` has **no UNIQUE constraints beyond the PK** — a
  user may hold many concurrent sessions (multi-device login,
  browser + Electron, etc.). ``POST`` therefore performs no pre-flush
  natural-key check. An invalid or missing ``user_id`` FK reference
  is rejected by the DB-level FK and surfaces as HTTP 422.
* List filter (``user_id``) matches the indexed column
  (``ix_user_sessions_user_id``) and covers the natural lookup
  path — "show every session for this user" (settings page / admin
  tooling / force-logout feature).
* List ordering (``created_at DESC``) is owned by the service so the
  most recently opened sessions appear first — matching the "Active
  sessions" UI convention where the current session typically sits at
  the top.
* ``user_sessions`` has no inbound FKs, so :func:`delete_user_session`
  needs no RESTRICT dependency check — simply drop the row. This is
  the canonical "logout" / "session expired" cleanup path.
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
    UserSessionCreate,
    UserSessionRead,
    UserSessionUpdate,
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


@router.get("/{user_session_id}", response_model=UserSessionRead)
def get_user_session(
    user_session_id: UUID,
    db: Session = Depends(get_db),
) -> UserSessionRead:
    """Return a single user session by primary key."""
    try:
        session_row = user_session_service.get_by_id(db, user_session_id)
    except ValueError as exc:
        raise _map_value_error(exc) from exc
    return UserSessionRead.model_validate(session_row)


@router.post(
    "",
    response_model=UserSessionRead,
    status_code=status.HTTP_201_CREATED,
)
def create_user_session(
    payload: UserSessionCreate,
    db: Session = Depends(get_db),
) -> UserSessionRead:
    """Create a new user session.

    ``token_version`` defaults to ``0`` via the Pydantic schema
    (mirroring the DB ``server_default='0'``) when omitted.
    ``last_seen_at`` is optional — when ``None``, the kwarg is dropped
    so the DB-level ``server_default=func.now()`` kicks in; supplying
    an explicit timestamp lets import / migration flows back-date
    sessions. An invalid or missing ``user_id`` FK reference is
    rejected by the DB-level FK and surfaces as HTTP 422.
    """
    try:
        session_row = user_session_service.create(db, payload)
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise _map_value_error(exc) from exc
    db.refresh(session_row)
    return UserSessionRead.model_validate(session_row)


@router.patch("/{user_session_id}", response_model=UserSessionRead)
def update_user_session(
    user_session_id: UUID,
    payload: UserSessionUpdate,
    db: Session = Depends(get_db),
) -> UserSessionRead:
    """Partially update a user session's mutable fields.

    Only ``token_version`` and ``last_seen_at`` may be changed — session
    identity is anchored to the user for its lifetime, so ``user_id``
    is immutable. ``id`` and ``created_at`` are likewise immutable;
    ``updated_at`` is auto-stamped by the ORM on flush via
    ``onupdate=func.now()``. Fields omitted from the payload are left
    unchanged.

    Typical call sites:
        * Authenticated request → refresh ``last_seen_at``.
        * Logout → bump ``token_version`` to invalidate every
          outstanding JWT issued against this session (DESIGN.md §1.1).
    """
    try:
        session_row = user_session_service.update(db, user_session_id, payload)
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise _map_value_error(exc) from exc
    db.refresh(session_row)
    return UserSessionRead.model_validate(session_row)


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
