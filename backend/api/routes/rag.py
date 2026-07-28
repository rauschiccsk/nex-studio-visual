"""RAG API routes — Qdrant-backed knowledge base search and listing.

Ported 1:1 from NEX Command (`backend/api/routes/rag.py`) per Director
mandate 2026-05-07 (M3 milestone of feature parity audit).

Adaptations for NEX Studio:

* Auth dependency: :func:`backend.core.security.get_current_user`
  (NEX Studio's flat ``role`` model — ``ri`` / ``ha`` / ``shu`` —
  replaces NEX Command's ``user.role == 'director'`` check via
  :func:`backend.core.security.has_full_kb_access`).
* :func:`backend.utils.kb_access.filter_kb_documents` and
  :func:`backend.utils.kb_access.is_path_allowed` accept a
  ``db: Session`` parameter (M2.E refactor — removes hidden
  production bug where a fresh ``SessionLocal()`` was opened
  inside the helper, ignoring the request transaction).
* Mounted at ``/api/v1/rag`` (NEX Studio prefix convention).
* A RAG outage is REPORTED, not disguised: :class:`backend.rag.reader.
  RagUnavailableError` becomes HTTP 503 naming the service and address
  (:func:`_unavailable`). The reader used to swallow an unreachable Qdrant /
  Ollama into an empty result list, so a misconfigured endpoint surfaced as a
  search that simply never found anything.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.api.routes.knowledge import _is_restricted
from backend.core.security import get_current_user, has_full_kb_access
from backend.db.models.foundation import User
from backend.db.session import get_db
from backend.rag import reader
from backend.rag.reader import RagUnavailableError
from backend.utils.kb_access import (
    filter_kb_documents,
    get_allowed_kb_categories,
    is_path_allowed,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["RAG"])


def _unavailable(exc: RagUnavailableError) -> HTTPException:
    """Turn a RAG outage into an HTTP 503 that names the REAL reason, in Slovak.

    The knowledge base has two independent halves: browsing reads Markdown straight
    off the disk (``/knowledge/*``) and keeps working, while search needs the vector
    index. When only the second half is down the user must be told that — and told
    which address is silent — instead of being shown a bare "Vyhľadávanie zlyhalo"
    or, worse, an empty result list that reads as "nothing found". Every message
    therefore says what broke, where, and that browsing still works.

    ``exc.url`` / ``exc.reason`` are credential-redacted at construction
    (:func:`backend.rag.reader._redact_credentials`), so nothing secret can ride out
    on the response body. 503 (not 500) because this is a dependency being
    unavailable — the request itself was fine and retrying after a fix will work.
    """
    where = f" na adrese {exc.url}" if exc.url else ""
    env_hint = f" (premenná prostredia {exc.env_var})" if exc.env_var else ""

    if exc.kind == "not_configured":
        detail = (
            f"Vyhľadávanie v znalostnej báze nie je nastavené — chýba adresa služby "
            f"{exc.service}{env_hint}. Prehliadanie dokumentov funguje ďalej."
        )
    elif exc.kind == "missing_index":
        detail = (
            f"Index znalostnej bázy zatiaľ neexistuje — služba {exc.service}{where} "
            f"ho nemá vytvorený. Treba spustiť indexáciu. Prehliadanie dokumentov funguje ďalej."
        )
    else:
        detail = (
            f"Znalostná báza je nedostupná — služba {exc.service}{where} neodpovedá"
            f"{env_hint}. Prehliadanie dokumentov funguje ďalej, vyhľadávanie nie."
        )

    logger.error("RAG unavailable (%s): %s", exc.kind, exc)
    return HTTPException(status_code=503, detail=detail)


@router.get("/search")
async def search(
    query: str = Query(..., description="Search query"),
    tenant: str = Query("icc", description="Tenant collection (icc, andros, dev)"),
    limit: int = Query(10, description="Max results"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Search knowledge base in Qdrant. Restricted categories hidden for non-ri."""
    try:
        results = reader.search(tenant=tenant, query=query, limit=limit)
    except RagUnavailableError as exc:
        raise _unavailable(exc) from exc

    if not has_full_kb_access(user):
        results = [r for r in results if not _is_restricted(r.get("category", ""))]

    # RBAC: filter by Shuhari role
    results = filter_kb_documents(results, user, db)

    return {"results": results, "count": len(results)}


@router.get("/document")
async def get_document(
    source_file: str = Query(..., description="Source file path"),
    tenant: str = Query("icc", description="Tenant collection"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Load full document from Qdrant chunks. Restricted categories blocked for non-ri."""
    path_category = source_file.split("/")[0] if "/" in source_file else ""
    if not has_full_kb_access(user) and _is_restricted(path_category):
        raise HTTPException(
            status_code=403,
            detail="Tento dokument je dostupný len pre oprávnených používateľov",
        )

    # RBAC: check Shuhari role access
    if not is_path_allowed(source_file, user, db):
        raise HTTPException(status_code=403, detail="Prístup zamietnutý na základe Shuhari role")

    try:
        doc = reader.get_document(tenant=tenant, source_file=source_file)
    except RagUnavailableError as exc:
        raise _unavailable(exc) from exc
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    return doc


@router.get("/list")
async def list_documents(
    tenant: str = Query("icc", description="Tenant collection (icc, andros, dev)"),
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(20, ge=1, le=100, description="Items per page"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List documents from Qdrant with pagination. Restricted categories hidden for non-ri."""
    try:
        result = reader.list_documents(tenant=tenant, page=page, per_page=per_page)
    except RagUnavailableError as exc:
        raise _unavailable(exc) from exc

    if not has_full_kb_access(user) and isinstance(result, dict) and "documents" in result:
        result["documents"] = [d for d in result["documents"] if not _is_restricted(d.get("category", ""))]

    # RBAC: filter by Shuhari role
    if isinstance(result, dict) and "documents" in result:
        result["documents"] = filter_kb_documents(result["documents"], user, db)
        result["count"] = len(result["documents"])

    return result


@router.get("/stats")
async def get_stats(user: User = Depends(get_current_user)):
    """Stats per tenant collection."""
    try:
        return reader.get_stats()
    except RagUnavailableError as exc:
        raise _unavailable(exc) from exc


@router.get("/categories")
async def get_categories(
    tenant: str = Query("icc", description="Tenant collection"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get unique categories for a tenant. Restricted categories hidden for non-ri."""
    try:
        cats = reader.get_categories(tenant=tenant)
    except RagUnavailableError as exc:
        raise _unavailable(exc) from exc

    if not has_full_kb_access(user):
        cats = [c for c in cats if not _is_restricted(c)]

    # RBAC: filter categories by Shuhari role
    allowed = get_allowed_kb_categories(user, db)
    if "*" not in allowed:
        cats = [c for c in cats if any(c.startswith(a.rstrip("/")) for a in allowed)]

    return {"categories": cats}
