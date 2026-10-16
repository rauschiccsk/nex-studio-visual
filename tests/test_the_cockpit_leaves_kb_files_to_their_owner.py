"""DEV-111 — everything the cockpit writes into the Znalostná báza belongs to its owner, not to root.

13.10.2026 on ANDROS: the backend runs as root; the port registry it rewrote through a temp file (09.10.2026) belonged
to root, and the owner of the Znalostná báza (andros) could no longer change or replace it in a terminal. A new
document of the Znalostná báza page and the project's live documents (STATUS.md, HISTORY.md) were founded as root too;
the approved schema handed over its file and the directory right above it, but not the directories above that.

Judged by a rule, not a list: whatever the write created or replaced (outside ``.git``) was handed to the owner.
The run as root is imitated — ``os.geteuid`` answers 0 and ``os.chown`` is recorded, since a test cannot chown.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from backend.services.knowledge_base_writer import KnowledgeBaseWriter
from backend.services.knowledge_manager import KnowledgeManager
from tests.test_the_cockpits_knowledge_base_writes_reach_github import (  # noqa: F401 — fixture
    _allocate,
    _publish_schema,
    kb,
)


@pytest.fixture
def as_root(monkeypatch) -> list[tuple[Path, int, int]]:
    """The cockpit runs as root: ``os.geteuid`` answers 0, every ``os.chown`` is recorded instead of done."""
    handed: list[tuple[Path, int, int]] = []
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.setattr(os, "chown", lambda path, uid, gid, **_: handed.append((Path(path), uid, gid)))
    return handed


def _inodes(root: Path) -> dict[Path, int]:
    return {p: p.lstat().st_ino for p in root.rglob("*") if ".git" not in p.relative_to(root).parts}


def _not_handed_over(root: Path, before: dict[Path, int], handed: list[tuple[Path, int, int]]) -> list[str]:
    """What the write created or replaced and did not give to the owner of the Znalostná báza."""
    owner = (root.stat().st_uid, root.stat().st_gid)
    given = {p for p, uid, gid in handed if (uid, gid) == owner}
    touched = [p for p, ino in _inodes(root).items() if before.get(p) != ino]
    assert touched, "the write touched nothing — the rule would guard nothing"
    return sorted(str(p.relative_to(root)) for p in touched if p not in given)


def test_a_new_document_of_the_kb_page_and_its_new_folder_belong_to_the_owner(kb, as_root):  # noqa: F811
    before = _inodes(kb)
    KnowledgeManager(base_path=kb).save_document("navody/nove", "postup.md", "# Postup\n")
    assert _not_handed_over(kb, before, as_root) == []


def test_an_edited_document_of_the_kb_page_stays_the_owners(kb, as_root):  # noqa: F811  # noqa: F811
    """Written in place it keeps its owner; written through a temp file it is a new file and must be handed over."""
    doc = kb / "notes/somebodys.md"
    inode = doc.lstat().st_ino
    KnowledgeManager(base_path=kb).update_document("notes/somebodys.md", "upravené v kokpite\n")
    owner = (kb.stat().st_uid, kb.stat().st_gid)
    assert doc.lstat().st_ino == inode or (doc, *owner) in as_root


def test_the_live_documents_of_a_new_project_belong_to_the_owner(kb, as_root):  # noqa: F811
    before = _inodes(kb)
    writer = KnowledgeBaseWriter(kb)
    writer.save("novy-projekt", "STATUS.md", "# Stav\n")
    writer.append("novy-projekt", "HISTORY.md", "## Verzia 0.1.0\n", header_if_new="# História\n")
    assert _not_handed_over(kb, before, as_root) == []


def test_the_port_registry_rewritten_by_an_allocation_belongs_to_the_owner(kb, as_root):  # noqa: F811
    before = _inodes(kb)
    assert _allocate() is None
    assert _not_handed_over(kb, before, as_root) == []


def test_an_approved_schema_and_every_folder_it_founds_belong_to_the_owner(kb, as_root):  # noqa: F811
    before = _inodes(kb)
    _publish_schema(kb)
    assert _not_handed_over(kb, before, as_root) == []


def test_without_root_nothing_is_handed_over(kb, monkeypatch):  # noqa: F811
    """Run by the owner itself (tests, the Dedos' tools), the files are already the owner's — no chown is tried."""
    calls: list = []
    monkeypatch.setattr(os, "chown", lambda *a, **k: calls.append(a))
    KnowledgeManager(base_path=kb).save_document("navody", "iny.md", "x\n")
    KnowledgeBaseWriter(kb).save("iny-projekt", "STATUS.md", "x\n")
    assert calls == []
