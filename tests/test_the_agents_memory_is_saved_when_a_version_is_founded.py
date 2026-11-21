"""DEV-451 — the agent's memory is not the Manažér's unsaved work; it is saved when a version is founded.

20.11.2026 Orthodox Hermenia: after 0.2.0 the agent's ``MEMORY.md`` held 48 new lines outside git (what the Príprava
found, the Manažér's decisions R-50, R-51, R-53, a lesson). The next fast fix stopped on „Projekt má 1 zmena, ktoré
neboli uložené“ and the Manažér had to choose Uložiť / Zahodiť for the agent's own notes. The check now leaves the
memory out, „Zahodiť“ never throws it away, and founding a version commits it — nothing else.
"""

from __future__ import annotations

import subprocess
import uuid
from pathlib import Path

import pytest

from backend.services import unsaved_work


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True).stdout


@pytest.fixture
def workspace(tmp_path) -> Path:
    ws = tmp_path / "projekt"
    ws.mkdir()
    (ws / "MEMORY.md").write_text("# Pamäť\n")
    (ws / "frontend").mkdir()
    (ws / "frontend" / "a.ts").write_text("export const a = 1;\n")
    _git(ws, "init", "-q", "-b", "main")
    _git(ws, "config", "user.email", "t@example.com")
    _git(ws, "config", "user.name", "t")
    _git(ws, "add", "-A")
    _git(ws, "commit", "-q", "-m", "founding")
    return ws


def _the_agent_notes(ws: Path) -> None:
    (ws / "MEMORY.md").write_text("# Pamäť\n\n## Príprava v0.2.0\n- R-50: Ako začať aj v hu/en.\n")
    (ws / ".memory").mkdir(exist_ok=True)
    (ws / ".memory" / "lessons.md").write_text("- tvrdenie najprv zmerať\n")


def test_the_agents_notes_are_not_the_managers_unsaved_work(workspace):
    _the_agent_notes(workspace)

    assert unsaved_work.status(str(workspace))["clean"] is True


def test_founding_saves_the_memory_and_nothing_else(workspace):
    _the_agent_notes(workspace)
    (workspace / "frontend" / "a.ts").write_text("export const a = 2;\n")

    refused = unsaved_work.refusal(uuid.uuid4(), str(workspace))

    assert _git(workspace, "log", "-1", "--format=%s").strip() == unsaved_work.MEMORY_COMMIT
    assert sorted(_git(workspace, "show", "--name-only", "--format=", "HEAD").split()) == [
        ".memory/lessons.md",
        "MEMORY.md",
    ]
    assert refused is not None and [f["path"] for f in refused["unsaved_work"]["files"]] == ["frontend/a.ts"]


def test_a_founding_over_clean_work_with_unsaved_memory_goes_ahead(workspace):
    _the_agent_notes(workspace)

    assert unsaved_work.refusal(uuid.uuid4(), str(workspace)) is None
    assert _git(workspace, "status", "--porcelain").strip() == ""


def test_discarding_the_managers_work_never_throws_the_memory_away(workspace):
    _the_agent_notes(workspace)
    (workspace / "frontend" / "a.ts").write_text("export const a = 2;\n")

    unsaved_work.discard(str(workspace))

    assert "R-50" in (workspace / "MEMORY.md").read_text()
    assert (workspace / "frontend" / "a.ts").read_text() == "export const a = 1;\n"


def test_nothing_to_save_makes_no_commit(workspace):
    before = _git(workspace, "rev-parse", "HEAD")

    assert unsaved_work.save_agent_memory(str(workspace)) is None
    assert _git(workspace, "rev-parse", "HEAD") == before


def test_the_memory_is_saved_without_the_projects_check(workspace):
    """The check type-checks the project for minutes; founding waits CHECK_CAP seconds. Notes need no type-check."""
    hook = workspace / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\necho '❌ check refused' >&2\nexit 1\n")
    hook.chmod(0o755)
    _the_agent_notes(workspace)

    assert unsaved_work.save_agent_memory(str(workspace)) is None
    assert _git(workspace, "log", "-1", "--format=%s").strip() == unsaved_work.MEMORY_COMMIT
