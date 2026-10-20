"""DEV-158 — ``/api/v1/projects/{slug}/ci-runner``: the project's own runner, and the jobs that run elsewhere."""

from __future__ import annotations

import asyncio
from dataclasses import asdict

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy.orm import Session

from backend.core import authz
from backend.core.security import get_current_user
from backend.db.models.foundation import User
from backend.db.models.projects import Project
from backend.db.session import get_db
from backend.schemas.ci_runner import CiRunnerRead
from backend.services import ci_runner, claude_agent, project_ci_runner, system_setting
from backend.services.template_bootstrap import resolve_github_org

router = APIRouter(tags=["CI Runner"])


async def _overview(db: Session, project: Project) -> project_ci_runner.Overview:
    timeout = system_setting.get_int(db, "github_api_timeout_seconds")
    return await asyncio.to_thread(
        project_ci_runner.overview,
        slug=project.slug,
        repo_url=project.repo_url,
        archived=project.status == project_ci_runner.ARCHIVED,
        checkout=claude_agent.PROJECTS_ROOT / project.slug,
        github_timeout=timeout,
    )


def _read(found: project_ci_runner.Overview) -> CiRunnerRead:
    return CiRunnerRead(**asdict(found))


@router.get("/projects/{slug}/ci-runner", response_model=CiRunnerRead)
async def get_ci_runner(
    slug: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CiRunnerRead:
    """Whether the project's CI runs on its own runner on ANDROS, and which of its jobs run elsewhere."""
    project = authz.assert_project_slug_access(db, current_user, slug)
    return _read(await _overview(db, project))


@router.post("/projects/{slug}/ci-runner/start", response_model=CiRunnerRead, status_code=status.HTTP_202_ACCEPTED)
async def start_project_ci_runner(
    slug: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CiRunnerRead:
    """Start (or renew) the project's runner as founding does. Accepted only where the tab offers the button; the
    start itself runs after the answer and the tab follows it with ``GET``."""
    project = authz.assert_project_slug_access(db, current_user, slug)
    found = await _overview(db, project)
    if found.action is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=found.summary)
    if found.repo and found.state in (ci_runner.OUTDATED, ci_runner.LEGACY):
        timeout = system_setting.get_int(db, "github_api_timeout_seconds")
        try:
            busy = await asyncio.to_thread(project_ci_runner.busy, found.repo, project.slug, github_timeout=timeout)
        except Exception as exc:  # noqa: BLE001 — renewing a runner that may be mid-job is never a guess
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="GitHub sa teraz nedá opýtať, či vykonávač nerobí kontrolu — skús to o chvíľu.",
            ) from exc
        if busy:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=project_ci_runner.BUSY)
    if not project_ci_runner.claim(project.slug):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=project_ci_runner.ALREADY_STARTING)
    background_tasks.add_task(project_ci_runner.run_start, project.slug, project.repo_url, resolve_github_org(db))
    return _read(await _overview(db, project))
