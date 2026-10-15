"""The „Plane tikety" tab — the tickets of a project's module in the DEV register (DEV-120)."""

from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field

from backend.schemas.delivery_statement import WorkKind


class PlaneTicketRead(BaseModel):
    issue_id: str = Field(description="The ticket's id in Plane — what „Do Zásobníka“ and a fast fix name it by.")
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
    text: str = Field(default="", description="The ticket's text without its „Robí:“ line — a fast fix's directive.")
    fix_version: Optional[str] = Field(default=None, description="The fast fix started from the ticket (DEV-122).")


class ProjectPlaneTicketsRead(BaseModel):
    project_id: UUID = Field(description="The project a fast fix from a ticket starts in (DEV-122).")
    project_name: str
    module: Optional[str] = Field(description="The project's module in DEV; none until its first ticket.")
    tickets: list[PlaneTicketRead]
