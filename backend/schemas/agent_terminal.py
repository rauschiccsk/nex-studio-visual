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

# CR-V2-007: SPAWN role — the interactive terminal is the AI Agent (the doer; replaces the v1
# Coordinator). Used by AgentTerminalSpawnRequest so the spawn API rejects every other role at the
# request boundary. These are CHARTER-PATH SLUGS (hyphen) — ``role`` flows straight to the filesystem
# charter path ``.claude/agents/<role>/CLAUDE.md``; the DB-side ``ai_agent`` (underscore) maps to it via
# orchestrator._charter_slug_for_role.
AgentRole = Literal["ai-agent"]
# Debug-attach (CR-NS-018 §10) targets an orchestrator-backed agent session — you attach to the AI Agent
# or the independent Auditor. A deliberately SEPARATE type from the spawn AgentRole (spawn ≠ debug-attach);
# this is also the READ type, because the SAME agent_terminal_sessions table holds debug-attach rows.
DebugAttachRole = Literal["ai-agent", "auditor"]
TerminatedBy = Literal["idle", "user", "crash", "server_restart"]


class AgentTerminalSessionRead(BaseModel):
    """Single agent terminal session row (active or historical)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    # READ accepts all debug-attach roles: this row may be an AI-Agent spawn OR an Auditor debug-attach
    # session (CR-V2-007). AI-Agent-only lives on the SpawnRequest, not here.
    role: DebugAttachRole
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
