"""The CI runner of a project — a GitHub Actions runner paired with a Docker daemon of its own (DEV-145).

Until 19.10.2026 founding started ``nex-ci-runner-<slug>`` with the server's live ``/var/run/docker.sock``: a CI job
of any project could see, stop and delete the customers' containers and databases on ANDROS, and whatever a
cancelled job left running stayed there. That runner also kept the cockpit's GitHub key in its environment for good.

The pair is described by ``templates/ci-runner/docker-compose.yml`` — why it looks the way it does is in its header.
That file is the only description of it: the cockpit starts it for its projects, and Dedo's ``nastroje/ci_bezec.sh``
starts the cockpit's own runner (the cockpit is not one of its projects) from the same file. Director 19.10.2026:
"Súhlasím".

DEV-215: a project gets ``settings.ci_runners_per_project`` runners — 23.10.2026 NEX Inbox's jobs waited up to 34 min
for its only one. Each is a whole pair of its own (the cleanup hook empties the whole daemon at every job), numbered by
``RUNNER_SUFFIX`` (:func:`suffix`); all carry the project's one label (:func:`runner_label`), so a job goes to whichever
is free. The first keeps the names it always had. Director: „Áno, súhlasím, založ všetkých päť tiketov.“, „Súhlasím“.

DEV-167: the runners' Docker is rootless and holds no rights on the server (the template's header says why). A project
whose workflow names :func:`privileged_label` also gets :data:`PRIVILEGED` — one runner with the old privileged Docker
(``docker-compose.privileged.yml`` laid over the template) that only the jobs naming that label reach. Director
25.10.2026: „Áno, súhlasím, začni DEV-167“.
"""

from __future__ import annotations

import hashlib
import logging
import os
import subprocess
from pathlib import Path
from typing import Any

import yaml

from backend.config.settings import settings
from backend.services import github_validation
from backend.services.claude_agent import PROJECTS_ROOT

logger = logging.getLogger(__name__)

TEMPLATE_DIR = Path(__file__).resolve().parents[2] / "templates" / "ci-runner"
COMPOSE_FILE = TEMPLATE_DIR / "docker-compose.yml"
#: DEV-167: laid over :data:`COMPOSE_FILE` for the project's :data:`PRIVILEGED` runner.
PRIVILEGED_FILE = TEMPLATE_DIR / "docker-compose.privileged.yml"
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


#: DEV-167: the number the functions below take for the project's privileged runner (:func:`needs_privileged`).
PRIVILEGED = 0


def runners() -> list[int]:
    """The numbers of a project's runners, 1… — as many as ``settings.ci_runners_per_project`` (at least one)."""
    return list(range(1, max(1, settings.ci_runners_per_project) + 1))


def project_runners(slug: str) -> list[int]:
    """Every runner the project should have: :func:`runners`, and :data:`PRIVILEGED` when it asks for one."""
    return runners() + ([PRIVILEGED] if needs_privileged(slug) else [])


def suffix(index: int) -> str:
    """What tells the runner ``index`` apart: nothing for the first (its names are the ones it always had), ``_2``…
    for the others — an underscore never appears in a project's name, so ``x``'s second is never ``x-2``'s first —
    and ``_privileged`` for :data:`PRIVILEGED`."""
    if index == PRIVILEGED:
        return "_privileged"
    return "" if index == 1 else f"_{index}"


def privileged_label(slug: str) -> str:
    """The label of the project's privileged runner — a job that needs a privileged Docker names it in ``runs-on``."""
    return f"{runner_label(slug)}-privileged"


def needs_privileged(slug: str) -> bool:
    """Does a job of the project's workflows name :func:`privileged_label` in its ``runs-on``? Read from its own
    workflows, so the runner comes and goes with the job that needs it — never from a list kept somewhere else; a
    workflow that cannot be read asks for nothing (the runner tab names it)."""
    folder = PROJECTS_ROOT / slug / ".github" / "workflows"
    label = privileged_label(slug)
    for path in sorted([*folder.glob("*.yml"), *folder.glob("*.yaml")]):
        try:
            workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, yaml.YAMLError):
            continue
        jobs = workflow.get("jobs") if isinstance(workflow, dict) else None
        if isinstance(jobs, dict) and any(
            isinstance(job, dict) and label in runs_on_labels(job.get("runs-on")) for job in jobs.values()
        ):
            return True
    return False


def runs_on_labels(runs_on: Any) -> list[str]:
    """The labels a job's ``runs-on`` asks for — a string, a list, or ``{group, labels}``."""
    if isinstance(runs_on, list):
        return [str(label) for label in runs_on]
    if isinstance(runs_on, dict):
        if "labels" in runs_on:
            return runs_on_labels(runs_on["labels"])
        return [f"group: {runs_on.get('group')}"]
    return [str(runs_on)]


def runner_container(slug: str, index: int = 1) -> str:
    return f"nex-ci-runner-{slug}{suffix(index)}"


def runner_label(slug: str) -> str:
    """The label every runner of the project carries on GitHub — what its jobs' ``runs-on`` names."""
    return f"andros-ubuntu-{slug}"


def runner_name(slug: str, index: int = 1) -> str:
    """The name the runner ``index`` registers under on GitHub — the first one's is the label, as it always was."""
    return f"{runner_label(slug)}{suffix(index)}"


def runner_names(slug: str) -> list[str]:
    return [runner_name(slug, index) for index in project_runners(slug)]


def compose_project(slug: str, index: int = 1) -> str:
    return f"nex-ci-{slug}{suffix(index)}"


def template_fingerprint(index: int = 1) -> str:
    """sha256 of the template followed by its cleanup hook — for :data:`PRIVILEGED` followed by its override too.

    ``nastroje/ci_bezec.sh`` computes the same (``cat docker-compose.yml cleanup.sh | sha256sum``), so a runner it
    starts is current for the cockpit too."""
    files = [COMPOSE_FILE, CLEANUP_HOOK] + ([PRIVILEGED_FILE] if index == PRIVILEGED else [])
    return hashlib.sha256(b"".join(f.read_bytes() for f in files)).hexdigest()


def compose_env(slug: str, repo_full: str, index: int = 1) -> dict[str, str]:
    """The environment ``docker compose`` reads the template with — never with a registration token in it."""
    env = {name: value for name, value in os.environ.items() if name != "RUNNER_TOKEN"}
    env.update(
        SLUG=slug,
        RUNNER_SUFFIX=suffix(index),
        REPO_URL=f"https://github.com/{repo_full}",
        CI_CLEANUP_HOOK=CLEANUP_HOOK.read_text(encoding="utf-8"),
        CI_RUNNER_TEMPLATE=template_fingerprint(index),
    )
    return env


def state(slug: str) -> str:
    """Which runners the project has now, as one word: :data:`NONE` (not one), :data:`LEGACY` (one still on the live
    Docker), :data:`CURRENT` (every one it should have, from this template, and no more) — else :data:`OUTDATED`."""
    states = [runner_state(slug, index) for index in project_runners(slug)]
    extra = _extra_runners(slug)
    if all(s == NONE for s in states) and not extra:
        return NONE
    if LEGACY in states:
        return LEGACY
    if all(s == CURRENT for s in states) and not extra:
        return CURRENT
    return OUTDATED


def runner_state(slug: str, index: int = 1) -> str:
    """Which runner ``index`` of the project is — :data:`NONE`, :data:`CURRENT`, :data:`OUTDATED` or :data:`LEGACY`."""
    found = _docker(
        [
            "docker",
            "ps",
            "-a",
            "--filter",
            f"name=^{runner_container(slug, index)}$",
            "--format",
            # The name first: a runner started with ``docker run`` has neither label, and its row must not read empty.
            f'{{{{.Names}}}}\t{{{{.Label "com.docker.compose.project"}}}}\t{{{{.Label "{TEMPLATE_LABEL}"}}}}',
        ],
        timeout=DOCKER_TIMEOUT,
    ).strip()
    if not found:
        return NONE
    _, project, fingerprint = (found.split("\t") + ["", ""])[:3]
    if project != compose_project(slug, index):
        return LEGACY
    return CURRENT if fingerprint == template_fingerprint(index) else OUTDATED


def _extra_runners(slug: str) -> list[int]:
    """The project's runners it should not have — numbered beyond :func:`runners` (left over when the setting went
    down), and :data:`PRIVILEGED` once no workflow asks for it."""
    names = _docker(
        [
            "docker",
            "ps",
            "-a",
            "--filter",
            f"name=^{runner_container(slug)}_([0-9]+|privileged)$",
            "--format",
            "{{.Names}}",
        ],
        timeout=DOCKER_TIMEOUT,
    ).split()
    found = {
        PRIVILEGED if end == "privileged" else int(end)
        for end in (name.rsplit("_", 1)[1] for name in names)
        if end == "privileged" or end.isdigit()
    }
    return sorted(found - set(project_runners(slug)))


def github_status(repo_full: str, slug: str, *, timeout: float) -> str | None:
    """``"online"`` when every runner of the project is online on GitHub, ``None`` when one is not registered (yet),
    else ``"offline"``. Raises when GitHub cannot be asked."""
    statuses = [github_validation.runner_status(repo_full, name, timeout=timeout) for name in runner_names(slug)]
    if any(status is None for status in statuses):
        return None
    return "online" if all(status == "online" for status in statuses) else "offline"


def github_busy(repo_full: str, slug: str, *, timeout: float) -> bool:
    """Whether any runner of the project is running a job now — recreating it would kill that job."""
    return any(github_validation.runner_busy(repo_full, name, timeout=timeout) for name in runner_names(slug))


def provision(slug: str, repo_full: str) -> None:
    """Start every runner the project should have from the template, or bring the ones it has to it, and remove the
    ones beyond the setting. Raises when it cannot.

    A runner started the old way is removed first; GitHub then lets the new one take over its name (``--replace``).
    A current pair is only started if it stopped. The registration token is asked for once, only when a runner is
    (re)created, and goes only to one-off containers — if the stored registration exists, that container keeps it and
    the token is never used."""
    token: str | None = None
    for index in project_runners(slug):
        current = runner_state(slug, index)
        env = compose_env(slug, repo_full, index)
        if current == LEGACY:
            _docker(["docker", "rm", "-f", "-v", runner_container(slug, index)], timeout=DOCKER_TIMEOUT)
            logger.info("CI runner %s started the old way (live Docker socket) removed", runner_container(slug, index))
        if current != CURRENT:
            token = token or github_validation.runner_registration_token(repo_full, timeout=GITHUB_TIMEOUT)
            _docker([*_compose(index), "run", "--rm", "--no-TTY", "runner", "true"], env={**env, "RUNNER_TOKEN": token})
        _docker([*_compose(index), "up", "-d", "--wait"], env=env)
        logger.info("CI runner %s runs with a Docker of its own (was: %s)", runner_container(slug, index), current)
    for index in _extra_runners(slug):
        _down(slug, index)
        logger.info("CI runner %s the project should not have removed", runner_container(slug, index))


def deprovision(slug: str) -> None:
    """Remove every runner of the project with its daemon and volumes — best effort, never raises (a deleted project).

    GitHub then shows the runners offline; a project founded again under the same name takes the names over. Compose
    goes by the project name alone (a deleted project has no repository to fill the template with) and runs from
    ``/``, so no compose file in the working directory joins in."""
    try:
        if runner_state(slug) == LEGACY:
            _docker(["docker", "rm", "-f", "-v", runner_container(slug)], timeout=DOCKER_TIMEOUT)
        # DEV-167: the privileged one by what is there — a deleted project may have no workflows left to ask.
        privileged = [PRIVILEGED] if runner_state(slug, PRIVILEGED) != NONE else []
        for index in sorted({*runners(), *_extra_runners(slug), *privileged}):
            _down(slug, index)
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        logger.warning("CI runner of deleted project %s not removed: %s", slug, exc)


def _down(slug: str, index: int) -> None:
    _docker(["docker", "compose", "--project-name", compose_project(slug, index), "down", "--volumes"], cwd="/")


def _compose(index: int = 1) -> list[str]:
    files = [COMPOSE_FILE] + ([PRIVILEGED_FILE] if index == PRIVILEGED else [])
    return ["docker", "compose", *(arg for f in files for arg in ("--file", str(f)))]


def _docker(
    cmd: list[str], *, env: dict[str, str] | None = None, timeout: float = COMPOSE_TIMEOUT, cwd: str | None = None
) -> str:
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False, env=env, cwd=cwd)
    if result.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd[:5])} failed: {result.stderr.strip()[-500:]}")
    return result.stdout
