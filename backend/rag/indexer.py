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
    PointIdsList,
    PointStruct,
)

from backend.config.settings import settings

logger = logging.getLogger(__name__)

#: Kategórie, ktoré sa do korpusu NESMÚ dostať vôbec (ICCINT-99).
#:
#: ⚠️ Filtrovanie až pri čítaní je obrana na nesprávnom konci. Vyhľadávanie aj čítanie dokumentu
#: kategóriu ``credentials`` bežným kontám skrývajú, ale to je clona pred niečím, čo v korpuse už
#: leží — a leží tam ticho: v prehliadači súborov ten dokument nikto nevidí, takže nikoho nenapadne,
#: že ešte niekde je. Zmerané 10.09.2026: dva kúsky ``credentials/CREDENTIALS.md`` boli v kolekcii
#: ``icc``, hoci priečinok ``/home/icc/knowledge/credentials/`` na disku už dávno neexistuje.
#:
#: Zápis sa preto odmieta hneď. Hlavná charta §4 zakazuje ten priečinok čo i len čítať; zaindexovať
#: jeho obsah do prehľadávateľného úložiska je to isté o stupeň horšie, lebo kópia prežije aj zmazanie
#: originálu.
NEVER_INDEX_CATEGORIES: frozenset[str] = frozenset({"credentials"})


class DocumentMustNotBeIndexed(Exception):
    """Dokument patrí do kategórie, ktorá sa do korpusu nesmie dostať (ICCINT-99).

    Nesie označenie dokumentu, nikdy nie jeho obsah — výnimka putuje do logu a do odpovede.
    """

    def __init__(self, source_file: str, category: str) -> None:
        self.source_file = source_file
        self.category = category
        super().__init__(
            f"Dokument '{source_file}' patrí do kategórie '{category}', ktorá sa do vyhľadávacieho "
            "indexu nesmie dostať — prístupové údaje v prehľadávateľnom úložisku prežijú aj zmazanie "
            "pôvodného súboru."
        )


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
                    prev_tail = prev_tail[cut + 1 :]
                overlapped.append(f"{prev_tail}\n\n{chunks[i]}")
            chunks = overlapped

        return chunks

    async def index_document(
        self,
        file_path: str,
        tenant: str,
        content: Optional[str] = None,
        source_file: Optional[str] = None,
    ) -> dict:
        """Read MD, chunk, embed, upsert to Qdrant. Returns stats.

        ``source_file`` je OZNAČENIE dokumentu v korpuse — to, podľa čoho sa jeho staré kúsky mažú
        a podľa čoho sa určuje kategória. Keď sa neuvedie, odvodí sa z ``file_path``
        (:meth:`_document_id`). Volajúci ho uvedie vtedy, keď sa cesta na disku a označenie
        v korpuse líšia — presne to robí :meth:`reindex_document` (ICCINT-98)."""
        # Read file if content not provided
        if content is None:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()

        source_file = self._document_id(file_path if source_file is None else source_file)
        filename = source_file.split("/")[-1]
        category = self._extract_category(source_file)

        # ICCINT-99: odmietni skôr, než sa čokoľvek zapíše. Zámerne až TU, po odvodení označenia —
        # kontrola nad surovou cestou by sa dala obísť iným tvarom tej istej cesty.
        if category.lower() in NEVER_INDEX_CATEGORIES:
            raise DocumentMustNotBeIndexed(source_file, category)

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
        logger.info(f"Indexed {total_chunks} chunks of {source_file} into '{tenant}'")

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

        doc_filter = Filter(must=[FieldCondition(key="source_file", match=MatchValue(value=source_file))])

        # Count existing points
        count_result = client.count(
            collection_name=tenant,
            count_filter=doc_filter,
            exact=True,
        )
        count = count_result.count

        if count == 0:
            logger.warning(f"Qdrant delete: no points found for '{source_file}' in '{tenant}'")
            return 0

        # Delete by filter — atomic single operation
        client.delete(
            collection_name=tenant,
            points_selector=FilterSelector(filter=doc_filter),
        )

        logger.info(f"Qdrant delete: removed {count} chunks of '{source_file}' from '{tenant}'")
        return count

    def delete_project_documents(self, project_slug: str, tenant: str = "icc") -> int:
        """Delete every Qdrant point belonging to a project's KB docs.

        Removes all points in ``tenant`` whose ``source_file`` payload is under
        ``projects/{project_slug}/`` — the project's KB document tree. Used by
        the ``DELETE /projects/{id}`` flow so a deleted project leaves no ghost
        in RAG search either (the disk folder is already ``rmtree``-d by the KB
        cleanup; this clears the vector store alongside it).

        Keyed on the slug PREFIX, deliberately NOT on a disk enumeration: by the
        time delete runs the folder is gone, so the only reliable key is the
        ``projects/{slug}/`` path prefix. The trailing slash makes the match
        collision-safe — ``projects/port-owner/`` does not match
        ``projects/cross-port-owner/...``.

        Qdrant keyword filters are exact-match only (no native prefix match), so
        we narrow the scan with an exact ``category == "projects"`` filter
        (``category`` = the first path component, set at index time) and
        prefix-match ``source_file`` client-side, then delete the collected
        point ids. Synchronous — the caller (``delete_project`` route) is sync
        and only Qdrant client calls (themselves sync) are used.

        Returns the number of points removed.
        """
        prefix = f"projects/{project_slug}/"
        client = self._get_client()

        # Narrow the scroll to project docs (category == first path component).
        project_filter = Filter(must=[FieldCondition(key="category", match=MatchValue(value="projects"))])

        ids_to_delete: list = []
        offset = None
        while True:
            points, offset = client.scroll(
                collection_name=tenant,
                scroll_filter=project_filter,
                limit=256,
                offset=offset,
                # Only ``source_file`` is needed to prefix-match; loading the
                # full ``content`` chunk of EVERY projects-category point across
                # all projects would be wasteful (mirror ``reader.py`` stats
                # scroll). Behaviour is unchanged — the delete still targets the
                # slug-prefixed points.
                with_payload=["source_file"],
                with_vectors=False,
            )
            for point in points:
                source_file = str((point.payload or {}).get("source_file", "")).replace("\\", "/")
                if source_file.startswith(prefix):
                    ids_to_delete.append(point.id)
            if offset is None:
                break

        if not ids_to_delete:
            logger.info("Qdrant delete: no points under '%s' in '%s'", prefix, tenant)
            return 0

        client.delete(collection_name=tenant, points_selector=PointIdsList(points=ids_to_delete))
        logger.info("Qdrant delete: removed %d points under '%s' from '%s'", len(ids_to_delete), prefix, tenant)
        return len(ids_to_delete)

    async def reindex_document(
        self,
        file_path: str,
        tenant: str,
        source_file: str,
        content: Optional[str] = None,
    ) -> dict:
        """Delete old chunks + index new. For update operations."""
        deleted = await self.delete_document(self._document_id(source_file), tenant)
        logger.info(f"Reindex: deleted {deleted} old chunks for {source_file}")
        # ⚠️ ``source_file`` sa MUSÍ odovzdať ďalej. Kým sa neodovzdával, mazalo sa podľa neho, ale
        # zapisovalo sa podľa označenia odvodeného z ``file_path`` — a keď sa tie dve líšili, nezmazalo
        # sa nič a vznikla druhá kópia toho istého dokumentu. Parameter, ktorý funkcia prijme a
        # nepoužije, je horší než žiadny: sľubuje niečo, čo nerobí (ICCINT-98).
        return await self.index_document(file_path, tenant, content=content, source_file=source_file)

    @staticmethod
    def _document_id(path: str) -> str:
        """Označenie dokumentu v korpuse — cesta V RÁMCI Znalostnej bázy, nie na disku (ICCINT-98).

        Podľa neho sa dokument v korpuse hľadá, maže a zaraďuje do kategórie. Musí byť pre ten istý
        dokument vždy rovnaké, nech ho volajúci pomenuje absolútnou cestou alebo cestou v rámci bázy —
        inak sa mazanie a zápis minú a v korpuse ostanú dve kópie.

        ⚠️ Zmerané 09.09.2026: volanie s absolútnou cestou zapísalo dokument s označením
        ``/home/icc/knowledge/projects/…`` a kategóriou ``home``, kým zvyšných 3 720 bodov v kolekcii
        používa tvar ``projects/…``. Mazanie ohlásilo „no points found“ — presne ten rozchod.
        Kategória sa určuje z prvej zložky, takže z absolútnej cesty vyšlo ``home``: filtrovanie podľa
        kategórie taký dokument nenájde.
        """
        normalised = path.replace("\\", "/")
        root = settings.knowledge_base_path.replace("\\", "/").rstrip("/")
        if root and normalised.startswith(root + "/"):
            normalised = normalised[len(root) + 1 :]
        return normalised.strip("/")

    @staticmethod
    def _extract_category(source_file: str) -> str:
        """Extract category from source_file path (first directory component)."""
        parts = source_file.replace("\\", "/").strip("/").split("/")
        if len(parts) > 1:
            return parts[0]
        return "general"
