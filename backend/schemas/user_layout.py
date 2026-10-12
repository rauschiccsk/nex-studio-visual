"""DEV-82 — how a person arranged the cockpit's screen, as the screen reads and changes it."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class LayoutLimitRead(BaseModel):
    min: int
    max: int
    default: int


class UserLayoutRead(BaseModel):
    #: ``None`` = the default width (``limits``).
    sidebar_width: Optional[int] = None
    sidebar_collapsed: bool = False
    plan_width: Optional[int] = None
    limits: dict[str, LayoutLimitRead] = Field(description="Per width: its bounds and the default, in px.")


class UserLayoutWrite(BaseModel):
    """Only the fields sent change; a width of ``null`` returns to the default."""

    sidebar_width: Optional[int] = None
    sidebar_collapsed: Optional[bool] = None
    plan_width: Optional[int] = None
