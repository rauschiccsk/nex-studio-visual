"""DEV-207 — the contract check runs before the commit, by the same script CI runs.

23.10.2026 (DEV-203) the docstrings of two routes changed — they are the routes' OpenAPI descriptions, from which the
frontend's contract types are generated. Codegen was not run; the pre-commit hook checked ruff, types and eslint but
not the contract, so only CI's „Contract Drift-Gate“ caught the stale file and a second commit fixed it. The hook now
runs ``scripts/contract_drift_check.sh`` whenever the backend's code or the generated file is staged, and CI runs the
same script. Director 23.10.2026: „Áno, súhlasím, založ tiket a oprav to“.

The hook is run for real in a throwaway repository; the drift script, poetry and npm are stand-ins that record that
they ran — the hook's job is to decide WHEN the check runs and to stop the commit when it fails.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
HOOK = REPO / ".githooks" / "pre-commit"
SCRIPT = "scripts/contract_drift_check.sh"
GENERATED = "frontend/src/services/api/pipeline.generated.ts"


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True)


def _stand_in(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!/usr/bin/env bash\n{body}\n")
    path.chmod(0o755)


@pytest.fixture
def repo(tmp_path) -> tuple[Path, Path, dict]:
    """A repository with the cockpit's hook, a drift script that fails (the contract is stale) and stand-ins for poetry
    and npm that pass. Returns (root, the drift script's run record, the hook's environment)."""
    root = tmp_path / "repo"
    (root / ".githooks").mkdir(parents=True)
    shutil.copy(HOOK, root / ".githooks" / "pre-commit")
    ran = tmp_path / "drift-ran"
    _stand_in(root / SCRIPT, f'echo ran >> "{ran}"\necho "::error::pipeline.generated.ts is stale"\nexit 1')
    tools = tmp_path / "bin"
    _stand_in(tools / "poetry", "exit 0")
    _stand_in(tools / "npm", "exit 0")
    (root / "frontend").mkdir()
    _git(tmp_path, "init", "-q", "-b", "main", str(root))
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "t")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "--no-verify", "-m", "base")
    env = {**os.environ, "PATH": f"{tools}{os.pathsep}{os.environ['PATH']}"}
    return root, ran, env


def _commit_hook(root: Path, env: dict, path: str) -> subprocess.CompletedProcess:
    (root / path).parent.mkdir(parents=True, exist_ok=True)
    (root / path).write_text("changed\n")
    _git(root, "add", path)
    return subprocess.run(
        [str(root / ".githooks" / "pre-commit")], cwd=root, env=env, capture_output=True, text=True, check=False
    )


@pytest.mark.parametrize("path", ["backend/api/routes/projects.py", "backend/schemas/dedo.py", GENERATED])
def test_a_stale_contract_stops_the_commit(repo, path):
    root, ran, env = repo

    answer = _commit_hook(root, env, path)

    assert answer.returncode == 1, answer.stdout
    assert ran.read_text() == "ran\n"
    assert "Contract check FAILED" in answer.stdout
    assert f"git add {GENERATED}" in answer.stdout, "the hook does not say what to do"


@pytest.mark.parametrize("path", ["backend/tests/test_x.py", "docs/notes.md", "frontend/src/App.tsx"])
def test_a_change_that_cannot_move_the_contract_does_not_run_it(repo, path):
    root, ran, env = repo

    answer = _commit_hook(root, env, path)

    assert answer.returncode == 0, answer.stdout
    assert not ran.exists()


def test_ci_runs_the_same_script():
    jobs = yaml.safe_load((REPO / ".github" / "workflows" / "ci.yml").read_text())["jobs"]
    runs = [step.get("run", "") for step in jobs["contract"]["steps"]]

    assert SCRIPT in runs
    assert not any("npm run codegen" in run or "git diff" in run for run in runs), "a second copy of the check in CI"


def test_the_hook_runs_the_script_ci_runs():
    assert f"{SCRIPT} " in HOOK.read_text() and (REPO / SCRIPT).stat().st_mode & 0o111
