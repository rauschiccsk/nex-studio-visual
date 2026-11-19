"""DEV-435 — the CI runners' own Docker stays bounded; the server's own build cache is left alone.

19.11.2026 the root disk of ANDROS crossed 85 %: 29 CI runners held 268 GB of build cache and 132 GB of images in their
own Docker — the runners' hook keeps tagged images and the build cache. The cockpit now prunes them once a day, only
what has gone unused for long, and never a runner that is running a job. The server's own build cache stays: ANDROS
keeps it for the builds and the tests (DEV-166, DEV-305). Director: „Súhlasím“.

Docker itself is never called here: the one function that runs it is replaced by a fake that answers like Docker.
"""

from __future__ import annotations

import subprocess

import pytest

from backend.config.settings import settings
from backend.services import orchestrator


class FakeDocker:
    """Answers the commands the retention sends, from a table of runners; records every command."""

    def __init__(self, runners: dict[str, dict]) -> None:
        self.runners = runners
        self.calls: list[list[str]] = []

    def __call__(self, cmd: list[str]) -> subprocess.CompletedProcess:
        self.calls.append(cmd)
        ok = subprocess.CompletedProcess(cmd, 0, "", "")
        if cmd[:2] == ["docker", "ps"]:
            names = [f"nex-ci-docker-{s}" for s in self.runners] + [f"nex-ci-runner-{s}" for s in self.runners]
            return subprocess.CompletedProcess(cmd, 0, "\n".join(names + ["nex-studio-visual-prod-db-1"]) + "\n", "")
        if cmd[:2] == ["docker", "exec"]:
            container, script = cmd[2], cmd[-1]
            suffix = container.split("-", 3)[3]
            runner = self.runners[suffix]
            if container.startswith("nex-ci-runner-"):
                return subprocess.CompletedProcess(cmd, runner.get("worker_rc", 1), "", "")
            if "docker ps -q" in script:
                return subprocess.CompletedProcess(cmd, runner.get("ps_rc", 0), runner.get("containers", ""), "")
            return subprocess.CompletedProcess(cmd, runner.get("prune_rc", 0), "Total reclaimed space: 1GB\n", "x")
        return ok

    def prunes_in(self, suffix: str) -> list[str]:
        return [
            c[-1] for c in self.calls if c[:3] == ["docker", "exec", f"nex-ci-docker-{suffix}"] and "prune" in c[-1]
        ]


@pytest.fixture
def docker(monkeypatch):
    def install(runners: dict[str, dict]) -> FakeDocker:
        fake = FakeDocker(runners)
        monkeypatch.setattr(orchestrator, "_ci_runner_docker", fake)
        return fake

    return install


def test_an_idle_runner_loses_only_what_went_unused_for_long(docker):
    fake = docker({"nex-inbox": {}})

    result = orchestrator.prune_ci_runner_caches()

    keep = settings.ci_runner_cache_keep_hours
    assert fake.prunes_in("nex-inbox") == [
        f"{orchestrator.CI_RUNNER_OWN_SOCKET}docker builder prune -af --filter until={keep}h"
        f" && docker image prune -af --filter until={keep}h"
    ]
    assert result.pruned == ["nex-ci-docker-nex-inbox"]


@pytest.mark.parametrize(
    ("runner", "reason"),
    [
        ({"worker_rc": 0}, orchestrator.CI_RUNNER_RUNNING_JOB),
        ({"worker_rc": 125}, orchestrator.CI_RUNNER_UNKNOWN),
        ({"containers": "abc123\n"}, orchestrator.CI_RUNNER_HAS_CONTAINERS),
        ({"ps_rc": 1}, orchestrator.CI_RUNNER_UNKNOWN),
    ],
)
def test_a_runner_that_may_be_working_is_left_alone(docker, runner, reason):
    fake = docker({"nex-knowledge": runner, "nex-manager": {}})

    result = orchestrator.prune_ci_runner_caches()

    assert fake.prunes_in("nex-knowledge") == []
    assert result.skipped == {"nex-ci-docker-nex-knowledge": reason}
    assert result.pruned == ["nex-ci-docker-nex-manager"]


def test_one_runner_failing_does_not_stop_the_others(docker):
    fake = docker({"nex-archive": {"prune_rc": 1}, "orthodox-typikon": {}})

    result = orchestrator.prune_ci_runner_caches()

    assert list(result.failed) == ["nex-ci-docker-nex-archive"]
    assert fake.prunes_in("orthodox-typikon")
    assert result.pruned == ["nex-ci-docker-orthodox-typikon"]


def test_only_runner_dockers_are_pruned_inside(docker):
    fake = docker({"dedo": {}})

    orchestrator.prune_ci_runner_caches()

    pruned_in = {c[2] for c in fake.calls if c[:2] == ["docker", "exec"] and "prune" in c[-1]}
    assert pruned_in == {"nex-ci-docker-dedo"}


def test_the_servers_own_build_cache_is_left_alone(docker):
    """DEV-166, DEV-305: ANDROS keeps its build cache for the builds and the tests — the pass reaches only runners."""
    fake = docker({"nex-inbox": {}})

    orchestrator.prune_ci_runner_caches()

    assert [c for c in fake.calls if c[:2] != ["docker", "exec"] and "prune" in c] == []
    assert {c[2] for c in fake.calls if c[:2] == ["docker", "exec"]} <= {
        "nex-ci-docker-nex-inbox",
        "nex-ci-runner-nex-inbox",
    }


def test_the_limits_are_settings_with_the_agreed_values():
    assert settings.ci_runner_cache_keep_hours == 72
    assert settings.ci_runner_cache_retention_interval_seconds == 24 * 3600


def test_the_daily_pass_never_runs_in_the_test_suite():
    assert settings.ci_runner_cache_retention_enabled is False
