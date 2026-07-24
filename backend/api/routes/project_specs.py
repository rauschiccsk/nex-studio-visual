"""REST router for ``/api/v1/project-specs/*`` — per-project spec browser.

Provides a filesystem view of ``/opt/projects/<slug>/docs/`` for every
project. The frontend's ``<KbTree />`` component (built for the KB
browser at ``/kb``) consumes the same ``KnowledgeDoc``-shaped response,
so a single page (``/project-specs``) can render the unified tree
without adaptation.

Why this exists (Director observation 2026-05-13):
Designer / Implementer / Auditor agents write spec documents into
``/opt/projects/<slug>/docs/`` (single source of truth), but the
Director had no UI to browse them — only ``ssh`` + ``nano``. This
router closes that gap.

Permissions (v4.0.43):
- Any authenticated user; scoped by project ownership (``backend.core.authz``). A Junior (``shu``) sees +
  reads only the docs of projects they OWN (``/list`` filtered to own slugs; ``/content`` owner-checked);
  ri/ha see all. ``PUT /content`` (edit) is owner-OR-``ri``.

Path safety:
- ``slug`` is validated against the kebab-case regex.
- ``path`` must start with ``docs/`` and contain no ``..`` segments.
- Resolved absolute path must stay within ``/opt/projects/<slug>/``.

Edit semantics:
- ``PUT`` only accepts edits to existing files. Creating new spec
  documents is reserved for agents (Designer / Implementer / Auditor)
  — Director uses ``PUT`` for typo fixes / small corrections.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.core import authz
from backend.core.security import get_current_user, require_shu_or_above
from backend.db.models.foundation import User
from backend.db.models.projects import Project
from backend.db.session import get_db
from backend.schemas.project_specs import (
    ProjectSpecContent,
    ProjectSpecListResponse,
    ProjectSpecUpdate,
)
from backend.services import project_specs as project_specs_service
from backend.services.project_specs import ProjectSpecsError

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Project Specs"])


@router.get("/list", response_model=ProjectSpecListResponse)
def list_project_specs(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ProjectSpecListResponse:
    """List every ``.md`` file under ``/opt/projects/*/docs/``.

    Returns a flat list sorted by ``relative_path``. The frontend wraps
    this into a hierarchical tree via :file:`src/lib/kbTreeBuilder.ts`.

    v4.0.43: a Junior (``shu``) sees only the docs of projects they OWN — the flat list is filtered to
    their own slugs (the ``relative_path`` is ``<slug>/docs/…``); ri/ha see every project's docs.
    """
    docs = project_specs_service.list_all_specs()
    if current_user.role not in authz.PRIVILEGED_ROLES:
        own_slugs = set(db.execute(select(Project.slug).where(Project.created_by == current_user.id)).scalars().all())
        docs = [d for d in docs if d.relative_path.split("/", 1)[0] in own_slugs]
    return ProjectSpecListResponse(documents=docs, count=len(docs))


@router.get("/content", response_model=ProjectSpecContent)
def get_project_spec_content(
    slug: str = Query(..., description="Project slug, e.g. 'nex-inbox'"),
    path: str = Query(
        ...,
        description="Path within the project, e.g. 'docs/specs/customer-requirements.md'",
    ),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ProjectSpecContent:
    """Read a file under ``/opt/projects/<slug>/``.

    Returns text content for whitelisted text extensions (``.md``,
    ``.txt``, ``.csv``, ``.json``, ``.yaml``, source code, etc.); for
    binary files returns ``is_text=False`` + empty content so the
    frontend can render a "cannot display" placeholder. v4.0.43: owner-or-privileged on ``slug``.
    """
    authz.assert_project_slug_access(db, current_user, slug)
    try:
        content, is_text = project_specs_service.read_content(slug, path)
    except ProjectSpecsError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    return ProjectSpecContent(
        relative_path=f"{slug}/{path}",
        content=content,
        is_text=is_text,
    )


@router.put("/content", status_code=status.HTTP_200_OK)
def update_project_spec_content(
    payload: ProjectSpecUpdate,
    slug: str = Query(..., description="Project slug"),
    path: str = Query(..., description="Path within the project"),
    current_user: User = Depends(require_shu_or_above),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    """Overwrite a single ``.md`` file. v4.0.43: owner-OR-``ri`` (was ``ri`` only) — the project owner or
    an ``ri`` may fix a typo; a Medior does not gain edits on projects it doesn't own.

    The file must already exist — this endpoint does **not** create new
    documents. New spec docs are produced by the agents (Designer /
    Implementer / Auditor) directly in the project repo.
    """
    authz.assert_project_slug_access(db, current_user, slug, ri_only=True)
    try:
        project_specs_service.write_content(slug, path, payload.content)
    except ProjectSpecsError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    return {"relative_path": f"{slug}/{path}", "status": "updated"}
