"""REST router for :class:`~backend.db.models.tasks.Feat`.

Exposes the standard CRUD surface for feats — the middle level of the
Epic/Feat/Task hierarchy (DESIGN.md §1.9 Tasks hierarchy) — that backs
the ``TasksPage`` / ``FeatCard`` UI (DESIGN.md §3.1):

* ``GET    /``         → paginated list (filter by ``epic_id`` and
  ``status``).
* ``GET    /{feat_id}`` → single feat by primary key.
* ``POST   /``          → create a new feat (``number`` is auto-assigned
  by the service layer as ``MAX(number) + 1`` per epic).
* ``PATCH  /{feat_id}`` → partial update of the mutable fields.
* ``DELETE /{feat_id}`` → hard-delete a feat (HTTP 204).

All endpoints are synchronous ``def`` — pg8000 is a synchronous driver
and FastAPI dispatches sync endpoints to a thread pool automatically.
The router delegates every persistence operation to
:mod:`backend.services.feat` and handles commit / rollback itself so the
service layer remains transaction-agnostic.

The router is prefix-less; the mount prefix (``/api/v1/feats``) is
applied in ``backend/main.py`` via ``app.include_router`` (Task 4.27).

Design notes (per DESIGN.md §1.9 Tasks (Epic/Feat/Task hierarchy), §2
``feats`` table, §2.6 ``POST /epics/{id}/feats`` /
``GET /epics/{id}/feats``, §3.1 ``FeatCard`` and §6 REST API
Architecture):

* ``id``, ``number``, ``task_count``, ``auto_fix_count``,
  ``created_at`` and ``updated_at`` are server-managed and therefore
  immutable. ``epic_id`` is an immutable foreign key — a feat belongs
  to exactly one epic for its lifetime.
  :class:`~backend.schemas.feat.FeatUpdate` deliberately omits all
  immutable / server-managed fields.
* ``number`` is auto-assigned by the service layer as
  ``MAX(number) + 1`` for the supplied ``epic_id`` (starts at ``1``
  for the first feat). Concurrent-create races on the same epic
  surface as HTTP 409 via the DB-level ``UNIQUE(epic_id, number)``
  constraint (``uq_feats_epic_id_number``).
* ``status`` is constrained by the ``ck_feats_status`` DB CHECK
  (``todo | in_progress | done | failed``). Invalid values surface at
  schema-validation time (HTTP 422) via the Pydantic ``Literal``.
* List filters (``epic_id``, ``status``) match the indexed columns
  (``ix_feats_epic_id``, ``ix_feats_status``) and back the Tasks UI
  ("show every feat in this epic", "show every in-progress feat") —
  ``GET /epics/{id}/feats`` (DESIGN.md §2.6) maps directly onto
  ``list_feats(epic_id=...)``.
* List ordering (``number ASC``) is owned by the service so feats
  appear in their stable, human-readable numbering sequence (feat 1,
  feat 2, …) matching the ``EpicList`` collapsible UI convention and
  the user-facing ``{epic.number}.{feat.number}`` identifiers.
* Inbound FKs on ``feats`` — ``tasks.feat_id`` (``ON DELETE
  CASCADE``), ``delegations.feat_id`` (``ON DELETE SET NULL``) and
  ``auto_fix_attempts.feat_id`` (``ON DELETE CASCADE``) — are all
  handled at the DB level, so :func:`delete_feat` needs no RESTRICT
  dependency check; dependent rows are either removed or NULL-ed
  automatically on flush.
"""

from __future__ import annotations

import logging
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from backend.db.session import SessionLocal, get_db
from backend.schemas.feat import (
    FeatCreate,
    FeatRead,
    FeatStatus,
    FeatUpdate,
)
from backend.schemas.pagination import PaginatedResponse
from backend.services import feat as feat_service
from backend.services import feat_executor

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Feats"])


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


@router.get("", response_model=PaginatedResponse[FeatRead])
def list_feats(
    epic_id: Optional[UUID] = Query(
        default=None,
        description=(
            "Filter by the epic the feat belongs to. Hits the "
            "``ix_feats_epic_id`` index — the core ``GET "
            "/epics/{id}/feats`` query (DESIGN.md §2.6) and the "
            "``EpicList`` per-epic feat display (DESIGN.md §3.1)."
        ),
    ),
    status_filter: Optional[FeatStatus] = Query(
        default=None,
        alias="status",
        description=(
            "Filter by lifecycle status (``todo`` | ``in_progress`` | "
            "``done`` | ``failed``). Hits the ``ix_feats_status`` index."
        ),
    ),
    skip: int = Query(default=0, ge=0, description="Number of rows to skip."),
    limit: int = Query(default=50, ge=1, le=100, description="Maximum rows to return."),
    db: Session = Depends(get_db),
) -> PaginatedResponse[FeatRead]:
    """Return a paginated list of feats.

    Results are ordered by ``number ASC`` (feat 1, feat 2, …) — owned
    by the service layer, matching the hierarchical-numbering
    convention (DESIGN.md §1.9) and the ``EpicList`` UI.
    """
    try:
        rows = feat_service.list_feats(
            db,
            epic_id=epic_id,
            status=status_filter,
            limit=limit,
            offset=skip,
        )
        total = feat_service.count_feats(
            db,
            epic_id=epic_id,
            status=status_filter,
        )
    except ValueError as exc:
        raise _map_value_error(exc) from exc

    return PaginatedResponse[FeatRead](
        items=[FeatRead.model_validate(row) for row in rows],
        total=total,
        skip=skip,
        limit=limit,
    )


@router.get("/{feat_id}", response_model=FeatRead)
def get_feat(
    feat_id: UUID,
    db: Session = Depends(get_db),
) -> FeatRead:
    """Return a single feat by primary key."""
    try:
        feat = feat_service.get_by_id(db, feat_id)
    except ValueError as exc:
        raise _map_value_error(exc) from exc
    return FeatRead.model_validate(feat)


@router.post(
    "",
    response_model=FeatRead,
    status_code=status.HTTP_201_CREATED,
)
def create_feat(
    payload: FeatCreate,
    db: Session = Depends(get_db),
) -> FeatRead:
    """Create a new feat.

    ``number`` is auto-assigned by the service layer as
    ``MAX(number) + 1`` for the supplied ``epic_id`` (starts at ``1``
    for the first feat in an epic). ``status`` and ``description``
    default to ``todo`` / ``""`` via the Pydantic schema / DB
    ``server_default`` when omitted. ``task_count`` and
    ``auto_fix_count`` are server-managed counters seeded to ``0`` by
    the DB ``server_default`` and are not accepted on input.
    Concurrent-create races on the same epic surface as HTTP 409.
    Missing or invalid ``epic_id`` foreign keys are rejected by the
    DB-level FK and surface as HTTP 422.
    """
    try:
        feat = feat_service.create(db, payload)
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise _map_value_error(exc) from exc
    db.refresh(feat)
    return FeatRead.model_validate(feat)


@router.patch("/{feat_id}", response_model=FeatRead)
def update_feat(
    feat_id: UUID,
    payload: FeatUpdate,
    db: Session = Depends(get_db),
) -> FeatRead:
    """Partially update a feat's mutable fields.

    Only ``title``, ``description``, ``status``, ``estimated_minutes``
    and ``actual_minutes`` are mutable. ``id``, ``epic_id``,
    ``number``, ``task_count``, ``auto_fix_count`` and ``created_at``
    are immutable — the feat identity, its position within the epic
    and the server-managed counters must not be rewritten after the
    fact; ``updated_at`` is refreshed by the ORM on flush via
    ``onupdate=func.now()``. Fields omitted from the payload are left
    unchanged.
    """
    try:
        feat = feat_service.update(db, feat_id, payload)
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise _map_value_error(exc) from exc
    db.refresh(feat)
    return FeatRead.model_validate(feat)


@router.delete(
    "/{feat_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
def delete_feat(
    feat_id: UUID,
    db: Session = Depends(get_db),
) -> Response:
    """Hard-delete a feat by primary key.

    Inbound FKs — ``tasks.feat_id`` (``ON DELETE CASCADE``),
    ``delegations.feat_id`` (``ON DELETE SET NULL``) and
    ``auto_fix_attempts.feat_id`` (``ON DELETE CASCADE``) — are all
    handled at the DB level, so dependent rows are either removed or
    NULL-ed automatically on flush. No RESTRICT dependency check is
    required.
    """
    try:
        feat_service.delete(db, feat_id)
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise _map_value_error(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{feat_id}/execute")
async def execute_feat(feat_id: UUID) -> StreamingResponse:
    """Stream-execute all todo/failed tasks in a feat via Claude CC.

    Streams SSE events::

        data: {"type": "task_start",  "task_id": "...", "task_number": N, "task_title": "..."}
        data: {"type": "chunk",       "text": "...", "task_id": "..."}
        data: {"type": "task_done",   "task_id": "...", "status": "done"|"failed"}
        data: {"type": "feat_done",   "feat_status": "...", "feat_id": "..."}
        data: {"type": "error",       "content": "..."}

    Requires ``Project.source_path`` to be set — CC runs in that directory.
    """
    import json as _json

    async def _sse_generator():
        exec_db = SessionLocal()
        try:
            async for event in feat_executor.execute_feat_stream(feat_id, exec_db):
                yield event
        except Exception as exc:
            logger.exception("Unexpected error in feat execute SSE for feat %s", feat_id)
            yield f"data: {_json.dumps({'type': 'error', 'content': str(exc)})}\n\n"
        finally:
            exec_db.close()

    return StreamingResponse(
        _sse_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
