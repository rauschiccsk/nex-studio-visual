"""DEV-435 — the CI runners' own Docker and the server's build cache stay bounded.

19.11.2026 the root disk of ANDROS crossed 85 %: 29 CI runners held 268 GB of build cache and 132 GB of images in their
own Docker, the server 137 GB of build cache — the runners' hook keeps tagged images and the build cache and nothing
pruned the server's. The cockpit now prunes both once a day, only what has gone unused for long, and never a runner
that is running a job. Director: „Súhlasím“.

Docker itself is never called here: the one function that runs it is replaced by a fake that answers like Docker.
"""

from __future__ import annotations

import subprocess

import pytest

from backend.config.settings import settings
from backend.services import docker_cache_retention as retention


class FakeDocker:
    """Answers the commands the retention sends, from a table of runners; records every command."""

    def __init__(self, runners: dict[str, dict]) -> None:
        self.runners = runners
        self.calls: list[list[str]] = []

    def __call__(self, cmd: list[str], timeout: float) -> subprocess.CompletedProcess:
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

    def host_prunes(self) -> list[list[str]]:
        return [c for c in self.calls if c[:3] == ["docker", "builder", "prune"]]


@pytest.fixture
def docker(monkeypatch):
    def install(runners: dict[str, dict]) -> FakeDocker:
        fake = FakeDocker(runners)
        monkeypatch.setattr(retention, "_run", fake)
        return fake

    return install


def test_an_idle_runner_loses_only_what_went_unused_for_long(docker):
    fake = docker({"nex-inbox": {}})

    result = retention.run_pass()

    keep = settings.ci_runner_cache_keep_hours
    assert fake.prunes_in("nex-inbox") == [
        f"{retention.OWN_SOCKET}docker builder prune -af --filter until={keep}h"
        f" && docker image prune -af --filter until={keep}h"
    ]
    assert result.pruned == ["nex-ci-docker-nex-inbox"]


@pytest.mark.parametrize(
    ("runner", "reason"),
    [
        ({"worker_rc": 0}, retention.RUNNING_JOB),
        ({"worker_rc": 125}, retention.UNKNOWN),
        ({"containers": "abc123\n"}, retention.HAS_CONTAINERS),
        ({"ps_rc": 1}, retention.UNKNOWN),
    ],
)
def test_a_runner_that_may_be_working_is_left_alone(docker, runner, reason):
    fake = docker({"nex-knowledge": runner, "nex-manager": {}})

    result = retention.run_pass()

    assert fake.prunes_in("nex-knowledge") == []
    assert result.skipped == {"nex-ci-docker-nex-knowledge": reason}
    assert result.pruned == ["nex-ci-docker-nex-manager"]


def test_one_runner_failing_does_not_stop_the_others(docker):
    fake = docker({"nex-archive": {"prune_rc": 1}, "orthodox-typikon": {}})

    result = retention.run_pass()

    assert list(result.failed) == ["nex-ci-docker-nex-archive"]
    assert fake.prunes_in("orthodox-typikon")
    assert result.pruned == ["nex-ci-docker-orthodox-typikon"]


def test_only_runner_dockers_are_pruned_inside(docker):
    fake = docker({"dedo": {}})

    retention.run_pass()

    pruned_in = {c[2] for c in fake.calls if c[:2] == ["docker", "exec"] and "prune" in c[-1]}
    assert pruned_in == {"nex-ci-docker-dedo"}


def test_the_server_keeps_its_recent_build_cache(docker):
    fake = docker({})

    retention.run_pass()

    assert fake.host_prunes() == [
        ["docker", "builder", "prune", "-af", "--filter", f"until={settings.host_build_cache_keep_hours}h"]
    ]


def test_the_limits_are_settings_with_the_agreed_values():
    assert (settings.ci_runner_cache_keep_hours, settings.host_build_cache_keep_hours) == (72, 168)
    assert settings.docker_cache_retention_interval_seconds == 24 * 3600


def test_the_daily_pass_never_runs_in_the_test_suite():
    assert settings.docker_cache_retention_enabled is False
