"""„Kde stojíme“ on the Prehľad — where every project's development stands (DEV-150)."""

from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel

from backend.schemas.version_note import VersionNoteRead


class OpenVersionStanding(BaseModel):
    """A version whose build is not done — its phase, its state, since when it waits on a person, its tasks."""

    version_id: UUID
    version_number: str
    #: The build's phase (priprava … verifikacia) and state (agent_working, awaiting_manazer, blocked, paused).
    stage: str
    status: str
    block_reason: Optional[str] = None
    #: Since when the build waits on the Manažér — set only while it does.
    waiting_since: Optional[datetime] = None
    tasks_done: int = 0
    tasks_total: int = 0
    fast_fix: bool = False
    #: DEV-151: the newest „Kde sme skončili“ — why the people stopped and what next, in their own words.
    last_note: Optional[VersionNoteRead] = None


class ProjectStanding(BaseModel):
    project_id: UUID
    name: str
    slug: str
    open_versions: list[OpenVersionStanding]
    #: The version last deployed to PROD with success, for any customer — ``None`` when none ever was.
    prod_version: Optional[str] = None


class StandingRead(BaseModel):
    """The projects the person works on, those waiting on a person first, the longest waiting at the top."""

    projects: list[ProjectStanding]
