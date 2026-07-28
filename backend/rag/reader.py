"""RAG Reader — Qdrant-backed Knowledge Base query.

Ported 1:1 from NEX Command (`backend/rag/reader.py`) per Director
mandate 2026-05-07 (M3 milestone of feature parity audit).

Adaptations for NEX Studio:

* Configuration via :data:`backend.config.settings.settings` (Pydantic
  Settings) instead of NEX Command's bare module-level constants. The two
  endpoints are env-overridable (``QDRANT_URL`` / ``OLLAMA_URL``); the
  defaults are host-side, so a containerized backend MUST set them — see
  :class:`backend.config.settings.Settings`.
* Chunk format, score threshold, snippet builder, tenant list and pagination
  are identical to NEX Command so existing Qdrant collections continue to work
  without re-indexing.
* Availability is REPORTED, not swallowed: an unset / unreachable endpoint or a
  missing collection raises :class:`RagUnavailableError` carrying the service,
  the (credential-redacted) address and a ``kind``. Previously every such
  failure was logged and converted into an empty result list, so a backend that
  could not reach its index looked identical to a query with no hits — on every
  query, on every install.
"""

from __future__ import annotations

import logging
import re
from contextlib import contextmanager
from typing import Dict, Iterator, List, Optional

import httpx
from qdrant_client import QdrantClient
from qdrant_client.http.exceptions import UnexpectedResponse
from qdrant_client.models import FieldCondition, Filter, MatchValue

from backend.config.settings import settings

logger = logging.getLogger(__name__)
TENANTS = ["icc", "andros", "dev"]

#: Env var that carries each endpoint (pydantic-settings maps the field name
#: 1:1). Named here so an error message can tell the operator exactly which
#: knob to turn instead of "search failed".
ENV_VAR_FOR_SERVICE: Dict[str, str] = {"Qdrant": "QDRANT_URL", "Ollama": "OLLAMA_URL"}

#: ``scheme://user:pass@host`` — the userinfo segment of a URL. A configured
#: endpoint is normally credential-free, but nothing stops an operator putting
#: basic-auth in ``QDRANT_URL``; every string this module hands upward (error
#: text reaches the HTTP client) goes through :func:`_redact_credentials` so a
#: secret can never ride out on a diagnostic. CLAUDE.md §4.
_USERINFO_RE = re.compile(r"(?<=://)[^/\s@]+@")


def _redact_credentials(text: str) -> str:
    """Replace any ``user:pass@`` userinfo in *text* with ``***@``."""
    return _USERINFO_RE.sub("***@", text or "")


class RagUnavailableError(RuntimeError):
    """The knowledge index could not be CONSULTED — distinct from "nothing matched".

    Every Qdrant/Ollama failure used to be swallowed into an empty result list, so a
    backend that could not reach its vector index was indistinguishable from a query
    with no hits: the operator saw a working search that never found anything, on every
    query, forever. This error carries the facts needed to state the real reason:

    * ``service`` — ``"Qdrant"`` (the index) or ``"Ollama"`` (the embedder).
    * ``url``     — the configured address that failed, credential-redacted.
    * ``kind``    — ``"not_configured"`` (no address set), ``"unreachable"``
      (connect/transport/HTTP error) or ``"missing_index"`` (service answered, but
      the tenant collection does not exist — the index was simply never built).
    * ``reason``  — the underlying technical detail, credential-redacted, for logs.

    The API layer turns this into an HTTP 503 with a plain-Slovak explanation; it is a
    deliberately transport-agnostic exception so the RAG layer stays HTTP-free.
    """

    def __init__(self, service: str, url: str, reason: str, *, kind: str = "unreachable") -> None:
        self.service = service
        self.url = _redact_credentials(url)
        self.reason = _redact_credentials(reason)
        self.kind = kind
        self.env_var = ENV_VAR_FOR_SERVICE.get(service, "")
        super().__init__(f"{service} unavailable ({kind}) at {self.url or '<unset>'}: {self.reason}")


def _endpoint(url: str, service: str) -> str:
    """Return the configured endpoint for *service*, or refuse to guess one.

    An empty setting is a legitimate, explicit state ("this instance has no vector
    index") — it is reported as ``not_configured`` rather than silently falling back
    to some invented address.
    """
    cleaned = (url or "").strip().rstrip("/")
    if not cleaned:
        raise RagUnavailableError(service, "", "no address configured", kind="not_configured")
    return cleaned


def _qdrant_failure(exc: Exception, *, tenant: Optional[str] = None) -> RagUnavailableError:
    """Translate a qdrant-client exception into a :class:`RagUnavailableError`.

    A 404 means the collection is absent (reachable service, unbuilt index) — a
    different fact from "cannot connect", and the caller may treat it differently.
    """
    url = (settings.qdrant_url or "").strip()
    if isinstance(exc, UnexpectedResponse) and exc.status_code == 404:
        return RagUnavailableError(
            "Qdrant",
            url,
            f"collection {tenant!r} does not exist" if tenant else "collection does not exist",
            kind="missing_index",
        )
    return RagUnavailableError("Qdrant", url, str(exc) or exc.__class__.__name__)


@contextmanager
def _qdrant_call(tenant: Optional[str] = None) -> Iterator[None]:
    """Run a qdrant-client call, surfacing failures as :class:`RagUnavailableError`."""
    try:
        yield
    except RagUnavailableError:
        raise
    except Exception as exc:  # noqa: BLE001 — every client failure is a reportable outage
        raise _qdrant_failure(exc, tenant=tenant) from exc


def _get_client() -> QdrantClient:
    return QdrantClient(url=_endpoint(settings.qdrant_url, "Qdrant"))


def list_documents(tenant: str = "icc", page: int = 1, per_page: int = 20) -> Dict:
    """List unique documents in a tenant collection with pagination."""
    client = _get_client()
    seen: Dict[str, Dict] = {}

    offset = None
    while True:
        with _qdrant_call(tenant):
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
        with _qdrant_call(tenant):
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
    """Generate embedding via Ollama API (sync).

    Raises :class:`RagUnavailableError` when Ollama is unset, unreachable or answers
    with an error status — the query cannot be embedded, so there is no honest way to
    return results (or an empty list, which would read as "nothing matched").
    """
    url = _endpoint(settings.ollama_url, "Ollama")
    try:
        response = httpx.post(
            f"{url}/api/embeddings",
            json={"model": settings.embed_model, "prompt": text},
            timeout=settings.rag_api_timeout,
        )
        response.raise_for_status()
        return response.json()["embedding"]
    except httpx.HTTPError as exc:
        raise RagUnavailableError("Ollama", url, str(exc) or exc.__class__.__name__) from exc
    except (KeyError, ValueError) as exc:
        # 200 OK with a body that is not an embedding — a wrong endpoint or a model
        # the server does not have. Still "the embedder did not work", not "no hits".
        raise RagUnavailableError("Ollama", url, f"unexpected response body ({exc})") from exc


def search(
    tenant: str = "icc",
    query: str = "",
    limit: int = 10,
    source_file_prefix: Optional[str] = None,
) -> List[Dict]:
    """Search documents via Qdrant vector similarity search.

    When source_file_prefix is set, only return documents whose source_file
    starts with the given prefix (e.g. "projects/nex-automat/").

    An empty list means exactly one thing: the index was consulted and nothing
    matched. Every way of NOT being able to consult it — Ollama or Qdrant unset,
    unreachable, or the tenant collection missing — raises
    :class:`RagUnavailableError` instead. (Both failures used to be logged and
    turned into ``[]``, which is why a backend pointed at a ``localhost`` that
    hosts neither service reported "no results" for every query ever typed.)
    """
    if not query.strip():
        return []

    client = _get_client()
    query_vector = _get_embedding(query)

    # Fetch more results when prefix-filtering (post-filter needs bigger pool)
    fetch_limit = limit * 3 if not source_file_prefix else limit * 10

    with _qdrant_call(tenant):
        response = client.query_points(
            collection_name=tenant,
            query=query_vector,
            limit=fetch_limit,
            score_threshold=0.3,
        )
        hits = response.points

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
    """Get document counts per tenant collection.

    A tenant whose collection does not exist keeps reporting zeros — that is a
    genuine "nothing indexed for this tenant yet". An UNREACHABLE Qdrant is a
    different fact and now propagates as :class:`RagUnavailableError`: reporting
    ``0 documents`` for a service we could not talk to is the same lie that made
    search look empty rather than broken.
    """
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
            failure = _qdrant_failure(e, tenant=tenant)
            if failure.kind != "missing_index":
                raise failure from e
            logger.warning(f"Collection '{tenant}' not accessible: {failure.reason}")
            stats["tenants"][tenant] = {"points": 0, "documents": 0}

    return stats


def get_categories(tenant: str = "icc") -> List[str]:
    """Get unique categories from a tenant collection."""
    client = _get_client()
    categories = set()

    offset = None
    while True:
        with _qdrant_call(tenant):
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
