"""A person's quick notes (DEV-89) — every function takes the caller and touches only the caller's notes.

A note of somebody else is :data:`NOT_FOUND`, exactly like one that does not exist: the cockpit does not even say
that it is there. A change carries the revision it was written over; when the note has moved on meanwhile (another
device), the change is refused with the note as it is now (:class:`Conflict`) — nothing is overwritten without a word.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db.models.foundation import User
from backend.db.models.projects import Project
from backend.db.models.user_note import UserNote
from backend.services import plane_responsible, plane_tickets
from backend.services.plane_gateway import PlaneError, PlaneGateway

NOT_FOUND = "Poznámka sa nenašla."
EMPTY = "Poznámka je prázdna — nie je z čoho založiť tiket."
ALREADY_A_TICKET = "Z tejto poznámky už vznikol tiket {identifier}."
NO_RESPONSIBLE = (
    "Konto {username} nemá zodpovedného v Plane. Manažér ho nastaví v Nastaveniach, karta Používatelia, časť "
    "„Zodpovedný v Plane“."
)
DELETE_OPEN = "Natrvalo sa maže len vyriešená poznámka — najprv ju označ ako vyriešenú."

#: A ticket's title bound in Plane.
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


def to_ticket(
    db: Session, plane: PlaneGateway, user: User, note_id: uuid.UUID, project: Project, work_kind: str
) -> UserNote:
    """DEV-455 — turn the note into exactly one ticket of the project in Plane (the Zásobník is gone): the first line
    its title, the rest its text; the note is resolved and points to the ticket. A second time is refused
    (``ValueError``); when Plane refuses, nothing changes (``PlaneError``)."""
    note = _own(db, user.id, note_id, lock=True)
    if note.plane_identifier:
        raise ValueError(ALREADY_A_TICKET.format(identifier=note.plane_identifier))
    name = title(note.body)
    if not name:
        raise ValueError(EMPTY)
    responsible = plane_responsible.responsible_of(user, plane_responsible.responsible_people(plane))
    if responsible is None:
        raise PlaneError(NO_RESPONSIBLE.format(username=user.username))
    rest = "\n".join(note.body.strip().splitlines()[1:]).strip()
    ref = plane_tickets.file_ticket(
        db,
        plane,
        project=project,
        user=user,
        source="kokpit",
        title=name[:TITLE_MAX],
        description=rest or name,
        labels=(
            f"{plane_responsible.TICKET_TYPE_GROUP}: Task",
            f"{plane_responsible.RESPONSIBLE_GROUP}: {responsible}",
        ),
        doer=responsible,
        work_kind=work_kind,
    )
    note.ticket_project_id = project.id
    note.plane_identifier, note.plane_url = ref.identifier, ref.url
    note.resolved_at = datetime.now(timezone.utc)
    db.flush()
    return note
