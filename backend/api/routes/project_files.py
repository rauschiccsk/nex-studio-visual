"""Files the Manažér hands the AI Agent from the Riadiace centrum (DEV-44).

``GET`` lists ``<project>/private/``, ``POST`` stores an attached file there, ``DELETE`` removes one. The routes
hang under the build (``/pipeline/{version_id}/files``) because that is where the Manažér works and what the
build history records; the folder itself is the project's, shared by its versions. Who may drive the build may
attach and delete — :func:`authz.assert_version_access`, the same door as the board.

Every upload and delete leaves a line in the build history (who, which file, how big — never the content) and
is broadcast to the open screens like any other message. That line is also where the list reads who attached a
file and when.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.config.settings import settings
from backend.core import authz
from backend.core.offload import BlockingWorkTimedOut, run_blocking
from backend.core.security import require_shu_or_above
from backend.db.models.foundation import User
from backend.db.models.pipeline import PipelineMessage, PipelineState
from backend.db.models.versions import Version
from backend.db.session import get_db
from backend.schemas.pipeline import PipelineMessageRead
from backend.schemas.project_files import PrivateFileRead, PrivateFilesRead, PrivateFileUploaded
from backend.services import claude_agent, orchestrator, project_files
from backend.services.pipeline_ws import registry

router = APIRouter(tags=["Pipeline"])

#: Storing a file is a local write plus one ``git check-ignore`` (itself capped) — generous.
FILE_WORK_CAP_SECONDS = 60


def _project_root(db: Session, version_id: uuid.UUID):
    return claude_agent.PROJECTS_ROOT / orchestrator._project_slug_for_version(db, version_id)


def _history(db: Session, version_id: uuid.UUID) -> dict[str, tuple[str, datetime]]:
    """``path → (who, when)`` of the latest upload of each file across the project's versions."""
    project_id = db.execute(select(Version.project_id).where(Version.id == version_id)).scalar_one()
    rows = db.execute(
        select(PipelineMessage.payload, PipelineMessage.created_at)
        .join(Version, Version.id == PipelineMessage.version_id)
        .where(Version.project_id == project_id, PipelineMessage.payload.has_key("private_file"))
        # ``seq``, not ``created_at``: the latter is the START of the writing transaction (PostgreSQL ``now()``),
        # so a delete written after an upload can carry the earlier time; ``seq`` is the order things were written.
        .order_by(PipelineMessage.seq)
    ).all()
    seen: dict[str, tuple[str, datetime]] = {}
    for payload, created_at in rows:
        entry = (payload or {}).get("private_file") or {}
        path = str(entry.get("path") or "")
        if entry.get("action") == "uploaded":
            seen[path] = (str(entry.get("by") or ""), created_at)
        elif entry.get("action") == "deleted":
            seen.pop(path, None)
    return seen


def _read(f: project_files.PrivateFile, history: dict[str, tuple[str, datetime]]) -> PrivateFileRead:
    who, when = history.get(f.path, (None, None))
    return PrivateFileRead(
        path=f.path,
        size_bytes=f.size_bytes,
        size_label=project_files.human_size(f.size_bytes),
        modified_at=f.modified_at,
        uploaded_by=who,
        uploaded_at=when,
    )


def _listing(db: Session, version_id: uuid.UUID, files: list[project_files.PrivateFile]) -> dict[str, Any]:
    history = _history(db, version_id)
    return {
        "files": [_read(f, history) for f in files],
        "max_bytes": settings.private_file_max_bytes,
        "max_label": project_files.human_size(settings.private_file_max_bytes),
    }


async def _record(db: Session, version_id: uuid.UUID, content: str, entry: dict[str, Any]) -> None:
    stage = db.execute(select(PipelineState.current_stage).where(PipelineState.version_id == version_id)).scalar()
    msg = orchestrator._record_message(
        db,
        version_id=version_id,
        stage=stage or "priprava",
        author="system",
        recipient="manazer",
        kind="notification",
        content=content,
        payload={"private_file": entry},
    )
    db.commit()
    await registry.broadcast(
        version_id,
        {"type": "message_added", "message": PipelineMessageRead.model_validate(msg).model_dump(mode="json")},
    )


async def _offload(fn, *args: Any, **kwargs: Any):
    try:
        return await run_blocking(fn, *args, cap=FILE_WORK_CAP_SECONDS, **kwargs)
    except project_files.PrivateFileRefused as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except BlockingWorkTimedOut as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Práca so súborom neskončila včas — skús to znova o chvíľu.",
        ) from exc


@router.get("/{version_id}/files", response_model=PrivateFilesRead)
async def list_private_files(
    version_id: uuid.UUID,
    current_user: User = Depends(require_shu_or_above),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """What ``private/`` of the project holds — also the files the agent put there itself."""
    authz.assert_version_access(db, current_user, version_id)
    files = await _offload(project_files.list_files, _project_root(db, version_id))
    return _listing(db, version_id, files)


@router.post("/{version_id}/files", response_model=PrivateFileUploaded)
async def upload_private_file(
    version_id: uuid.UUID,
    file: UploadFile,
    current_user: User = Depends(require_shu_or_above),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Store the attached file in ``private/`` and return the line for the Manažér's message."""
    authz.assert_version_access(db, current_user, version_id)
    limit = settings.private_file_max_bytes
    content = await file.read(limit + 1)
    root = _project_root(db, version_id)
    stored = await _offload(project_files.save, root, file.filename or "", content, max_bytes=limit)
    size = project_files.human_size(stored.size_bytes)
    await _record(
        db,
        version_id,
        f"{current_user.username} priložil súbor {stored.path} ({size}) — leží v projekte mimo gitu.",
        {"action": "uploaded", "path": stored.path, "size_bytes": stored.size_bytes, "by": current_user.username},
    )
    files = await _offload(project_files.list_files, root)
    listing = _listing(db, version_id, files)
    mine = next(f for f in listing["files"] if f.path == stored.path)
    return {**listing, "file": mine, "answer_line": project_files.answer_line(stored)}


@router.delete("/{version_id}/files", response_model=PrivateFilesRead)
async def delete_private_file(
    version_id: uuid.UUID,
    path: str = Query(..., min_length=1, max_length=512),
    current_user: User = Depends(require_shu_or_above),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Remove one file from ``private/`` and record who did it."""
    authz.assert_version_access(db, current_user, version_id)
    root = _project_root(db, version_id)
    removed: Optional[project_files.PrivateFile] = await _offload(project_files.delete, root, path)
    await _record(
        db,
        version_id,
        f"{current_user.username} zmazal súbor {removed.path} z priečinka projektu.",
        {"action": "deleted", "path": removed.path, "by": current_user.username},
    )
    files = await _offload(project_files.list_files, root)
    return _listing(db, version_id, files)
