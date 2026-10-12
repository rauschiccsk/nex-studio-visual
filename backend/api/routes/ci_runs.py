"""DEV-84 — ``GET /api/v1/projects/{slug}/ci-runs``: the repository tab of the selected project."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.config.settings import settings
from backend.core import authz
from backend.core.security import get_current_user
from backend.db.models.foundation import User
from backend.db.session import get_db
from backend.schemas.ci_runs import CiRunDeployed, CiRunRead, CiRunsRead, CiRunTask
from backend.services import ci_runs, claude_agent, system_setting

router = APIRouter(tags=["CI Runs"])


def _repo(repo_url: Optional[str]) -> Optional[str]:
    """``owner/name`` from what the project keeps — the short form, or a GitHub address."""
    text = (repo_url or "").strip().removesuffix(".git")
    if "github.com" in text:
        text = text.split("github.com", 1)[1].lstrip("/:")
    text = text.strip("/")
    return text if text.count("/") == 1 else None


@router.get("/projects/{slug}/ci-runs", response_model=CiRunsRead)
async def get_ci_runs(
    slug: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CiRunsRead:
    """The project's newest CI runs as GitHub lists them, with the task of each commit and the customers it runs at.

    GitHub is asked at most once per ``ci_runs_cache_seconds`` per repository for everybody; the token stays on the
    server. Never fails on GitHub: the reason comes in ``error``, the last answer with ``stale``."""
    project = authz.assert_project_slug_access(db, current_user, slug)
    repo = _repo(project.repo_url)
    found = await ci_runs.listing(repo, timeout=system_setting.get_int(db, "github_api_timeout_seconds"))

    labels = {label for run in found.runs if (label := ci_runs.task_label(run.subject))}
    tasks = ci_runs.task_links(db, project.id, labels)
    where = ci_runs.deployed_versions(db, project) if found.runs else []
    deployed = (
        await asyncio.to_thread(ci_runs.deployed_commits, claude_agent.PROJECTS_ROOT / project.slug, where)
        if where
        else {}
    )
    now = datetime.now(timezone.utc)
    return CiRunsRead(
        repo=found.repo,
        actions_url=f"https://github.com/{found.repo}/actions" if found.repo else None,
        total_count=found.total_count,
        fetched_at=found.fetched_at,
        error=found.error,
        stale=found.stale,
        refresh_seconds=settings.ci_runs_cache_seconds,
        runs=[
            CiRunRead(
                id=run.id,
                number=run.number,
                attempt=run.attempt,
                workflow=run.workflow,
                subject=run.subject,
                event=run.event,
                event_label=run.event_label,
                state=run.state,
                state_label=run.state_label,
                branch=run.branch,
                sha=run.sha,
                actor=run.actor,
                created_at=run.created_at,
                duration_seconds=run.duration_seconds(now),
                url=run.url,
                failed_step=run.failed_step,
                task=(CiRunTask(**vars(link)) if (link := tasks.get(ci_runs.task_label(run.subject) or "")) else None),
                deployed=[CiRunDeployed(**vars(d)) for d in deployed.get(run.sha, [])],
            )
            for run in found.runs
        ],
    )
