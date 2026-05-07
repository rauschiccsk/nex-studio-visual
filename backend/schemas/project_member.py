"""Pydantic schemas for ProjectMember.

M2.B milestone (2026-05-07): assigns users to projects so ``shu`` users
can see KB documents under ``projects/<slug>/`` they are members of.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ProjectMemberCreate(BaseModel):
    project_id: UUID
    user_id: UUID
    role: str = Field(default="member", min_length=1, max_length=50)


class ProjectMemberUpdate(BaseModel):
    role: str | None = Field(default=None, min_length=1, max_length=50)


class ProjectMemberRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    user_id: UUID
    role: str
    created_at: datetime
    updated_at: datetime
