"""v4.0.28: verify_mechanical must accept commits[] reported as the full ``git log --oneline``
line ("<sha> <subject>"), not only bare SHAs. The "hash subject" form previously errored git
("invalid object name") and surfaced as a FALSE "commit not found" that halted the build."""

from __future__ import annotations

import subprocess
from pathlib import Path

from backend.services import claude_agent, orchestrator
from backend.services.pipeline_status import PipelineStatusBlock


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=str(root), capture_output=True, text=True, check=True).stdout.strip()


def _init(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@e.com")
    _git(root, "config", "user.name", "T")


def _block(commits: list[str]) -> PipelineStatusBlock:
    return PipelineStatusBlock(stage="programovanie", kind="done", summary="s", awaiting="none", commits=commits)


def test_bare_hash_extracts_the_sha() -> None:
    assert orchestrator._bare_hash("abc1234 test(x): a subject line") == "abc1234"
    assert orchestrator._bare_hash("abc1234") == "abc1234"
    assert orchestrator._bare_hash("") == ""


def test_verify_mechanical_accepts_hash_subject_format(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(claude_agent, "PROJECTS_ROOT", tmp_path)
    root = tmp_path / "demo"
    _init(root)
    (root / "a.txt").write_text("1\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "base")
    baseline = _git(root, "rev-parse", "HEAD")
    (root / "b.txt").write_text("2\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "task work")
    head = _git(root, "rev-parse", "HEAD")
    # The agent reported the full "<sha> <subject>" line (the real-world bug) — must now PASS.
    block = _block([f"{head} feat(x): the task commit subject line (TASK 14.1.1)"])
    assert orchestrator.verify_mechanical("demo", block, baseline) is None


def test_verify_mechanical_still_rejects_a_truly_absent_commit(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(claude_agent, "PROJECTS_ROOT", tmp_path)
    root = tmp_path / "demo"
    _init(root)
    (root / "a.txt").write_text("1\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "base")
    ghost = "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"
    msg = orchestrator.verify_mechanical("demo", _block([f"{ghost} not a real commit"]), baseline_sha=None)
    assert msg is not None and "not found" in msg
