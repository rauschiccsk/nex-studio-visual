"""DEV-85 — a failed CI run on a project's main branch reaches the project's addressee on Telegram once, and so does the
return to green; the repository tab is marked red while the main branch is.

12.10.2026 the Director keeps GitHub open on a second monitor to see when CI fails. Now the cockpit watches:

* **The main branch is the branch the cockpit pushes** — the one its checkout of the project is on (DEV-96), read from
  git, never assumed to be ``main``.
* **Per workflow.** A commit starts a run of every workflow (NEX Inbox: CI and the release gate); each is followed
  on its own — the newest finished run of each workflow on the branch.
* **Once.** A failed run is told when it is first seen finished; seeing it again tells nothing. A green run after a red
  one of the same workflow is told once as „znovu zelené“. A re-run of a failed run is a new attempt and counts anew.
* **Nothing old is news.** The first time a project is seen, what is there is remembered and nothing is sent — the
  watch tells what happens from now on, not the history of the repository.
* **Kept in the database** (:class:`ProjectCiWatch`), so a restart or a deploy of the cockpit never repeats a message;
  a message Telegram refused is tried again until :attr:`Settings.ci_watch_retry_hours` pass.
* **No extra questions to GitHub.** The runs come from :func:`ci_runs.listing`, the answer the repository tab shares.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.config.settings import settings
from backend.db.models.foundation import User
from backend.db.models.project_ci_watch import ProjectCiWatch
from backend.db.models.projects import Project
from backend.services import ci_runs, claude_agent, notify, system_setting

logger = logging.getLogger(__name__)

FAILED = "failure"
RECOVERED = "recovery"
_FINISHED = (ci_runs.SUCCESS, ci_runs.FAILURE, ci_runs.CANCELLED, ci_runs.OTHER)


def checkout_branch(project_root: Path) -> Optional[str]:
    """The branch the cockpit's checkout of the project is on — the branch it pushes; ``None`` when unknown."""
    try:
        r = subprocess.run(
            ["git", "-C", str(project_root), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    branch = r.stdout.strip()
    return branch if r.returncode == 0 and branch and branch != "HEAD" else None


def newest_finished(runs: list[ci_runs.Run], branch: str) -> dict[str, ci_runs.Run]:
    """``{workflow: its newest finished run on branch}`` — ``runs`` come newest first, as GitHub lists them."""
    found: dict[str, ci_runs.Run] = {}
    for run in runs:
        if run.branch == branch and run.state in _FINISHED and run.workflow not in found:
            found[run.workflow] = run
    return found


def remembered(run: ci_runs.Run) -> dict:
    return {
        "run_id": run.id,
        "attempt": run.attempt,
        "number": run.number,
        "state": run.state,
        "subject": run.subject,
        "failed_step": run.failed_step,
        "url": run.url,
    }


def changes(seen: dict[str, dict], newest: dict[str, ci_runs.Run]) -> list[tuple[str, ci_runs.Run]]:
    """What to tell: ``(FAILED, run)`` for a failed run not told yet, ``(RECOVERED, run)`` for a green one after red."""
    told: list[tuple[str, ci_runs.Run]] = []
    for workflow, run in newest.items():
        before = seen.get(workflow)
        if before and (before.get("run_id"), before.get("attempt")) == (run.id, run.attempt):
            continue
        if run.state == ci_runs.FAILURE:
            told.append((FAILED, run))
        elif run.state == ci_runs.SUCCESS and before and before.get("state") == ci_runs.FAILURE:
            told.append((RECOVERED, run))
    return told


def link(slug: str) -> str:
    """The repository tab of the project in the cockpit, or why there is no link."""
    base = settings.app_public_url.strip()
    if not base:
        return "(odkaz do kokpitu chýba — na serveri nie je nastavené APP_PUBLIC_URL)"
    return f"{base.rstrip('/')}/repozitar?projekt={slug}"


def message(kind: str, project: Project, branch: str, run: ci_runs.Run) -> str:
    if kind == FAILED:
        step = f"\nZlyhal krok: {run.failed_step}" if run.failed_step else ""
        return (
            f"❌ {project.name}: kontroly na vetve {branch} zlyhali — {run.workflow} #{run.number}, "
            f"commit „{run.subject}“.{step}\n{link(project.slug)}"
        )
    return (
        f"✅ {project.name}: kontroly na vetve {branch} sú znovu zelené — {run.workflow} #{run.number}, "
        f"commit „{run.subject}“.\n{link(project.slug)}"
    )


def failing(row: Optional[ProjectCiWatch]) -> list[dict]:
    """The workflows whose newest finished run on the main branch is red — the red mark of the repository tab."""
    if row is None:
        return []
    return [{"workflow": w, **r} for w, r in sorted((row.runs or {}).items()) if r.get("state") == ci_runs.FAILURE]


def _chat_id(db: Session, project: Project) -> Optional[str]:
    if project.owner_id is None:
        return None
    user = db.get(User, project.owner_id)
    return (user.telegram_chat_id or "").strip() or None if user else None


async def _deliver(texts: list[dict], chat_id: Optional[str], now: datetime) -> list[dict]:
    """Send each message; return the ones Telegram did not take and that are still young enough to try again."""
    left: list[dict] = []
    oldest = now - timedelta(hours=settings.ci_watch_retry_hours)
    for item in texts:
        if not chat_id or datetime.fromisoformat(item["at"]) <= oldest:
            continue  # nobody to tell, or old news by now — a stale alarm would only mislead
        if not await notify.send_telegram(item["text"], chat_id):
            left.append(item)
    return left


async def watch_project(db: Session, project: Project, *, timeout: float) -> list[str]:
    """Look at one project's main branch; returns the messages that were due (sent or kept to try again)."""
    repo = ci_runs.repo_of(project.repo_url)
    root = claude_agent.PROJECTS_ROOT / project.slug
    branch = await asyncio.to_thread(checkout_branch, root)
    if not repo or not branch:
        return []
    # A GitHub that does not answer gives no runs or the ones already seen — either way nothing new to tell.
    found = await ci_runs.listing(repo, timeout=timeout)
    newest = newest_finished(found.runs, branch)
    row = db.get(ProjectCiWatch, project.id)
    now = datetime.now(timezone.utc)
    if row is None or row.branch != branch:
        # First look (or the checkout moved to another branch): remember, tell nothing — history is not news.
        if row is None:
            row = ProjectCiWatch(project_id=project.id, branch=branch)
            db.add(row)
        row.branch, row.runs, row.undelivered = branch, {w: remembered(r) for w, r in newest.items()}, []
        db.flush()
        return []
    due = [message(kind, project, branch, run) for kind, run in changes(row.runs or {}, newest)]
    pending = list(row.undelivered or []) + [{"text": text, "at": now.isoformat()} for text in due]
    row.runs = {**(row.runs or {}), **{w: remembered(r) for w, r in newest.items()}}
    row.undelivered = await _deliver(pending, _chat_id(db, project), now) if pending else []
    db.flush()
    return due


async def watch_once(db: Session) -> None:
    """One look at every active project with a repository. A failure of one project never stops the others."""
    timeout = system_setting.get_int(db, "github_api_timeout_seconds")
    projects = db.execute(
        select(Project).where(Project.repo_url.isnot(None), Project.status == "active").order_by(Project.slug)
    ).scalars()
    for project in list(projects):
        try:
            await watch_project(db, project, timeout=timeout)
            db.commit()
        except Exception:  # noqa: BLE001 — one project's trouble must not silence the others
            db.rollback()
            logger.exception("CI watch of %s failed", project.slug)
