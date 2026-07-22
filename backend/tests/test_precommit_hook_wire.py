"""v4.0.29: the scaffold installs + activates the ruff/type-check pre-commit hook in a new project,
so the AI Agent can never commit code the CI Lint stage would reject."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from backend.services import create_project_postscaffold as ps


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=str(root), capture_output=True, text=True, check=True).stdout.strip()


def test_wire_precommit_hook_installs_activates_and_tracks(tmp_path) -> None:
    root = tmp_path / "proj"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@e.com")
    _git(root, "config", "user.name", "T")

    ps._wire_precommit_hook(root)

    hook = root / ".githooks" / "pre-commit"
    assert hook.is_file(), "hook file installed"
    assert os.access(hook, os.X_OK), "hook is executable"
    assert _git(root, "config", "core.hooksPath") == ".githooks", "hook path activated for the clone"
    assert _git(root, "ls-files", ".githooks/pre-commit") == ".githooks/pre-commit", "hook is tracked/committed"
    # The installed hook mirrors the CI Lint gate.
    body = hook.read_text(encoding="utf-8")
    assert "ruff format --check" in body and "ruff check" in body and "type-check" in body
