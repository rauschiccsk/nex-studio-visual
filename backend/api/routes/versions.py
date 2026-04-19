"""REST router for :class:`~backend.db.models.versions.Version`.

Implements DESIGN.md §2.6 *Version Management* — the release-version
container that groups Epics and Bugs targeted at a specific release of a
project. The endpoint set deliberately straddles two URL families
(``/projects/{project_id}/versions`` for project-scoped operations and
``/versions/{version_id}`` for version-scoped operations), so the router
is mounted with the bare ``/api/v1`` prefix in :mod:`backend.main` rather
than a single resource prefix:

* ``GET    /projects/{project_id}/versions`` → list every version of a
  project, ordered by ``version_number DESC`` (DESIGN.md §2.6 ``GET
  /projects/{id}/versions``). Each row carries the
  ``epic_count`` / ``epics_done`` / ``bug_count`` aggregates that drive
  the ``VersionsPage`` UI cards. Authenticated users only.
* ``POST   /projects/{project_id}/versions`` → create a new version.
  Status defaults to ``planned`` (DESIGN.md §4.0 Rule 1). ``ri`` role
  only (DESIGN.md §2.6 ``POST /projects/{id}/versions``).
* ``GET    /versions/{version_id}`` → version detail with all EPICs and
  BUGs eagerly loaded for the ``VersionDetailPage`` UI. Authenticated
  users only.
* ``PATCH  /versions/{version_id}`` → partial update of mutable fields.
  ``ri`` role only. Note: the gated ``planned → released`` transition
  goes through :func:`release_version`, **not** through PATCH —
  status-only patches are reserved for backfill / correction flows
  (DESIGN.md §4.0 Rule 5).
* ``POST   /versions/{version_id}/release`` → the DESIGN.md §4.0 Rule 5
  release gate. Sets ``status = 'released'`` and ``release_date = today``
  on success, returns HTTP 422 with the list of blocking EPIC IDs when
  one or more EPICs are still ``planned`` / ``in_progress``. ``ri`` role
  only.

All endpoints are synchronous ``def`` — pg8000 is a synchronous driver
and FastAPI dispatches sync endpoints to a thread pool automatically.
The router delegates every persistence operation to
:mod:`backend.services.version` and handles ``commit`` / ``rollback``
itself so the service layer stays transaction-agnostic.

The router is *prefix-less*; the mount prefix (``/api/v1``) is applied
in ``backend/main.py`` via ``app.include_router(versions_router,
prefix="/api/v1")``.
"""

from __future__ import annotations

import logging
import re
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.core.security import get_current_user, require_ri_role
from backend.db.models.foundation import User
from backend.db.session import SessionLocal, get_db
from backend.schemas.version import VersionCreate, VersionRead, VersionUpdate
from backend.services import task_plan_generator
from backend.services import version as version_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Versions"])

# Match the canonical ``[<uuid>, <uuid>, ...]`` payload that
# :func:`backend.services.version.release` embeds in its blocking-EPICs
# ``ValueError`` message. Keeps the router free of direct DB queries
# while still surfacing structured IDs in the 422 response.
_BLOCKING_IDS_RE = re.compile(r"\[([^\]]*)\]")


def _map_value_error(exc: ValueError) -> HTTPException:
    """Translate a service-layer ``ValueError`` into an HTTP exception.

    Mirrors the ICC error-handling pattern: ``not found`` → 404,
    duplicates / conflicts → 409, everything else (constraint / FK /
    validation failures) → 422. Used by every endpoint *except*
    :func:`release_version`, which has bespoke handling for the blocking
    -EPICs case (HTTP 422 with structured payload).
    """
    message = str(exc)
    lowered = message.lower()
    if "not found" in lowered:
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=message)
    if "already exists" in lowered or "duplicate" in lowered or "conflict" in lowered:
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=message)
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=message)


def _parse_blocking_ids(message: str) -> list[str]:
    """Extract the list of blocking EPIC ids embedded in a ``release`` ValueError.

    The service formats the message as ``"... blocking EPICs (status !=
    'done'): [<uuid1>, <uuid2>, ...]"`` (see
    :func:`backend.services.version.release`). This helper isolates the
    bracketed segment so the router can return both the human-readable
    message *and* a machine-readable id list in the 422 detail payload.
    """
    match = _BLOCKING_IDS_RE.search(message)
    if not match:
        return []
    return [part.strip() for part in match.group(1).split(",") if part.strip()]


# ---------------------------------------------------------------------------
# Project-scoped endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/projects/{project_id}/versions",
    response_model=list[VersionRead],
)
def list_versions(
    project_id: UUID,
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_user),
) -> list[VersionRead]:
    """Return every version belonging to ``project_id``.

    Results are ordered by ``version_number DESC`` (most recent release
    first) and each row carries the ``epic_count`` / ``epics_done`` /
    ``bug_count`` aggregates the ``VersionsPage`` UI cards consume —
    see DESIGN.md §2.6 ``GET /projects/{id}/versions``.

    No pagination envelope: the version list per project is bounded by
    business reality (single-digit to low-double-digit counts) and the
    UI renders the entire collection at once.
    """
    rows = version_service.list_versions(db, project_id)
    return [VersionRead.model_validate(row) for row in rows]


@router.post(
    "/projects/{project_id}/versions",
    response_model=VersionRead,
    status_code=status.HTTP_201_CREATED,
)
def create_version(
    project_id: UUID,
    payload: VersionCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_ri_role),
) -> VersionRead:
    """Create a new version for ``project_id``.

    ``status`` defaults to ``planned`` via the DB ``server_default``
    (DESIGN.md §4.0 Rule 1) — callers cannot override it here.
    Concurrent creates that race on the same ``(project_id,
    version_number)`` pair surface as HTTP 409 via the unique-constraint
    pre-check in :func:`backend.services.version.create`.

    Restricted to users with role ``ri`` (DESIGN.md §2.6 ``POST
    /projects/{id}/versions``).
    """
    try:
        version = version_service.create(db, project_id, payload, current_user.id)
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise _map_value_error(exc) from exc
    db.refresh(version)
    return VersionRead.model_validate(version)


# ---------------------------------------------------------------------------
# Version-scoped endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/versions/{version_id}",
    response_model=VersionRead,
)
def get_version(
    version_id: UUID,
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_user),
) -> VersionRead:
    """Return a single version by primary key.

    The service eagerly loads ``epics`` and ``bugs`` via ``selectinload``
    so the ``VersionDetailPage`` UI can render the EPIC / BUG groups
    without an N+1 round-trip — see DESIGN.md §2.6 ``GET /versions/{id}``.
    """
    try:
        version = version_service.get_by_id(db, version_id)
    except ValueError as exc:
        raise _map_value_error(exc) from exc
    return VersionRead.model_validate(version)


@router.patch(
    "/versions/{version_id}",
    response_model=VersionRead,
)
def update_version(
    version_id: UUID,
    payload: VersionUpdate,
    db: Session = Depends(get_db),
    _current_user: User = Depends(require_ri_role),
) -> VersionRead:
    """Partially update a version's mutable fields.

    Allowed fields: ``version_number``, ``name``, ``status``,
    ``description``, ``target_date``, ``release_date``. ``id``,
    ``project_id`` and ``created_at`` are immutable; ``updated_at`` is
    refreshed by the ORM ``onupdate=func.now()`` trigger.

    .. note::

       This endpoint is **not** the gated release transition. Patching
       ``status = 'released'`` here bypasses the release gate (DESIGN.md
       §4.0 Rule 5) and is reserved for backfill / correction flows;
       production callers must use :func:`release_version`.
    """
    try:
        version = version_service.update(db, version_id, payload)
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise _map_value_error(exc) from exc
    db.refresh(version)
    return VersionRead.model_validate(version)


@router.delete(
    "/versions/{version_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_version(
    version_id: UUID,
    db: Session = Depends(get_db),
    _current_user: User = Depends(require_ri_role),
) -> Response:
    """Permanently delete a version.

    Only allowed when the version has no EPICs (Task Plan is empty) and
    its status is not ``released``. Released versions are permanent
    artefacts of project history and may not be deleted.

    Error mapping:

    * **404** — the version does not exist.
    * **409** — the version is ``released``, or it still has one or more
      EPICs attached (Task Plan not empty).

    Restricted to users with role ``ri``.
    """
    try:
        version_service.delete(db, version_id)
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise _map_value_error(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


class _GenerateTaskPlanRequest(BaseModel):
    """Request body for POST /versions/{version_id}/generate-task-plan."""

    replace_existing: bool = False
    """When True, all existing EPICs under this version are deleted before
    generating the new plan. Defaults to False (append / new plan)."""


@router.post(
    "/versions/{version_id}/generate-task-plan",
    status_code=status.HTTP_200_OK,
)
async def generate_task_plan(
    version_id: UUID,
    payload: _GenerateTaskPlanRequest = _GenerateTaskPlanRequest(),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_ri_role),
):
    """Stream-generate a VERSION → EPIC → FEAT → TASK plan from the project's DESIGN.md.

    Reads the latest DESIGN.md (and BEHAVIOR.md if available) stored in the
    ``design_documents`` table for the parent project, calls Claude CLI to
    generate the plan JSON, persists the resulting ``epics`` / ``feats`` /
    ``tasks`` records, and streams SSE progress events::

        data: {"type": "progress", "message": "...", "percent": N}
        data: {"type": "done", "plan": [...], "epic_count": N, "feat_count": N, "task_count": N}
        data: {"type": "error", "content": "..."}
        data: {"type": "validation_error", "content": "..."}

    ``ri`` role only.
    """
    try:
        version = version_service.get_by_id(db, version_id)
    except ValueError as exc:
        raise _map_value_error(exc) from exc

    project_id = version.project_id

    async def _sse_generator():
        gen_db = SessionLocal()
        try:
            async for event in task_plan_generator.generate_task_plan_stream(
                version_id=version_id,
                project_id=project_id,
                db=gen_db,
                replace_existing=payload.replace_existing,
            ):
                yield event
        except Exception as exc:
            import json as _json

            logger.exception("Unexpected error in task plan SSE generator for version %s", version_id)
            yield f"data: {_json.dumps({'type': 'error', 'content': str(exc)})}\n\n"
        finally:
            gen_db.close()

    return StreamingResponse(
        _sse_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post(
    "/versions/{version_id}/release",
    response_model=VersionRead,
)
def release_version(
    version_id: UUID,
    db: Session = Depends(get_db),
    _current_user: User = Depends(require_ri_role),
) -> VersionRead:
    """Release the version — the DESIGN.md §4.0 Rule 5 release gate.

    Delegates to :func:`backend.services.version.release`, which checks
    that every EPIC assigned to the version has ``status='done'``. On
    success: ``status`` becomes ``released``, ``release_date`` is stamped
    to ``date.today()`` and the updated row is returned.

    Error mapping:

    * **404** — the version does not exist.
    * **409** — the version is already in the ``released`` state
      (forward-only transition guard; DESIGN.md §4.0 Rule 3).
    * **422** — one or more EPICs are still ``planned`` /
      ``in_progress``. The detail payload is structured as ``{"message":
      "...", "blocking_epic_ids": ["<uuid>", ...]}`` so the
      ``VersionsPage`` UI can render the blockers inline.

    Restricted to users with role ``ri`` (DESIGN.md §2.6 ``POST
    /versions/{id}/release``).
    """
    try:
        version = version_service.release(db, version_id)
        db.commit()
    except ValueError as exc:
        db.rollback()
        message = str(exc)
        lowered = message.lower()
        if "not found" in lowered:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=message,
            ) from exc
        if "already released" in lowered:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=message,
            ) from exc
        # Blocking-EPICs path — return a structured 422 so clients can
        # render the blockers without re-parsing the human message.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "message": message,
                "blocking_epic_ids": _parse_blocking_ids(message),
            },
        ) from exc
    db.refresh(version)
    return VersionRead.model_validate(version)
