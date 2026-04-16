"""Service layer for :class:`~backend.db.models.bugs.BugFixTask`.

Provides the synchronous CRUD surface used by API routers. All methods
accept ``db: Session`` as the first argument and only ever call
``session.flush()`` — transaction commit is the router's responsibility.
Errors are signalled via :class:`ValueError` so the router can translate
them to the appropriate HTTP status code.

Design notes (per DESIGN.md §1.17 BugFixTask,
:mod:`backend.db.models.bugs.BugFixTask` and DESIGN.md §1.6 Bug Tracking
``bug_fix_tasks`` table):
    * ``id``, ``number``, ``created_at`` and ``updated_at`` are
      server-managed and therefore immutable from the service layer.
    * ``number`` is auto-assigned by :func:`create` as
      ``MAX(number) + 1`` for the supplied ``bug_id`` (starts at ``1``
      for the first fix task of a bug). The DB-level
      ``UNIQUE(bug_id, number)`` constraint
      (``uq_bug_fix_tasks_bug_id_number``) is re-validated defensively
      before flush so concurrent creates on the same bug — rare but
      possible — still surface as :class:`ValueError` instead of raw
      :class:`~sqlalchemy.exc.IntegrityError`.
    * ``task_type`` and ``status`` are constrained by DB CHECKs
      (``ck_bug_fix_tasks_task_type``, ``ck_bug_fix_tasks_status``). The
      Pydantic :data:`~backend.schemas.bug_fix_task.BugFixTaskType` /
      :data:`~backend.schemas.bug_fix_task.BugFixTaskStatus` literals
      mirror the DB constraints, so the service does not revalidate them
      — if an invalid value ever reaches the service (e.g. a bypassed
      schema) the DB CHECK will reject it on flush.
    * ``bug_id`` is immutable after creation via
      :class:`BugFixTaskUpdate` — moving a fix task between bugs would
      invalidate its ``number`` uniqueness guarantee.
    * ``bug_fix_tasks`` has one inbound FK
      (``delegations.bug_fix_task_id``) with ``ON DELETE SET NULL`` —
      :func:`delete` therefore needs no RESTRICT dependency check, the
      DB nulls out the delegation reference cleanly (delegations are
      retained for the audit trail per DESIGN.md §1.18).
    * List filters (``bug_id``, ``status``, ``task_type``) support the
      bug-detail page — "show all todo backend fix tasks for bug
      BUG-017", "show every failed fix task across a bug" etc.
"""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.db.models.bugs import BugFixTask
from backend.schemas.bug_fix_task import (
    BugFixTaskCreate,
    BugFixTaskStatus,
    BugFixTaskType,
    BugFixTaskUpdate,
)


def list_bug_fix_tasks(
    db: Session,
    *,
    bug_id: Optional[UUID] = None,
    status: Optional[BugFixTaskStatus] = None,
    task_type: Optional[BugFixTaskType] = None,
    limit: int = 100,
    offset: int = 0,
) -> list[BugFixTask]:
    """Return bug fix tasks filtered by the supplied criteria.

    Results are ordered by ``(bug_id, number)`` so fix tasks appear in
    their natural display order (``BUG-001 / FIX-1``, ``FIX-2``, …)
    matching the bug-detail page convention.

    Args:
        db: Active SQLAlchemy session.
        bug_id: Optional filter — restrict to fix tasks for a specific
            bug.
        status: Optional lifecycle-status filter (``todo`` |
            ``in_progress`` | ``done`` | ``failed``).
        task_type: Optional type filter (``backend`` | ``frontend`` |
            ``migration`` | ``test`` | ``docs``).
        limit: Maximum number of rows to return.
        offset: Number of rows to skip.

    Returns:
        List of :class:`BugFixTask` instances.
    """
    stmt = select(BugFixTask)
    if bug_id is not None:
        stmt = stmt.where(BugFixTask.bug_id == bug_id)
    if status is not None:
        stmt = stmt.where(BugFixTask.status == status)
    if task_type is not None:
        stmt = stmt.where(BugFixTask.task_type == task_type)
    stmt = stmt.order_by(BugFixTask.bug_id, BugFixTask.number).limit(limit).offset(offset)
    return list(db.execute(stmt).scalars().all())


def get_by_id(db: Session, bug_fix_task_id: UUID) -> BugFixTask:
    """Return a single bug fix task by primary key.

    Raises:
        ValueError: If no bug fix task with the supplied
            ``bug_fix_task_id`` exists. The router converts this to an
            HTTP 404 response.
    """
    task = db.get(BugFixTask, bug_fix_task_id)
    if task is None:
        raise ValueError(f"BugFixTask {bug_fix_task_id} not found")
    return task


def _next_number(db: Session, bug_id: UUID) -> int:
    """Return the next ``number`` to assign within a bug.

    Scans ``MAX(number)`` for the supplied ``bug_id`` and returns
    ``max + 1`` (or ``1`` when the bug has no fix tasks yet). The
    DB-level ``UNIQUE(bug_id, number)`` constraint is the ultimate guard
    against concurrent duplicates — the service also re-checks the pair
    before flush (see :func:`_get_by_bug_and_number`).
    """
    stmt = select(func.max(BugFixTask.number)).where(BugFixTask.bug_id == bug_id)
    current_max = db.execute(stmt).scalar()
    return (current_max or 0) + 1


def _get_by_bug_and_number(db: Session, bug_id: UUID, number: int) -> Optional[BugFixTask]:
    """Internal helper — look up a fix task by the unique ``(bug_id, number)`` pair."""
    stmt = select(BugFixTask).where(
        BugFixTask.bug_id == bug_id,
        BugFixTask.number == number,
    )
    return db.execute(stmt).scalar_one_or_none()


def create(db: Session, data: BugFixTaskCreate) -> BugFixTask:
    """Create a new bug fix task.

    Auto-assigns ``number`` as ``MAX(number) + 1`` for the supplied
    ``bug_id``. The computed pair is re-validated against the DB unique
    constraint before flush so a race between concurrent creates on the
    same bug surfaces as a clean :class:`ValueError` (HTTP 409 at the
    router layer) rather than a raw
    :class:`~sqlalchemy.exc.IntegrityError`.

    ``description`` defaults to ``""`` and ``status`` defaults to
    ``todo`` via the Pydantic schema when omitted, matching the DB
    ``server_default``.

    Args:
        db: Active SQLAlchemy session.
        data: Validated creation payload.

    Returns:
        The newly created and flushed :class:`BugFixTask` with its
        server-generated ``id``, ``number``, ``created_at`` and
        ``updated_at`` populated.

    Raises:
        ValueError: If another fix task already uses the same
            ``(bug_id, number)`` pair (concurrent-create race).
    """
    number = _next_number(db, data.bug_id)
    if _get_by_bug_and_number(db, data.bug_id, number) is not None:
        raise ValueError(f"BugFixTask with bug_id={data.bug_id} and number={number} already exists")

    task = BugFixTask(
        bug_id=data.bug_id,
        number=number,
        title=data.title,
        description=data.description,
        task_type=data.task_type,
        status=data.status,
        estimated_minutes=data.estimated_minutes,
        actual_minutes=data.actual_minutes,
        checklist_type=data.checklist_type,
    )
    db.add(task)
    db.flush()
    return task


def update(db: Session, bug_fix_task_id: UUID, data: BugFixTaskUpdate) -> BugFixTask:
    """Partially update a bug fix task.

    Only ``title``, ``description``, ``task_type``, ``status``,
    ``estimated_minutes``, ``actual_minutes`` and ``checklist_type`` may
    be changed. ``id``, ``bug_id``, ``number`` and ``created_at`` are
    immutable; ``updated_at`` is refreshed automatically by the ORM
    ``onupdate=func.now()`` trigger. Fields that are ``None`` in the
    payload are treated as "leave unchanged" to support PATCH semantics.

    Raises:
        ValueError: If the bug fix task does not exist.
    """
    task = get_by_id(db, bug_fix_task_id)

    update_data = data.model_dump(exclude_unset=True)
    # Defensive guard — the schema already excludes immutable fields, but
    # silently dropping any that slip through keeps the service honest.
    allowed_fields = {
        "title",
        "description",
        "task_type",
        "status",
        "estimated_minutes",
        "actual_minutes",
        "checklist_type",
    }

    for field, value in update_data.items():
        if field in allowed_fields and value is not None:
            setattr(task, field, value)

    db.flush()
    return task


def delete(db: Session, bug_fix_task_id: UUID) -> None:
    """Hard-delete a bug fix task.

    The single inbound FK (``delegations.bug_fix_task_id``) uses
    ``ON DELETE SET NULL``, so dependent delegation rows are kept for
    the audit trail with their ``bug_fix_task_id`` nulled out at the DB
    level. No RESTRICT dependency check is required.
    ``status='failed'`` or re-opening via :func:`update` is the
    preferred path for routine operation; :func:`delete` is reserved
    for test fixtures / admin tooling.

    Raises:
        ValueError: If the bug fix task does not exist.
    """
    task = get_by_id(db, bug_fix_task_id)
    db.delete(task)
    db.flush()
