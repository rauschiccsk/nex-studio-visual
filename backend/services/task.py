"""Service layer for :class:`~backend.db.models.tasks.Task` — the next task to build, read one, create, and the
feat / epic status that follows from the tasks.

The build writes a version's plan through :func:`create`; every function takes ``db: Session`` first and only
flushes — the caller commits. ``number`` is ``MAX(number) + 1`` per feat, re-validated against
``uq_tasks_feat_id_number`` so a concurrent create surfaces as :class:`ValueError`. DEV-155 (23.10.2026) removed
update, delete and the listings, which only the first version's API called.
"""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.db.models.tasks import Epic, Feat, Task
from backend.schemas.task import (
    TaskCreate,
)


def get_next_todo_task(db: Session, version_id: UUID) -> Optional[Task]:
    """Return the first ``todo`` Task for a version in plan order, or ``None`` if none remain.

    Plan order is the hierarchical numbering ``Epic.number → Feat.number → Task.number``
    (all unique within their scope, so the ordering is total — repeated calls return the
    same task until it transitions out of ``todo``). Drives the F-007 §6 per-task build
    loop: the orchestrator dispatches this task, and when no ``todo`` task remains the
    build stage is complete. ``Task`` has no ``version_id`` — it joins up through
    ``Feat → Epic → Epic.version_id``.
    """
    stmt = (
        select(Task)
        .join(Feat, Feat.id == Task.feat_id)
        .join(Epic, Epic.id == Feat.epic_id)
        .where(Epic.version_id == version_id, Task.status == "todo")
        .order_by(Epic.number.asc(), Feat.number.asc(), Task.number.asc())
        .limit(1)
    )
    return db.execute(stmt).scalar_one_or_none()


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
        plain_description=data.plain_description,
        task_type=data.task_type,
        status=data.status,
        priority=data.priority,
        checklist_type=data.checklist_type,
        billing_category=data.billing_category,
    )
    db.add(task)
    db.flush()
    return task


def recompute_feat_status(db: Session, feat_id: UUID) -> None:
    """Recompute and persist ``Feat.status`` from its tasks.

    Rules (mirrors NEX Command FeatExecutor logic):
    - All tasks done → ``done``
    - Any task ``failed`` (and none ``in_progress``) → ``failed``
    - Any task ``in_progress`` → ``in_progress``
    - Otherwise → ``todo``

    Also propagates to the parent Epic via :func:`recompute_epic_status`.
    """
    feat = db.get(Feat, feat_id)
    if feat is None:
        return

    tasks = list(db.execute(select(Task).where(Task.feat_id == feat_id)).scalars().all())
    if not tasks:
        return

    statuses = {t.status for t in tasks}
    if statuses == {"done"}:
        new_status = "done"
    elif "in_progress" in statuses:
        new_status = "in_progress"
    elif "failed" in statuses:
        new_status = "failed"
    else:
        new_status = "todo"

    if feat.status != new_status:
        feat.status = new_status
        db.flush()
    # ICCINT-31: recompute the epic ALWAYS, not only when this feat's own status changed. The epic derives
    # from ALL its feats, so a sibling can make it stale while this one stands still — and the propagation
    # sat inside the ``if`` above, which is a rollup that silently skips the case it exists for.
    recompute_epic_status(db, feat.epic_id)


def recompute_epic_status(db: Session, epic_id: UUID) -> None:
    """Recompute and persist ``Epic.status`` from its feats.

    Rules:
    - All feats done → ``done``
    - Any feat ``in_progress`` or ``failed`` → ``in_progress``
    - Otherwise → ``planned``
    """
    epic = db.get(Epic, epic_id)
    if epic is None:
        return

    feats = list(db.execute(select(Feat).where(Feat.epic_id == epic_id)).scalars().all())
    if not feats:
        return

    statuses = {f.status for f in feats}
    if statuses == {"done"}:
        new_status = "done"
    elif "in_progress" in statuses or "failed" in statuses:
        new_status = "in_progress"
    else:
        new_status = "planned"

    if epic.status != new_status:
        epic.status = new_status
        db.flush()
