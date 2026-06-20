"""Public (no-auth) release-notes changelog router (``GET /api/v1/release-notes``).

Serves the *Aktualizácie* feature. The changelog is user-facing and carries
no credentials, so — like :func:`backend.api.routes.health.health_check` — the
endpoint is intentionally mounted **without** an auth dependency. The router is
prefix-less; the ``/api/v1`` mount prefix is applied in :mod:`backend.main`.

The heavy lifting (glob discovery of image-baked ``RELEASE_NOTES.md`` files,
the ``versions`` DB join for the release date, newest-first ordering) lives in
:mod:`backend.services.release_notes`.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.db.session import get_db
from backend.schemas.release_notes import ReleaseNote
from backend.services import release_notes as release_notes_service

router = APIRouter(tags=["Release Notes"])


@router.get("/release-notes", response_model=list[ReleaseNote])
def list_release_notes(db: Session = Depends(get_db)) -> list[ReleaseNote]:
    """Return the per-version user-facing changelog, newest version first."""
    notes = release_notes_service.list_release_notes(db)
    return [ReleaseNote.model_validate(note) for note in notes]
