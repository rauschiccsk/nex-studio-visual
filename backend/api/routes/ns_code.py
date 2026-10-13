"""DEV-86 — NS Code: ``/api/v1/projects/{slug}/code/…`` — the files of the selected project, read-only, no secrets."""

from __future__ import annotations

import uuid
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.config.settings import settings
from backend.core import authz
from backend.core.security import get_current_user
from backend.db.models.foundation import User
from backend.db.models.versions import Version
from backend.db.session import get_db
from backend.schemas.ci_runs import CiRunTask
from backend.schemas.ns_code import (
    CodeFileRead,
    CodeFilesRead,
    CodeHit,
    CodeLastCommit,
    CodeOriginalRead,
    CodeSearchRead,
)
from backend.services import ci_runs, claude_agent, delivery_statement, ns_code

router = APIRouter(tags=["NS Code"])


def _root(db: Session, user: User, slug: str):
    project = authz.assert_project_slug_access(db, user, slug)
    return claude_agent.PROJECTS_ROOT / project.slug


#: DEV-87 — said when the start of the version cannot be found in git.
NO_BASE = "Začiatok tejto verzie sa v gite nedá určiť — porovnaj s posledným commitom."


@router.get("/projects/{slug}/code/files", response_model=CodeFilesRead)
def get_code_files(
    slug: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
) -> CodeFilesRead:
    """Every file of the project NS Code shows: what git knows or would add, without secrets."""
    root = _root(db, current_user, slug)
    try:
        found = ns_code.listing(root)
    except ns_code.NoCheckout as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return CodeFilesRead(
        head=found.head,
        files=found.files,
        changes=found.changes,
        stamps=found.stamps,
        refresh_seconds=settings.ns_code_refresh_seconds,
    )


@router.get("/projects/{slug}/code/file", response_model=CodeFileRead)
def get_code_file(
    slug: str,
    path: str = Query(..., min_length=1, max_length=1000),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CodeFileRead:
    """One file — only one the list shows; anything else (outside the project, ignored, a secret) is 404. DEV-87: with
    the newest commit that changed it and that commit's task."""
    project = authz.assert_project_slug_access(db, current_user, slug)
    root = claude_agent.PROJECTS_ROOT / project.slug
    try:
        view = ns_code.read(root, path)
    except ns_code.NotServed as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ns_code.NoCheckout as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    last = ns_code.last_commit(root, path)
    last_read = None
    if last is not None:
        label = ci_runs.task_label(last.subject)
        link = ci_runs.task_links(db, project.id, {label}).get(label) if label else None
        last_read = CodeLastCommit(
            sha=last.sha, at=last.at, subject=last.subject, task=CiRunTask(**vars(link)) if link else None
        )
    return CodeFileRead(
        path=view.path,
        size=view.size,
        text=view.text,
        binary=view.binary,
        too_big=view.too_big,
        max_bytes=settings.ns_code_max_file_bytes,
        stamp=view.stamp,
        last_commit=last_read,
    )


@router.get("/projects/{slug}/code/original", response_model=CodeOriginalRead)
def get_code_original(
    slug: str,
    path: str = Query(..., min_length=1, max_length=1000),
    against: Literal["head", "base"] = "head",
    version_id: Optional[uuid.UUID] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CodeOriginalRead:
    """DEV-87 — the file as it was: at the last commit (``head``), or where ``version_id`` began (``base`` — the same
    start the delivery statement counts from). Only for a file NS Code shows."""
    project = authz.assert_project_slug_access(db, current_user, slug)
    root = claude_agent.PROJECTS_ROOT / project.slug
    try:
        current = ns_code.head(root)
        if against == "base":
            version = db.get(Version, version_id) if version_id else None
            if version is None or version.project_id != project.id or not current:
                raise HTTPException(status_code=404, detail="Verzia sa v tomto projekte nenašla.")
            rev = delivery_statement.base_commit(db, version, root, current)
            if not rev:
                raise HTTPException(status_code=409, detail=NO_BASE)
        else:
            rev = current
        if not rev:
            raise HTTPException(status_code=409, detail=ns_code.NO_CHECKOUT)
        view = ns_code.original(root, path, rev)
    except ns_code.NotServed as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ns_code.NoCheckout as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if view is None:
        return CodeOriginalRead(path=path, against=against, rev=rev, existed=False)
    return CodeOriginalRead(
        path=path,
        against=against,
        rev=rev,
        existed=True,
        text=view.text,
        binary=view.binary,
        too_big=view.too_big,
    )


@router.get("/projects/{slug}/code/search", response_model=CodeSearchRead)
def search_code(
    slug: str,
    q: str = Query(..., max_length=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CodeSearchRead:
    """Lines with the text (any case) in the files NS Code shows."""
    root = _root(db, current_user, slug)
    try:
        found = ns_code.search(root, q)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ns_code.NoCheckout as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return CodeSearchRead(
        query=found.query,
        hits=[CodeHit(path=h.path, line=h.line, text=h.text) for h in found.hits],
        truncated=found.truncated,
    )
