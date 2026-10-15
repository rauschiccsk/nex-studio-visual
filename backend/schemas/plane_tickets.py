"""The „Plane tikety" tab — the tickets of a project's module in the DEV register (DEV-120)."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from backend.schemas.delivery_statement import WorkKind


class PlaneTicketRead(BaseModel):
    identifier: str = Field(description="DEV-n")
    title: str
    state: str = Field(description="The ticket's column in Plane.")
    closed: bool = Field(description="Done or cancelled — whatever the column is called.")
    ticket_type: Optional[str] = Field(default=None, description="Bug or Task, from the „Typ“ label.")
    work_kind: Optional[WorkKind] = Field(default=None, description="From the „Druh práce“ label.")
    work_kind_label: Optional[str] = Field(default=None, description="The label's words, e.g. „Oprava (BUG)“.")
    warning: Optional[str] = Field(default=None, description="What is wrong with the ticket's labels, in words.")
    doer: Optional[str] = Field(default=None, description="Who works on it now — the ticket's „Robí:“ line.")
    updated_at: Optional[datetime] = None
    url: str
    requirement: Optional[str] = Field(default=None, description="REQ-N of the Zásobník that founded the ticket.")
    requirement_version: Optional[str] = Field(default=None, description="The version that requirement is in.")


class ProjectPlaneTicketsRead(BaseModel):
    module: Optional[str] = Field(description="The project's module in DEV; none until its first ticket.")
    tickets: list[PlaneTicketRead]
