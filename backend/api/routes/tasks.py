"""REST router for :class:`~backend.db.models.tasks.Task`.

Exposes the standard CRUD surface for tasks — the leaf level of the
Epic/Feat/Task hierarchy (DESIGN.md §1.9 Tasks hierarchy) — that backs
the ``TasksPage`` / ``TaskItem`` UI (DESIGN.md §3.1):

* ``GET    /``          → paginated list (filter by ``feat_id``,
  ``status`` and ``task_type``).
* ``GET    /{task_id}``  → single task by primary key.
* ``POST   /``           → create a new task (``number`` is auto-assigned
  by the service layer as ``MAX(number) + 1`` per feat).
* ``PATCH  /{task_id}``  → partial update of the mutable fields.
* ``DELETE /{task_id}``  → hard-delete a task (HTTP 204).

All endpoints are synchronous ``def`` — pg8000 is a synchronous driver
and FastAPI dispatches sync endpoints to a thread pool automatically.
The router delegates every persistence operation to
:mod:`backend.services.task` and handles commit / rollback itself so
the service layer remains transaction-agnostic.

The router is prefix-less; the mount prefix (``/api/v1/tasks``) is
applied in ``backend/main.py`` via ``app.include_router`` (Task 4.27).

Design notes (per DESIGN.md §1.9 Tasks (Epic/Feat/Task hierarchy), §2
``tasks`` table, §2.6 ``POST /feats/{id}/tasks`` /
``GET /feats/{id}/tasks``, §3.1 ``TaskItem`` and §6 REST API
Architecture):

* ``id``, ``number``, ``created_at`` and ``updated_at`` are
  server-managed and therefore immutable. ``feat_id`` is an immutable
  foreign key — a task belongs to exactly one feat for its lifetime.
  :class:`~backend.schemas.task.TaskUpdate` deliberately omits all
  immutable / server-managed fields.
* ``number`` is auto-assigned by the service layer as
  ``MAX(number) + 1`` for the supplied ``feat_id`` (starts at ``1``
  for the first task in a feat). Concurrent-create races on the same
  feat surface as HTTP 409 via the DB-level ``UNIQUE(feat_id, number)``
  constraint (``uq_tasks_feat_id_number``).
* ``task_type`` is constrained by the ``ck_tasks_task_type`` DB CHECK
  (``backend | frontend | migration | test | docs``). ``status`` is
  constrained by the ``ck_tasks_status`` DB CHECK (``todo |
  in_progress | done | failed``). Invalid values surface at schema
  validation time (HTTP 422) via the Pydantic ``Literal`` types.
* List filters (``feat_id``, ``status``, ``task_type``) match the
  indexed columns (``ix_tasks_feat_id``, ``ix_tasks_status``) and back
  the Tasks UI ("show every task in this feat", "show every
  in-progress task", "show every frontend task") —
  ``GET /feats/{id}/tasks`` (DESIGN.md §2.6) maps directly onto
  ``list_tasks(feat_id=...)``.
* List ordering (``number ASC``) is owned by the service so tasks
  appear in their stable, human-readable numbering sequence (task 1,
  task 2, …) matching the ``TaskItem`` UI convention and the
  user-facing ``{epic.number}.{feat.number}.{task.number}`` identifiers.
* Inbound FKs on ``tasks`` — ``delegations.task_id`` and
  ``execution_logs.task_id``, both with ``ON DELETE SET NULL`` — are
  handled at the DB level, so :func:`delete_task` needs no RESTRICT
  dependency check; dependent rows are NULL-ed automatically on flush.
"""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.api.dependencies import get_knowledge_base_writer
from backend.core.security import require_ha_or_above
from backend.db.models.projects import Project
from backend.db.models.tasks import Epic, Feat, Task
from backend.db.session import get_db
from backend.schemas.live_documents import TaskCompletionData
from backend.schemas.pagination import PaginatedResponse
from backend.schemas.task import (
    TaskCreate,
    TaskRead,
    TaskStatus,
    TaskType,
    TaskUpdate,
)
from backend.services import task as task_service
from backend.services.knowledge_base_writer import KnowledgeBaseWriter
from backend.services.live_documents import LiveDocumentService

router = APIRouter(
    tags=["Tasks"],
    dependencies=[Depends(require_ha_or_above)],
)


def _task_context(db: Session, task_id: UUID) -> tuple[Task, Feat, Project] | None:
    """Return the ``(task, feat, project)`` triple for a task in one query.

    Used by the live-document hook on ``PATCH`` to resolve the slug
    for the KB writer and the feat number for ``HISTORY.md`` entries.
    Returns ``None`` when the task does not exist.
    """
    row = db.execute(
        select(Task, Feat, Project)
        .join(Feat, Task.feat_id == Feat.id)
        .join(Epic, Feat.epic_id == Epic.id)
        .join(Project, Epic.project_id == Project.id)
        .where(Task.id == task_id)
    ).first()
    if row is None:
        return None
    return row[0], row[1], row[2]


def _build_task_completion_data(task: Task, feat: Feat) -> TaskCompletionData:
    """Assemble the ``TaskCompletionData`` payload for a just-completed task.

    Commit-hash / duration / CC-agent enrichment used to come from the
    ``ExecutionLog`` delegation pipeline. That subsystem has been removed
    (CR-NS-008), so the DTO now always carries neutral defaults — no
    commit suffix, zero duration, agent ``"unknown"`` — and the
    generators render a minimal entry.
    """
    return TaskCompletionData(
        feat_number=feat.number,
        task_number=task.number,
        task_title=task.title,
        status="done",
        duration_seconds=0.0,
        agent="unknown",
        commit_hashes=[],
    )


def _map_value_error(exc: ValueError) -> HTTPException:
    """Translate a service-layer ``ValueError`` into an HTTP exception.

    Mirrors the ICC error-handling pattern: ``not found`` → 404,
    duplicates/conflicts → 409, everything else (constraint / FK /
    validation failures) → 422.
    """
    message = str(exc)
    lowered = message.lower()
    if "not found" in lowered:
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=message)
    if "already exists" in lowered or "duplicate" in lowered or "conflict" in lowered:
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=message)
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=message)


@router.get("", response_model=PaginatedResponse[TaskRead])
def list_tasks(
    feat_id: Optional[UUID] = Query(
        default=None,
        description=(
            "Filter by the feat the task belongs to. Hits the "
            "``ix_tasks_feat_id`` index — the core ``GET "
            "/feats/{id}/tasks`` query (DESIGN.md §2.6) and the "
            "``FeatCard`` per-feat task display (DESIGN.md §3.1)."
        ),
    ),
    status_filter: Optional[TaskStatus] = Query(
        default=None,
        alias="status",
        description=(
            "Filter by lifecycle status (``todo`` | ``in_progress`` | "
            "``done`` | ``failed``). Hits the ``ix_tasks_status`` index."
        ),
    ),
    task_type: Optional[TaskType] = Query(
        default=None,
        description=("Filter by task type (``backend`` | ``frontend`` | ``migration`` | ``test`` | ``docs``)."),
    ),
    skip: int = Query(default=0, ge=0, description="Number of rows to skip."),
    limit: int = Query(default=50, ge=1, le=100, description="Maximum rows to return."),
    db: Session = Depends(get_db),
) -> PaginatedResponse[TaskRead]:
    """Return a paginated list of tasks.

    Results are ordered by ``number ASC`` (task 1, task 2, …) — owned
    by the service layer, matching the hierarchical-numbering
    convention (DESIGN.md §1.9) and the ``TaskItem`` UI.
    """
    try:
        rows = task_service.list_tasks(
            db,
            feat_id=feat_id,
            status=status_filter,
            task_type=task_type,
            limit=limit,
            offset=skip,
        )
        total = task_service.count_tasks(
            db,
            feat_id=feat_id,
            status=status_filter,
            task_type=task_type,
        )
    except ValueError as exc:
        raise _map_value_error(exc) from exc

    return PaginatedResponse[TaskRead](
        items=[TaskRead.model_validate(row) for row in rows],
        total=total,
        skip=skip,
        limit=limit,
    )


@router.get("/{task_id}", response_model=TaskRead)
def get_task(
    task_id: UUID,
    db: Session = Depends(get_db),
) -> TaskRead:
    """Return a single task by primary key."""
    try:
        task = task_service.get_by_id(db, task_id)
    except ValueError as exc:
        raise _map_value_error(exc) from exc
    return TaskRead.model_validate(task)


@router.post(
    "",
    response_model=TaskRead,
    status_code=status.HTTP_201_CREATED,
)
def create_task(
    payload: TaskCreate,
    db: Session = Depends(get_db),
) -> TaskRead:
    """Create a new task.

    ``number`` is auto-assigned by the service layer as
    ``MAX(number) + 1`` for the supplied ``feat_id`` (starts at ``1``
    for the first task in a feat). ``status`` and ``description``
    default to ``todo`` / ``""`` via the Pydantic schema / DB
    ``server_default`` when omitted. ``task_type`` is required — there
    is no server default. Concurrent-create races on the same feat
    surface as HTTP 409. Missing or invalid ``feat_id`` foreign keys are
    rejected by the DB-level FK and surface as HTTP 422.
    """
    try:
        task = task_service.create(db, payload)
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise _map_value_error(exc) from exc
    db.refresh(task)
    return TaskRead.model_validate(task)


@router.patch("/{task_id}", response_model=TaskRead)
def update_task(
    task_id: UUID,
    payload: TaskUpdate,
    db: Session = Depends(get_db),
    kb_writer: KnowledgeBaseWriter = Depends(get_knowledge_base_writer),
) -> TaskRead:
    """Partially update a task's mutable fields.

    Only ``title``, ``description``, ``task_type``, ``status``,
    ``estimated_minutes``, ``actual_minutes`` and ``checklist_type``
    are mutable. ``id``, ``feat_id``, ``number`` and ``created_at``
    are immutable — the task identity and its position within the feat
    must not be rewritten after the fact; ``updated_at`` is refreshed
    by the ORM on flush via ``onupdate=func.now()``. Fields omitted
    from the payload are left unchanged.

    **Live documents side effect.** When a task transitions to
    ``status='done'`` (and was not already done), this endpoint
    appends a ``HISTORY.md`` entry and regenerates ``STATUS.md`` for
    the owning project. The KB writes happen before ``db.commit()``
    so an I/O failure rolls the status change back — the DB and the
    KB never disagree.
    """
    # Snapshot previous status before the update is applied — after
    # task_service.update, the in-session task object will carry the
    # new status, not the pre-update one.
    ctx = _task_context(db, task_id)
    previous_status = ctx[0].status if ctx is not None else None

    try:
        task = task_service.update(db, task_id, payload)

        if ctx is not None and previous_status != "done" and task.status == "done":
            _, feat, project = ctx
            data = _build_task_completion_data(task, feat)
            svc = LiveDocumentService(project.slug, writer=kb_writer)
            svc.append_history(data)
            svc.regenerate_status(db, project.id)

        db.commit()
    except ValueError as exc:
        db.rollback()
        raise _map_value_error(exc) from exc
    except OSError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update live documents: {exc}",
        ) from exc
    db.refresh(task)
    return TaskRead.model_validate(task)


@router.delete(
    "/{task_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
def delete_task(
    task_id: UUID,
    db: Session = Depends(get_db),
) -> Response:
    """Hard-delete a task by primary key.

    Inbound FKs — ``delegations.task_id`` (``ON DELETE SET NULL``) and
    ``execution_logs.task_id`` (``ON DELETE SET NULL``) — are handled
    at the DB level, so dependent rows are NULL-ed automatically on
    flush. No RESTRICT dependency check is required.
    """
    try:
        task_service.delete(db, task_id)
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise _map_value_error(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
