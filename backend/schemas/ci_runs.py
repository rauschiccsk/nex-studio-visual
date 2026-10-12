"""DEV-84 — the repository tab: a project's CI runs as GitHub lists them, with what only the cockpit knows."""

from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel


class CiRunTask(BaseModel):
    """The task whose commit the run checked — from ``(TASK #20.1.1)`` in the commit's subject."""

    label: str
    task_id: UUID
    title: str
    version_id: UUID
    version_number: str


class CiRunDeployed(BaseModel):
    """A customer whose UAT or PROD runs the run's commit."""

    environment: str
    customer: str
    version: str


class CiRunRead(BaseModel):
    id: int
    number: int
    attempt: int
    workflow: str
    subject: str
    event: str
    event_label: str
    #: success · failure · running · waiting · cancelled · other — picks the colour.
    state: str
    state_label: str
    branch: str
    sha: str
    actor: str
    created_at: Optional[datetime] = None
    duration_seconds: Optional[int] = None
    url: str
    failed_step: Optional[str] = None
    task: Optional[CiRunTask] = None
    deployed: list[CiRunDeployed] = []


class CiRunsRead(BaseModel):
    #: ``owner/name`` on GitHub; ``None`` when the project has none.
    repo: Optional[str] = None
    #: The repository's Actions page on GitHub — for the runs older than the list.
    actions_url: Optional[str] = None
    total_count: int = 0
    fetched_at: Optional[datetime] = None
    #: Why the runs are not GitHub's answer of now; with ``stale`` they are the last answer, from ``fetched_at``.
    error: Optional[str] = None
    stale: bool = False
    #: How often the server asks GitHub — the screen asks no more often.
    refresh_seconds: int
    runs: list[CiRunRead] = []
