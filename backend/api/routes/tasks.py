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
from sqlalchemy.orm import Session

from backend.core import authz
from backend.core.security import get_current_user, require_shu_or_above
from backend.db.models.foundation import User
from backend.db.session import get_db
from backend.schemas.pagination import PaginatedResponse
from backend.schemas.task import (
    TaskCreate,
    TaskRead,
    TaskStatus,
    TaskType,
    TaskUpdate,
)
from backend.services import task as task_service

router = APIRouter(
    tags=["Tasks"],
    # v4.0.35: any authenticated user may reach the tasks surface; per-route ownership checks
    # (backend.core.authz) then scope a Junior (shu) to tasks under their OWN projects.
    dependencies=[Depends(require_shu_or_above)],
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
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PaginatedResponse[TaskRead]:
    """Return a paginated list of tasks.

    Results are ordered by ``number ASC`` (task 1, task 2, …) — owned
    by the service layer, matching the hierarchical-numbering
    convention (DESIGN.md §1.9) and the ``TaskItem`` UI.

    Ownership (v4.0.35): when ``feat_id`` is supplied the caller must own that feat's project
    (ri/ha see all). A Junior (``shu``) MUST supply ``feat_id`` — an unscoped list across all
    projects would leak other owners' tasks, so it is rejected with HTTP 400.
    """
    if feat_id is not None:
        authz.assert_feat_access(db, current_user, feat_id)
    elif current_user.role not in authz.PRIVILEGED_ROLES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="feat_id je povinný — nemôžeš vypísať úlohy naprieč projektami.",
        )
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
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TaskRead:
    """Return a single task by primary key."""
    authz.assert_task_access(db, current_user, task_id)
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
    current_user: User = Depends(get_current_user),
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

    Ownership (v4.0.35): the parent feat (``payload.feat_id``) must belong to a project the
    caller owns (ri/ha may create under any feat); a Junior placing a task under someone
    else's feat gets HTTP 403.
    """
    authz.assert_feat_access(db, current_user, payload.feat_id)
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
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TaskRead:
    """Partially update a task's mutable fields.

    Only ``title``, ``description``, ``task_type``, ``status``,
    ``estimated_minutes``, ``actual_minutes`` and ``checklist_type``
    are mutable. ``id``, ``feat_id``, ``number`` and ``created_at``
    are immutable — the task identity and its position within the feat
    must not be rewritten after the fact; ``updated_at`` is refreshed
    by the ORM on flush via ``onupdate=func.now()``. Fields omitted
    from the payload are left unchanged.

    CR-V2-016: the old STATUS.md / HISTORY.md write side-effect on a
    task→done transition is RETIRED. Those DB-driven files were a second,
    independent writer of project status / history; the single source of
    truth is now the AI Agent's own ``MEMORY.md`` plus the Vývoj phase
    tabs (R-DOUBLEWRITE). This endpoint is now a pure DB update.

    Ownership (v4.0.35): the caller must own the task's project (ri/ha see all).
    """
    authz.assert_task_access(db, current_user, task_id)
    try:
        task = task_service.update(db, task_id, payload)
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise _map_value_error(exc) from exc
    db.refresh(task)
    return TaskRead.model_validate(task)


@router.delete(
    "/{task_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
def delete_task(
    task_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    """Hard-delete a task by primary key.

    Inbound FKs — ``delegations.task_id`` (``ON DELETE SET NULL``) and
    ``execution_logs.task_id`` (``ON DELETE SET NULL``) — are handled
    at the DB level, so dependent rows are NULL-ed automatically on
    flush. No RESTRICT dependency check is required.

    Ownership (v4.0.35): the caller must own the task's project (ri/ha see all).
    """
    authz.assert_task_access(db, current_user, task_id)
    try:
        task_service.delete(db, task_id)
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise _map_value_error(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
