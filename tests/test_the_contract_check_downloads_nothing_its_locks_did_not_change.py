"""DEV-216 — the contract check in CI downloads nothing its locks did not change; a network outage does not stop it.

24.10.2026 03:06 UTC the job „Contract Drift-Gate“ (run 40056954219, attempt 1) fell installing numpy — „Connection
broken: BrokenPipeError … Cannot install numpy“ — on a commit with nothing wrong in it, and the deploy of DEV-211 waited
until the job was run again. Every run installed Poetry, every Python package and every npm package anew. Director:
„Áno, súhlasím, založ všetkých päť tiketov.“, „Áno začni“.

Pinned: every install step of the job runs only when its cache missed, the caches are keyed by what decides their
content (the exact Python and Node versions, the Poetry version, poetry.lock, frontend/package-lock.json), and the
Poetry version is the one that wrote poetry.lock — read from it, never written a second time.
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
INSTALL = re.compile(r"\b(pip install|poetry install|npm install|npm ci)\b")


def _job() -> dict:
    return yaml.safe_load(CI.read_text(encoding="utf-8"))["jobs"]["contract"]


def _step(step_id: str) -> dict:
    found = [s for s in _job()["steps"] if s.get("id") == step_id]
    assert len(found) == 1, f"the job has no single step {step_id!r}"
    return found[0]


def _caches() -> dict[str, dict]:
    return {s["id"]: s for s in _job()["steps"] if str(s.get("uses", "")).startswith("actions/cache@") and s.get("id")}


def test_every_install_runs_only_when_its_cache_missed():
    installs = [s for s in _job()["steps"] if INSTALL.search(s.get("run", ""))]
    caches = _caches()

    assert len(installs) >= 2, "the job installs both the backend's and the frontend's packages — read wrong"
    for step in installs:
        guard = re.fullmatch(r"steps\.([a-z-]+)\.outputs\.cache-hit != 'true'", str(step.get("if", "")).strip())
        assert guard and guard.group(1) in caches, f"„{step.get('name')}“ installs on every run"


def test_the_backends_cache_is_keyed_by_what_decides_it():
    caches = _caches()
    python = next(c for c in caches.values() if ".venv" in c["with"]["path"])
    key = python["with"]["key"]

    assert "steps.python.outputs.python-version" in key, "the virtualenv points at the exact Python it was built with"
    assert "steps.poetry.outputs.version" in key
    assert "hashFiles('poetry.lock')" in key
    assert _step("python")["uses"].startswith("actions/setup-python@")


def test_the_frontends_cache_is_keyed_by_what_decides_it():
    caches = _caches()
    node = next(c for c in caches.values() if "frontend/node_modules" in c["with"]["path"])
    key = node["with"]["key"]

    assert "steps.node.outputs.node-version" in key
    assert "hashFiles('frontend/package-lock.json')" in key
    assert _step("node")["uses"].startswith("actions/setup-node@")


def test_the_backend_resolves_into_the_cached_virtualenv():
    assert _job()["env"]["POETRY_VIRTUALENVS_IN_PROJECT"] == "true"


def test_the_poetry_version_is_not_written_a_second_time():
    assert not re.search(r"poetry==\d", CI.read_text(encoding="utf-8")), "the version lives in poetry.lock"


def _read_poetry_version(cwd: Path, tmp_path: Path) -> tuple[int, str]:
    out = tmp_path / "github_output"
    out.write_text("", encoding="utf-8")
    run = subprocess.run(
        ["bash", "-euo", "pipefail", "-c", _step("poetry")["run"]],
        cwd=cwd,
        env={**os.environ, "GITHUB_OUTPUT": str(out)},
        capture_output=True,
        text=True,
        timeout=30,
    )
    return run.returncode, out.read_text(encoding="utf-8")


def test_the_poetry_version_is_read_from_the_lock_it_wrote(tmp_path):
    header = (REPO / "poetry.lock").read_text(encoding="utf-8").splitlines()[0]
    wrote = re.search(r"Poetry (\d+\.\d+\.\d+)", header).group(1)

    code, output = _read_poetry_version(REPO, tmp_path)

    assert (code, output) == (0, f"version={wrote}\n")


@pytest.mark.parametrize("header", ["# no generator named here", ""])
def test_a_lock_that_does_not_name_its_poetry_stops_the_job(tmp_path, header):
    work = tmp_path / "repo"
    work.mkdir()
    (work / "poetry.lock").write_text(f"{header}\n\n[[package]]\n", encoding="utf-8")

    code, output = _read_poetry_version(work, tmp_path)

    assert code != 0 and output == "", "an empty version would key every cache the same and install the latest Poetry"
