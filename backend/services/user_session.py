"""Service layer for :class:`~backend.db.models.foundation.UserSession` — list, read and delete.

Sessions are made by the login (``backend/services/auth.py``, ``backend/services/user.py``); the Relácie tab lists
them and revokes one. All functions take ``db: Session`` first and only ``flush`` — the router commits. A missing id
raises :class:`ValueError`, which the router turns into 404. ``user_sessions`` has no inbound foreign keys, so a delete
needs no dependency check; ``user_id`` cascades on the user's deletion. DEV-155 (22.10.2026) removed ``create`` and
``update``, which only routes no screen called used.
"""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.db.models.foundation import UserSession


def list_user_sessions(
    db: Session,
    *,
    user_id: Optional[UUID] = None,
    limit: int = 100,
    offset: int = 0,
) -> list[UserSession]:
    """Return user sessions filtered by the supplied criteria.

    Results are ordered by ``created_at DESC`` so the most recently
    opened sessions appear first — matching the "Active sessions" UI
    convention on the settings page.

    Args:
        db: Active SQLAlchemy session.
        user_id: Optional user filter — restrict to sessions belonging
            to a specific user (the core "list my sessions" query,
            hitting ``ix_user_sessions_user_id``).
        limit: Maximum number of rows to return.
        offset: Number of rows to skip.

    Returns:
        List of :class:`UserSession` instances.
    """
    stmt = select(UserSession)
    if user_id is not None:
        stmt = stmt.where(UserSession.user_id == user_id)
    stmt = stmt.order_by(UserSession.created_at.desc()).limit(limit).offset(offset)
    return list(db.execute(stmt).scalars().all())


def count_user_sessions(
    db: Session,
    *,
    user_id: Optional[UUID] = None,
) -> int:
    """Return the total number of user sessions matching the given filters.

    Mirrors the ``user_id`` filter of :func:`list_user_sessions` so a
    paginated response can report the unfiltered total alongside the
    current page of items.

    Args:
        db: Active SQLAlchemy session.
        user_id: Optional user filter — restrict the count to sessions
            belonging to a specific user (hits
            ``ix_user_sessions_user_id``).

    Returns:
        Total number of rows matching the filters.
    """
    stmt = select(func.count()).select_from(UserSession)
    if user_id is not None:
        stmt = stmt.where(UserSession.user_id == user_id)
    return int(db.execute(stmt).scalar_one())


def get_by_id(db: Session, session_id: UUID) -> UserSession:
    """Return a single user session by primary key.

    Raises:
        ValueError: If no session with the supplied ``session_id``
            exists. The router converts this to an HTTP 404 response.
    """
    session = db.get(UserSession, session_id)
    if session is None:
        raise ValueError(f"UserSession {session_id} not found")
    return session


def delete(db: Session, session_id: UUID) -> None:
    """Hard-delete a user session.

    ``user_sessions`` has no inbound foreign keys, so no RESTRICT
    dependency check is required — simply drop the row. This is the
    canonical "logout" / "session expired" cleanup path; deleting the
    parent :class:`~backend.db.models.foundation.User` cascades
    automatically via ``ON DELETE CASCADE`` on ``user_id`` and is the
    usual "wipe all sessions on account removal" path.

    Raises:
        ValueError: If the session does not exist.
    """
    session = get_by_id(db, session_id)
    db.delete(session)
    db.flush()
