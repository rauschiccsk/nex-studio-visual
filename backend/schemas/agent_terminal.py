"""Pydantic schemas for the embedded agent terminal API.

Mirrors :class:`backend.db.models.agent_terminal.AgentTerminalSession`
for REST responses, plus separate input schemas for spawn / WS messages.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

_SLUG_RE = re.compile(r"^[a-z][a-z0-9-]*[a-z0-9]$|^[a-z]$")

AgentRole = Literal["designer", "implementer", "auditor", "coordinator"]
TerminatedBy = Literal["idle", "user", "crash", "server_restart"]


class AgentTerminalSessionRead(BaseModel):
    """Single agent terminal session row (active or historical)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    role: AgentRole
    project_slug: str
    pid: int
    created_at: datetime
    ended_at: Optional[datetime]
    exit_code: Optional[int]
    terminated_by: Optional[TerminatedBy]
    last_activity_at: datetime


class AgentTerminalSpawnRequest(BaseModel):
    """POST /agent-terminal/spawn body."""

    role: AgentRole
    project_slug: str = Field(min_length=1, max_length=100)

    @field_validator("project_slug")
    @classmethod
    def _validate_slug(cls, v: str) -> str:
        if not _SLUG_RE.match(v):
            raise ValueError(f"Invalid project slug: {v!r}")
        return v
