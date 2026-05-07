"""RAG Reader — Qdrant-backed Knowledge Base query.

Ported 1:1 from NEX Command (`backend/rag/reader.py`) per Director
mandate 2026-05-07 (M3 milestone of feature parity audit).

Adaptations for NEX Studio:

* Configuration via :data:`backend.config.settings.settings` (Pydantic
  Settings) instead of NEX Command's bare module-level constants.
* No other behaviour changes — chunk format, score threshold, snippet
  builder, tenant list and pagination are identical so existing Qdrant
  collections continue to work without re-indexing.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

import httpx
from qdrant_client import QdrantClient
from qdrant_client.models import FieldCondition, Filter, MatchValue

from backend.config.settings import settings

logger = logging.getLogger(__name__)
TENANTS = ["icc", "andros", "dev"]


def _get_client() -> QdrantClient:
    return QdrantClient(url=settings.qdrant_url)


def list_documents(tenant: str = "icc", page: int = 1, per_page: int = 20) -> Dict:
    """List unique documents in a tenant collection with pagination."""
    client = _get_client()
    seen: Dict[str, Dict] = {}

    offset = None
    while True:
        results, next_offset = client.scroll(
            collection_name=tenant,
            limit=100,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        for point in results:
            payload = point.payload or {}
            source = payload.get("source_file", payload.get("filename", ""))
            if source and source not in seen:
                seen[source] = {
                    "source_file": source,
                    "title": _make_title(source),
                    "category": _extract_category(source),
                    "total_chunks": payload.get("total_chunks", 1),
                    "ingested_at": payload.get("ingested_at", ""),
                }
        if next_offset is None:
            break
        offset = next_offset

    all_docs = sorted(seen.values(), key=lambda d: d["source_file"])
    total = len(all_docs)
    start = (page - 1) * per_page
    end = start + per_page

    return {
        "documents": all_docs[start:end],
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": (total + per_page - 1) // per_page,
    }


def get_document(tenant: str, source_file: str) -> Optional[Dict]:
    """Load full document by reconstructing from chunks."""
    client = _get_client()
    chunks = []

    offset = None
    while True:
        results, next_offset = client.scroll(
            collection_name=tenant,
            scroll_filter=Filter(must=[FieldCondition(key="source_file", match=MatchValue(value=source_file))]),
            limit=100,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        chunks.extend(results)
        if next_offset is None:
            break
        offset = next_offset

    if not chunks:
        return None

    chunks.sort(key=lambda p: (p.payload or {}).get("chunk_index", 0))
    content = "\n\n".join((p.payload or {}).get("content", "") for p in chunks)
    first = chunks[0].payload or {}  # noqa: F841 — kept for parity with NEX Command source

    return {
        "source_file": source_file,
        "title": _make_title(source_file),
        "content": content,
        "category": _extract_category(source_file),
        "total_chunks": len(chunks),
    }


def _get_embedding(text: str) -> list[float]:
    """Generate embedding via Ollama API (sync)."""
    response = httpx.post(
        f"{settings.ollama_url}/api/embeddings",
        json={"model": settings.embed_model, "prompt": text},
        timeout=settings.rag_api_timeout,
    )
    response.raise_for_status()
    return response.json()["embedding"]


def search(
    tenant: str = "icc",
    query: str = "",
    limit: int = 10,
    source_file_prefix: Optional[str] = None,
) -> List[Dict]:
    """Search documents via Qdrant vector similarity search.

    When source_file_prefix is set, only return documents whose source_file
    starts with the given prefix (e.g. "projects/nex-automat/").
    """
    if not query.strip():
        return []

    client = _get_client()

    try:
        query_vector = _get_embedding(query)
    except Exception as e:
        logger.error(f"Embedding error: {e}")
        return []

    # Fetch more results when prefix-filtering (post-filter needs bigger pool)
    fetch_limit = limit * 3 if not source_file_prefix else limit * 10

    try:
        response = client.query_points(
            collection_name=tenant,
            query=query_vector,
            limit=fetch_limit,
            score_threshold=0.3,
        )
        hits = response.points
    except Exception as e:
        logger.error(f"Qdrant search error: {e}")
        return []

    results = []
    seen_sources = set()
    for hit in hits:
        payload = hit.payload or {}
        source = payload.get("source_file", payload.get("filename", ""))

        if source_file_prefix and not source.startswith(source_file_prefix):
            continue

        if source in seen_sources:
            continue
        seen_sources.add(source)

        content = payload.get("content", "")
        results.append(
            {
                "source_file": source,
                "title": _make_title(source),
                "category": _extract_category(source),
                "snippet": _make_context_snippet(content, query),
                "score": round(hit.score, 4),
                "ingested_at": payload.get("ingested_at", ""),
            }
        )
        if len(results) >= limit:
            break

    return results


def get_stats() -> Dict:
    """Get document counts per tenant collection."""
    client = _get_client()
    stats = {"tenants": {}}

    for tenant in TENANTS:
        try:
            info = client.get_collection(tenant)
            # Count unique documents
            seen_sources = set()
            offset = None
            while True:
                points, next_offset = client.scroll(
                    collection_name=tenant,
                    limit=100,
                    offset=offset,
                    with_payload=["source_file"],
                    with_vectors=False,
                )
                for p in points:
                    source = (p.payload or {}).get("source_file", "")
                    if source:
                        seen_sources.add(source)
                if next_offset is None:
                    break
                offset = next_offset

            stats["tenants"][tenant] = {
                "points": info.points_count,
                "documents": len(seen_sources),
            }
        except Exception as e:
            logger.warning(f"Collection '{tenant}' not accessible: {e}")
            stats["tenants"][tenant] = {"points": 0, "documents": 0}

    return stats


def get_categories(tenant: str = "icc") -> List[str]:
    """Get unique categories from a tenant collection."""
    client = _get_client()
    categories = set()

    offset = None
    while True:
        points, next_offset = client.scroll(
            collection_name=tenant,
            limit=100,
            offset=offset,
            with_payload=["source_file"],
            with_vectors=False,
        )
        for p in points:
            source = (p.payload or {}).get("source_file", "")
            cat = _extract_category(source)
            if cat:
                categories.add(cat)
        if next_offset is None:
            break
        offset = next_offset

    return sorted(categories)


def _make_title(source_file: str) -> str:
    """Derive clean display title from source_file path."""
    basename = source_file.replace("\\", "/").split("/")[-1]
    if basename.endswith(".md"):
        basename = basename[:-3]
    return basename.replace("-", " ").replace("_", " ").title()


def _extract_category(source_file: str) -> str:
    """Extract category from source_file path (first directory component)."""
    parts = source_file.replace("\\", "/").strip("/").split("/")
    if len(parts) > 1:
        return parts[0]
    return "general"


def _make_context_snippet(content: str, query: str, max_length: int = 300) -> str:
    """Extract snippet centered around query match in content."""
    if not content:
        return ""

    content_lower = content.lower()
    query_lower = query.lower()

    # Try full query match first, then individual words
    pos = content_lower.find(query_lower)
    if pos == -1:
        for word in query_lower.split():
            if len(word) > 3:
                pos = content_lower.find(word)
                if pos != -1:
                    break

    if pos == -1:
        return (content[:max_length] + "...") if len(content) > max_length else content

    # Center snippet around match
    half = max_length // 2
    start = max(0, pos - half)
    end = min(len(content), pos + half)
    snippet = content[start:end]

    # Align to word boundaries
    if start > 0:
        space = snippet.find(" ")
        if space != -1 and space < 30:
            snippet = snippet[space + 1 :]
        snippet = "..." + snippet
    if end < len(content):
        space = snippet.rfind(" ")
        if space != -1 and len(snippet) - space < 30:
            snippet = snippet[:space]
        snippet = snippet + "..."

    return snippet
