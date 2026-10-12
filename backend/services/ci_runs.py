"""DEV-84 — the CI runs of a project's GitHub repository, for the repository tab.

12.10.2026 the Director keeps GitHub open on another monitor to follow CI and wants a tab in the cockpit that shows at
least what GitHub's Actions page does. The tab lists the runs as GitHub's Actions page does, and adds
what only the cockpit knows: the task a commit belongs to and the commits that run at a customer.

* **One question per repository, shared.** GitHub is asked at most once per :attr:`Settings.ci_runs_cache_seconds`
  per repository, whoever looks and however many windows are open; the token stays on the server.
* **The failed step is read once.** A finished run's jobs never change, so the step that failed is asked for once per
  run attempt and kept.
* **Never raises, never pretends.** When GitHub cannot be asked, the last answer stays on the screen with the reason
  and the time it is from; with no answer at all, the reason alone. Old runs are never shown as fresh.
* **Red is the gate's red.** A run is „Zlyhalo“ by the same conclusions the verification gate floors a version on
  (:data:`backend.services.ci_status.CI_CONCLUSIONS_RED`), so the tab and the gate can never disagree.
"""

from __future__ import annotations

import asyncio
import re
import subprocess
import time
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.config.settings import settings
from backend.db.models.customers import Customer
from backend.db.models.projects import Project
from backend.db.models.tasks import Epic, Feat, Task
from backend.db.models.versions import Version
from backend.services import ci_status, deploy
from backend.services.github_validation import GITHUB_API_BASE, github_headers

# ── what a run is ──────────────────────────────────────────────────────────────────────────────────────────────────

SUCCESS = "success"
FAILURE = "failure"
RUNNING = "running"
WAITING = "waiting"
CANCELLED = "cancelled"
OTHER = "other"

STATE_LABELS = {
    SUCCESS: "Prešlo",
    FAILURE: "Zlyhalo",
    RUNNING: "Beží",
    WAITING: "Čaká",
    CANCELLED: "Zrušené",
    OTHER: "Bez výsledku",
}

#: The finished-but-fine conclusions get their own word; their colour is the success one (they did not fail).
_OK_WORDS = {"success": "Prešlo", "skipped": "Preskočené", "neutral": "Bez chyby"}

EVENT_LABELS = {
    "push": "odoslanie",
    "workflow_dispatch": "spustené ručne",
    "pull_request": "žiadosť o zlúčenie",
    "schedule": "plánované",
    "release": "vydanie",
}

#: The number the AI Agent writes into every commit of a task (DEV-75): ``(TASK #20.1.1)``.
_TASK_LABEL = re.compile(r"TASK #(\d+\.\d+\.\d+)")

NO_REPOSITORY = "Projekt nemá repozitár na GitHube — kokpit nemá odkiaľ behy CI čítať."
NO_TOKEN = "Kokpit nemá prístup ku GitHubu (chýba prístupový token na serveri)."
NOT_REACHED = "GitHub neodpovedal"


def state_of(status: Optional[str], conclusion: Optional[str]) -> tuple[str, str]:
    """``(state, word)`` of a run — the state picks the colour, the word is what the person reads."""
    if status == "in_progress":
        return RUNNING, STATE_LABELS[RUNNING]
    if status != "completed":
        return WAITING, STATE_LABELS[WAITING]
    if conclusion in ci_status.CI_CONCLUSIONS_RED:
        return FAILURE, STATE_LABELS[FAILURE]
    if conclusion in ci_status.CI_CONCLUSIONS_OK:
        return SUCCESS, _OK_WORDS.get(conclusion or "", STATE_LABELS[SUCCESS])
    if conclusion == "cancelled":
        return CANCELLED, STATE_LABELS[CANCELLED]
    return OTHER, STATE_LABELS[OTHER]


def _when(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


@dataclass(frozen=True)
class Run:
    """One run as GitHub reported it, already in the words the tab shows."""

    id: int
    number: int
    attempt: int
    workflow: str
    subject: str
    event: str
    state: str
    state_label: str
    branch: str
    sha: str
    actor: str
    created_at: Optional[datetime]
    started_at: Optional[datetime]
    updated_at: Optional[datetime]
    url: str
    failed_step: Optional[str] = None

    @property
    def event_label(self) -> str:
        return EVENT_LABELS.get(self.event, self.event)

    def duration_seconds(self, now: datetime) -> Optional[int]:
        """How long it ran — up to its last change once finished, up to ``now`` while it runs."""
        if self.started_at is None:
            return None
        end = now if self.state in (RUNNING, WAITING) else self.updated_at
        if end is None:
            return None
        return max(0, int((end - self.started_at).total_seconds()))


def run_from_github(row: dict) -> Run:
    """GitHub's run → :class:`Run`. The subject is the commit's first line, not GitHub's title: a run started by hand
    is titled with the workflow's name alone — the rows „CI“ without a description the Director asked about."""
    commit = (row.get("head_commit") or {}).get("message") or ""
    subject = commit.split("\n", 1)[0].strip() or str(row.get("display_title") or "")
    state, word = state_of(row.get("status"), row.get("conclusion"))
    actor = (row.get("triggering_actor") or row.get("actor") or {}).get("login") or ""
    return Run(
        id=int(row["id"]),
        number=int(row.get("run_number") or 0),
        attempt=int(row.get("run_attempt") or 1),
        workflow=str(row.get("name") or ""),
        subject=subject,
        event=str(row.get("event") or ""),
        state=state,
        state_label=word,
        branch=str(row.get("head_branch") or ""),
        sha=str(row.get("head_sha") or ""),
        actor=actor,
        created_at=_when(row.get("created_at")),
        started_at=_when(row.get("run_started_at")) or _when(row.get("created_at")),
        updated_at=_when(row.get("updated_at")),
        url=str(row.get("html_url") or ""),
    )


def failed_step_from_jobs(jobs: list[dict]) -> Optional[str]:
    """``„job › step“`` of the first job that failed; the job alone when no step says so (a job that never started)."""
    for job in jobs:
        if job.get("conclusion") not in ci_status.CI_CONCLUSIONS_RED:
            continue
        for step in job.get("steps") or []:
            if step.get("conclusion") in ci_status.CI_CONCLUSIONS_RED:
                return f"{job.get('name', '')} › {step.get('name', '')}"
        return str(job.get("name") or "") or None
    return None


# ── asking GitHub, once for everybody ──────────────────────────────────────────────────────────────────────────────


@dataclass
class Listing:
    """What the tab shows for one repository: the newest runs and how many the repository has in total."""

    repo: Optional[str]
    runs: list[Run] = field(default_factory=list)
    total_count: int = 0
    fetched_at: Optional[datetime] = None
    #: Why the runs are not GitHub's answer of now — then they are the last answer (``stale``) or none.
    error: Optional[str] = None
    stale: bool = False


class _GitHubRefused(Exception):
    pass


_listings: dict[str, tuple[float, Listing]] = {}
_failed_steps: dict[tuple[int, int], Optional[str]] = {}
_locks: dict[str, asyncio.Lock] = {}


def _clock() -> float:
    """The age of answers — its own name, so a test moves it without stopping the event loop's clock."""
    return time.monotonic()


def forget_all() -> None:
    """Drop what was remembered — for tests."""
    _listings.clear()
    _failed_steps.clear()
    _locks.clear()


async def _get(client: httpx.AsyncClient, path: str, params: Optional[dict] = None) -> dict:
    response = await client.get(f"{GITHUB_API_BASE}/{path}", params=params, headers=github_headers())
    if response.status_code == 404:
        raise _GitHubRefused("repozitár na GitHube nie je, alebo k nemu kokpit nemá prístup")
    if response.status_code in (401, 403):
        raise _GitHubRefused(f"GitHub odmietol prístup (kód {response.status_code})")
    if response.status_code != 200:
        raise _GitHubRefused(f"GitHub odpovedal kódom {response.status_code}")
    return response.json()


async def _failed_step(client: httpx.AsyncClient, repo: str, run: Run) -> Optional[str]:
    key = (run.id, run.attempt)
    if key not in _failed_steps:
        data = await _get(client, f"repos/{repo}/actions/runs/{run.id}/attempts/{run.attempt}/jobs", {"per_page": 100})
        _failed_steps[key] = failed_step_from_jobs(data.get("jobs") or [])
    return _failed_steps[key]


async def _ask_github(repo: str, timeout: float) -> Listing:
    async with httpx.AsyncClient(timeout=timeout) as client:
        data = await _get(client, f"repos/{repo}/actions/runs", {"per_page": settings.ci_runs_listed})
        runs = [run_from_github(row) for row in data.get("workflow_runs") or []]
        failed = [r for r in runs if r.state == FAILURE]
        steps = await asyncio.gather(*(_failed_step(client, repo, r) for r in failed), return_exceptions=True)
    by_id = {r.id: (s if isinstance(s, str) else None) for r, s in zip(failed, steps)}
    runs = [replace(r, failed_step=by_id.get(r.id)) for r in runs]
    return Listing(
        repo=repo,
        runs=runs,
        total_count=int(data.get("total_count") or len(runs)),
        fetched_at=datetime.now(timezone.utc),
    )


async def listing(repo: Optional[str], *, timeout: float) -> Listing:
    """The repository's newest runs — GitHub's answer of at most :attr:`Settings.ci_runs_cache_seconds` ago."""
    if not repo:
        return Listing(repo=None, error=NO_REPOSITORY)
    if not settings.github_token:
        return Listing(repo=repo, error=NO_TOKEN)
    lock = _locks.setdefault(repo, asyncio.Lock())
    async with lock:  # a second window waits for the first one's answer instead of asking again
        remembered = _listings.get(repo)
        if remembered and _clock() - remembered[0] < settings.ci_runs_cache_seconds:
            return remembered[1]
        try:
            fresh = await _ask_github(repo, timeout)
        except (_GitHubRefused, httpx.HTTPError, ValueError) as exc:
            why = str(exc) if isinstance(exc, _GitHubRefused) else NOT_REACHED
            last = remembered[1] if remembered and remembered[1].fetched_at else None
            fresh = replace(last, error=why, stale=True) if last else Listing(repo=repo, error=why)
        _listings[repo] = (_clock(), fresh)
        return fresh


# ── what only the cockpit knows ────────────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TaskLink:
    label: str
    task_id: uuid.UUID
    title: str
    version_id: uuid.UUID
    version_number: str


@dataclass(frozen=True)
class Deployed:
    environment: str  # uat · prod
    customer: str
    version: str


def task_label(subject: str) -> Optional[str]:
    match = _TASK_LABEL.search(subject)
    return match.group(1) if match else None


def task_links(db: Session, project_id: uuid.UUID, labels: set[str]) -> dict[str, TaskLink]:
    """``{„20.1.1“: the task}`` — epic numbers run through the whole project, so a label names one task."""
    if not labels:
        return {}
    rows = db.execute(
        select(Task.id, Task.title, Task.number, Feat.number, Epic.number, Version.id, Version.version_number)
        .join(Feat, Feat.id == Task.feat_id)
        .join(Epic, Epic.id == Feat.epic_id)
        .join(Version, Version.id == Epic.version_id)
        .where(Epic.project_id == project_id)
    ).all()
    links = {}
    for task_id, title, t, f, e, version_id, version_number in rows:
        label = f"{e}.{f}.{t}"
        if label in labels:
            links[label] = TaskLink(label, task_id, title, version_id, version_number)
    return links


def _tag_commit(project_root: Path, version_number: str) -> Optional[str]:
    """The commit the version's tag names — the verified commit a deploy of that version runs."""
    try:
        r = subprocess.run(
            [
                "git",
                "-C",
                str(project_root),
                "rev-parse",
                "--verify",
                "--quiet",
                f"refs/tags/v{version_number}^{{commit}}",
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else None


def deployed_versions(db: Session, project: Project) -> list[Deployed]:
    """Every customer's UAT and PROD version of the project — PROD first, then by customer."""
    customers = db.execute(select(Customer).where(Customer.project_id == project.id)).scalars().all()
    where = [
        Deployed(environment, customer.name, version)
        for customer in customers
        for environment in ("uat", "prod")
        if (version := deploy.current_version(db, customer.id, environment))
    ]
    return sorted(where, key=lambda d: (d.environment != "prod", d.customer))


def deployed_commits(project_root: Path, where: list[Deployed]) -> dict[str, list[Deployed]]:
    """``{commit: [where it runs]}`` through each version's tag. Asks git — run it off the main loop."""
    commits = {version: _tag_commit(project_root, version) for version in {d.version for d in where}}
    found: dict[str, list[Deployed]] = {}
    for d in where:
        sha = commits.get(d.version)
        if sha:
            found.setdefault(sha, []).append(d)
    return found
