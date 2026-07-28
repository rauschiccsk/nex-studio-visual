"""Unit tests for RAG availability reporting (:mod:`backend.rag.reader`).

The defect these pin down: every Qdrant / Ollama failure was caught and turned
into an empty result list, so a backend pointed at a ``localhost`` that hosts
neither service (the default INSIDE the container) answered "no results" for
every query ever typed — indistinguishable from a query that genuinely matched
nothing. The contract now under test:

* the index was consulted and nothing matched  → ``[]``
* the index could NOT be consulted             → :class:`RagUnavailableError`
  carrying which service, the address and a ``kind``.

Qdrant/Ollama are not available in CI, so the client and the HTTP call are
stubbed; what is verified is the classification, not the wire protocol.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest
from qdrant_client.http.exceptions import ResponseHandlingException, UnexpectedResponse

from backend.config.settings import settings
from backend.rag import reader
from backend.rag.reader import RagUnavailableError


def _missing_collection(name: str = "icc") -> UnexpectedResponse:
    return UnexpectedResponse(
        status_code=404,
        reason_phrase="Not Found",
        content=f"Collection `{name}` doesn't exist!".encode(),
        headers=httpx.Headers(),
    )


def _unreachable() -> ResponseHandlingException:
    return ResponseHandlingException(httpx.ConnectError("[Errno 111] Connection refused"))


@pytest.fixture()
def stub_client(monkeypatch) -> MagicMock:
    """Replace the Qdrant client with a mock, keeping the endpoint check live."""
    client = MagicMock()
    monkeypatch.setattr(reader, "_get_client", lambda: client)
    return client


@pytest.fixture()
def stub_embedding(monkeypatch):
    """Make embedding succeed so Qdrant-side classification is isolated."""
    monkeypatch.setattr(reader, "_get_embedding", lambda _text: [0.1, 0.2, 0.3])


# ---------------------------------------------------------------------------
# Endpoint configuration
# ---------------------------------------------------------------------------


class TestEndpointConfiguration:
    def test_endpoints_are_env_overridable(self, monkeypatch):
        """The two URLs are settings, not constants — the whole point of the fix.

        A container cannot reach Qdrant/Ollama on its own ``localhost``; the operator
        must be able to point the backend elsewhere without a code change.
        """
        from backend.config.settings import Settings

        monkeypatch.setenv("QDRANT_URL", "http://qdrant.internal:6333")
        monkeypatch.setenv("OLLAMA_URL", "http://ollama.internal:11434")
        fresh = Settings(_env_file=None)
        assert fresh.qdrant_url == "http://qdrant.internal:6333"
        assert fresh.ollama_url == "http://ollama.internal:11434"

    def test_empty_qdrant_url_is_reported_not_guessed(self, monkeypatch):
        monkeypatch.setattr(settings, "qdrant_url", "")
        with pytest.raises(RagUnavailableError) as excinfo:
            reader.search(tenant="icc", query="čokoľvek")
        assert excinfo.value.kind == "not_configured"
        assert excinfo.value.service == "Qdrant"
        assert excinfo.value.env_var == "QDRANT_URL"

    def test_empty_ollama_url_is_reported_not_guessed(self, monkeypatch, stub_client):
        monkeypatch.setattr(settings, "ollama_url", "   ")
        with pytest.raises(RagUnavailableError) as excinfo:
            reader.search(tenant="icc", query="čokoľvek")
        assert excinfo.value.kind == "not_configured"
        assert excinfo.value.service == "Ollama"
        assert excinfo.value.env_var == "OLLAMA_URL"

    def test_trailing_slash_is_normalised(self, monkeypatch):
        monkeypatch.setattr(settings, "ollama_url", "http://ollama:11434/")
        captured: dict = {}

        def _fake_post(url, **kwargs):  # noqa: ARG001
            captured["url"] = url
            raise httpx.ConnectError("refused")

        monkeypatch.setattr(reader.httpx, "post", _fake_post)
        with pytest.raises(RagUnavailableError):
            reader._get_embedding("x")
        assert captured["url"] == "http://ollama:11434/api/embeddings"


# ---------------------------------------------------------------------------
# search() — the surface the Manažér actually touches
# ---------------------------------------------------------------------------


class TestSearchReportsRealReason:
    def test_ollama_unreachable_raises_instead_of_empty_list(self, monkeypatch, stub_client):
        monkeypatch.setattr(settings, "ollama_url", "http://localhost:9132")
        monkeypatch.setattr(
            reader.httpx,
            "post",
            lambda *_a, **_k: (_ for _ in ()).throw(httpx.ConnectError("[Errno 111] Connection refused")),
        )
        with pytest.raises(RagUnavailableError) as excinfo:
            reader.search(tenant="icc", query="faktúra")
        assert excinfo.value.service == "Ollama"
        assert excinfo.value.kind == "unreachable"
        assert "localhost:9132" in excinfo.value.url

    def test_ollama_http_error_status_raises(self, monkeypatch, stub_client):
        monkeypatch.setattr(settings, "ollama_url", "http://localhost:9132")
        response = httpx.Response(500, request=httpx.Request("POST", "http://localhost:9132/api/embeddings"))
        monkeypatch.setattr(reader.httpx, "post", lambda *_a, **_k: response)
        with pytest.raises(RagUnavailableError) as excinfo:
            reader.search(tenant="icc", query="faktúra")
        assert excinfo.value.service == "Ollama"

    def test_ollama_returns_non_embedding_body_raises(self, monkeypatch, stub_client):
        monkeypatch.setattr(settings, "ollama_url", "http://localhost:9132")
        response = httpx.Response(
            200,
            json={"error": "model not found"},
            request=httpx.Request("POST", "http://localhost:9132/api/embeddings"),
        )
        monkeypatch.setattr(reader.httpx, "post", lambda *_a, **_k: response)
        with pytest.raises(RagUnavailableError) as excinfo:
            reader.search(tenant="icc", query="faktúra")
        assert excinfo.value.service == "Ollama"

    def test_qdrant_unreachable_raises(self, monkeypatch, stub_client, stub_embedding):
        stub_client.query_points.side_effect = _unreachable()
        with pytest.raises(RagUnavailableError) as excinfo:
            reader.search(tenant="icc", query="faktúra")
        assert excinfo.value.service == "Qdrant"
        assert excinfo.value.kind == "unreachable"

    def test_qdrant_missing_collection_is_its_own_kind(self, monkeypatch, stub_client, stub_embedding):
        stub_client.query_points.side_effect = _missing_collection("icc")
        with pytest.raises(RagUnavailableError) as excinfo:
            reader.search(tenant="icc", query="faktúra")
        assert excinfo.value.kind == "missing_index"

    def test_blank_query_still_returns_empty_without_touching_services(self, monkeypatch):
        """A blank query is not an outage — it must not raise, nor dial out."""
        monkeypatch.setattr(settings, "qdrant_url", "")
        assert reader.search(tenant="icc", query="   ") == []

    def test_genuine_no_match_still_returns_empty_list(self, stub_client, stub_embedding):
        """The one case that legitimately yields ``[]`` — index consulted, nothing scored."""
        stub_client.query_points.return_value = SimpleNamespace(points=[])
        assert reader.search(tenant="icc", query="nič také") == []

    def test_hits_are_returned_unchanged(self, stub_client, stub_embedding):
        hit = SimpleNamespace(
            payload={"source_file": "icc/DECISIONS.md", "content": "telo dokumentu", "ingested_at": "2026-07-01"},
            score=0.9123456,
        )
        stub_client.query_points.return_value = SimpleNamespace(points=[hit])
        results = reader.search(tenant="icc", query="telo")
        assert len(results) == 1
        assert results[0]["source_file"] == "icc/DECISIONS.md"
        assert results[0]["category"] == "icc"
        assert results[0]["score"] == 0.9123


# ---------------------------------------------------------------------------
# The other reader entry points
# ---------------------------------------------------------------------------


class TestOtherEntryPointsReportOutages:
    def test_list_documents_unreachable_raises_typed_error(self, stub_client):
        stub_client.scroll.side_effect = _unreachable()
        with pytest.raises(RagUnavailableError):
            reader.list_documents(tenant="icc")

    def test_get_document_unreachable_raises_typed_error(self, stub_client):
        stub_client.scroll.side_effect = _unreachable()
        with pytest.raises(RagUnavailableError):
            reader.get_document(tenant="icc", source_file="icc/X.md")

    def test_get_categories_unreachable_raises_typed_error(self, stub_client):
        stub_client.scroll.side_effect = _unreachable()
        with pytest.raises(RagUnavailableError):
            reader.get_categories(tenant="icc")

    def test_stats_missing_collection_still_reports_zeros(self, stub_client):
        """Absent collection = genuinely nothing indexed — keep the tolerant path."""
        stub_client.get_collection.side_effect = _missing_collection()
        stats = reader.get_stats()
        assert stats["tenants"]["icc"] == {"points": 0, "documents": 0}

    def test_stats_unreachable_raises_instead_of_reporting_zeros(self, stub_client):
        """Zeros for a service we could not reach is the same lie as an empty search."""
        stub_client.get_collection.side_effect = _unreachable()
        with pytest.raises(RagUnavailableError) as excinfo:
            reader.get_stats()
        assert excinfo.value.kind == "unreachable"


# ---------------------------------------------------------------------------
# Credential hygiene (CLAUDE.md §4) — diagnostics must never carry a secret
# ---------------------------------------------------------------------------


class TestCredentialRedaction:
    def test_userinfo_stripped_from_url(self):
        exc = RagUnavailableError("Qdrant", "http://admin:hunter2@qdrant:6333", "boom")
        assert "hunter2" not in exc.url
        assert "hunter2" not in str(exc)
        assert exc.url == "http://***@qdrant:6333"

    def test_userinfo_stripped_from_reason(self):
        exc = RagUnavailableError("Qdrant", "", "connect to http://admin:hunter2@qdrant:6333 failed")
        assert "hunter2" not in exc.reason
        assert "hunter2" not in str(exc)

    def test_credential_free_url_is_untouched(self):
        exc = RagUnavailableError("Ollama", "http://ollama:11434", "refused")
        assert exc.url == "http://ollama:11434"
