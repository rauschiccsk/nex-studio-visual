"""Service layer for :class:`~backend.db.models.architect.ArchitectSession`.

Provides the synchronous CRUD surface used by API routers. All methods
accept ``db: Session`` as the first argument and only ever call
``session.flush()`` — transaction commit is the router's responsibility.
Errors are signalled via :class:`ValueError` so the router can translate
them to the appropriate HTTP status code.

Design notes (per DESIGN.md §1.11 ArchitectSession, §1.5 Architect
Sessions / ``architect_sessions`` table, D-08 SSE streaming, and
:mod:`backend.db.models.architect.ArchitectSession`):

    * ``id``, ``created_at`` and ``updated_at`` are server-managed and
      therefore immutable from the service layer (``updated_at`` is
      auto-stamped by the ORM via ``onupdate=func.now()`` on flush).
    * ``project_id`` and ``created_by`` are immutable foreign keys — a
      session belongs to exactly one project and is attributed to exactly
      one user for its lifetime (sessions are closed, not reassigned).
      :class:`ArchitectSessionUpdate` deliberately omits them and the
      service's ``allowed_fields`` allow-list enforces that contract
      defensively.
    * ``module_id`` remains mutable: ``NULL`` denotes a Foundation /
      project-level session (DESIGN.md §1.5 context-injection note
      "NULL = Foundation/project session"). Flipping between
      project-level and module-scoped is a legitimate UI operation, so
      the column is updatable. The FK uses ``ON DELETE SET NULL``, so
      deleting the referenced module silently downgrades the session to
      project-level.
    * ``status`` is constrained by the ``ck_architect_sessions_status``
      DB CHECK (``active | closed``). The Pydantic
      :data:`~backend.schemas.architect_session.ArchitectSessionStatus`
      literal mirrors the DB constraint, so the service does not
      revalidate — if an invalid value ever reaches the service (e.g. a
      bypassed schema) the DB CHECK rejects it on flush.
    * ``ArchitectSession`` has **no** UNIQUE constraints beyond the PK —
      a single user may open many sessions on the same project/module
      (e.g. historical conversations). :func:`create` therefore performs
      no pre-flush natural-key check.
    * Convenience behaviour: when :func:`update` transitions ``status``
      from ``active`` to ``closed`` and the caller did not explicitly
      supply ``closed_at`` in the same payload, the service stamps
      ``closed_at = now()`` automatically so the UI doesn't have to.
      Explicit ``closed_at`` values always win, so backfill / correction
      flows remain possible — mirroring the analogous pattern used by
      :mod:`backend.services.bug` when transitioning a bug to
      ``resolved``.
    * Inbound foreign keys referencing ``architect_sessions.id`` —
      ``architect_messages.session_id`` — use ``ON DELETE CASCADE``. No
      inbound FK uses ``RESTRICT``, so :func:`delete` performs no
      dependency check; dependent messages are removed automatically at
      the DB level.
    * List filters (``project_id``, ``module_id``, ``status``,
      ``created_by``) match the indexed columns
      (``ix_architect_sessions_project_id``,
      ``ix_architect_sessions_module_id``,
      ``ix_architect_sessions_status``) and support the Architect UI
      (DESIGN.md §3.1 ``ArchitectPage``) — "list all sessions for this
      project", "list module-scoped sessions", "show only active
      sessions", "show sessions started by this user".
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db.models.architect import ArchitectSession
from backend.schemas.architect_session import (
    ArchitectSessionCreate,
    ArchitectSessionStatus,
    ArchitectSessionUpdate,
)


def list_architect_sessions(
    db: Session,
    *,
    project_id: Optional[UUID] = None,
    module_id: Optional[UUID] = None,
    status: Optional[ArchitectSessionStatus] = None,
    created_by: Optional[UUID] = None,
    limit: int = 100,
    offset: int = 0,
) -> list[ArchitectSession]:
    """Return Architect sessions filtered by the supplied criteria.

    Results are ordered by ``created_at DESC`` so the most recently
    opened sessions appear first, matching the Architect UI convention
    (latest conversations on top).

    Args:
        db: Active SQLAlchemy session.
        project_id: Optional project filter — restrict to sessions for a
            specific project (the core Architect list query, DESIGN.md
            §3.1 ``ArchitectPage``).
        module_id: Optional module filter — restrict to sessions scoped
            to a specific module. Pass the module UUID to fetch
            module-level sessions; project-level (``module_id IS NULL``)
            sessions are filtered out when this argument is supplied.
        status: Optional lifecycle-status filter (``active`` |
            ``closed``).
        created_by: Optional author filter — restrict to sessions opened
            by a specific user.
        limit: Maximum number of rows to return.
        offset: Number of rows to skip.

    Returns:
        List of :class:`ArchitectSession` instances.
    """
    stmt = select(ArchitectSession)
    if project_id is not None:
        stmt = stmt.where(ArchitectSession.project_id == project_id)
    if module_id is not None:
        stmt = stmt.where(ArchitectSession.module_id == module_id)
    if status is not None:
        stmt = stmt.where(ArchitectSession.status == status)
    if created_by is not None:
        stmt = stmt.where(ArchitectSession.created_by == created_by)
    stmt = stmt.order_by(ArchitectSession.created_at.desc()).limit(limit).offset(offset)
    return list(db.execute(stmt).scalars().all())


def get_by_id(db: Session, session_id: UUID) -> ArchitectSession:
    """Return a single Architect session by primary key.

    Raises:
        ValueError: If no session with the supplied ``session_id``
            exists. The router converts this to an HTTP 404 response.
    """
    session_obj = db.get(ArchitectSession, session_id)
    if session_obj is None:
        raise ValueError(f"ArchitectSession {session_id} not found")
    return session_obj


def create(db: Session, data: ArchitectSessionCreate) -> ArchitectSession:
    """Create a new Architect chat session.

    ``status`` defaults to the value set by the Pydantic schema / DB
    ``server_default`` when omitted (``active``). ``module_id`` may be
    ``None`` to open a Foundation / project-level session (DESIGN.md
    §1.5 "NULL = Foundation/project session"). ``closed_at`` is
    typically ``None`` at creation but is accepted on the payload for
    backfill / import flows.

    :class:`ArchitectSession` has no UNIQUE constraints beyond the PK,
    so no pre-flush natural-key validation is required; if the supplied
    ``project_id``, ``module_id`` or ``created_by`` foreign keys do not
    match existing rows the DB-level FK rejects the flush and the error
    propagates as-is (routed at the API layer as a 409/422).

    Args:
        db: Active SQLAlchemy session.
        data: Validated creation payload.

    Returns:
        The newly created and flushed :class:`ArchitectSession` with
        its server-generated ``id``, ``created_at`` and ``updated_at``
        populated.
    """
    session_obj = ArchitectSession(
        project_id=data.project_id,
        module_id=data.module_id,
        status=data.status,
        created_by=data.created_by,
        closed_at=data.closed_at,
    )
    db.add(session_obj)
    db.flush()
    return session_obj


def update(
    db: Session,
    session_id: UUID,
    data: ArchitectSessionUpdate,
) -> ArchitectSession:
    """Partially update an Architect chat session.

    Only ``module_id``, ``status`` and ``closed_at`` may be changed.
    ``id``, ``project_id``, ``created_by`` and ``created_at`` are
    immutable — a session belongs to exactly one project and creator
    for its lifetime (sessions are closed rather than reassigned) and
    ``updated_at`` is auto-stamped by the ORM on flush via
    ``onupdate=func.now()``.

    Fields that are ``None`` in the payload are treated as "leave
    unchanged" to support PATCH semantics. ``module_id`` is therefore
    sticky: the explicit-null "downgrade to project-level" transition
    must be performed by :func:`delete`-ing and re-:func:`create`-ing
    the session, matching the immutable-project rule. In practice the
    UI never triggers this — sessions are opened with their scope set.

    Convenience behaviour: when ``status`` transitions from ``active``
    to ``closed`` and the caller did not explicitly supply
    ``closed_at`` in the same payload, the service stamps
    ``closed_at = now()`` automatically. Explicit ``closed_at`` values
    always win, so backfill / correction flows remain possible. This
    mirrors the ``resolved`` auto-stamp pattern in
    :mod:`backend.services.bug`.

    Raises:
        ValueError: If the session does not exist.
    """
    session_obj = get_by_id(db, session_id)

    update_data = data.model_dump(exclude_unset=True)
    # Defensive guard — the schema already excludes immutable fields,
    # but silently dropping any that slip through keeps the service
    # honest.
    allowed_fields = {
        "module_id",
        "status",
        "closed_at",
    }

    new_status = update_data.get("status")
    # Auto-stamp ``closed_at`` on transition into ``closed`` when the
    # caller did not set it explicitly. ``exclude_unset=True`` above
    # means the key is present iff the client sent it, so we can
    # distinguish "not supplied" from "explicitly None".
    auto_closed_at: Optional[datetime] = None
    if new_status == "closed" and session_obj.status != "closed" and "closed_at" not in update_data:
        auto_closed_at = datetime.now(tz=timezone.utc)

    for field, value in update_data.items():
        if field in allowed_fields and value is not None:
            setattr(session_obj, field, value)

    if auto_closed_at is not None:
        session_obj.closed_at = auto_closed_at

    db.flush()
    return session_obj


def delete(db: Session, session_id: UUID) -> None:
    """Hard-delete an Architect session.

    The single inbound FK (``architect_messages.session_id``) uses
    ``ON DELETE CASCADE``, so dependent messages are removed
    automatically at the DB level. No RESTRICT dependency check is
    required. ``status='closed'`` via :func:`update` is the preferred
    soft-close path; :func:`delete` is reserved for test fixtures /
    admin tooling where the conversation history itself must go.

    Raises:
        ValueError: If the session does not exist.
    """
    session_obj = get_by_id(db, session_id)
    db.delete(session_obj)
    db.flush()
