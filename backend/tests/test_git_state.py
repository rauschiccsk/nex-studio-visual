"""Git working-tree preflight (v4.0.25) — the dirty-tree guard behind version founding."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from backend.services import git_state


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(root), capture_output=True, text=True, check=True)


def _init_repo(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    (root / "README.md").write_text("hello\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "initial")


def test_clean_tree_reports_clean(tmp_path: Path) -> None:
    repo = tmp_path / "proj"
    _init_repo(repo)
    st = git_state.working_tree_status(str(repo))
    assert st["clean"] is True
    assert st["dirty_count"] == 0
    assert st["files"] == []


def test_dirty_tree_lists_tracked_and_untracked(tmp_path: Path) -> None:
    repo = tmp_path / "proj"
    _init_repo(repo)
    (repo / "README.md").write_text("changed\n", encoding="utf-8")  # modified tracked
    (repo / "new.txt").write_text("x\n", encoding="utf-8")  # untracked
    st = git_state.working_tree_status(str(repo))
    assert st["clean"] is False
    assert st["dirty_count"] == 2
    assert {f["path"] for f in st["files"]} == {"README.md", "new.txt"}


def test_commit_all_cleans_the_tree(tmp_path: Path) -> None:
    repo = tmp_path / "proj"
    _init_repo(repo)
    (repo / "new.txt").write_text("x\n", encoding="utf-8")
    res = git_state.commit_all(str(repo), "test commit")
    assert res["ok"] is True
    assert git_state.working_tree_status(str(repo))["clean"] is True


def test_commit_all_nothing_to_commit_is_ok(tmp_path: Path) -> None:
    repo = tmp_path / "proj"
    _init_repo(repo)
    res = git_state.commit_all(str(repo), "noop")
    assert res["ok"] is True  # benign no-op, not an error


def test_discard_all_reverts_tracked_and_removes_untracked(tmp_path: Path) -> None:
    repo = tmp_path / "proj"
    _init_repo(repo)
    (repo / "README.md").write_text("changed\n", encoding="utf-8")
    (repo / "junk.txt").write_text("junk\n", encoding="utf-8")
    res = git_state.discard_all(str(repo))
    assert res["ok"] is True
    assert git_state.working_tree_status(str(repo))["clean"] is True
    assert (repo / "README.md").read_text(encoding="utf-8") == "hello\n"  # reverted
    assert not (repo / "junk.txt").exists()  # cleaned


def test_missing_or_non_git_path_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        git_state.working_tree_status("")
    with pytest.raises(ValueError):
        git_state.working_tree_status(str(tmp_path / "does-not-exist"))
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(ValueError):
        git_state.working_tree_status(str(plain))  # exists but not a git repo
