"""DEV-71 — the CI a new project gets runs every ``docker compose`` call under the run's own project name, and removes
what the run left on the server even when a job failed.

12.10.2026: the CI template ran on ANDROS's runners — the Docker of the customers' installations — with no project name
of its own, so compose took the repository's name, and ``docker compose down -v`` after the tests would have deleted the
database of anything running under that name; two runs at once would have dropped each other's. Career Asistent's own
production runbook would have started it under exactly that name.

The CI is read as the cockpit hands it to a new project (:func:`_wire_cicd_workflow` renders it into a git checkout),
and judged by a rule over the whole file — every job on an ANDROS runner, every step that calls compose — not a list.
"""

from __future__ import annotations

import re
import subprocess

import pytest
import yaml

from backend.services import create_project_postscaffold as scaffold

SLUG = "career-asistent"
RUN_NAME = re.compile(r"^ci-career-asistent-\$\{\{ github\.run_id \}\}-\$\{\{ github\.run_attempt \}\}$")
COMPOSE_CALL = re.compile(r"\bdocker[ -]compose\b")
#: Ways a script could switch the project name away from the run's.
NAME_SWITCH = re.compile(r"(?:\s-p\s|--project-name|COMPOSE_PROJECT_NAME=)")


@pytest.fixture
def workflow(tmp_path) -> dict:
    """The CI as a new project receives it."""
    subprocess.run(["git", "init", "-q", "-b", "main", str(tmp_path)], check=True)
    for key, value in (("user.email", "studio@isnex.eu"), ("user.name", "NEX Studio")):
        subprocess.run(["git", "-C", str(tmp_path), "config", key, value], check=True)
    scaffold._wire_cicd_workflow(tmp_path, SLUG)  # no origin — the push fails, the file stays
    text = (tmp_path / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "{{PROJECT_SLUG}}" not in text
    return yaml.safe_load(text)


def _on_andros(job: dict) -> bool:
    return str(job.get("runs-on", "")).startswith("andros-ubuntu-")


def _name_for(workflow: dict, job: dict, step: dict) -> str:
    """The project name a step's compose runs under — the step's env over the job's over the workflow's."""
    env = {**(workflow.get("env") or {}), **(job.get("env") or {}), **(step.get("env") or {})}
    return str(env.get("COMPOSE_PROJECT_NAME", ""))


def test_every_compose_call_on_the_server_runs_under_the_run_name(workflow):
    calls = []
    for job_name, job in workflow["jobs"].items():
        if not _on_andros(job):
            continue
        for step in job.get("steps", []):
            script = str(step.get("run", ""))
            if not COMPOSE_CALL.search(script):
                continue
            calls.append(job_name)
            assert RUN_NAME.match(_name_for(workflow, job, step)), (job_name, step.get("name"))
            assert not NAME_SWITCH.search(script), f"{job_name} / {step.get('name')} switches the project name"

    assert calls, "the template calls no compose — the rule would measure nothing"


def test_whatever_the_run_left_is_removed_even_when_a_job_failed(workflow):
    jobs = workflow["jobs"]
    cleanup = jobs.get("cleanup")
    assert cleanup is not None and _on_andros(cleanup)
    assert "always()" in str(cleanup.get("if", ""))
    users = {
        name
        for name, job in jobs.items()
        if name != "cleanup" and any(COMPOSE_CALL.search(str(s.get("run", ""))) for s in job.get("steps", []))
    }
    assert users <= set(cleanup.get("needs", [])), "cleanup may run before a job that still uses compose"
    script = "\n".join(str(s.get("run", "")) for s in cleanup["steps"])
    assert RUN_NAME.match(_name_for(workflow, cleanup, cleanup["steps"][0]))
    for listed, removed in (
        ("docker ps -aq", "docker rm -f"),
        ("docker volume ls -q", "docker volume rm"),
        ("docker network ls -q", "docker network rm"),
    ):
        command = f'{listed} --filter "label=com.docker.compose.project=$p" | xargs -r {removed}'
        assert command in script, f"cleanup does not remove: {command}"
    # Images go by NAME: an image id may be shared with a base image other tags still need.
    assert (
        "docker image ls --filter \"reference=$p-*\" --format '{{.Repository}}:{{.Tag}}' | xargs -r docker rmi"
        in script
    )
    assert "rmi -f" not in script
    assert "exit 1" in script, "a run that leaves something behind must say so"
