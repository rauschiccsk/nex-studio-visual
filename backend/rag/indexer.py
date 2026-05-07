"""RAG Indexer — chunking, embedding, Qdrant upsert pipeline.

Ported 1:1 from NEX Command (`backend/rag/indexer.py`) per Director
mandate 2026-05-07 (M3 milestone of feature parity audit).

Adaptations for NEX Studio:

* Configuration via :data:`backend.config.settings.settings` (Pydantic
  Settings) instead of NEX Command's bare module-level constants. The
  defaults injected into ``__init__`` are now resolved at call time so
  ``Settings`` overrides take effect — NEX Command bound them at import.
* No behaviour changes — chunk format, payload schema and upsert
  semantics are identical so existing Qdrant collections remain valid.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Optional

import httpx
from qdrant_client import QdrantClient
from qdrant_client.models import (
    FieldCondition,
    Filter,
    FilterSelector,
    MatchValue,
    PointStruct,
)

from backend.config.settings import settings

logger = logging.getLogger(__name__)


class RAGIndexer:
    """Index documents into Qdrant: chunk -> embed -> upsert."""

    def __init__(
        self,
        qdrant_url: Optional[str] = None,
        ollama_url: Optional[str] = None,
        embed_model: Optional[str] = None,
    ):
        self.qdrant_url = qdrant_url or settings.qdrant_url
        self.ollama_url = ollama_url or settings.ollama_url
        self.embed_model = embed_model or settings.embed_model

    def _get_client(self) -> QdrantClient:
        return QdrantClient(url=self.qdrant_url)

    async def _get_embedding(self, text: str) -> list[float]:
        """Generate embedding via Ollama API — same method as reader.py."""
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.ollama_url}/api/embeddings",
                json={"model": self.embed_model, "prompt": text},
                timeout=settings.rag_api_timeout,
            )
            response.raise_for_status()
            return response.json()["embedding"]

    def _chunk_markdown(
        self,
        content: str,
        max_chars: Optional[int] = None,
        overlap: Optional[int] = None,
    ) -> list[str]:
        """Split markdown into chunks, preferring heading boundaries."""
        if max_chars is None:
            max_chars = settings.rag_chunk_max_chars
        if overlap is None:
            overlap = settings.rag_chunk_overlap

        if not content or not content.strip():
            return []

        # Split on markdown headings (##, ###, etc.)
        sections = re.split(r"(?=^#{1,4}\s)", content, flags=re.MULTILINE)
        sections = [s.strip() for s in sections if s.strip()]

        chunks: list[str] = []
        current = ""

        for section in sections:
            # If section alone exceeds max, split by paragraphs
            if len(section) > max_chars:
                if current:
                    chunks.append(current)
                    current = ""

                paragraphs = section.split("\n\n")
                para_buf = ""
                for para in paragraphs:
                    para = para.strip()
                    if not para:
                        continue
                    if len(para_buf) + len(para) + 2 > max_chars:
                        if para_buf:
                            chunks.append(para_buf)
                        para_buf = para
                    else:
                        para_buf = f"{para_buf}\n\n{para}" if para_buf else para

                if para_buf:
                    current = para_buf
                continue

            # Try to add section to current chunk
            if len(current) + len(section) + 2 > max_chars:
                if current:
                    chunks.append(current)
                current = section
            else:
                current = f"{current}\n\n{section}" if current else section

        if current:
            chunks.append(current)

        # Apply overlap: prepend tail of previous chunk to next
        if overlap > 0 and len(chunks) > 1:
            overlapped = [chunks[0]]
            for i in range(1, len(chunks)):
                prev_tail = chunks[i - 1][-overlap:]
                # Find a clean break (newline or space)
                cut = prev_tail.find("\n")
                if cut == -1:
                    cut = prev_tail.find(" ")
                if cut != -1:
                    prev_tail = prev_tail[cut + 1:]
                overlapped.append(f"{prev_tail}\n\n{chunks[i]}")
            chunks = overlapped

        return chunks

    async def index_document(
        self, file_path: str, tenant: str, content: Optional[str] = None
    ) -> dict:
        """Read MD, chunk, embed, upsert to Qdrant. Returns stats."""
        # Read file if content not provided
        if content is None:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()

        # Derive source_file (relative path used as document ID in payloads)
        source_file = file_path.replace("\\", "/")
        filename = source_file.split("/")[-1]
        category = self._extract_category(source_file)

        # Delete existing chunks for this document (upsert semantics)
        await self.delete_document(source_file=source_file, tenant=tenant)

        chunks = self._chunk_markdown(content)
        if not chunks:
            logger.warning(f"No chunks produced for {file_path}")
            return {"source_file": source_file, "chunks": 0, "tenant": tenant}

        total_chunks = len(chunks)
        now_iso = datetime.now(timezone.utc).isoformat()

        # Generate embeddings for all chunks
        logger.info(f"Generating embeddings for {total_chunks} chunks of {filename}")
        points: list[PointStruct] = []

        for i, chunk_text in enumerate(chunks):
            try:
                embedding = await self._get_embedding(chunk_text)
            except Exception as e:
                logger.error(f"Embedding failed for chunk {i} of {filename}: {e}")
                raise

            payload = {
                "source_file": source_file,
                "content": chunk_text,
                "chunk_index": i,
                "total_chunks": total_chunks,
                "ingested_at": now_iso,
                "filename": filename,
                "category": category,
                "tenant": tenant,
            }

            points.append(
                PointStruct(
                    id=str(uuid.uuid4()),
                    vector=embedding,
                    payload=payload,
                )
            )

        # Upsert to Qdrant
        client = self._get_client()
        client.upsert(collection_name=tenant, points=points)
        logger.info(
            f"Indexed {total_chunks} chunks of {source_file} into '{tenant}'"
        )

        return {
            "source_file": source_file,
            "chunks": total_chunks,
            "tenant": tenant,
        }

    async def delete_document(self, source_file: str, tenant: str) -> int:
        """Delete all chunks for a document from Qdrant. Returns count deleted."""
        # Normalize path (same as index_document does)
        source_file = source_file.replace("\\", "/")
        logger.info(f"Qdrant delete: source_file='{source_file}', tenant='{tenant}'")

        client = self._get_client()

        doc_filter = Filter(
            must=[
                FieldCondition(
                    key="source_file", match=MatchValue(value=source_file)
                )
            ]
        )

        # Count existing points
        count_result = client.count(
            collection_name=tenant,
            count_filter=doc_filter,
            exact=True,
        )
        count = count_result.count

        if count == 0:
            logger.warning(
                f"Qdrant delete: no points found for '{source_file}' in '{tenant}'"
            )
            return 0

        # Delete by filter — atomic single operation
        client.delete(
            collection_name=tenant,
            points_selector=FilterSelector(filter=doc_filter),
        )

        logger.info(
            f"Qdrant delete: removed {count} chunks of '{source_file}' from '{tenant}'"
        )
        return count

    async def reindex_document(
        self,
        file_path: str,
        tenant: str,
        source_file: str,
        content: Optional[str] = None,
    ) -> dict:
        """Delete old chunks + index new. For update operations."""
        deleted = await self.delete_document(source_file, tenant)
        logger.info(f"Reindex: deleted {deleted} old chunks for {source_file}")
        return await self.index_document(file_path, tenant, content=content)

    @staticmethod
    def _extract_category(source_file: str) -> str:
        """Extract category from source_file path (first directory component)."""
        parts = source_file.replace("\\", "/").strip("/").split("/")
        if len(parts) > 1:
            return parts[0]
        return "general"
