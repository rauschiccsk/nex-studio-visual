"""DEV-216 — the contract check in CI downloads nothing its locks did not change; a network outage does not stop it.

24.10.2026 03:06 UTC the job „Contract Drift-Gate“ (run 40056954219, attempt 1) fell installing numpy — „Connection
broken: BrokenPipeError … Cannot install numpy“ — on a commit with nothing wrong in it, and the deploy of DEV-211 waited
until the job was run again. Every run installed Poetry, every Python package and every npm package anew. Director:
„Áno, súhlasím, založ všetkých päť tiketov.“, „Áno začni“.

Pinned: the job installs nothing itself — it uses the shared actions (DEV-219: Lint and Build Frontend use them too),
in which every install runs only when its cache missed; the backend's cache is keyed by what decides it (the exact
Python version, the Poetry version, poetry.lock), and the Poetry version is the one that wrote poetry.lock — read from
it, never written a second time.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
CI = REPO / ".github" / "workflows" / "ci.yml"
BACKEND = REPO / ".github" / "actions" / "backend-deps" / "action.yml"
FRONTEND = REPO / ".github" / "actions" / "frontend-deps" / "action.yml"
INSTALL = re.compile(r"\b(pip install|poetry install|npm install|npm ci)\b")


def _job() -> dict:
    return yaml.safe_load(CI.read_text(encoding="utf-8"))["jobs"]["contract"]


def _action(path: Path) -> list[dict]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))["runs"]["steps"]


def _step(steps: list[dict], step_id: str) -> dict:
    found = [s for s in steps if s.get("id") == step_id]
    assert len(found) == 1, f"no single step {step_id!r}"
    return found[0]


def _caches(steps: list[dict]) -> dict[str, dict]:
    return {s["id"]: s for s in steps if str(s.get("uses", "")).startswith("actions/cache@") and s.get("id")}


def test_the_job_takes_both_from_the_shared_cached_actions_and_installs_nothing_itself():
    steps = _job()["steps"]
    uses = [str(s.get("uses", "")) for s in steps]

    assert "./.github/actions/backend-deps" in uses and "./.github/actions/frontend-deps" in uses
    assert not any(INSTALL.search(str(s.get("run", ""))) for s in steps)


@pytest.mark.parametrize("action", [BACKEND, FRONTEND], ids=["backend", "frontend"])
def test_every_install_runs_only_when_its_cache_missed(action):
    steps = _action(action)
    installs = [s for s in steps if INSTALL.search(s.get("run", ""))]
    caches = _caches(steps)

    assert installs, "the action installs its packages — read wrong"
    for step in installs:
        guard = re.fullmatch(r"steps\.([a-z-]+)\.outputs\.cache-hit != 'true'", str(step.get("if", "")).strip())
        assert guard and guard.group(1) in caches, f"„{step.get('name')}“ installs on every run"


def test_the_backends_cache_is_keyed_by_what_decides_it():
    steps = _action(BACKEND)
    [cache] = _caches(steps).values()
    key = cache["with"]["key"]

    assert ".venv" in cache["with"]["path"]
    assert "steps.python.outputs.python-version" in key, "the virtualenv points at the exact Python it was built with"
    assert "steps.poetry.outputs.version" in key
    assert "hashFiles('poetry.lock')" in key
    assert _step(steps, "python")["uses"].startswith("actions/setup-python@")


def test_the_poetry_version_is_not_written_a_second_time():
    for path in (CI, BACKEND):
        assert not re.search(r"poetry==\d", path.read_text(encoding="utf-8")), "the version lives in poetry.lock"


def _read_poetry_version(cwd: Path, tmp_path: Path) -> tuple[int, str, str]:
    out, env = tmp_path / "github_output", tmp_path / "github_env"
    out.write_text("", encoding="utf-8")
    env.write_text("", encoding="utf-8")
    run = subprocess.run(
        ["bash", "-e", "-c", _step(_action(BACKEND), "poetry")["run"]],
        cwd=cwd,
        env={**os.environ, "GITHUB_OUTPUT": str(out), "GITHUB_ENV": str(env)},
        capture_output=True,
        text=True,
        timeout=30,
    )
    return run.returncode, out.read_text(encoding="utf-8"), env.read_text(encoding="utf-8")


def test_the_poetry_version_is_read_from_the_lock_it_wrote_and_the_virtualenv_stays_in_the_project(tmp_path):
    header = (REPO / "poetry.lock").read_text(encoding="utf-8").splitlines()[0]
    wrote = re.search(r"Poetry (\d+\.\d+\.\d+)", header).group(1)

    code, output, env = _read_poetry_version(REPO, tmp_path)

    assert (code, output, env) == (0, f"version={wrote}\n", "POETRY_VIRTUALENVS_IN_PROJECT=true\n")


@pytest.mark.parametrize("header", ["# no generator named here", ""])
def test_a_lock_that_does_not_name_its_poetry_stops_the_job(tmp_path, header):
    work = tmp_path / "repo"
    work.mkdir()
    (work / "poetry.lock").write_text(f"{header}\n\n[[package]]\n", encoding="utf-8")

    code, output, _env = _read_poetry_version(work, tmp_path)

    assert code != 0 and output == "", "an empty version would key every cache the same and install the latest Poetry"
