"""DEV-89 — the caller's own quick notes.

* ``GET    /``                  → the caller's notes, the most recently changed first.
* ``POST   /``                  → a new note.
* ``PUT    /{id}``              → its text, written over a revision; 409 with the note as it is now when it moved on.
* ``POST   /{id}/resolve``      → resolved, or back among the open ones.
* ``DELETE /{id}``              → for good — only a resolved note.
* ``POST   /{id}/to-backlog``   → a request REQ-N in the backlog of the project (by slug, once), the note resolved and
  pointing to it.

Scoped to ``current_user``: a note of somebody else answers 404 like one that does not exist — no admin
exception. The mount prefix (``/api/v1/notes``) is applied in :mod:`backend.main`.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from backend.core import authz
from backend.core.security import get_current_user
from backend.db.models.foundation import User
from backend.db.models.projects import Project
from backend.db.models.user_note import UserNote
from backend.db.session import get_db
from backend.schemas.user_note import (
    NoteTicketRead,
    UserNoteConflictRead,
    UserNoteCreate,
    UserNoteRead,
    UserNoteResolve,
    UserNoteSave,
    UserNoteToTicket,
)
from backend.services import plane_tickets
from backend.services import user_notes as notes
from backend.services.plane_gateway import PlaneError

router = APIRouter(tags=["User Notes"])


def _read(db: Session, note: UserNote) -> UserNoteRead:
    project = db.get(Project, note.ticket_project_id) if note.ticket_project_id else None
    return UserNoteRead(
        id=note.id,
        body=note.body,
        title=notes.title(note.body),
        revision=note.revision,
        created_at=note.created_at,
        updated_at=note.updated_at,
        resolved_at=note.resolved_at,
        ticket=NoteTicketRead(
            project_slug=project.slug,
            project_name=project.name,
            identifier=note.plane_identifier,
            url=note.plane_url,
        )
        if project is not None and note.plane_identifier
        else None,
    )


def _not_found(exc: LookupError) -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc))


@router.get("", response_model=list[UserNoteRead])
def list_my_notes(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> list[UserNoteRead]:
    return [_read(db, n) for n in notes.list_for(db, current_user.id)]


@router.post("", response_model=UserNoteRead, status_code=status.HTTP_201_CREATED)
def create_note(
    payload: UserNoteCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
) -> UserNoteRead:
    note = notes.create(db, current_user.id, payload.body)
    db.commit()
    db.refresh(note)
    return _read(db, note)


@router.put(
    "/{note_id}",
    response_model=UserNoteRead,
    responses={status.HTTP_409_CONFLICT: {"model": UserNoteConflictRead}},
)
def save_note(
    note_id: UUID, payload: UserNoteSave, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
):
    try:
        note = notes.save(db, current_user.id, note_id, payload.body, payload.revision)
    except LookupError as exc:
        raise _not_found(exc) from exc
    except notes.Conflict as exc:
        current = _read(db, exc.note)
        db.rollback()
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content=UserNoteConflictRead(detail=str(exc), note=current).model_dump(mode="json"),
        )
    db.commit()
    db.refresh(note)
    return _read(db, note)


@router.post("/{note_id}/resolve", response_model=UserNoteRead)
def resolve_note(
    note_id: UUID,
    payload: UserNoteResolve,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> UserNoteRead:
    try:
        note = notes.resolve(db, current_user.id, note_id, payload.resolved)
    except LookupError as exc:
        raise _not_found(exc) from exc
    db.commit()
    db.refresh(note)
    return _read(db, note)


@router.delete("/{note_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_note(
    note_id: UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
) -> Response:
    try:
        notes.delete(db, current_user.id, note_id)
    except LookupError as exc:
        raise _not_found(exc) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{note_id}/to-ticket", response_model=UserNoteRead)
def note_to_ticket(
    note_id: UUID,
    payload: UserNoteToTicket,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> UserNoteRead:
    """DEV-455 — a ticket of the project in Plane, exactly once; the caller must be allowed into the project."""
    project = authz.assert_project_slug_access(db, current_user, payload.project_slug)
    try:
        note = notes.to_ticket(db, plane_tickets.gateway(), current_user, note_id, project, payload.work_kind)
    except LookupError as exc:
        raise _not_found(exc) from exc
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except PlaneError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    db.commit()
    db.refresh(note)
    return _read(db, note)
