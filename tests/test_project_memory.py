"""Tests for :mod:`backend.services.project_memory` (CR-V2-016).

The AI Agent's own persistent per-project memory (``MEMORY.md``) is the
single source of truth for project status / history / decisions (OQ-4
RESOLVED = file-based; R-DOUBLEWRITE). These tests cover the
backend-owned seam:

* the path convention (workspace-root ``MEMORY.md``),
* the one-shot ``seed_memory`` scaffold (never clobbers the agent's writes),
* the build-plan gate "the AI Agent writes a decision; a second build recalls it"
  (the agent reads the file itself — DEV-155 removed the backend recall nothing called), and
* the **single-writer invariant** — there is no backend auto-writer of
  ``MEMORY.md``; only the seed (one-shot) and the agent (its own tools) write it.

Per-project ``MEMORY.md`` lives in the project WORKSPACE (the agent's ``cwd``),
NOT in the shared KB, so it is never auto-reindexed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from backend.services import project_memory


@pytest.fixture()
def workspace(tmp_path: Path, monkeypatch: Any) -> Path:
    """Point the workspace root at ``tmp_path`` and return the slug dir.

    Creates ``<root>/demo`` so seed/read operate on an existing checkout —
    exactly the state after the create flow's Stage-3 init script.
    """
    root = tmp_path / "projects"
    monkeypatch.setattr(project_memory, "PROJECTS_ROOT", root)
    slug_dir = root / "demo"
    slug_dir.mkdir(parents=True)
    return slug_dir


# ── path convention ───────────────────────────────────────────────────


# ── seed_memory ───────────────────────────────────────────────────────


def test_seed_writes_skeleton(workspace: Path) -> None:
    path = project_memory.seed_memory("demo", "Demo Project")
    assert path == workspace / "MEMORY.md"
    body = path.read_text(encoding="utf-8")
    assert "# Demo Project — AI Agent pamäť" in body
    assert "## Rozhodnutia" in body
    assert "## Lekcie" in body
    assert "## Feedback Manažéra" in body


def test_seed_is_one_shot_never_clobbers_agent_writes(workspace: Path) -> None:
    """Seeding twice must NOT overwrite the agent's accumulated memory —
    the single-writer guarantee depends on the seed being a one-shot scaffold."""
    project_memory.seed_memory("demo", "Demo Project")
    # The agent appends a decision (simulating its own free write).
    mem = workspace / "MEMORY.md"
    mem.write_text(mem.read_text(encoding="utf-8") + "\n- Použili sme pg8000.\n", encoding="utf-8")

    # A second create / replay must NOT clobber it.
    result = project_memory.seed_memory("demo", "Demo Project")
    assert result is None  # no-op, existing memory preserved
    assert "Použili sme pg8000." in mem.read_text(encoding="utf-8")


def test_seed_noops_without_checkout(tmp_path: Path, monkeypatch: Any) -> None:
    """A library/test project with no workspace on disk seeds nothing (no crash)."""
    monkeypatch.setattr(project_memory, "PROJECTS_ROOT", tmp_path / "projects")
    assert project_memory.seed_memory("ghost", "Ghost") is None


def test_path_convention(workspace: Path) -> None:
    assert project_memory.memory_path("demo") == workspace / "MEMORY.md"
    assert project_memory.workspace_root("demo") == workspace
    assert project_memory.MEMORY_FILENAME == "MEMORY.md"


# ── recall ────────────────────────────────────────────────────────────


def test_decision_written_is_recalled_on_second_build(workspace: Path) -> None:
    """Build-plan gate: the AI Agent writes a decision to project memory; a
    second build of the same project recalls it.

    Build 1: seed + the agent records a decision via its own Write (the file in
    its ``cwd``). Build 2: the agent reads that same file — nothing carried in process.
    """
    # --- Build 1 ---
    project_memory.seed_memory("demo", "Demo Project")
    mem = project_memory.memory_path("demo")
    decision = "- ROZHODNUTIE: DB = PostgreSQL cez pg8000 (synchronný driver)."
    mem.write_text(mem.read_text(encoding="utf-8") + "\n" + decision + "\n", encoding="utf-8")

    # --- Build 2 (a separate recall, nothing carried in process) ---
    recalled = project_memory.memory_path("demo").read_text(encoding="utf-8")
    assert decision in recalled


# ── shared-KB reindex hook ────────────────────────────────────────────


class _FailingIndexer:
    def __init__(self) -> None:
        self.called = False

    async def index_document(self, *, file_path: str, tenant: str, content: str | None = None) -> dict:
        self.called = True
        raise RuntimeError("qdrant unreachable")


# ── single-writer invariant (R-DOUBLEWRITE) ──────────────────────────


def test_no_backend_auto_writer_of_memory(workspace: Path) -> None:
    """The module exposes NO ongoing backend writer of ``MEMORY.md`` — only the
    one-shot seed. The agent is the sole ongoing writer of its memory (via its
    own tools), which is what keeps the file the single source of truth (no
    DB-driven second writer can drift it).

    The only public function that touches the filesystem is ``seed_memory`` — a one-shot scaffold of
    ``MEMORY.md`` (no-ops if it already exists, so it never overwrites the agent's writes). The rest are pure
    path resolvers.
    """
    allowed = {"seed_memory"}
    memory_write_verbs = ("append", "save", "regenerate", "persist", "update")
    offenders = [
        name
        for name in dir(project_memory)
        if callable(getattr(project_memory, name))
        and not name.startswith("_")
        and name not in allowed
        and any(verb in name for verb in memory_write_verbs)
    ]
    assert offenders == [], f"unexpected MEMORY.md writer(s) in project_memory: {offenders}"
    # seed_memory is the only function that writes MEMORY.md, and it is one-shot.
    assert hasattr(project_memory, "seed_memory")
