"""Bounded Docker caches on the server — the CI runners' own Docker and the server's build cache (DEV-435).

**Why.** 19.11.2026 the root disk of ANDROS crossed 85 % (733 GB used) and fired its alert. 29 CI runners held 268 GB of
build cache and 132 GB of images in their own Docker; the server 137 GB of build cache. The runners' hook
(``templates/ci-runner/cleanup.sh``, ICCINT-261) empties containers, volumes, networks and dangling images at every
job but keeps tagged images and the build cache on purpose — the next build reuses them — so both grew with every run
without a bound. Nothing pruned the server's build cache either. Director: „Súhlasím“.

**What.** Once a day the cockpit prunes, in the own Docker of every runner that is not running a job, the build cache
and the images unused for longer than ``settings.ci_runner_cache_keep_hours``, and on the server the build cache unused
for longer than ``settings.host_build_cache_keep_hours``. What is recent stays, so CI stays fast.

**Why from outside, not in cleanup.sh.** A changed hook reaches a runner only when the runner is renewed (its template
fingerprint), one project at a time; a prune from outside reaches every runner at once — the cockpit's own runner and
the workplace's too, which are not among the cockpit's projects.

**Never a working runner.** A runner whose worker process runs (a job), whose Docker has a container, or whose state
cannot be read is left alone and named in the pass's result — a prune never races a build.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass, field
from typing import Optional

from backend.config.settings import settings

logger = logging.getLogger(__name__)

RUNNER_DOCKER_PREFIX = "nex-ci-docker-"
RUNNER_PREFIX = "nex-ci-runner-"
#: A rootless runner Docker listens on the socket of its user, a privileged one on the default socket.
OWN_SOCKET = "if [ -S /run/user/1000/docker.sock ]; then export DOCKER_HOST=unix:///run/user/1000/docker.sock; fi; "
#: How long one Docker command may take — a prune of tens of gigabytes takes a few minutes.
COMMAND_TIMEOUT = 900

RUNNING_JOB = "beží úloha CI"
HAS_CONTAINERS = "v jeho Dockeri bežia kontajnery"
UNKNOWN = "stav sa nedá zistiť"


@dataclass
class Pass:
    """What one pass did: the runner Dockers pruned, left alone (with why) and failed, and the server's prune."""

    pruned: list[str] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)
    failed: dict[str, str] = field(default_factory=dict)
    host: Optional[str] = None


def _run(cmd: list[str], timeout: float) -> subprocess.CompletedProcess:
    """The one place that runs Docker — the suite replaces it."""
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)


def _in_runner_docker(name: str, script: str) -> subprocess.CompletedProcess:
    return _run(["docker", "exec", name, "sh", "-c", OWN_SOCKET + script], COMMAND_TIMEOUT)


def runner_dockers() -> list[str]:
    """The running CI runner Dockers on this server, by name."""
    listed = _run(["docker", "ps", "--format", "{{.Names}}"], COMMAND_TIMEOUT)
    if listed.returncode != 0:
        raise RuntimeError(f"docker ps failed: {listed.stderr.strip()[-300:]}")
    return sorted(n for n in listed.stdout.split() if n.startswith(RUNNER_DOCKER_PREFIX))


def why_not(name: str) -> Optional[str]:
    """Why the runner Docker ``name`` must be left alone now — or ``None`` when it is idle."""
    runner = RUNNER_PREFIX + name.removeprefix(RUNNER_DOCKER_PREFIX)
    worker = _run(["docker", "exec", runner, "pgrep", "-f", "Runner.Worker"], COMMAND_TIMEOUT)
    if worker.returncode == 0:
        return RUNNING_JOB
    if worker.returncode != 1:
        return UNKNOWN
    containers = _in_runner_docker(name, "docker ps -q")
    if containers.returncode != 0:
        return UNKNOWN
    return HAS_CONTAINERS if containers.stdout.strip() else None


def run_pass() -> Pass:
    """One pass over every runner Docker and the server. Blocking — call it off the event loop."""
    result = Pass()
    keep = settings.ci_runner_cache_keep_hours
    for name in runner_dockers():
        try:
            reason = why_not(name)
            if reason:
                result.skipped[name] = reason
                continue
            done = _in_runner_docker(
                name,
                f"docker builder prune -af --filter until={keep}h && docker image prune -af --filter until={keep}h",
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            result.failed[name] = str(exc)[-300:]
            continue
        if done.returncode != 0:
            result.failed[name] = done.stderr.strip()[-300:]
            continue
        result.pruned.append(name)
    host = _run(
        ["docker", "builder", "prune", "-af", "--filter", f"until={settings.host_build_cache_keep_hours}h"],
        COMMAND_TIMEOUT,
    )
    result.host = host.stdout.strip().splitlines()[-1] if host.returncode == 0 and host.stdout.strip() else None
    if host.returncode != 0:
        result.failed["server"] = host.stderr.strip()[-300:]
    logger.info(
        "Docker cache retention: pruned %d runner Docker(s), left alone %s, failed %s, server %s",
        len(result.pruned),
        result.skipped,
        result.failed,
        result.host,
    )
    return result
