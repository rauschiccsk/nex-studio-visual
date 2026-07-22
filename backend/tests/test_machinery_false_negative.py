"""K2 (v4.0.27): the halt classifier that tells a NEX Studio MACHINERY false-negative
(correct, committed work rejected by verify) from a genuine project failure."""

from __future__ import annotations

import subprocess
from pathlib import Path

from backend.services import claude_agent, orchestrator
from backend.services.pipeline_status import PipelineStatusBlock


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=str(root), capture_output=True, text=True, check=True).stdout.strip()


def _init_repo_with_commit(root: Path) -> str:
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@e.com")
    _git(root, "config", "user.name", "T")
    (root / "f.txt").write_text("x\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "work")
    return _git(root, "rev-parse", "HEAD")


def _block(commits: list[str]) -> PipelineStatusBlock:
    return PipelineStatusBlock(stage="programovanie", kind="done", summary="done", awaiting="none", commits=commits)


def test_false_negative_detected_when_reported_commit_is_reachable(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(claude_agent, "PROJECTS_ROOT", tmp_path)
    head = _init_repo_with_commit(tmp_path / "demo")
    prior = [f"commit {head!r} not found in demo"]
    reason = orchestrator._classify_machinery_false_negative("demo", prior, _block([head]))
    assert reason is not None and "falošný poplach" in reason


def test_genuine_failure_when_commit_absent(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(claude_agent, "PROJECTS_ROOT", tmp_path)
    _init_repo_with_commit(tmp_path / "demo")
    ghost = "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"
    reason = orchestrator._classify_machinery_false_negative(
        "demo", [f"commit '{ghost}' not found in demo"], _block([ghost])
    )
    assert reason is None


def test_non_commit_not_found_failures_are_not_machinery(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(claude_agent, "PROJECTS_ROOT", tmp_path)
    head = _init_repo_with_commit(tmp_path / "demo")
    # A genuine test/code failure (not a "not found" verdict) is a project problem, never machinery.
    reason = orchestrator._classify_machinery_false_negative("demo", ["tests failed: 3 errors"], _block([head]))
    assert reason is None


def test_no_commits_reported_is_not_machinery(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(claude_agent, "PROJECTS_ROOT", tmp_path)
    _init_repo_with_commit(tmp_path / "demo")
    reason = orchestrator._classify_machinery_false_negative("demo", ["commit 'x' not found in demo"], _block([]))
    assert reason is None
