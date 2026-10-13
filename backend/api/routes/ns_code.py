"""DEV-86 — NS Code: ``/api/v1/projects/{slug}/code/…`` — the files of the selected project, read-only, no secrets."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.config.settings import settings
from backend.core import authz
from backend.core.security import get_current_user
from backend.db.models.foundation import User
from backend.db.session import get_db
from backend.schemas.ns_code import CodeFileRead, CodeFilesRead, CodeHit, CodeSearchRead
from backend.services import claude_agent, ns_code

router = APIRouter(tags=["NS Code"])


def _root(db: Session, user: User, slug: str):
    project = authz.assert_project_slug_access(db, user, slug)
    return claude_agent.PROJECTS_ROOT / project.slug


@router.get("/projects/{slug}/code/files", response_model=CodeFilesRead)
def get_code_files(
    slug: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
) -> CodeFilesRead:
    """Every file of the project NS Code shows: what git knows or would add, without secrets."""
    root = _root(db, current_user, slug)
    try:
        return CodeFilesRead(head=ns_code.head(root), files=ns_code.files(root))
    except ns_code.NoCheckout as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/projects/{slug}/code/file", response_model=CodeFileRead)
def get_code_file(
    slug: str,
    path: str = Query(..., min_length=1, max_length=1000),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CodeFileRead:
    """One file — only one the list shows; anything else (outside the project, ignored, a secret) is 404."""
    root = _root(db, current_user, slug)
    try:
        view = ns_code.read(root, path)
    except ns_code.NotServed as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ns_code.NoCheckout as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return CodeFileRead(
        path=view.path,
        size=view.size,
        text=view.text,
        binary=view.binary,
        too_big=view.too_big,
        max_bytes=settings.ns_code_max_file_bytes,
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
