"""Service layer for :class:`~backend.db.models.bugs.Bug`.

Provides the synchronous CRUD surface used by API routers. All methods accept
``db: Session`` as the first argument and only ever call ``session.flush()`` —
transaction commit is the router's responsibility. Errors are signalled via
``ValueError`` so the router can translate them to the appropriate HTTP
status code.

Design notes (per DESIGN.md §1.9 Bug Tracking, §4.0 Version Lifecycle
Rules and :mod:`backend.db.models.bugs`):
    * ``id``, ``bug_number``, ``created_at`` and ``updated_at`` are
      server-managed and therefore immutable from the service layer.
    * ``bug_number`` is auto-assigned by :func:`create` as
      ``MAX(bug_number) + 1`` for the supplied ``project_id`` (starts at
      ``1`` for the first bug in a project). The DB-level
      ``UNIQUE(project_id, bug_number)`` constraint (``uq_bugs_project_id_bug_number``)
      is re-validated defensively before flush so concurrent creates on the
      same project — which are rare but possible — still surface as
      :class:`ValueError` instead of raw
      :class:`~sqlalchemy.exc.IntegrityError`.
    * ``version_id`` is **required** at creation time (DESIGN.md §4.0
      Rule 2 — every new BUG belongs to a release version). The
      underlying column is nullable at the DB level only so that the FK
      ``ON DELETE RESTRICT`` remains expressible for legacy rows; the
      service enforces the stronger application-level contract and
      rejects ``version_id is None`` with :class:`ValueError` (HTTP 422
      at the router layer).
    * ``severity``, ``status`` and ``source`` are constrained by DB CHECKs
      (``ck_bugs_severity``, ``ck_bugs_status``, ``ck_bugs_source``). The
      Pydantic ``BugSeverity`` / ``BugStatus`` / ``BugSource`` literals
      mirror the DB constraints, so the service does not revalidate them —
      if an invalid value ever reaches the service (e.g. a bypassed schema)
      the DB CHECK will reject it on flush.
    * ``project_id`` and ``created_by`` are FK audit columns — immutable
      after creation via :class:`BugUpdate`.
    * When :func:`update` transitions ``status`` to ``resolved`` and the
      caller did not explicitly set ``resolved_at`` on the payload, the
      service stamps ``resolved_at = now()`` automatically so the UI
      doesn't have to.
    * ``bugs`` has one inbound FK (``bug_fix_tasks.bug_id``) with
      ``ON DELETE CASCADE`` — :func:`delete` therefore needs no RESTRICT
      dependency check, the DB cleans up dependent tasks cleanly.
    * List filters (``project_id``, ``status``, ``severity``, ``source``,
      ``created_by``) support the bugs-page UI — "show all open critical
      bugs in project X", "show all customer-reported bugs across
      projects", etc.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.db.models.bugs import Bug
from backend.schemas.bug import (
    BugCreate,
    BugUpdate,
)


def get_by_id(db: Session, bug_id: UUID) -> Bug:
    """Return a single bug by primary key.

    Raises:
        ValueError: If no bug with the supplied ``bug_id`` exists. The
            router converts this to an HTTP 404 response.
    """
    bug = db.get(Bug, bug_id)
    if bug is None:
        raise ValueError(f"Bug {bug_id} not found")
    return bug


def _next_bug_number(db: Session, project_id: UUID) -> int:
    """Return the next ``bug_number`` to assign within a project.

    Scans ``MAX(bug_number)`` for the supplied ``project_id`` and returns
    ``max + 1`` (or ``1`` when the project has no bugs yet). The DB-level
    ``UNIQUE(project_id, bug_number)`` constraint is the ultimate guard
    against concurrent duplicates — the service also re-checks the pair
    before flush (see :func:`_get_by_project_and_number`).
    """
    stmt = select(func.max(Bug.bug_number)).where(Bug.project_id == project_id)
    current_max = db.execute(stmt).scalar()
    return (current_max or 0) + 1


def _get_by_project_and_number(db: Session, project_id: UUID, bug_number: int) -> Optional[Bug]:
    """Internal helper — look up a bug by the unique ``(project_id, bug_number)`` pair."""
    stmt = select(Bug).where(
        Bug.project_id == project_id,
        Bug.bug_number == bug_number,
    )
    return db.execute(stmt).scalar_one_or_none()


def create(db: Session, data: BugCreate) -> Bug:
    """Create a new bug.

    Auto-assigns ``bug_number`` as ``MAX(bug_number) + 1`` for the supplied
    ``project_id``. The computed pair is re-validated against the DB
    unique constraint before flush so a race between concurrent creates
    on the same project surfaces as a clean :class:`ValueError` (HTTP 409
    at the router layer) rather than a raw
    :class:`~sqlalchemy.exc.IntegrityError`.

    ``version_id`` is **required** (DESIGN.md §4.0 Rule 2 — every new
    BUG belongs to a release version). The service raises
    :class:`ValueError` when the caller passes ``None`` so the router
    can translate it to HTTP 422. The underlying column is nullable at
    the DB level only because ``ON DELETE RESTRICT`` must remain
    expressible for legacy rows; the application-level contract is
    stricter.

    ``status`` and ``source`` default to ``new`` / ``internal`` via the
    Pydantic schema when omitted, matching the DB ``server_default``.

    Args:
        db: Active SQLAlchemy session.
        data: Validated creation payload.

    Returns:
        The newly created and flushed :class:`Bug` with its server-
        generated ``id``, ``bug_number``, ``created_at`` and ``updated_at``
        populated.

    Raises:
        ValueError: If ``version_id`` is ``None`` (DESIGN.md §4.0
            Rule 2) or if another bug already uses the same
            ``(project_id, bug_number)`` pair (concurrent-create race).
    """
    if data.version_id is None:
        raise ValueError("version_id required for new bugs")

    bug_number = _next_bug_number(db, data.project_id)
    if _get_by_project_and_number(db, data.project_id, bug_number) is not None:
        raise ValueError(f"Bug with project_id={data.project_id} and bug_number={bug_number} already exists")

    bug = Bug(
        project_id=data.project_id,
        version_id=data.version_id,
        bug_number=bug_number,
        title=data.title,
        description=data.description,
        severity=data.severity,
        status=data.status,
        source=data.source,
        reported_by=data.reported_by,
        environment=data.environment,
        resolved_at=data.resolved_at,
        commit_hash=data.commit_hash,
        created_by=data.created_by,
    )
    db.add(bug)
    db.flush()
    return bug


def update(db: Session, bug_id: UUID, data: BugUpdate) -> Bug:
    """Partially update a bug.

    Only ``title``, ``description``, ``severity``, ``status``, ``source``,
    ``reported_by``, ``environment``, ``resolved_at`` and ``commit_hash``
    may be changed. ``id``, ``project_id``, ``bug_number``, ``created_by``
    and ``created_at`` are immutable; ``updated_at`` is refreshed
    automatically by the ORM ``onupdate=func.now()`` trigger. Fields that
    are ``None`` in the payload are treated as "leave unchanged" to
    support PATCH semantics.

    Convenience behaviour: when ``status`` transitions to ``resolved`` and
    the caller did not explicitly supply ``resolved_at`` in the same
    payload, the service stamps ``resolved_at = now()`` automatically.
    Explicit ``resolved_at`` values always win, so backfill / correction
    flows remain possible.

    Raises:
        ValueError: If the bug does not exist.
    """
    bug = get_by_id(db, bug_id)

    update_data = data.model_dump(exclude_unset=True)
    # Defensive guard — the schema already excludes immutable fields, but
    # silently dropping any that slip through keeps the service honest.
    allowed_fields = {
        "title",
        "description",
        "severity",
        "status",
        "source",
        "reported_by",
        "environment",
        "resolved_at",
        "commit_hash",
    }

    new_status = update_data.get("status")
    # Auto-stamp ``resolved_at`` on transition into ``resolved`` when the
    # caller did not set it explicitly. ``exclude_unset=True`` above means
    # the key is present iff the client sent it, so we can distinguish
    # "not supplied" from "explicitly None".
    auto_resolved_at: Optional[datetime] = None
    if new_status == "resolved" and bug.status != "resolved" and "resolved_at" not in update_data:
        auto_resolved_at = datetime.now(tz=timezone.utc)

    for field, value in update_data.items():
        if field in allowed_fields and value is not None:
            setattr(bug, field, value)

    if auto_resolved_at is not None:
        bug.resolved_at = auto_resolved_at

    db.flush()
    return bug


def delete(db: Session, bug_id: UUID) -> None:
    """Hard-delete a bug.

    The single inbound FK (``bug_fix_tasks.bug_id``) uses
    ``ON DELETE CASCADE``, so dependent bug-fix tasks are removed
    automatically at the DB level. No RESTRICT dependency check is
    required. ``status='wont_fix'`` via :func:`update` is the preferred
    soft-disable path; :func:`delete` is reserved for test fixtures /
    admin tooling.

    Raises:
        ValueError: If the bug does not exist.
    """
    bug = get_by_id(db, bug_id)
    db.delete(bug)
    db.flush()
