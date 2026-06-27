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

import re
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.core.security import get_current_user, require_ri_role
from backend.db.models.foundation import User
from backend.db.models.tasks import Epic, Feat, Task
from backend.db.session import get_db
from backend.schemas.version import VersionCreate, VersionRead, VersionUpdate
from backend.services import version as version_service

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


class _TaskPlanResponse(BaseModel):
    """Response for GET /versions/{version_id}/task-plan."""

    plan: list[dict]
    epic_count: int
    feat_count: int
    task_count: int


@router.get(
    "/versions/{version_id}/task-plan",
    response_model=_TaskPlanResponse,
)
def get_task_plan(
    version_id: UUID,
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_user),
) -> _TaskPlanResponse:
    """Return the existing task plan (EPICs → Feats → Tasks) for a version.

    Returns an empty plan (``epic_count=0``) when no EPICs exist yet.
    """
    epics = db.execute(select(Epic).where(Epic.version_id == version_id).order_by(Epic.number)).scalars().all()

    if not epics:
        return _TaskPlanResponse(plan=[], epic_count=0, feat_count=0, task_count=0)

    epic_ids = [e.id for e in epics]
    feats = (
        db.execute(select(Feat).where(Feat.epic_id.in_(epic_ids)).order_by(Feat.epic_id, Feat.number)).scalars().all()
    )

    feat_ids = [f.id for f in feats]
    tasks_all = (
        db.execute(select(Task).where(Task.feat_id.in_(feat_ids)).order_by(Task.feat_id, Task.number)).scalars().all()
        if feat_ids
        else []
    )

    tasks_by_feat: dict[str, list[dict]] = {}
    for t in tasks_all:
        tasks_by_feat.setdefault(str(t.feat_id), []).append(
            {
                "id": str(t.id),
                "number": t.number,
                "title": t.title,
                "task_type": t.task_type,
                "status": t.status,
                "priority": t.priority,
                "checklist_type": t.checklist_type,
                "description": t.description,
            }
        )

    feats_by_epic: dict[str, list[dict]] = {}
    for f in feats:
        feats_by_epic.setdefault(str(f.epic_id), []).append(
            {
                "id": str(f.id),
                "number": f.number,
                "title": f.title,
                "status": f.status,
                "tasks": tasks_by_feat.get(str(f.id), []),
            }
        )

    plan = [
        {
            "id": str(e.id),
            "number": e.number,
            "title": e.title,
            "status": e.status,
            "feats": feats_by_epic.get(str(e.id), []),
        }
        for e in epics
    ]

    return _TaskPlanResponse(
        plan=plan,
        epic_count=len(epics),
        feat_count=len(feats),
        task_count=len(tasks_all),
    )


class _ZadanieWrite(BaseModel):
    """Request body for ``PUT /versions/{version_id}/zadanie``."""

    content: str


class _ZadanieWriteResponse(BaseModel):
    """Response for ``PUT /versions/{version_id}/zadanie``."""

    relative_path: str
    status: str


@router.put(
    "/versions/{version_id}/zadanie",
    response_model=_ZadanieWriteResponse,
)
def write_zadanie(
    version_id: UUID,
    payload: _ZadanieWrite,
    db: Session = Depends(get_db),
    _current_user: User = Depends(require_ri_role),
) -> _ZadanieWriteResponse:
    """Save a version's free-text Zadanie to ``customer-requirements.md`` (CR-V2-024, design §4.3).

    The New-Version flow saves the brief here on "Uložiť Zadanie"; the Príprava phase (CR-V2-010)
    reads exactly this file when the build starts. Create-or-overwrite — the version's spec
    directory is created if it does not yet exist. ``ri`` role only.

    * **404** — the version (or its project) does not exist.
    """
    try:
        rel = version_service.write_zadanie(db, version_id, payload.content)
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise _map_value_error(exc) from exc
    return _ZadanieWriteResponse(relative_path=rel, status="saved")


@router.post("/versions/{version_id}/reset-tasks", status_code=status.HTTP_200_OK)
def reset_tasks(
    version_id: UUID,
    db: Session = Depends(get_db),
    _current_user: User = Depends(require_ri_role),
) -> dict:
    """Reset all tasks in a version back to ``todo`` status.

    Sets every Task under every Epic of this version to ``todo``, and
    recomputes Feat / Epic statuses accordingly. Does not delete any records.
    ``ri`` role only.
    """
    epics = db.execute(select(Epic).where(Epic.version_id == version_id)).scalars().all()
    epic_ids = [e.id for e in epics]
    if not epic_ids:
        return {"reset": 0}

    feats = db.execute(select(Feat).where(Feat.epic_id.in_(epic_ids))).scalars().all()
    feat_ids = [f.id for f in feats]

    task_count = 0
    if feat_ids:
        tasks = db.execute(select(Task).where(Task.feat_id.in_(feat_ids))).scalars().all()
        for t in tasks:
            t.status = "todo"
            task_count += 1
        db.flush()

    for f in feats:
        f.status = "todo"
    for e in epics:
        e.status = "planned"
    db.commit()

    return {"reset": task_count}


@router.post("/versions/{version_id}/reset-plan", status_code=status.HTTP_200_OK)
def reset_plan(
    version_id: UUID,
    db: Session = Depends(get_db),
    _current_user: User = Depends(require_ri_role),
) -> dict:
    """Delete the entire task plan for a version (all EPICs, Feats and Tasks).

    Hard-deletes every Epic under this version. Feats and Tasks are removed
    via ``ON DELETE CASCADE`` at the DB level. ``ri`` role only.
    """
    epics = db.execute(select(Epic).where(Epic.version_id == version_id)).scalars().all()
    count = len(epics)
    for e in epics:
        db.delete(e)
    db.commit()
    return {"deleted_epics": count}


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
