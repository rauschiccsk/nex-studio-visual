"""Service layer for :class:`~backend.db.models.tasks.Task`.

Provides the synchronous CRUD surface used by API routers. All methods
accept ``db: Session`` as the first argument and only ever call
``session.flush()`` — transaction commit is the router's responsibility.
Errors are signalled via :class:`ValueError` so the router can translate
them to the appropriate HTTP status code.

Design notes (per DESIGN.md §1.9 Tasks (Epic/Feat/Task hierarchy), §2
``tasks`` table, §2.6 ``POST /feats/{id}/tasks`` / ``GET /feats/{id}/tasks``,
§6.6 list filters, §6.8 Service Layer Extension and
:mod:`backend.db.models.tasks.Task`):

    * ``id``, ``number``, ``created_at`` and ``updated_at`` are
      server-managed and therefore immutable from the service layer
      (``updated_at`` is auto-stamped by the ORM via
      ``onupdate=func.now()`` on flush).
    * ``feat_id`` is an immutable foreign key — a task belongs to
      exactly one feat for its lifetime. :class:`TaskUpdate`
      deliberately omits it and the service's ``allowed_fields``
      allow-list enforces that contract defensively.
    * ``number`` is auto-assigned by :func:`create` as
      ``MAX(number) + 1`` for the supplied ``feat_id`` (starts at
      ``1`` for the first task in a feat). The DB-level
      ``UNIQUE(feat_id, number)`` constraint
      (``uq_tasks_feat_id_number``) is re-validated defensively before
      flush so concurrent creates on the same feat — which are rare but
      possible — still surface as :class:`ValueError` instead of raw
      :class:`~sqlalchemy.exc.IntegrityError`.
    * ``task_type`` is constrained by the ``ck_tasks_task_type`` DB
      CHECK (``backend | frontend | migration | test | docs``). The
      Pydantic :data:`~backend.schemas.task.TaskType` literal mirrors
      the DB constraint, so the service does not revalidate — if an
      invalid value ever reaches the service (e.g. a bypassed schema)
      the DB CHECK rejects it on flush.
    * ``status`` is constrained by the ``ck_tasks_status`` DB CHECK
      (``todo | in_progress | done | failed``). The Pydantic
      :data:`~backend.schemas.task.TaskStatus` literal mirrors the DB
      constraint.
    * ``actual_minutes`` is normally measured from delegation duration
      but is exposed in :class:`TaskUpdate` for backfill / correction
      flows (consistent with :mod:`backend.services.feat` and
      :mod:`backend.services.bug_fix_task`).
    * Inbound FKs on ``tasks`` — ``delegations.task_id`` and
      ``execution_logs.task_id``, both with ``ON DELETE SET NULL`` —
      are handled at the DB level, so :func:`delete` needs no RESTRICT
      dependency check; dependent rows are NULL-ed automatically on
      flush.
    * List filters (``feat_id``, ``status``, ``task_type``) match the
      indexed columns (``ix_tasks_feat_id``, ``ix_tasks_status``) and
      support the Tasks UI (DESIGN.md §3.1 ``TasksPage`` / ``FeatCard``
      / ``TaskItem`` with task-type badge and status display).
      ``GET /feats/{id}/tasks`` (DESIGN.md §2.6) maps directly onto
      ``list_tasks(feat_id=...)``.
    * List ordering is ``number ASC`` — tasks display in creation order
      (task 1, task 2, …) to match the hierarchical-numbering
      convention described in DESIGN.md §1.9 and the ``TaskItem`` UI.
      Within a feat the ``number`` column is monotonically increasing,
      so ordering by ``number`` gives a stable, human-readable sequence
      that aligns with the user-facing
      ``{epic.number}.{feat.number}.{task.number}`` identifiers.
"""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.db.models.tasks import Task
from backend.schemas.task import (
    TaskCreate,
    TaskStatus,
    TaskType,
    TaskUpdate,
)


def list_tasks(
    db: Session,
    *,
    feat_id: Optional[UUID] = None,
    status: Optional[TaskStatus] = None,
    task_type: Optional[TaskType] = None,
    limit: int = 100,
    offset: int = 0,
) -> list[Task]:
    """Return tasks filtered by the supplied criteria.

    Results are ordered by ``number ASC`` so tasks appear in their
    stable, human-readable numbering order (task 1, task 2, …) — this
    matches the hierarchical-numbering convention documented in
    DESIGN.md §1.9 and the ``TaskItem`` UI (DESIGN.md §3.1).

    Args:
        db: Active SQLAlchemy session.
        feat_id: Optional feat filter — restrict to tasks belonging to
            a specific feat (the core ``GET /feats/{id}/tasks`` query,
            DESIGN.md §2.6).
        status: Optional lifecycle-status filter (``todo`` |
            ``in_progress`` | ``done`` | ``failed``).
        task_type: Optional type filter (``backend`` | ``frontend`` |
            ``migration`` | ``test`` | ``docs``).
        limit: Maximum number of rows to return.
        offset: Number of rows to skip.

    Returns:
        List of :class:`Task` instances.
    """
    stmt = select(Task)
    if feat_id is not None:
        stmt = stmt.where(Task.feat_id == feat_id)
    if status is not None:
        stmt = stmt.where(Task.status == status)
    if task_type is not None:
        stmt = stmt.where(Task.task_type == task_type)
    stmt = stmt.order_by(Task.number.asc()).limit(limit).offset(offset)
    return list(db.execute(stmt).scalars().all())


def get_by_id(db: Session, task_id: UUID) -> Task:
    """Return a single task by primary key.

    Raises:
        ValueError: If no task with the supplied ``task_id`` exists.
            The router converts this to an HTTP 404 response.
    """
    task = db.get(Task, task_id)
    if task is None:
        raise ValueError(f"Task {task_id} not found")
    return task


def _next_task_number(db: Session, feat_id: UUID) -> int:
    """Return the next ``number`` to assign within a feat.

    Scans ``MAX(number)`` for the supplied ``feat_id`` and returns
    ``max + 1`` (or ``1`` when the feat has no tasks yet). The DB-level
    ``UNIQUE(feat_id, number)`` constraint is the ultimate guard against
    concurrent duplicates — the service also re-checks the pair before
    flush (see :func:`_get_by_feat_and_number`).
    """
    stmt = select(func.max(Task.number)).where(Task.feat_id == feat_id)
    current_max = db.execute(stmt).scalar()
    return (current_max or 0) + 1


def _get_by_feat_and_number(
    db: Session,
    feat_id: UUID,
    number: int,
) -> Optional[Task]:
    """Internal helper — look up a task by the ``(feat_id, number)`` pair."""
    stmt = select(Task).where(
        Task.feat_id == feat_id,
        Task.number == number,
    )
    return db.execute(stmt).scalar_one_or_none()


def create(db: Session, data: TaskCreate) -> Task:
    """Create a new task.

    Auto-assigns ``number`` as ``MAX(number) + 1`` for the supplied
    ``feat_id``. The computed pair is re-validated against the DB
    unique constraint before flush so a race between concurrent creates
    on the same feat surfaces as a clean :class:`ValueError` (HTTP 409
    at the router layer) rather than a raw
    :class:`~sqlalchemy.exc.IntegrityError`.

    ``status`` and ``description`` default to ``todo`` / ``""`` via the
    Pydantic schema / DB ``server_default`` when omitted, matching the
    model declaration. ``task_type`` is required — there is no server
    default.

    If the supplied ``feat_id`` foreign key does not match an existing
    row the DB-level FK rejects the flush and the error propagates
    as-is (routed at the API layer as a 409/422).

    Args:
        db: Active SQLAlchemy session.
        data: Validated creation payload.

    Returns:
        The newly created and flushed :class:`Task` with its
        server-generated ``id``, ``number``, ``created_at`` and
        ``updated_at`` populated.

    Raises:
        ValueError: If another task already uses the same
            ``(feat_id, number)`` pair (concurrent-create race).
    """
    number = _next_task_number(db, data.feat_id)
    if _get_by_feat_and_number(db, data.feat_id, number) is not None:
        raise ValueError(f"Task with feat_id={data.feat_id} and number={number} already exists")

    task = Task(
        feat_id=data.feat_id,
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


def update(db: Session, task_id: UUID, data: TaskUpdate) -> Task:
    """Partially update a task.

    Only ``title``, ``description``, ``task_type``, ``status``,
    ``estimated_minutes``, ``actual_minutes`` and ``checklist_type`` may
    be changed. ``id``, ``feat_id``, ``number`` and ``created_at`` are
    immutable — a task belongs to exactly one feat for its lifetime, its
    position within the feat (``number``) must not be rewritten after
    the fact, and ``updated_at`` is auto-stamped by the ORM on flush via
    ``onupdate=func.now()``.

    Fields that are ``None`` in the payload are treated as "leave
    unchanged" to support PATCH semantics. Consequently, the
    explicit-null transitions ``estimated_minutes -> NULL``,
    ``actual_minutes -> NULL`` and ``checklist_type -> NULL`` are not
    expressible through this service; they are rare corrections that
    belong to admin tooling rather than the UI.

    Raises:
        ValueError: If the task does not exist.
    """
    task = get_by_id(db, task_id)

    update_data = data.model_dump(exclude_unset=True)
    # Defensive guard — the schema already excludes immutable and
    # server-managed fields, but silently dropping any that slip
    # through keeps the service honest.
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


def delete(db: Session, task_id: UUID) -> None:
    """Hard-delete a task.

    Inbound FKs — ``delegations.task_id`` (``ON DELETE SET NULL``) and
    ``execution_logs.task_id`` (``ON DELETE SET NULL``) — are handled at
    the DB level, so dependent rows are NULL-ed automatically on flush.
    No RESTRICT dependency check is required.

    Raises:
        ValueError: If the task does not exist.
    """
    task = get_by_id(db, task_id)
    db.delete(task)
    db.flush()
