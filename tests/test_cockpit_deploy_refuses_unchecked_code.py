"""DEV-99 — the cockpit deploy builds only code that is in git and passed CI on GitHub.

``scripts/deploy-prod.sh`` builds its images from the working tree (``docker build .``). 13.10.2026 the tree carried an
uncommitted fix while 4.44.0 was being deployed — it would have gone live without CI, and only a deploy from a clean
checkout made by hand kept it out. The rule "deploy only after a green CI" was guarded by attention alone.

The script is run for real here, in a throwaway repository, with ``docker`` and ``gh`` replaced by stubs that record
their calls: every refusal must come before Docker is touched, and a clean, green commit must get past both checks.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "deploy-prod.sh"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A cockpit checkout with one commit: the deploy script and a release note."""
    root = tmp_path / "nex-studio-visual"
    (root / "scripts").mkdir(parents=True)
    shutil.copy2(SCRIPT, root / "scripts" / "deploy-prod.sh")
    (root / "README.md").write_text("cockpit\n", encoding="utf-8")
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "t@t.sk")
    _git(root, "config", "user.name", "T")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "release")
    return root


def _stub(bin_dir: Path, name: str, body: str) -> None:
    path = bin_dir / name
    path.write_text(f'#!/usr/bin/env bash\necho "{name} $*" >> "$STUB_LOG"\n{body}\n', encoding="utf-8")
    path.chmod(0o755)


def _deploy(repo: Path, tmp_path: Path, *, ci: str, gh_exit: int = 0) -> tuple[subprocess.CompletedProcess, list[str]]:
    """Run the script from the checkout; ``ci`` is what GitHub answers about the commit (``status conclusion``)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    log = tmp_path / "calls.log"
    log.write_text("", encoding="utf-8")
    _stub(bin_dir, "gh", f'echo "{ci}"\nexit {gh_exit}')
    _stub(bin_dir, "docker", "exit 0")
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "STUB_LOG": str(log),
        "DOCKER_HOST": "unix:///nonexistent",
    }
    done = subprocess.run(
        ["bash", "scripts/deploy-prod.sh", "--dry-run", "--version", "9.9.9", "all"],
        cwd=repo,
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    return done, log.read_text(encoding="utf-8").splitlines()


def test_a_tree_with_uncommitted_changes_is_refused_before_docker(repo, tmp_path):
    (repo / "README.md").write_text("cockpit, changed\n", encoding="utf-8")

    done, calls = _deploy(repo, tmp_path, ci="completed success")

    assert done.returncode == 1
    assert "ODMIETNUTÉ: pracovný priečinok má nezapísané zmeny (1: README.md)" in done.stderr
    assert not [c for c in calls if c.startswith("docker")]


def test_an_untracked_file_is_refused_too(repo, tmp_path):
    (repo / "hotfix.py").write_text("print('not in git')\n", encoding="utf-8")

    done, calls = _deploy(repo, tmp_path, ci="completed success")

    assert done.returncode == 1
    assert "nezapísané zmeny (1: hotfix.py)" in done.stderr
    assert not [c for c in calls if c.startswith("docker")]


@pytest.mark.parametrize(
    ("ci", "gh_exit", "said"),
    [
        ("completed failure", 0, "CI pre {sha} nie je zelené (completed failure)"),
        ("in_progress ", 0, "CI pre {sha} nie je zelené (in_progress )"),
        ("", 0, "CI pre {sha} nie je zelené (žiadny beh)"),
        ("", 1, "stav CI pre {sha} sa nedal zistiť"),
    ],
)
def test_a_commit_without_a_green_ci_is_refused_before_docker(repo, tmp_path, ci, gh_exit, said):
    sha = _git(repo, "rev-parse", "HEAD").strip()

    done, calls = _deploy(repo, tmp_path, ci=ci, gh_exit=gh_exit)

    assert done.returncode == 1
    assert said.format(sha=sha[:7]) in done.stderr
    assert not [c for c in calls if c.startswith("docker")]


def test_a_clean_green_commit_gets_past_both_checks_and_asks_about_that_commit(repo, tmp_path):
    sha = _git(repo, "rev-parse", "HEAD").strip()

    done, calls = _deploy(repo, tmp_path, ci="completed success")

    asked = [c for c in calls if c.startswith("gh ")]
    assert len(asked) == 1 and f"--commit {sha}" in asked[0] and "-R rauschiccsk/nex-studio-visual" in asked[0]
    # It goes on to the checks that follow — the live compose and its containers — which a throwaway checkout fails.
    assert "nezapísané zmeny" not in done.stderr and "CI pre" not in done.stderr
    assert "ODMIETNUTÉ" in done.stderr
