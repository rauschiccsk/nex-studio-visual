"""DEV-184 — a change runs the tests it touches; the gate in CI runs every test, in parallel, through the same script.

22.10.2026 every change, one line included, ran the whole suite twice (15½ minutes here, as long in CI). Director:
„Neexistuje nejaký rozumný manažment testov, ktorý by dokázal spustiť testy, ktoré majú zmysel?“ — and to the plan:
„Súhlasím“. The selection follows Python code only; anything else a change touches runs the whole suite.
"""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "backend_tests.py"


def _script():
    spec = importlib.util.spec_from_file_location("backend_tests", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bt = _script()


@pytest.mark.parametrize(
    "path",
    [
        "templates/auditor-charter.md",
        "backend/services/slovak_words.txt",
        "templates/uat/docker-compose.yml.j2",
        "migrations/versions/132_deploy_refusals.py",
        "tests/conftest.py",
        "conftest.py",
        "pyproject.toml",
        "frontend/src/components/layout/Sidebar.tsx",
        "docs/specs/versions/v4.69.0/RELEASE_NOTES.md",
    ],
)
def test_a_change_the_selection_cannot_follow_runs_the_whole_suite(path):
    cmd, why = bt.command(False, ["backend/services/orchestrator.py", path], [])

    assert cmd == bt.whole_suite([])
    assert why == f"the whole suite — the selection cannot follow: {path}"


def test_a_change_of_python_code_runs_the_tests_that_run_it():
    cmd, why = bt.command(False, ["backend/services/orchestrator.py", "tests/test_backend_tests_run.py"], [])

    assert cmd[cmd.index("pytest") :] == ["pytest", "--testmon", "--tb=short", "-q", "--no-cov"]
    assert why == "the tests the changed code runs"


def test_the_selection_keeps_the_cache_it_needs():
    """pytest-testmon keeps its record in pytest's cache; ``-p no:cacheprovider`` stopped it with „KeyError: 'lf'“."""
    cmd, _why = bt.command(
        False, ["backend/services/standing.py"], ["-p", "no:cacheprovider", "-rf", "-pno:cacheprovider"]
    )

    assert cmd[cmd.index("pytest") :] == ["pytest", "--testmon", "--tb=short", "-q", "--no-cov", "-rf"]


def test_the_gate_runs_every_test_in_parallel_with_the_coverage_floor():
    cmd, why = bt.command(True, ["backend/services/orchestrator.py"], ["-rf"])

    assert cmd[cmd.index("pytest") :] == [
        "pytest",
        "-n",
        "8",
        "--tb=short",
        "-q",
        "--cov=backend",
        "--cov-report=term",
        "--cov-fail-under=88",
        "-rf",
    ]
    assert why == "the whole suite (the gate)"


def test_ci_runs_the_backend_through_the_same_script():
    """One place says how the gate runs — CI and the developer cannot drift apart."""
    jobs = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))["jobs"]
    runs = [step.get("run", "") for job in jobs.values() for step in job.get("steps", [])]

    assert [r for r in runs if "pytest" in r] == []
    assert [r for r in runs if "scripts/backend_tests.py" in r] == ["poetry run python scripts/backend_tests.py --all"]


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True)


def test_the_changed_paths_are_what_differs_from_the_last_commit(tmp_path):
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "studio@isnex.eu")
    _git(tmp_path, "config", "user.name", "NEX Studio")
    (tmp_path / ".gitignore").write_text(".testmondata\n", encoding="utf-8")
    (tmp_path / "kept.py").write_text("a = 1\n", encoding="utf-8")
    (tmp_path / "edited.py").write_text("b = 1\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "start")
    (tmp_path / "edited.py").write_text("b = 2\n", encoding="utf-8")
    (tmp_path / "staged.md").write_text("x\n", encoding="utf-8")
    _git(tmp_path, "add", "staged.md")
    (tmp_path / "new.py").write_text("c = 1\n", encoding="utf-8")
    (tmp_path / ".testmondata").write_text("ignored\n", encoding="utf-8")

    assert bt.changed_paths(tmp_path) == ["edited.py", "new.py", "staged.md"]
