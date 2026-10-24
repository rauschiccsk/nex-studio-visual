"""DEV-219 — no job of the cockpit's CI on GitHub's machines downloads what did not change.

DEV-216 cached the contract check after it fell on a network outage installing numpy (24.10.2026, run 40056954219).
The same cause stood in every other job on GitHub's machines (measured 24.10.2026, run 40096792303): Lint installed
Poetry and every Python package anew, Build Frontend every npm package and the Chromium of the layout guard, Build
Docker Images both images whole — and in the backend's Dockerfile the dead-code tools were installed after the code
was copied, in the frontend's curl after the site, so any commit downloaded them again. Director: „Áno, súhlasím,
založ tiket a oprav to“.

The guard is a rule, not a list: every job on GitHub's machines and every action it uses is read, and every install
must stand behind a cache keyed by what decides it; the Dockerfiles are read stage by stage, and nothing is installed
after something other than a manifest was copied.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
CI = REPO / ".github" / "workflows" / "ci.yml"
ACTIONS = REPO / ".github" / "actions"
BUILD_IMAGE = "scripts/ci_build_image.sh"
INSTALL = re.compile(r"\b(pip install|poetry install|npm install|npm ci|playwright install(?!-deps))\b")
CACHE_HIT = re.compile(r"steps\.([a-z-]+)\.outputs\.cache-hit != 'true'")


def _jobs() -> dict:
    return yaml.safe_load(CI.read_text(encoding="utf-8"))["jobs"]


def _hosted() -> dict:
    hosted = {name: job for name, job in _jobs().items() if job.get("runs-on") == "ubuntu-latest"}
    assert {"lint", "build-frontend", "contract", "build"} <= set(hosted), "the jobs were read wrong"
    return hosted


def _action(uses: str) -> list[dict]:
    return yaml.safe_load((REPO / uses.removeprefix("./") / "action.yml").read_text(encoding="utf-8"))["runs"]["steps"]


def _scopes() -> list[tuple[str, list[dict]]]:
    """Every list of steps that runs on GitHub's machines — a job's own, and each local action it uses."""
    scopes = []
    for name, job in _hosted().items():
        scopes.append((name, job["steps"]))
        for step in job["steps"]:
            if str(step.get("uses", "")).startswith("./.github/actions/"):
                scopes.append((step["uses"], _action(step["uses"])))
    return scopes


def _caches(steps: list[dict]) -> dict[str, dict]:
    return {s["id"]: s for s in steps if str(s.get("uses", "")).startswith("actions/cache@") and s.get("id")}


def _uses(job: str) -> set[str]:
    return {str(s.get("uses", "")) for s in _jobs()[job]["steps"]}


# ── jobs ─────────────────────────────────────────────────────────────────────────────────────────────────────────


def test_no_job_on_githubs_machines_installs_without_its_cache():
    installs = 0
    for scope, steps in _scopes():
        caches = _caches(steps)
        for step in steps:
            if not INSTALL.search(str(step.get("run", ""))):
                continue
            installs += 1
            guard = CACHE_HIT.fullmatch(str(step.get("if", "")).strip())
            assert guard and guard.group(1) in caches, f"{scope}: „{step.get('name')}“ installs on every run"

    assert installs >= 3, "the backend, the frontend and the browser are installed somewhere — read wrong"


def test_the_backend_and_the_frontend_are_installed_by_one_definition_each():
    assert "./.github/actions/backend-deps" in _uses("lint") & _uses("contract")
    assert "./.github/actions/frontend-deps" in _uses("build-frontend") & _uses("contract")
    for name, job in _hosted().items():
        for step in job["steps"]:
            assert not re.search(r"\b(poetry install|npm install|npm ci)\b", str(step.get("run", ""))), (
                f"{name}: „{step.get('name')}“ installs packages itself instead of using the shared action"
            )


def test_the_frontends_cache_is_keyed_by_what_decides_it():
    steps = _action("./.github/actions/frontend-deps")
    [cache] = _caches(steps).values()

    assert cache["with"]["path"].strip() == "frontend/node_modules"
    assert "steps.node.outputs.node-version" in cache["with"]["key"]
    assert "hashFiles('frontend/package-lock.json')" in cache["with"]["key"]


def test_the_browser_is_cached_by_the_playwright_the_frontend_locks():
    steps = _jobs()["build-frontend"]["steps"]
    browser = next(c for c in _caches(steps).values() if "ms-playwright" in c["with"]["path"])

    assert "steps.playwright.outputs.version" in browser["with"]["key"]
    assert "chromium" in browser["with"]["key"]


def _run_step(script: str, cwd: Path, tmp_path: Path) -> tuple[int, str]:
    out = tmp_path / "github_output"
    out.write_text("", encoding="utf-8")
    run = subprocess.run(
        ["bash", "-e", "-c", script],
        cwd=cwd,
        env={**os.environ, "GITHUB_OUTPUT": str(out)},
        capture_output=True,
        text=True,
        timeout=60,
    )
    return run.returncode, out.read_text(encoding="utf-8")


def _playwright_step() -> dict:
    [step] = [s for s in _jobs()["build-frontend"]["steps"] if s.get("id") == "playwright"]
    return step


def test_the_playwright_version_is_the_one_the_frontends_lock_pins(tmp_path):
    lock = json.loads((REPO / "frontend" / "package-lock.json").read_text(encoding="utf-8"))
    pinned = lock["packages"]["node_modules/@playwright/test"]["version"]

    assert _run_step(_playwright_step()["run"], REPO / "frontend", tmp_path) == (0, f"version={pinned}\n")


@pytest.mark.parametrize("lock", ['{"packages": {}}', ""], ids=["no-playwright", "no-lock"])
def test_a_frontend_that_does_not_lock_playwright_stops_the_job(tmp_path, lock):
    if lock:
        (tmp_path / "package-lock.json").write_text(lock, encoding="utf-8")

    code, output = _run_step(_playwright_step()["run"], tmp_path, tmp_path)

    assert code != 0 and output == "", "an empty version would key every browser the same"


# ── images ───────────────────────────────────────────────────────────────────────────────────────────────────────

DOCKERFILES = {"Dockerfile": "", "frontend/Dockerfile": "frontend/"}
PACKAGE_INSTALL = re.compile(r"\b(apt-get install|apk add|pip install|poetry install|npm ci|npm install)\b")
MANIFESTS = {"pyproject.toml", "poetry.lock", "package.json", "package-lock.json"}


def _stages(dockerfile: str) -> list[list[str]]:
    """The instructions of each stage, continuation lines joined, comments dropped."""
    text = re.sub(r"\\\n", " ", (REPO / dockerfile).read_text(encoding="utf-8"))
    stages: list[list[str]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.upper().startswith("FROM "):
            stages.append([])
        stages[-1].append(line)
    return stages


def _copied(instruction: str) -> list[str]:
    parts = [p for p in instruction.split()[1:] if not p.startswith("--")]
    return parts[:-1]


@pytest.mark.parametrize("dockerfile", DOCKERFILES)
def test_no_package_is_installed_after_the_code_is_copied(dockerfile):
    for stage in _stages(dockerfile):
        code_copied = None
        for instruction in stage:
            word = instruction.split()[0].upper()
            if word == "COPY" and "--from=" not in instruction:
                if any(Path(src).name not in MANIFESTS for src in _copied(instruction)):
                    code_copied = code_copied or instruction
            elif word == "COPY":
                code_copied = code_copied or instruction
            elif word == "RUN" and PACKAGE_INSTALL.search(instruction):
                assert code_copied is None, (
                    f"{dockerfile}: „{instruction[:80]}“ installs after „{code_copied[:60]}“ — every commit downloads"
                    " it again"
                )


def _layer_key() -> str:
    steps = _jobs()["build"]["steps"]
    [cache] = _caches(steps).values()
    return cache["with"]["key"]


def test_the_layer_cache_is_keyed_by_every_file_the_installs_are_built_from():
    key = _layer_key()
    for dockerfile, context in DOCKERFILES.items():
        assert f"'{dockerfile}'" in key
        for stage in _stages(dockerfile):
            for instruction in stage:
                if instruction.split()[0].upper() == "COPY" and "--from=" not in instruction:
                    for src in _copied(instruction):
                        if Path(src).name in MANIFESTS:
                            assert f"'{context}{src.rstrip('/')}'" in key, f"{context}{src} is not in the key"


def test_every_image_is_built_from_the_cached_layers():
    builds = [
        s
        for s in _jobs()["build"]["steps"]
        if re.search(r"docker (buildx )?build|ci_build_image", str(s.get("run", "")))
    ]

    assert len(builds) == 2
    for step in builds:
        assert step["run"].startswith(f"{BUILD_IMAGE} "), f"„{step['name']}“ builds without the layer cache"
        assert "steps.layers.outputs.cache-hit" in step["run"]


def _build(tmp_path: Path, hit: str, *, cached: bool) -> list[str]:
    """Run the real build script with a docker that records its arguments."""
    home, bin_dir = tmp_path / "home", tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "docker.log"
    (bin_dir / "docker").write_text(f'#!/usr/bin/env bash\nprintf "%s\\n" "$@" > {log}\n', encoding="utf-8")
    (bin_dir / "docker").chmod(0o755)
    if cached:
        (home / ".image-layers" / "backend").mkdir(parents=True)
    subprocess.run(
        [str(REPO / BUILD_IMAGE), "backend", ".", hit],
        cwd=REPO,
        env={**os.environ, "HOME": str(home), "PATH": f"{bin_dir}:{os.environ['PATH']}", "GITHUB_SHA": "abc123"},
        check=True,
        timeout=30,
    )
    return log.read_text(encoding="utf-8").splitlines()


def test_a_hit_builds_from_the_layers_and_saves_nothing(tmp_path):
    args = _build(tmp_path, "true", cached=True)

    assert args == [
        "buildx",
        "build",
        "--load",
        "-t",
        "nex-studio-visual-backend:ci-abc123",
        "--cache-from",
        f"type=local,src={tmp_path}/home/.image-layers/backend",
        ".",
    ]


def test_a_miss_saves_every_layer_for_the_next_run(tmp_path):
    args = _build(tmp_path, "", cached=False)

    assert "--cache-from" not in args
    assert args[args.index("--cache-to") + 1] == f"type=local,dest={tmp_path}/home/.image-layers/backend,mode=max"
