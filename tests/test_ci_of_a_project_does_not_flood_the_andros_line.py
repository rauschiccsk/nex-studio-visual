"""DEV-130 — the CI a project gets downloads nothing its runner already has, and a newer commit stops the older run.

15.10.2026 the Director: „V androse sťažujú sa že tak pomali ide internet že nedá sa robiť.“ and „Keď píše písmená
vypisuje cca 1 písmeno za 2 sec“. The build of NEX Manager 1.3.0 pushed seven commits in a quarter of an hour; every
run pulled the npm cache from GitHub's storage (348 MiB) in three jobs — about 1 GB a commit — while the runner on
ANDROS keeps those packages on its own disk. The runs queued behind each other and filled the line for hours; seven of
them were cancelled by hand. The same morning NEX Websites' runs did the same.

The CI is read as the cockpit hands it to a new project (:func:`_wire_cicd_workflow`) and judged by rules over the
whole file — every job on an ANDROS runner, every step — not a list of the steps there are today.
"""

from __future__ import annotations

import re
import subprocess

import pytest
import yaml

from backend.services import create_project_postscaffold as scaffold

SLUG = "nex-manager"
#: Actions whose ``cache`` input pulls (and pushes) a cache through GitHub's storage instead of the runner's disk.
CACHING_SETUP = re.compile(r"^actions/setup-(node|python|go|java|dotnet)@")
NPM_INSTALL = re.compile(r"\bnpm (ci|install)\b")


@pytest.fixture
def workflow(tmp_path) -> dict:
    """The CI as a new project receives it."""
    subprocess.run(["git", "init", "-q", "-b", "main", str(tmp_path)], check=True)
    for key, value in (("user.email", "studio@isnex.eu"), ("user.name", "NEX Studio")):
        subprocess.run(["git", "-C", str(tmp_path), "config", key, value], check=True)
    scaffold._wire_cicd_workflow(tmp_path, SLUG)  # no origin — the push fails, the file stays
    return yaml.safe_load((tmp_path / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))


def _steps_on_andros(workflow: dict):
    jobs = [(name, job) for name, job in workflow["jobs"].items() if str(job.get("runs-on", "")).startswith("andros-")]
    assert jobs, "no job runs on an ANDROS runner — the rules below would guard nothing"
    for job_name, job in jobs:
        for step in job.get("steps", []):
            yield job_name, step


def test_no_step_on_the_runner_pulls_a_cache_from_github(workflow):
    pulling = [
        f"{job}: {step.get('name') or step['uses']}"
        for job, step in _steps_on_andros(workflow)
        if "uses" in step
        and (
            str(step["uses"]).startswith("actions/cache")
            or (CACHING_SETUP.match(str(step["uses"])) and "cache" in (step.get("with") or {}))
        )
    ]
    assert pulling == [], f"these steps download a cache over ANDROS's line the runner already has on disk: {pulling}"


def test_no_install_on_the_runner_throws_its_local_cache_away(workflow):
    scripts = [(job, str(step.get("run", ""))) for job, step in _steps_on_andros(workflow)]
    assert [job for job, run in scripts if "--no-cache-dir" in run] == []
    assert [job for job, run in scripts if "npm cache clean" in run] == []


def test_npm_installs_from_the_runners_cache_first(workflow):
    installs = [
        line.strip()
        for _, step in _steps_on_andros(workflow)
        for line in str(step.get("run", "")).splitlines()
        if NPM_INSTALL.search(line)
    ]
    assert installs, "no npm install found — the rule guards nothing"
    assert [line for line in installs if "--prefer-offline" not in line] == []


def test_a_newer_commit_stops_the_older_run_of_the_same_branch(workflow):
    concurrency = workflow.get("concurrency") or {}
    group = str(concurrency.get("group", ""))
    assert "github.ref" in group and "github.workflow" in group, (
        "the group must be per workflow and branch — never stopping another branch's or another workflow's run"
    )
    assert concurrency.get("cancel-in-progress") is True


def test_a_stopped_run_still_cleans_up_after_itself(workflow):
    """A run stopped by a newer commit leaves its containers like a failed one would — the cleanup must still run."""
    cleanup = workflow["jobs"]["cleanup"]
    assert "always()" in str(cleanup.get("if", ""))
    assert set(cleanup["needs"]) == set(workflow["jobs"]) - {"cleanup"}
