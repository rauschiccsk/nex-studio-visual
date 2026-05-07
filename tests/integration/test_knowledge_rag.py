"""Integration tests for the M3 RAG wire-up.

Covers two surfaces:

1. ``backend/api/routes/knowledge.py`` — POST / PUT / DELETE now call
   :class:`backend.rag.indexer.RAGIndexer`. The contract: when Qdrant
   is down, the disk write is still source of truth and the response
   must carry a ``warning`` field instead of HTTP 5xx.

2. ``backend/api/routes/rag.py`` — wraps :mod:`backend.rag.reader`.
   Tests verify routing + RBAC plumbing; ``reader`` itself is mocked
   because Qdrant is not available in CI.
"""

from __future__ import annotations

import bcrypt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.routes.knowledge import router as knowledge_router
from backend.api.routes.rag import router as rag_router
from backend.config.settings import settings
from backend.core.security import (
    get_current_user,
    require_shu_or_above,
)
from backend.db.models.foundation import User
from backend.db.session import get_db


def _make_user(db_session, role: str = "ri") -> User:
    user = User(
        username=f"{role}_m3_test",
        email=f"{role}_m3_test@test.local",
        password_hash=bcrypt.hashpw(b"test", bcrypt.gensalt(rounds=4)).decode(),
        role=role,
        is_active=True,
    )
    db_session.add(user)
    db_session.flush()
    return user


def _build_client(db_session, user: User) -> TestClient:
    app = FastAPI()
    app.include_router(knowledge_router, prefix="/api/v1/knowledge")
    app.include_router(rag_router, prefix="/api/v1/rag")

    def _override_get_db():
        yield db_session

    def _override_user() -> User:
        return user

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_current_user] = _override_user
    app.dependency_overrides[require_shu_or_above] = _override_user

    return TestClient(app)


# ---------------------------------------------------------------------------
# Knowledge router — Qdrant graceful degradation
# ---------------------------------------------------------------------------


class TestKnowledgeRouterRagWireup:
    """POST/PUT/DELETE must succeed even when Qdrant raises."""

    def _patch_indexer_to_fail(self, monkeypatch, *, op: str, exc: Exception):
        """Patch one ``RAGIndexer`` method to raise ``exc``."""
        from backend.api.routes import knowledge as knowledge_module

        class _FailingIndexer:
            async def index_document(self, **_kwargs):
                if op == "index":
                    raise exc
                return {"chunks": 1, "tenant": _kwargs.get("tenant", "icc")}

            async def reindex_document(self, **_kwargs):
                if op == "reindex":
                    raise exc
                return {"chunks": 1, "tenant": _kwargs.get("tenant", "icc")}

            async def delete_document(self, *_args, **_kwargs):
                if op == "delete":
                    raise exc
                return 0

        monkeypatch.setattr(knowledge_module, "_get_indexer", lambda: _FailingIndexer())

    def test_post_qdrant_down_returns_warning(self, db_session, tmp_path, monkeypatch):
        ri = _make_user(db_session, "ri")
        monkeypatch.setattr(settings, "knowledge_base_path", str(tmp_path))
        self._patch_indexer_to_fail(monkeypatch, op="index", exc=RuntimeError("qdrant down"))

        client = _build_client(db_session, ri)
        resp = client.post(
            "/api/v1/knowledge/documents",
            json={
                "category": "icc",
                "filename": "TEST.md",
                "content": "# Body",
                "tenant": "icc",
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["filename"] == "TEST.md"
        assert "warning" in body
        assert "qdrant down" in body["warning"]
        # Disk side must be source of truth — file lands on disk
        assert (tmp_path / "icc" / "TEST.md").exists()

    def test_post_qdrant_ok_returns_chunks(self, db_session, tmp_path, monkeypatch):
        ri = _make_user(db_session, "ri")
        monkeypatch.setattr(settings, "knowledge_base_path", str(tmp_path))
        self._patch_indexer_to_fail(monkeypatch, op="never", exc=RuntimeError("unused"))

        client = _build_client(db_session, ri)
        resp = client.post(
            "/api/v1/knowledge/documents",
            json={
                "category": "icc",
                "filename": "OK.md",
                "content": "# Body",
                "tenant": "icc",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["chunks"] == 1
        assert body["tenant"] == "icc"
        assert "warning" not in body

    def test_put_qdrant_down_returns_warning(self, db_session, tmp_path, monkeypatch):
        ri = _make_user(db_session, "ri")
        # Seed a doc on disk first via the indexer-OK path
        (tmp_path / "icc").mkdir()
        (tmp_path / "icc" / "EDIT.md").write_text("# old", encoding="utf-8")
        monkeypatch.setattr(settings, "knowledge_base_path", str(tmp_path))
        self._patch_indexer_to_fail(monkeypatch, op="reindex", exc=RuntimeError("qdrant boom"))

        client = _build_client(db_session, ri)
        resp = client.put(
            "/api/v1/knowledge/documents",
            json={
                "relative_path": "icc/EDIT.md",
                "content": "# new content",
                "tenant": "icc",
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "warning" in body
        # Disk write must have happened
        assert (tmp_path / "icc" / "EDIT.md").read_text(encoding="utf-8") == "# new content"

    def test_delete_qdrant_down_still_removes_disk(
        self, db_session, tmp_path, monkeypatch
    ):
        ri = _make_user(db_session, "ri")
        (tmp_path / "icc").mkdir()
        target = tmp_path / "icc" / "DEL.md"
        target.write_text("# bye", encoding="utf-8")
        monkeypatch.setattr(settings, "knowledge_base_path", str(tmp_path))
        self._patch_indexer_to_fail(monkeypatch, op="delete", exc=RuntimeError("qdrant gone"))

        client = _build_client(db_session, ri)
        resp = client.delete(
            "/api/v1/knowledge/documents",
            params={"relative_path": "icc/DEL.md", "tenant": "icc"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["deleted"] is True
        assert "warning" in body
        assert not target.exists()

    def test_post_project_slug_mismatch_logs_warning(
        self, db_session, tmp_path, monkeypatch, caplog
    ):
        import logging as _logging

        ri = _make_user(db_session, "ri")
        monkeypatch.setattr(settings, "knowledge_base_path", str(tmp_path))
        self._patch_indexer_to_fail(monkeypatch, op="never", exc=RuntimeError("unused"))

        # main.py sets propagate=False on the ``backend`` logger so pytest's
        # caplog (rooted at the root logger) misses warnings from
        # backend.api.routes.knowledge. Re-enable propagation just for
        # this test — restored automatically by monkeypatch.
        backend_logger = _logging.getLogger("backend")
        monkeypatch.setattr(backend_logger, "propagate", True)

        client = _build_client(db_session, ri)
        with caplog.at_level("WARNING", logger="backend.api.routes.knowledge"):
            resp = client.post(
                "/api/v1/knowledge/documents",
                json={
                    # category mismatches the declared project_slug
                    "category": "icc",
                    "filename": "X.md",
                    "content": "# x",
                    "tenant": "icc",
                    "project_slug": "nex-studio",
                },
            )
        assert resp.status_code == 200
        assert any("KB category mismatch" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# RAG router — wrap reader, RBAC plumbing
# ---------------------------------------------------------------------------


class _FakeReader:
    """Stand-in for :mod:`backend.rag.reader` — deterministic outputs."""

    @staticmethod
    def search(*, tenant, query, limit):  # noqa: ARG004
        return [
            {
                "source_file": "icc/DECISIONS.md",
                "title": "Decisions",
                "category": "icc",
                "snippet": "matched body",
                "score": 0.85,
                "ingested_at": "2026-05-07T00:00:00+00:00",
            },
            {
                "source_file": "credentials/SECRET.md",
                "title": "Secret",
                "category": "credentials",
                "snippet": "password=...",
                "score": 0.92,
                "ingested_at": "",
            },
        ]

    @staticmethod
    def list_documents(*, tenant, page, per_page):  # noqa: ARG004
        return {
            "documents": [
                {
                    "source_file": "icc/DECISIONS.md",
                    "title": "Decisions",
                    "category": "icc",
                    "total_chunks": 1,
                    "ingested_at": "",
                },
                {
                    "source_file": "credentials/SECRET.md",
                    "title": "Secret",
                    "category": "credentials",
                    "total_chunks": 1,
                    "ingested_at": "",
                },
            ],
            "total": 2,
            "page": page,
            "per_page": per_page,
            "pages": 1,
        }

    @staticmethod
    def get_document(*, tenant, source_file):  # noqa: ARG004
        if source_file == "missing.md":
            return None
        return {
            "source_file": source_file,
            "title": "T",
            "content": "hello",
            "category": source_file.split("/")[0] if "/" in source_file else "general",
            "total_chunks": 1,
        }

    @staticmethod
    def get_stats():
        return {"tenants": {"icc": {"points": 5, "documents": 2}}}

    @staticmethod
    def get_categories(*, tenant):  # noqa: ARG004
        return ["icc", "shuhari", "credentials", "projects"]


@pytest.fixture()
def fake_reader(monkeypatch):
    from backend.api.routes import rag as rag_module

    monkeypatch.setattr(rag_module, "reader", _FakeReader)
    return _FakeReader


class TestRagRouter:
    def test_search_ri_sees_credentials(self, db_session, fake_reader):
        ri = _make_user(db_session, "ri")
        client = _build_client(db_session, ri)
        resp = client.get("/api/v1/rag/search", params={"query": "foo"})
        assert resp.status_code == 200
        results = resp.json()["results"]
        assert any(r["category"] == "credentials" for r in results)

    def test_search_shu_filtered_credentials_hidden(self, db_session, fake_reader):
        shu = _make_user(db_session, "shu")
        client = _build_client(db_session, shu)
        resp = client.get("/api/v1/rag/search", params={"query": "foo"})
        assert resp.status_code == 200
        results = resp.json()["results"]
        # credentials must be filtered out
        assert all(r["category"] != "credentials" for r in results)
        # shu's kb_access baseline is icc/ + shuhari/ — icc passes
        assert any(r["category"] == "icc" for r in results)

    def test_get_document_404(self, db_session, fake_reader):
        ri = _make_user(db_session, "ri")
        client = _build_client(db_session, ri)
        resp = client.get(
            "/api/v1/rag/document",
            params={"source_file": "missing.md"},
        )
        assert resp.status_code == 404

    def test_get_document_credentials_blocked_for_shu(self, db_session, fake_reader):
        shu = _make_user(db_session, "shu")
        client = _build_client(db_session, shu)
        resp = client.get(
            "/api/v1/rag/document",
            params={"source_file": "credentials/SECRET.md"},
        )
        assert resp.status_code == 403

    def test_get_document_credentials_ok_for_ri(self, db_session, fake_reader):
        ri = _make_user(db_session, "ri")
        client = _build_client(db_session, ri)
        resp = client.get(
            "/api/v1/rag/document",
            params={"source_file": "credentials/SECRET.md"},
        )
        assert resp.status_code == 200
        assert resp.json()["source_file"] == "credentials/SECRET.md"

    def test_list_filters_for_shu(self, db_session, fake_reader):
        shu = _make_user(db_session, "shu")
        client = _build_client(db_session, shu)
        resp = client.get("/api/v1/rag/list")
        assert resp.status_code == 200
        body = resp.json()
        assert all(d["category"] != "credentials" for d in body["documents"])

    def test_stats_passthrough(self, db_session, fake_reader):
        ri = _make_user(db_session, "ri")
        client = _build_client(db_session, ri)
        resp = client.get("/api/v1/rag/stats")
        assert resp.status_code == 200
        assert resp.json()["tenants"]["icc"]["documents"] == 2

    def test_categories_credentials_hidden_for_shu(self, db_session, fake_reader):
        shu = _make_user(db_session, "shu")
        client = _build_client(db_session, shu)
        resp = client.get("/api/v1/rag/categories")
        assert resp.status_code == 200
        cats = resp.json()["categories"]
        assert "credentials" not in cats
        # shu's kb_access baseline grants icc/ — must be in result
        assert "icc" in cats
        # projects/ is ri/ha-only baseline, shu without project membership
        # should not see it
        assert "projects" not in cats

    def test_categories_full_for_ri(self, db_session, fake_reader):
        ri = _make_user(db_session, "ri")
        client = _build_client(db_session, ri)
        resp = client.get("/api/v1/rag/categories")
        assert resp.status_code == 200
        cats = resp.json()["categories"]
        # ri has full KB access — both _is_restricted and kb_access filters
        # are skipped, so every category from the reader passes through.
        assert "credentials" in cats
        assert "icc" in cats
        assert "projects" in cats
