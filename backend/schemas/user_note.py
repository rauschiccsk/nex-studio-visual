"""DEV-89 — a person's quick notes as the screen reads and changes them."""

from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field

from backend.schemas.delivery_statement import WorkKind


class NoteTicketRead(BaseModel):
    """The project ticket a note became (DEV-455)."""

    project_slug: str
    project_name: str
    identifier: str = Field(description="DEV-n")
    url: Optional[str] = None


class UserNoteRead(BaseModel):
    id: UUID
    body: str
    #: The first line that is not empty — the note's name in the list ("" for an empty note).
    title: str
    revision: int
    created_at: datetime
    updated_at: datetime
    resolved_at: Optional[datetime] = None
    ticket: Optional[NoteTicketRead] = None


class UserNoteCreate(BaseModel):
    body: str = ""


class UserNoteSave(BaseModel):
    body: str
    #: The revision the text was written over — a note that moved on meanwhile is not overwritten (409).
    revision: int


class UserNoteConflictRead(BaseModel):
    """409 — the note changed on another device; ``note`` is how it is now."""

    detail: str
    note: UserNoteRead


class UserNoteResolve(BaseModel):
    resolved: bool


class UserNoteToTicket(BaseModel):
    #: The project the screen has selected, as it knows it.
    project_slug: str
    #: Who pays the work (DEV-118: fix, gift, change) — a project ticket carries it.
    work_kind: WorkKind
