"""DEV-158 — the repository tab says where the project's CI runs, and starts its own runner when it has none.

20.10.2026 GitHub warned that the account had used 90 % of its Actions minutes. NEX Inbox — a project the cockpit took
over, not founded — had run its CI on GitHub's machines since 14.06.2026, against ICC decision D-009 (all CI on runners
of our own on ANDROS): the cockpit gives a runner only to a project it founds, and nothing showed that a project's jobs
ran elsewhere. 1 640 paid minutes went by in October alone. Director 20.10.2026: „Súhlasím“.

* **The runner** — the project's pair (:mod:`backend.services.ci_runner`) and what GitHub says of it.
* **One click** — the Manažér starts a missing runner exactly as founding does
  (:func:`backend.services.create_project_postscaffold.start_ci_runner`). That takes up to a few minutes, so it runs
  after the answer and the tab asks again until it is done. :func:`decide` is the one rule for both the button and
  the request it sends — the button is never offered for something the request would refuse.
* **Jobs elsewhere** — every job of the project's workflows whose ``runs-on`` does not name the runner, read from the
  project's checkout. Changing those is a change of the project (a version or a fast fix), not of the cockpit.
"""

from __future__ import annotations

import logging
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

from backend.services import ci_runner, ci_runs, create_project_postscaffold, github_validation

logger = logging.getLogger(__name__)

ARCHIVED = "archived"

START = "Spustiť vykonávač kontrol"
RENEW = "Obnoviť vykonávač kontrol"
STARTING = "Vykonávač kontrol sa spúšťa — zvyčajne do dvoch minút; stav sa obnoví sám."
NO_REPOSITORY = "Projekt nemá repozitár na GitHube — vykonávač kontrol nemá kde sa prihlásiť."
IS_ARCHIVED = "Projekt je archivovaný — vykonávač kontrol mu kokpit nespúšťa."
DOCKER_UNKNOWN = "Stav vykonávača kontrol sa nedá zistiť — kokpit sa teraz nevie opýtať Dockeru servera."
BUSY = "Vykonávač práve robí kontrolu — obnovenie by ju zrušilo. Obnov ho, keď kontrola skončí."
ALREADY_STARTING = "Vykonávač kontrol sa už spúšťa."


@dataclass(frozen=True)
class JobElsewhere:
    """A job whose ``runs-on`` does not name the project's runner — it runs on GitHub's machines or somewhere else."""

    workflow: str
    job: str
    runs_on: str


@dataclass
class Overview:
    label: str
    repo: Optional[str]
    state: Optional[str]
    github_status: Optional[str]
    starting: bool
    healthy: bool
    summary: str
    action: Optional[str]
    note: Optional[str]
    jobs_elsewhere: list[JobElsewhere] = field(default_factory=list)
    unreadable_workflows: list[str] = field(default_factory=list)


# --- the start, after the answer ------------------------------------------------------------------------------------

_lock = threading.Lock()
_starting: set[str] = set()
#: What the last start of a project's runner ended with, when it did not end online — shown until the runner is.
_notes: dict[str, str] = {}


def claim(slug: str) -> bool:
    """Mark the project's runner as starting; ``False`` when it already is."""
    with _lock:
        if slug in _starting:
            return False
        _starting.add(slug)
        _notes.pop(slug, None)
        return True


def run_start(slug: str, repo_url: Optional[str], github_org: Optional[str]) -> None:
    """Start the runner as founding does and remember how it ended — runs after the answer, never raises."""
    note: Optional[str] = create_project_postscaffold.CI_RUNNER_NOT_STARTED
    try:
        note = create_project_postscaffold.start_ci_runner(slug, repo_url, github_org=github_org)
    except Exception:  # noqa: BLE001 — start_ci_runner never raises; this keeps the flag from sticking if it did
        logger.exception("CI runner start of %s failed", slug)
    finally:
        with _lock:
            _starting.discard(slug)
            if note:
                _notes[slug] = note
            else:
                _notes.pop(slug, None)
    logger.info("CI runner of %s started by the Manažér: %s", slug, note or "online")


def forget_all() -> None:
    """Tests only: no project is starting and no start left a note."""
    with _lock:
        _starting.clear()
        _notes.clear()


# --- what the project has --------------------------------------------------------------------------------------------


def jobs_elsewhere(checkout: Path, slug: str) -> tuple[list[JobElsewhere], list[str]]:
    """The jobs of the checkout's workflows that do not run on the project's runner, and the workflows not readable.

    A job calling a reusable workflow (``uses:``) has no ``runs-on`` of its own — the workflow it calls is read like
    any other when it lives in the repository."""
    label = ci_runner.runner_label(slug)
    folder = checkout / ".github" / "workflows"
    if not folder.is_dir():
        return [], []
    found: list[JobElsewhere] = []
    unreadable: list[str] = []
    for path in sorted([*folder.glob("*.yml"), *folder.glob("*.yaml")]):
        try:
            workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, yaml.YAMLError):
            unreadable.append(path.name)
            continue
        jobs = workflow.get("jobs") if isinstance(workflow, dict) else None
        if not isinstance(jobs, dict):
            unreadable.append(path.name)
            continue
        for name, job in jobs.items():
            if not isinstance(job, dict) or "runs-on" not in job:
                continue
            labels = _labels(job["runs-on"])
            if label not in labels:
                found.append(JobElsewhere(workflow=path.name, job=str(name), runs_on=", ".join(labels)))
    return found, unreadable


def _labels(runs_on: Any) -> list[str]:
    """The labels a ``runs-on`` asks for — a string, a list, or ``{group, labels}``."""
    if isinstance(runs_on, list):
        return [str(label) for label in runs_on]
    if isinstance(runs_on, dict):
        if "labels" in runs_on:
            return _labels(runs_on["labels"])
        return [f"group: {runs_on.get('group')}"]
    return [str(runs_on)]


def decide(
    *,
    label: str,
    repo: Optional[str],
    archived: bool,
    state: Optional[str],
    github_status: Optional[str],
    github_error: bool,
    starting: bool,
) -> tuple[str, Optional[str]]:
    """``(what the Manažér reads, the button — or None when there is nothing to offer)``. One rule for the tab and
    for the start request: what this offers, the request accepts."""
    if repo is None:
        return NO_REPOSITORY, None
    if archived:
        return IS_ARCHIVED, None
    if starting:
        return STARTING, None
    if state is None:
        return DOCKER_UNKNOWN, None
    if state == ci_runner.NONE:
        return "Projekt nemá vlastný vykonávač kontrol na ANDROSe.", START
    if state == ci_runner.OUTDATED:
        return f"Vykonávač kontrol {label} beží podľa staršieho predpisu.", RENEW
    if state == ci_runner.LEGACY:
        return f"Vykonávač kontrol {label} ešte používa ostrý Docker servera, nie vlastný.", RENEW
    if github_status == "online":
        return f"Kontroly bežia na vlastnom vykonávači {label} na ANDROSe, prihlásenom na GitHube.", None
    if github_error:
        return f"Vykonávač kontrol {label} je na ANDROSe; či je prihlásený na GitHube, sa teraz nedá zistiť.", None
    return f"Vykonávač kontrol {label} je na ANDROSe, ale na GitHube nie je prihlásený — kontroly čakajú.", START


def overview(*, slug: str, repo_url: Optional[str], archived: bool, checkout: Path, github_timeout: float) -> Overview:
    """Everything the tab shows about the project's runner. Asks Docker and GitHub — call it off the event loop."""
    label = ci_runner.runner_label(slug)
    repo = ci_runs.repo_of(repo_url)
    elsewhere, unreadable = jobs_elsewhere(checkout, slug)
    with _lock:
        starting = slug in _starting
        note = _notes.get(slug)

    state: Optional[str] = None
    try:
        state = ci_runner.state(slug)
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        logger.warning("CI runner state of %s unreadable: %s", slug, exc)

    github_status: Optional[str] = None
    github_error = False
    if repo is not None:
        try:
            github_status = github_validation.runner_status(repo, label, timeout=github_timeout)
        except Exception as exc:  # noqa: BLE001 — an unreadable GitHub is said, never guessed as offline
            logger.warning("CI runner status of %s unreadable: %s", slug, exc)
            github_error = True

    summary, action = decide(
        label=label,
        repo=repo,
        archived=archived,
        state=state,
        github_status=github_status,
        github_error=github_error,
        starting=starting,
    )
    healthy = state == ci_runner.CURRENT and github_status == "online"
    return Overview(
        label=label,
        repo=repo,
        state=state,
        github_status=github_status,
        starting=starting,
        healthy=healthy,
        summary=summary,
        action=action,
        note=None if healthy or starting else note,
        jobs_elsewhere=elsewhere,
        unreadable_workflows=unreadable,
    )


def busy(repo: str, slug: str, *, github_timeout: float) -> bool:
    """Whether the runner is running a job now — renewing it would kill the job (as ``backend.scripts.ci_runners``)."""
    return github_validation.runner_busy(repo, ci_runner.runner_label(slug), timeout=github_timeout)
