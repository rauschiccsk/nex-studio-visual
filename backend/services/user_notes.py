"""A person's quick notes (DEV-89) — every function takes the caller and touches only the caller's notes.

A note of somebody else is :data:`NOT_FOUND`, exactly like one that does not exist: the cockpit does not even say
that it is there. A change carries the revision it was written over; when the note has moved on meanwhile (another
device), the change is refused with the note as it is now (:class:`Conflict`) — nothing is overwritten without a word.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db.models.backlog import BacklogItem
from backend.db.models.user_note import UserNote
from backend.schemas.backlog import BacklogItemCreate
from backend.services import backlog as backlog_service

NOT_FOUND = "Poznámka sa nenašla."
EMPTY = "Poznámka je prázdna — nie je z čoho založiť požiadavku."
ALREADY_IN_BACKLOG = "Z tejto poznámky už vznikla požiadavka REQ-{number}."
DELETE_OPEN = "Natrvalo sa maže len vyriešená poznámka — najprv ju označ ako vyriešenú."

#: The backlog's own bound on a request's title.
TITLE_MAX = 500


class Conflict(Exception):
    """The note changed meanwhile — ``note`` is how it is now."""

    def __init__(self, note: UserNote):
        super().__init__("Poznámka sa medzitým zmenila na inom zariadení.")
        self.note = note


def title(body: str) -> str:
    """A note's name: its first line that is not empty."""
    return next((line.strip() for line in body.splitlines() if line.strip()), "")


def _own(db: Session, user_id: uuid.UUID, note_id: uuid.UUID, *, lock: bool = False) -> UserNote:
    query = select(UserNote).where(UserNote.id == note_id, UserNote.user_id == user_id)
    note = db.execute(query.with_for_update() if lock else query).scalar_one_or_none()
    if note is None:
        raise LookupError(NOT_FOUND)
    return note


def list_for(db: Session, user_id: uuid.UUID) -> list[UserNote]:
    """The caller's notes, the most recently changed first."""
    return list(
        db.execute(select(UserNote).where(UserNote.user_id == user_id).order_by(UserNote.updated_at.desc())).scalars()
    )


def create(db: Session, user_id: uuid.UUID, body: str = "") -> UserNote:
    note = UserNote(user_id=user_id, body=body, revision=1)
    db.add(note)
    db.flush()
    return note


def save(db: Session, user_id: uuid.UUID, note_id: uuid.UUID, body: str, based_on: int) -> UserNote:
    """Store the note's text written over revision ``based_on``; :class:`Conflict` when the note moved on."""
    note = _own(db, user_id, note_id, lock=True)
    if note.revision != based_on:
        raise Conflict(note)
    if note.body != body:
        note.body = body
        note.revision += 1
        db.flush()
    return note


def resolve(db: Session, user_id: uuid.UUID, note_id: uuid.UUID, resolved: bool) -> UserNote:
    note = _own(db, user_id, note_id)
    note.resolved_at = datetime.now(timezone.utc) if resolved else None
    db.flush()
    return note


def delete(db: Session, user_id: uuid.UUID, note_id: uuid.UUID) -> None:
    """Delete a resolved note for good — an open one is refused (``ValueError``)."""
    note = _own(db, user_id, note_id)
    if note.resolved_at is None:
        raise ValueError(DELETE_OPEN)
    db.delete(note)
    db.flush()


def backlog_item(db: Session, note: UserNote) -> Optional[BacklogItem]:
    return db.get(BacklogItem, note.backlog_item_id) if note.backlog_item_id else None


def to_backlog(db: Session, user_id: uuid.UUID, note_id: uuid.UUID, project_id: uuid.UUID) -> UserNote:
    """Turn the note into exactly one request in the project's backlog — the first line its title, the rest its
    description — and resolve the note, pointing to the request. A second time is refused (``ValueError``)."""
    note = _own(db, user_id, note_id, lock=True)
    existing = backlog_item(db, note)
    if existing is not None:
        raise ValueError(ALREADY_IN_BACKLOG.format(number=existing.number))
    name = title(note.body)
    if not name:
        raise ValueError(EMPTY)
    lines = note.body.strip().splitlines()
    rest = "\n".join(lines[1:]).strip()
    item = backlog_service.create(
        db,
        BacklogItemCreate(project_id=project_id, title=name[:TITLE_MAX], description=rest or None),
    )
    note.backlog_item_id = item.id
    note.resolved_at = datetime.now(timezone.utc)
    db.flush()
    return note
