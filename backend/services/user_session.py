"""Service layer for :class:`~backend.db.models.foundation.UserSession`.

Provides the synchronous CRUD surface used by API routers. All methods
accept ``db: Session`` as the first argument and only ever call
``session.flush()`` — transaction commit is the router's responsibility.
Errors are signalled via :class:`ValueError` so the router can translate
them to the appropriate HTTP status code.

Design notes (per DESIGN.md §1.1 "Auth pattern" / ``user_sessions``
table and :mod:`backend.db.models.foundation.UserSession`):

    * ``id``, ``created_at`` and ``updated_at`` are server-managed and
      therefore immutable from the service layer (``updated_at`` is
      auto-stamped by the ORM via ``onupdate=func.now()`` on flush).
    * ``user_id`` is the session's immutable "parent" reference — a
      session belongs to exactly one user for its lifetime. Rotating a
      JWT to a different user means deleting the old session and
      creating a fresh one; silently reassigning ``user_id`` would
      invalidate auditing. The FK uses ``ON DELETE CASCADE`` at the DB
      level, so deleting the parent :class:`User` drops every attached
      session automatically. :class:`UserSessionUpdate` deliberately
      omits ``user_id`` and the service's ``allowed_fields`` allow-list
      enforces that contract defensively.
    * ``token_version`` is a monotonically increasing counter used by
      the ``tv`` JWT claim to invalidate all outstanding tokens on
      logout (DESIGN.md §1.1 "Auth pattern"). The column defaults to
      ``0`` via the DB-level ``server_default``; :func:`update` bumps
      it by one (or to any explicit value ≥ current) on logout.
    * ``last_seen_at`` is refreshed on every authenticated request
      (the router resolves the session, calls :func:`update` with the
      new timestamp, and relies on the ``updated_at`` ``onupdate``
      trigger to stamp the audit column). The column defaults to
      ``NOW()`` at insert time via the DB-level ``server_default``.
    * ``user_sessions`` has **no UNIQUE constraints beyond the PK** —
      a user may hold many concurrent sessions (multi-device login,
      browser + Electron, etc.). :func:`create` therefore performs no
      pre-flush natural-key check. If the supplied ``user_id`` foreign
      key does not match an existing row the DB-level FK rejects the
      flush and the error propagates as-is (routed at the API layer
      as a 409/422).
    * ``user_sessions`` has **no inbound foreign keys** — no other
      table references it. :func:`delete` performs no dependency check
      and is a straightforward hard-delete. This is the standard
      "logout-everywhere" or "session-expired" cleanup path.
    * List filters (``user_id``) match the indexed column
      (``ix_user_sessions_user_id``) and cover the natural lookup
      paths — "show every active session for this user" (settings page
      / admin tooling / force-logout feature).
    * List ordering is ``created_at DESC`` — the most recently
      opened sessions appear first, matching the "Active sessions" UI
      convention where the current session typically sits at the top.
"""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db.models.foundation import UserSession
from backend.schemas.user_session import UserSessionCreate, UserSessionUpdate


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


def create(db: Session, data: UserSessionCreate) -> UserSession:
    """Create a new user session.

    ``token_version`` defaults to ``0`` via the Pydantic schema
    (mirroring the DB ``server_default='0'``) when omitted.
    ``last_seen_at`` is optional here — when ``None``, the kwarg is
    omitted so the DB-level ``server_default=func.now()`` kicks in;
    supplying an explicit timestamp lets import / migration flows
    back-date sessions.

    :class:`UserSession` has no UNIQUE constraints beyond the PK — a
    user may hold many concurrent sessions — so no pre-flush
    natural-key validation is required. If the supplied ``user_id``
    foreign key does not match an existing row the DB-level FK rejects
    the flush and the error propagates as-is (routed at the API layer
    as a 409/422).

    Args:
        db: Active SQLAlchemy session.
        data: Validated creation payload.

    Returns:
        The newly created and flushed :class:`UserSession` with its
        server-generated ``id``, ``created_at``, ``updated_at`` and —
        when omitted — ``last_seen_at`` populated.
    """
    kwargs: dict = {
        "user_id": data.user_id,
        "token_version": data.token_version,
    }
    # Defer to the DB-level NOW() server_default when the caller omits
    # the timestamp; otherwise honour the explicit value.
    if data.last_seen_at is not None:
        kwargs["last_seen_at"] = data.last_seen_at

    session = UserSession(**kwargs)
    db.add(session)
    db.flush()
    return session


def update(
    db: Session,
    session_id: UUID,
    data: UserSessionUpdate,
) -> UserSession:
    """Partially update a user session.

    Only ``token_version`` and ``last_seen_at`` may be changed —
    session identity is anchored to the user for its lifetime, so
    ``user_id`` is immutable. ``id`` and ``created_at`` are likewise
    immutable; ``updated_at`` is auto-stamped by the ORM on flush via
    ``onupdate=func.now()``.

    Fields that are ``None`` in the payload are treated as "leave
    unchanged" to support PATCH semantics — both mutable columns are
    ``NOT NULL`` at the DB level, so there is no valid "clear" (i.e.
    set-to-``NULL``) transition to express.

    Typical call sites:
        * Authenticated request → refresh ``last_seen_at``.
        * Logout → bump ``token_version`` to invalidate every
          outstanding JWT issued against this session.

    Raises:
        ValueError: If the session does not exist.
    """
    session = get_by_id(db, session_id)

    update_data = data.model_dump(exclude_unset=True)
    # Defensive guard — the schema already excludes immutable fields,
    # but silently dropping any that slip through keeps the service
    # honest.
    allowed_fields = {
        "token_version",
        "last_seen_at",
    }

    for field, value in update_data.items():
        if field in allowed_fields and value is not None:
            setattr(session, field, value)

    db.flush()
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
