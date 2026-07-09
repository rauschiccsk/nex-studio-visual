"""Unit tests for ``RAGIndexer.delete_project_documents`` (Fix 2 — KB ghost root-cause).

Project delete must ALSO clear the project's Qdrant points (tenant ``icc``,
``source_file`` under ``projects/<slug>/``) so a deleted project leaves no ghost
in RAG search — not just on disk. The delete is keyed on the slug PREFIX (the
``delete_project`` flow rmtree's the folder first, so a disk enumeration would
find nothing), and must be collision-safe: ``projects/port-owner/`` must NOT
match ``projects/cross-port-owner/...``.

The live Qdrant client is stubbed here (mirrors the ``test_rag_indexer`` /
``test_knowledge_rag`` split — pure logic under unit, live paths under
integration).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from qdrant_client.models import Filter, PointIdsList

from backend.rag.indexer import RAGIndexer


def _record(point_id: str, source_file: str) -> SimpleNamespace:
    """A minimal stand-in for a Qdrant ``Record`` (``.id`` + ``.payload``)."""
    return SimpleNamespace(id=point_id, payload={"source_file": source_file, "category": "projects"})


def _indexer_with_client(client: MagicMock, monkeypatch) -> RAGIndexer:
    idx = RAGIndexer()
    monkeypatch.setattr(idx, "_get_client", lambda: client)
    return idx


def test_delete_removes_only_prefix_matches(monkeypatch):
    """Only points under ``projects/<slug>/`` are deleted — collision-safe."""
    records = [
        _record("a1", "projects/port-owner/STATUS.md"),
        _record("a2", "projects/port-owner/HISTORY.md"),
        # Decoy sharing the slug as a prefix — must NOT be swept (the trailing
        # slash in the match key makes ``port-owner`` != ``cross-port-owner``).
        _record("b1", "projects/cross-port-owner/STATUS.md"),
        _record("c1", "projects/other-proj/STATUS.md"),
    ]
    client = MagicMock()
    client.scroll.return_value = (records, None)  # (points, next_offset); None ends pagination
    idx = _indexer_with_client(client, monkeypatch)

    removed = idx.delete_project_documents("port-owner", tenant="icc")

    assert removed == 2
    client.delete.assert_called_once()
    _, kwargs = client.delete.call_args
    assert kwargs["collection_name"] == "icc"
    selector = kwargs["points_selector"]
    assert isinstance(selector, PointIdsList)
    assert sorted(selector.points) == ["a1", "a2"]


def test_delete_narrows_scroll_to_projects_category(monkeypatch):
    """The scroll is narrowed by an exact ``category == projects`` filter."""
    client = MagicMock()
    client.scroll.return_value = ([_record("a1", "projects/p/STATUS.md")], None)
    idx = _indexer_with_client(client, monkeypatch)

    idx.delete_project_documents("p", tenant="icc")

    client.scroll.assert_called()
    _, kwargs = client.scroll.call_args
    assert kwargs["collection_name"] == "icc"
    assert isinstance(kwargs["scroll_filter"], Filter)


def test_delete_scroll_requests_only_source_file_payload(monkeypatch):
    """Fix B (efficiency): the scroll loads ONLY the ``source_file`` payload —
    not the full ``content`` chunk of every projects-category point across all
    projects (mirror ``reader.py`` stats scroll). Behaviour is unchanged."""
    client = MagicMock()
    client.scroll.return_value = ([_record("a1", "projects/p/STATUS.md")], None)
    idx = _indexer_with_client(client, monkeypatch)

    idx.delete_project_documents("p", tenant="icc")

    client.scroll.assert_called()
    _, kwargs = client.scroll.call_args
    assert kwargs["with_payload"] == ["source_file"]
    assert kwargs["with_vectors"] is False


def test_delete_no_matches_is_noop(monkeypatch):
    """No point under the prefix → returns 0 and issues no delete."""
    client = MagicMock()
    client.scroll.return_value = ([_record("z1", "projects/unrelated/STATUS.md")], None)
    idx = _indexer_with_client(client, monkeypatch)

    removed = idx.delete_project_documents("port-owner")

    assert removed == 0
    client.delete.assert_not_called()


def test_delete_paginates_until_offset_exhausted(monkeypatch):
    """Scroll pagination is drained (loop until next-offset is None)."""
    client = MagicMock()
    client.scroll.side_effect = [
        ([_record("a1", "projects/p/STATUS.md")], "cursor"),
        ([_record("a2", "projects/p/HISTORY.md")], None),
    ]
    idx = _indexer_with_client(client, monkeypatch)

    removed = idx.delete_project_documents("p")

    assert removed == 2
    assert client.scroll.call_count == 2
    _, kwargs = client.delete.call_args
    assert sorted(kwargs["points_selector"].points) == ["a1", "a2"]
