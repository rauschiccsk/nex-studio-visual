"""Pydantic schemas for UIDesign domain objects."""

from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class UIDesignCreate(BaseModel):
    project_id: UUID
    content: str = Field(default="")
    html_preview: Optional[str] = None
    approved_by: Optional[UUID] = None
    approved_at: Optional[datetime] = None


class UIDesignUpdate(BaseModel):
    content: Optional[str] = Field(default=None, min_length=0)
    html_preview: Optional[str] = None
    approved_by: Optional[UUID] = None
    approved_at: Optional[datetime] = None


class UIDesignRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    content: str
    html_preview: Optional[str] = None
    approved_by: Optional[UUID] = None
    approved_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
