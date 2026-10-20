"""DEV-158 — the repository tab: where the project's CI runs, and its own runner on ANDROS."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class CiJobElsewhereRead(BaseModel):
    """A job whose ``runs-on`` does not name the project's runner."""

    workflow: str
    job: str
    runs_on: str


class CiRunnerRead(BaseModel):
    """The project's runner as the Manažér reads it; ``action`` is the button, absent when there is nothing to do."""

    label: str
    repo: Optional[str] = None
    state: Optional[str] = None
    github_status: Optional[str] = None
    starting: bool
    healthy: bool
    summary: str
    action: Optional[str] = None
    note: Optional[str] = None
    jobs_elsewhere: list[CiJobElsewhereRead]
    unreadable_workflows: list[str]
