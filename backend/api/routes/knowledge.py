"""Knowledge CRUD API routes — filesystem-based, M1+M3 milestones of feature parity audit.

Ported 1:1 from NEX Command (`backend/api/routes/knowledge.py`) per
Director mandate 2026-05-07. NEX Studio musí mať identický KB system
ako NEX Command. See FEATURE_PARITY_AUDIT.md for the broader plan.

Differences from NEX Command source:

* Auth dependency: ``backend.core.security.get_current_user`` (NEX Studio
  re-exports the same JWT-backed helper that NEX Command uses).
* Mounted at ``/api/v1/knowledge`` (NEX Studio prefix convention).
* M2 (Shuhari RBAC): :func:`_has_full_access`, :func:`_is_restricted`,
  :func:`filter_kb_documents`, :func:`is_path_allowed` are real
  implementations — see :mod:`backend.utils.kb_access`.
* M3 (RAG indexing): POST / PUT / DELETE now auto-ingest into Qdrant
  via :class:`backend.rag.indexer.RAGIndexer`. Failures degrade
  gracefully — disk write is the source of truth, Qdrant errors
  return a ``warning`` field instead of failing the request.
* Knowledge proposal workflow (proposal_repo) is OUT of scope for the
  feature parity audit.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.core.security import get_current_user, has_full_kb_access
from backend.db.models.foundation import User
from backend.db.session import get_db
from backend.rag.indexer import RAGIndexer
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
    tenant: str = "icc"
    # Optional — when set, server logs a warning if ``category`` does
    # not start with ``projects/<slug>``. Helps catch UI bugs that
    # drop a project-scoped doc into the wrong KB tree.
    project_slug: Optional[str] = None


class UpdateDocumentRequest(BaseModel):
    relative_path: str
    content: str
    tenant: str = "icc"


class DeleteDocumentRequest(BaseModel):
    relative_path: str


# --- Helpers ---


def _get_manager() -> KnowledgeManager:
    return KnowledgeManager()


def _get_indexer() -> RAGIndexer:
    return RAGIndexer()


# --- Routes ---


@router.get("/documents")
def list_knowledge_documents(
    category: Optional[str] = Query(None, description="Filter by category"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List knowledge documents on disk."""
    manager = _get_manager()

    try:
        documents = manager.list_documents(category=category)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    if not _has_full_access(user):
        documents = [d for d in documents if not _is_restricted(d.get("category", ""))]

    documents = _filter_documents_by_role(documents, user, db)

    return {"documents": documents, "count": len(documents)}


@router.get("/documents/content")
def get_document_content(
    relative_path: str = Query(..., description="Relative path to document"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Read raw markdown content from disk."""
    path_category = relative_path.split("/")[0] if "/" in relative_path else ""
    if not _has_full_access(user) and _is_restricted(path_category):
        raise HTTPException(
            status_code=403,
            detail="Tento dokument je dostupný len pre oprávnených používateľov",
        )

    if not _is_path_allowed(relative_path, user, db):
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
async def create_document(
    request: CreateDocumentRequest,
    user: User = Depends(get_current_user),
):
    """Save document to disk + auto-ingest into Qdrant."""
    if not _has_full_access(user) and _is_restricted(request.category):
        raise HTTPException(
            status_code=403,
            detail="Túto kategóriu môžu upravovať len oprávnení používatelia",
        )

    manager = _get_manager()
    indexer = _get_indexer()

    try:
        relative_path = manager.save_document(
            request.category,
            request.filename,
            request.content,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    if request.project_slug:
        expected_prefix = f"projects/{request.project_slug}"
        if not request.category.startswith(expected_prefix):
            logger.warning(
                f"KB category mismatch: project_slug='{request.project_slug}' "
                f"but category='{request.category}' (expected prefix '{expected_prefix}')"
            )

    try:
        result = await indexer.index_document(
            file_path=relative_path,
            tenant=request.tenant,
            content=request.content,
        )
    except Exception as e:
        logger.error(f"Qdrant ingest failed for {relative_path}: {e}")
        return {
            "relative_path": relative_path,
            "filename": request.filename,
            "category": request.category,
            "size_bytes": len(request.content.encode("utf-8")),
            "chunks": 0,
            "tenant": request.tenant,
            "warning": f"Saved to disk but Qdrant ingest failed: {e}",
        }

    return {
        "relative_path": relative_path,
        "filename": request.filename,
        "category": request.category,
        "size_bytes": len(request.content.encode("utf-8")),
        "chunks": result["chunks"],
        "tenant": request.tenant,
    }


@router.put("/documents")
async def update_document(
    request: UpdateDocumentRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Overwrite existing markdown file content + reindex in Qdrant."""
    if not _is_path_allowed(request.relative_path, user, db):
        raise HTTPException(status_code=403, detail="Prístup zamietnutý na základe Shuhari role")

    path_category = request.relative_path.split("/")[0] if "/" in request.relative_path else ""
    if not _has_full_access(user) and _is_restricted(path_category):
        raise HTTPException(
            status_code=403,
            detail="Tento dokument môžu upravovať len oprávnení používatelia",
        )

    manager = _get_manager()
    indexer = _get_indexer()

    try:
        manager.update_document(request.relative_path, request.content)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail="Document not found") from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    try:
        result = await indexer.reindex_document(
            file_path=request.relative_path,
            tenant=request.tenant,
            source_file=request.relative_path,
            content=request.content,
        )
    except Exception as e:
        logger.error(f"Qdrant reindex failed for {request.relative_path}: {e}")
        return {
            "relative_path": request.relative_path,
            "size_bytes": len(request.content.encode("utf-8")),
            "chunks": 0,
            "tenant": request.tenant,
            "warning": f"Updated on disk but Qdrant reindex failed: {e}",
        }

    return {
        "relative_path": request.relative_path,
        "size_bytes": len(request.content.encode("utf-8")),
        "chunks": result["chunks"],
        "tenant": request.tenant,
    }


@router.delete("/documents")
async def delete_document(
    relative_path: str = Query(..., description="Relative path to document"),
    tenant: str = Query("icc", description="Qdrant tenant collection"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Delete markdown file from disk + remove chunks from Qdrant."""
    if not _is_path_allowed(relative_path, user, db):
        raise HTTPException(status_code=403, detail="Prístup zamietnutý na základe Shuhari role")

    path_category = relative_path.split("/")[0] if "/" in relative_path else ""
    if not _has_full_access(user) and _is_restricted(path_category):
        raise HTTPException(
            status_code=403,
            detail="Tento dokument môžu mazať len oprávnení používatelia",
        )

    manager = _get_manager()
    indexer = _get_indexer()

    logger.info(f"DELETE knowledge: relative_path='{relative_path}', tenant='{tenant}'")

    # Delete from Qdrant first — failures don't block disk delete
    qdrant_error: Optional[str] = None
    try:
        deleted_chunks = await indexer.delete_document(relative_path, tenant)
        logger.info(f"Qdrant delete result: {deleted_chunks} chunks removed")
    except Exception as e:
        logger.error(f"Qdrant delete FAILED for '{relative_path}': {e}", exc_info=True)
        deleted_chunks = 0
        qdrant_error = str(e)

    try:
        disk_deleted = manager.delete_document(relative_path)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    if not disk_deleted and deleted_chunks == 0:
        raise HTTPException(status_code=404, detail="Document not found")

    result: dict = {
        "deleted": True,
        "relative_path": relative_path,
        "chunks_removed": deleted_chunks,
        "tenant": tenant,
    }

    if qdrant_error:
        result["warning"] = f"Disk deleted but Qdrant cleanup failed: {qdrant_error}"
        logger.warning(f"Partial delete for '{relative_path}': disk OK, Qdrant FAILED")

    if disk_deleted and deleted_chunks == 0 and not qdrant_error:
        logger.warning(
            f"Disk deleted but 0 Qdrant chunks found for '{relative_path}' — "
            "possible orphan vectors with different source_file path"
        )

    return result
