"""``/versions/{version_id}/notes`` — „Kde sme skončili“: why the development stands and what next (DEV-151).

Read and written by everyone who may work on the project (the owner and the admin, as the version itself); a note
carries its author and time and stays in the history. No agent reads these — they are no instruction for the build.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from backend.core import authz
from backend.core.security import get_current_user
from backend.db.models.foundation import User
from backend.db.session import get_db
from backend.schemas.version_note import VersionNoteCreate, VersionNoteRead
from backend.services import version_notes

router = APIRouter(tags=["Kde sme skončili"])


@router.get("/versions/{version_id}/notes", response_model=list[VersionNoteRead])
def list_version_notes(
    version_id: UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
) -> list[VersionNoteRead]:
    """The version's notes, newest first."""
    authz.assert_version_access(db, current_user, version_id)
    return version_notes.history(db, version_id)


@router.post("/versions/{version_id}/notes", response_model=VersionNoteRead, status_code=status.HTTP_201_CREATED)
def add_version_note(
    version_id: UUID,
    body: VersionNoteCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> VersionNoteRead:
    """A new note — one to three sentences; the older ones stay."""
    authz.assert_version_access(db, current_user, version_id)
    note = version_notes.add(db, version_id, current_user, body.text)
    db.commit()
    return note
