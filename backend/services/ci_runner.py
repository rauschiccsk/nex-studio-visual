"""The CI runner of a project — a GitHub Actions runner paired with a Docker daemon of its own (DEV-145).

Until 19.10.2026 founding started ``nex-ci-runner-<slug>`` with the server's live ``/var/run/docker.sock``: a CI job
of any project could see, stop and delete the customers' containers and databases on ANDROS, and whatever a
cancelled job left running stayed there. That runner also kept the cockpit's GitHub key in its environment for good.

The pair is described by ``templates/ci-runner/docker-compose.yml`` — why it looks the way it does is in its header.
That file is the only description of it: the cockpit starts it for its projects, and Dedo's ``nastroje/ci_bezec.sh``
starts the cockpit's own runner (the cockpit is not one of its projects) from the same file. Director 19.10.2026:
"Súhlasím".
"""

from __future__ import annotations

import hashlib
import logging
import os
import subprocess
from pathlib import Path

from backend.services import github_validation

logger = logging.getLogger(__name__)

TEMPLATE_DIR = Path(__file__).resolve().parents[2] / "templates" / "ci-runner"
COMPOSE_FILE = TEMPLATE_DIR / "docker-compose.yml"
CLEANUP_HOOK = TEMPLATE_DIR / "cleanup.sh"
#: The runner container's label carrying :func:`template_fingerprint` — set by the template.
TEMPLATE_LABEL = "icc.ci-runner.template"
#: A cold start pulls both images, then the daemon must turn healthy before the runner starts.
COMPOSE_TIMEOUT = 600
GITHUB_TIMEOUT = 20
DOCKER_TIMEOUT = 60

#: What :func:`state` says about a project's runner.
NONE = "none"  # no runner container
CURRENT = "current"  # the pair, started from this template
OUTDATED = "outdated"  # the pair, started from an older template
LEGACY = "legacy"  # a lone runner started with ``docker run`` and the live Docker socket


def runner_container(slug: str) -> str:
    return f"nex-ci-runner-{slug}"


def compose_project(slug: str) -> str:
    return f"nex-ci-{slug}"


def template_fingerprint() -> str:
    """sha256 of the template followed by its cleanup hook.

    ``nastroje/ci_bezec.sh`` computes the same (``cat docker-compose.yml cleanup.sh | sha256sum``), so a runner it
    starts is current for the cockpit too."""
    return hashlib.sha256(COMPOSE_FILE.read_bytes() + CLEANUP_HOOK.read_bytes()).hexdigest()


def compose_env(slug: str, repo_full: str) -> dict[str, str]:
    """The environment ``docker compose`` reads the template with — never with a registration token in it."""
    env = {name: value for name, value in os.environ.items() if name != "RUNNER_TOKEN"}
    env.update(
        SLUG=slug,
        REPO_URL=f"https://github.com/{repo_full}",
        CI_CLEANUP_HOOK=CLEANUP_HOOK.read_text(encoding="utf-8"),
        CI_RUNNER_TEMPLATE=template_fingerprint(),
    )
    return env


def state(slug: str) -> str:
    """Which runner the project has now — :data:`NONE`, :data:`CURRENT`, :data:`OUTDATED` or :data:`LEGACY`."""
    found = _docker(
        [
            "docker",
            "ps",
            "-a",
            "--filter",
            f"name=^{runner_container(slug)}$",
            "--format",
            # The name first: a runner started with ``docker run`` has neither label, and its row must not read empty.
            f'{{{{.Names}}}}\t{{{{.Label "com.docker.compose.project"}}}}\t{{{{.Label "{TEMPLATE_LABEL}"}}}}',
        ],
        timeout=DOCKER_TIMEOUT,
    ).strip()
    if not found:
        return NONE
    _, project, fingerprint = (found.split("\t") + ["", ""])[:3]
    if project != compose_project(slug):
        return LEGACY
    return CURRENT if fingerprint == template_fingerprint() else OUTDATED


def provision(slug: str, repo_full: str) -> None:
    """Start the project's pair from the template, or bring the runner it has to it. Raises when it cannot.

    A runner started the old way is removed first; GitHub then lets the new one take over its name (``--replace``).
    A current pair is only started if it stopped. The registration token is asked for only when the runner is
    (re)created and goes only to a one-off container — if the stored registration exists, that container keeps it
    and the token is never used."""
    current = state(slug)
    env = compose_env(slug, repo_full)
    if current == LEGACY:
        _docker(["docker", "rm", "-f", runner_container(slug)], timeout=DOCKER_TIMEOUT)
        logger.info("CI runner of %s started the old way (live Docker socket) removed", slug)
    if current != CURRENT:
        token = github_validation.runner_registration_token(repo_full, timeout=GITHUB_TIMEOUT)
        _docker([*_compose(), "run", "--rm", "--no-TTY", "runner", "true"], env={**env, "RUNNER_TOKEN": token})
    _docker([*_compose(), "up", "-d", "--wait"], env=env)
    logger.info("CI runner of %s runs with a Docker of its own (was: %s)", slug, current)


def deprovision(slug: str) -> None:
    """Remove the project's runner with its daemon and volumes — best effort, never raises (a deleted project).

    GitHub then shows the runner offline; a project founded again under the same name takes the name over. Compose
    goes by the project name alone (a deleted project has no repository to fill the template with) and runs from
    ``/``, so no compose file in the working directory joins in."""
    try:
        if state(slug) == LEGACY:
            _docker(["docker", "rm", "-f", runner_container(slug)], timeout=DOCKER_TIMEOUT)
        _docker(["docker", "compose", "--project-name", compose_project(slug), "down", "--volumes"], cwd="/")
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        logger.warning("CI runner of deleted project %s not removed: %s", slug, exc)


def _compose() -> list[str]:
    return ["docker", "compose", "--file", str(COMPOSE_FILE)]


def _docker(
    cmd: list[str], *, env: dict[str, str] | None = None, timeout: float = COMPOSE_TIMEOUT, cwd: str | None = None
) -> str:
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False, env=env, cwd=cwd)
    if result.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd[:5])} failed: {result.stderr.strip()[-500:]}")
    return result.stdout
