"""„Kde sme skončili“ — the notes at a version, newest first (DEV-151). For the people only: no agent reads them."""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db.models.foundation import User
from backend.db.models.version_note import VersionNote
from backend.schemas.version_note import VersionNoteRead
from backend.services.people import person_name


def _read(db: Session, note: VersionNote) -> VersionNoteRead:
    author = db.get(User, note.author_id) if note.author_id else None
    return VersionNoteRead(
        id=note.id,
        version_id=note.version_id,
        text=note.text,
        author_name=person_name(author),
        created_at=note.created_at,
    )


def add(db: Session, version_id: UUID, author: User, text: str) -> VersionNoteRead:
    note = VersionNote(version_id=version_id, author_id=author.id, text=text)
    db.add(note)
    db.flush()
    db.refresh(note)
    return _read(db, note)


def history(db: Session, version_id: UUID) -> list[VersionNoteRead]:
    notes = db.execute(
        select(VersionNote).where(VersionNote.version_id == version_id).order_by(VersionNote.created_at.desc())
    ).scalars()
    return [_read(db, n) for n in notes]


def latest(db: Session, version_id: UUID) -> Optional[VersionNoteRead]:
    note = db.execute(
        select(VersionNote).where(VersionNote.version_id == version_id).order_by(VersionNote.created_at.desc()).limit(1)
    ).scalar_one_or_none()
    return _read(db, note) if note else None
