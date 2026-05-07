"""Knowledge Search Service — Qdrant vector search + file listing for KB Browser.

Ported 1:1 from NEX Command (`backend/services/knowledge_search.py`)
per Director mandate 2026-05-07 (M3 milestone of feature parity audit).

Adaptations for NEX Studio:

* Configuration via :data:`backend.config.settings.settings` (Pydantic
  Settings). NEX Command imports bare module-level constants
  (``QDRANT_URL``, ``OLLAMA_URL`` ...).
* ``KNOWLEDGE_BASE_PATH`` resolved from :attr:`Settings.knowledge_base_path`.
* Project-scoped vector search uses Qdrant's REST API directly (same
  as NEX Command) rather than ``qdrant-client``; preserved verbatim
  so re-indexing produces identical results.

Used by the Workflow chat (M4 milestone) to fetch project-scoped
knowledge context. Kept independent of :mod:`backend.rag.reader` /
:mod:`backend.rag.indexer` because those operate on entire tenants
while this one filters by project slug embedded in ``source_file``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import httpx

from backend.config.settings import settings

logger = logging.getLogger(__name__)


async def _get_embedding(query: str) -> list[float]:
    """Generate embedding via Ollama API."""
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{settings.ollama_url}/api/embeddings",
            json={"model": settings.embed_model, "prompt": query},
            timeout=settings.rag_api_timeout,
        )
        response.raise_for_status()
        return response.json()["embedding"]


async def search_knowledge(
    query: str,
    project: str = "nex-studio",
    limit: int = 10,
) -> list[dict]:
    """Vector search in Qdrant collection 'icc' filtered by project slug.

    Returns list of dicts with: filename, title, excerpt, score, full_path.
    """
    embedding = await _get_embedding(query)

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{settings.qdrant_url}/collections/icc/points/search",
            json={
                "vector": embedding,
                "limit": limit,
                "with_payload": True,
                "filter": {
                    "must": [
                        {
                            "key": "source_file",
                            "match": {"text": project},
                        }
                    ]
                },
            },
            timeout=settings.rag_api_timeout,
        )
        response.raise_for_status()
        results = response.json().get("result", [])

    documents: list[dict] = []
    seen_files: set[str] = set()

    for hit in results:
        payload = hit.get("payload", {})
        source_file = payload.get("source_file", "")
        content = payload.get("content", "")
        filename = payload.get(
            "filename", source_file.split("/")[-1] if source_file else ""
        )

        # Deduplicate by source_file — keep highest score (results sorted by score)
        if source_file in seen_files:
            continue
        seen_files.add(source_file)

        # Title: first heading or filename
        title = filename
        for line in content.split("\n"):
            stripped = line.strip()
            if stripped.startswith("#"):
                title = stripped.lstrip("#").strip()
                break

        documents.append(
            {
                "filename": filename,
                "title": title,
                "excerpt": content[:200].strip(),
                "score": round(hit.get("score", 0.0), 4),
                "full_path": source_file,
            }
        )

    return documents


def list_project_files(
    project: str = "nex-studio",
) -> list[dict]:
    """List all KB files for a project from disk.

    Returns list of dicts with: filename, title, excerpt, full_path.
    """
    project_dir = Path(settings.knowledge_base_path) / "projects" / project

    if not project_dir.exists() or not project_dir.is_dir():
        logger.warning(f"KB project directory not found: {project_dir}")
        return []

    documents: list[dict] = []

    for md_file in sorted(project_dir.rglob("*.md")):
        relative = md_file.relative_to(Path(settings.knowledge_base_path))
        full_path = str(relative).replace("\\", "/")
        filename = md_file.name

        try:
            content = md_file.read_text(encoding="utf-8")
        except OSError as e:
            logger.error(f"Failed to read {md_file}: {e}")
            continue

        # Title: first heading or filename
        title = filename
        for line in content.split("\n"):
            stripped = line.strip()
            if stripped.startswith("#"):
                title = stripped.lstrip("#").strip()
                break

        documents.append(
            {
                "filename": filename,
                "title": title,
                "excerpt": content[:200].strip(),
                "full_path": full_path,
            }
        )

    return documents


def read_document(relative_path: str) -> Optional[dict]:
    """Read a KB document by relative path.

    Returns dict with: path, content, filename.
    Raises ValueError if path traversal detected.
    Raises FileNotFoundError if file does not exist.
    """
    if ".." in relative_path:
        raise ValueError("Path traversal not allowed")

    clean_path = relative_path.replace("\\", "/").lstrip("/")
    full_path = Path(settings.knowledge_base_path) / clean_path

    # Ensure resolved path stays inside knowledge_base_path
    resolved = full_path.resolve()
    base_resolved = Path(settings.knowledge_base_path).resolve()
    if not str(resolved).startswith(str(base_resolved)):
        raise ValueError("Path traversal not allowed")

    if not full_path.exists() or not full_path.is_file():
        raise FileNotFoundError(f"Document not found: {clean_path}")

    content = full_path.read_text(encoding="utf-8")
    filename = full_path.name

    return {
        "path": clean_path,
        "content": content,
        "filename": filename,
    }
