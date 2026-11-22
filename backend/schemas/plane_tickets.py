"""The „Plane tikety" tab — the tickets of a project's module in the DEV register (DEV-120)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field

from backend.schemas.delivery_statement import WorkKind

#: The type of a ticket in the DEV register — the „Typ“ label (MAGER-34).
TicketType = Literal["Bug", "Task"]


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
    text: str = Field(default="", description="The ticket's text without its „Robí:“ line — a fast fix's directive.")
    fix_version: Optional[str] = Field(default=None, description="The fast fix started from the ticket (DEV-122).")
    in_version: Optional[str] = Field(
        default=None, description="The version the Manažér put the ticket into — it solves the ticket (DEV-455)."
    )


class OpenVersionRead(BaseModel):
    """A version a ticket may still go into — in Príprava or not started (DEV-455)."""

    id: UUID
    version_number: str
    #: DEV-492 — a fast fix waiting for its tickets; the tab names it so the Manažér knows where the ticket goes.
    fast_fix: bool = False


class VersionTicketRead(BaseModel):
    """The ticket put into a version, and the sentence when Plane did not follow (DEV-455)."""

    issue_id: str
    identifier: str
    version_number: str
    plane_warning: Optional[str] = None


class ProjectPlaneTicketsRead(BaseModel):
    project_id: UUID = Field(description="The project a fast fix from a ticket starts in (DEV-122).")
    project_name: str
    module: Optional[str] = Field(description="The project's module in DEV; none until its first ticket.")
    tickets: list[PlaneTicketRead]
    open_versions: list[OpenVersionRead] = Field(description="The versions a ticket may still go into (DEV-455).")


class PreparedTicketCreate(BaseModel):
    """DEV-492 — a ticket of the project from a text the cockpit prepared (a broken UAT test, dead code)."""

    title: str = Field(..., min_length=1, max_length=255)
    description: str = Field(..., min_length=1, max_length=20000)
    work_kind: WorkKind
    source: Literal["uat", "dead_code"]


class PreparedTicketFounded(BaseModel):
    identifier: str
    url: str
