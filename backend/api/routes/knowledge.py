"""Knowledge CRUD API routes — filesystem-based, M1 milestone of feature parity audit.

Ported 1:1 from NEX Command (`backend/api/routes/knowledge.py`) per
Director mandate 2026-05-07. NEX Studio musí mať identický KB system
ako NEX Command. See FEATURE_PARITY_AUDIT.md for the broader plan.

Differences from NEX Command source:

* Auth dependency: ``backend.core.security.get_current_user`` (NEX Studio
  re-exports the same JWT-backed helper that NEX Command uses).
* Mounted at ``/api/v1/knowledge`` (NEX Studio prefix convention).
* RBAC stubs (``_has_full_access``, ``_is_restricted``, ``filter_kb_documents``,
  ``is_path_allowed``) are NO-OPS in M1 — they are filled in M2
  (Shuhari RBAC milestone).
* Qdrant indexing (RAGIndexer auto-ingest after save) is a NO-OP in M1
  — wired up in M3 (RAG search milestone).
* Knowledge proposal workflow (proposal_repo) is OUT of M1 scope.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from backend.core.security import get_current_user, has_full_kb_access
from backend.db.models.foundation import User
from backend.services.knowledge_manager import KnowledgeManager
from backend.utils.kb_access import (
    filter_kb_documents as _filter_documents_by_role,
)
from backend.utils.kb_access import (
    is_path_allowed as _is_path_allowed,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Knowledge"])


# Restricted categories — readable only by users with full KB access
# (``ri`` role). NEX Studio credentials live in their own store
# (/opt/data/nex-studio/credentials/) outside KB root, so this list is
# empty in practice; preserved for parity with NEX Command which uses
# the same ``_is_restricted`` predicate to gate the credentials/ KB dir.
_RESTRICTED_CATEGORIES: frozenset[str] = frozenset({"credentials"})


def _has_full_access(user: User) -> bool:
    """Real M2 implementation: ``ri`` role has full KB access."""
    return has_full_kb_access(user)


def _is_restricted(category: str) -> bool:
    """Real M2 implementation: category is restricted iff in the
    ``_RESTRICTED_CATEGORIES`` set."""
    return category.lower() in _RESTRICTED_CATEGORIES


# --- Request models ---


class CreateDocumentRequest(BaseModel):
    category: str
    filename: str
    content: str


class UpdateDocumentRequest(BaseModel):
    relative_path: str
    content: str


class DeleteDocumentRequest(BaseModel):
    relative_path: str


# --- Helpers ---


def _get_manager() -> KnowledgeManager:
    return KnowledgeManager()


# --- Routes ---


@router.get("/documents")
def list_knowledge_documents(
    category: Optional[str] = Query(None, description="Filter by category"),
    user: User = Depends(get_current_user),
):
    """List knowledge documents on disk."""
    manager = _get_manager()

    try:
        documents = manager.list_documents(category=category)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    if not _has_full_access(user):
        documents = [d for d in documents if not _is_restricted(d.get("category", ""))]

    documents = _filter_documents_by_role(documents, user)

    return {"documents": documents, "count": len(documents)}


@router.get("/documents/content")
def get_document_content(
    relative_path: str = Query(..., description="Relative path to document"),
    user: User = Depends(get_current_user),
):
    """Read raw markdown content from disk."""
    path_category = relative_path.split("/")[0] if "/" in relative_path else ""
    if not _has_full_access(user) and _is_restricted(path_category):
        raise HTTPException(
            status_code=403,
            detail="Tento dokument je dostupný len pre oprávnených používateľov",
        )

    if not _is_path_allowed(relative_path, user):
        raise HTTPException(status_code=403, detail="Prístup zamietnutý na základe Shuhari role")

    manager = _get_manager()

    try:
        content = manager.read_document(relative_path)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail="Document not found") from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    return {
        "relative_path": relative_path,
        "content": content,
    }


@router.get("/categories")
def get_knowledge_categories(
    user: User = Depends(get_current_user),
):
    """List available knowledge categories (dynamic scan of filesystem)."""
    manager = _get_manager()
    categories = manager.get_categories()

    if not _has_full_access(user):
        categories = [c for c in categories if not _is_restricted(c)]

    return {"categories": categories}


@router.post("/documents")
def create_document(
    request: CreateDocumentRequest,
    user: User = Depends(get_current_user),
):
    """Save document to disk."""
    if not _has_full_access(user) and _is_restricted(request.category):
        raise HTTPException(
            status_code=403,
            detail="Túto kategóriu môžu upravovať len oprávnení používatelia",
        )

    manager = _get_manager()

    try:
        relative_path = manager.save_document(
            request.category,
            request.filename,
            request.content,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    # M3 (RAG): wire up Qdrant indexing here.

    return {
        "relative_path": relative_path,
        "filename": request.filename,
        "category": request.category,
        "size_bytes": len(request.content.encode("utf-8")),
    }


@router.put("/documents")
def update_document(
    request: UpdateDocumentRequest,
    user: User = Depends(get_current_user),
):
    """Overwrite existing markdown file content."""
    if not _is_path_allowed(request.relative_path, user):
        raise HTTPException(status_code=403, detail="Prístup zamietnutý na základe Shuhari role")

    path_category = request.relative_path.split("/")[0] if "/" in request.relative_path else ""
    if not _has_full_access(user) and _is_restricted(path_category):
        raise HTTPException(
            status_code=403,
            detail="Tento dokument môžu upravovať len oprávnení používatelia",
        )

    manager = _get_manager()

    try:
        manager.update_document(request.relative_path, request.content)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail="Document not found") from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    # M3 (RAG): re-index here.

    return {
        "relative_path": request.relative_path,
        "size_bytes": len(request.content.encode("utf-8")),
    }


@router.delete("/documents")
def delete_document(
    relative_path: str = Query(..., description="Relative path to document"),
    user: User = Depends(get_current_user),
):
    """Delete markdown file from disk."""
    if not _is_path_allowed(relative_path, user):
        raise HTTPException(status_code=403, detail="Prístup zamietnutý na základe Shuhari role")

    path_category = relative_path.split("/")[0] if "/" in relative_path else ""
    if not _has_full_access(user) and _is_restricted(path_category):
        raise HTTPException(
            status_code=403,
            detail="Tento dokument môžu mazať len oprávnení používatelia",
        )

    manager = _get_manager()

    try:
        deleted = manager.delete_document(relative_path)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    if not deleted:
        raise HTTPException(status_code=404, detail="Document not found")

    # M3 (RAG): Qdrant cleanup here.

    return {"deleted": True, "relative_path": relative_path}
