"""Unit tests for :mod:`backend.rag.indexer` — M3 milestone.

These tests cover pure functions only (chunking and category extraction).
Live Qdrant / Ollama paths are covered via the integration tests under
``tests/integration/test_knowledge_rag.py`` with mocked indexer.
"""

from __future__ import annotations

from backend.rag.indexer import RAGIndexer


def _idx() -> RAGIndexer:
    return RAGIndexer()


# --- _extract_category ------------------------------------------------------


def test_extract_category_with_subdir():
    assert RAGIndexer._extract_category("projects/nex-studio/STATUS.md") == "projects"


def test_extract_category_with_leading_slash():
    assert RAGIndexer._extract_category("/icc/DECISIONS.md") == "icc"


def test_extract_category_top_level_file():
    assert RAGIndexer._extract_category("README.md") == "general"


def test_extract_category_normalises_windows_separators():
    assert RAGIndexer._extract_category("projects\\foo\\BAR.md") == "projects"


# --- _chunk_markdown --------------------------------------------------------


def test_chunk_markdown_empty_returns_empty_list():
    assert _idx()._chunk_markdown("") == []
    assert _idx()._chunk_markdown("   \n\n   ") == []


def test_chunk_markdown_short_doc_yields_single_chunk():
    content = "# Title\n\nSome short body."
    chunks = _idx()._chunk_markdown(content, max_chars=1000, overlap=0)
    assert len(chunks) == 1
    assert "Title" in chunks[0]
    assert "Some short body." in chunks[0]


def test_chunk_markdown_splits_on_headings():
    # Each section is well under max_chars but their concatenation exceeds it,
    # so the splitter must emit one chunk per section boundary.
    big = "x" * 600
    content = f"# A\n\n{big}\n\n## B\n\n{big}\n\n## C\n\n{big}"
    chunks = _idx()._chunk_markdown(content, max_chars=1000, overlap=0)
    assert len(chunks) >= 3
    assert any(c.lstrip().startswith("# A") for c in chunks)
    assert any(c.lstrip().startswith("## B") for c in chunks)
    assert any(c.lstrip().startswith("## C") for c in chunks)


def test_chunk_markdown_oversized_section_falls_back_to_paragraphs():
    long_section = "# Big\n\n" + "\n\n".join("p" * 400 for _ in range(5))
    chunks = _idx()._chunk_markdown(long_section, max_chars=600, overlap=0)
    # 5 paragraphs of ~400 chars each, max 600 → at least 3 chunks
    assert len(chunks) >= 3
    for c in chunks:
        assert c.strip()


def test_chunk_markdown_overlap_prepends_previous_tail():
    # Two distinct sections so the splitter produces 2 chunks; with overlap
    # the second chunk should contain a prefix taken from the first.
    section_a = "# A\n\n" + ("alpha " * 100).strip()
    section_b = "## B\n\n" + ("beta " * 100).strip()
    content = f"{section_a}\n\n{section_b}"
    chunks = _idx()._chunk_markdown(content, max_chars=700, overlap=50)
    assert len(chunks) == 2
    # The second chunk starts with overlapped text followed by ## B
    assert "## B" in chunks[1]
    assert chunks[1].split("## B")[0].strip() != ""


def test_chunk_markdown_overlap_zero_keeps_chunks_pristine():
    section_a = "# A\n\n" + ("alpha " * 100).strip()
    section_b = "## B\n\n" + ("beta " * 100).strip()
    content = f"{section_a}\n\n{section_b}"
    chunks = _idx()._chunk_markdown(content, max_chars=700, overlap=0)
    assert len(chunks) == 2
    assert chunks[1].lstrip().startswith("## B")


# --- označenie dokumentu v korpuse (ICCINT-98) --------------------------------
#
# Podľa neho sa dokument v korpuse hľadá, maže a zaraďuje do kategórie. Kým sa mazanie a zápis
# opierali o dve rôzne odvodenia toho istého, preindexovanie nezmazalo nič a pribudla druhá kópia.


def test_a_document_is_named_the_same_however_the_caller_spells_its_path():
    """⚠️ Jadro nálezu: absolútna cesta na disku a cesta v rámci bázy sú TEN ISTÝ dokument."""
    v_baze = "projects/nex-studio-visual/NAVOD.md"
    na_disku = "/home/icc/knowledge/projects/nex-studio-visual/NAVOD.md"

    assert RAGIndexer._document_id(na_disku) == RAGIndexer._document_id(v_baze) == v_baze


def test_the_category_survives_an_absolute_path():
    """Zmerané 09.09.2026: z absolútnej cesty vyšla kategória ``home`` namiesto ``projects`` —
    a filtrovanie podľa kategórie taký dokument nenájde."""
    id_dokumentu = RAGIndexer._document_id("/home/icc/knowledge/projects/x/Y.md")

    assert RAGIndexer._extract_category(id_dokumentu) == "projects"


def test_a_path_outside_the_knowledge_base_is_left_alone():
    """Cesta, ktorá pod bázou neleží, sa neskracuje — inak by sa z nej stalo označenie, ktoré klame."""
    assert RAGIndexer._document_id("/opt/projects/nex-inbox/README.md") == "opt/projects/nex-inbox/README.md"


def test_a_leading_slash_does_not_make_a_second_document():
    """``/icc/DECISIONS.md`` a ``icc/DECISIONS.md`` musia byť jeden dokument, nie dva."""
    assert RAGIndexer._document_id("/icc/DECISIONS.md") == RAGIndexer._document_id("icc/DECISIONS.md")
