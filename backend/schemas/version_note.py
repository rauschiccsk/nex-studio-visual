"""„Kde sme skončili“ — a note at a version (DEV-151)."""

from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, field_validator

#: One to three sentences — why the development stands and what next; longer belongs to the Špecifikácia.
NOTE_MAX_CHARS = 600
NOTE_EMPTY = "Poznámka je prázdna — napíš, prečo stojíme a čo ďalej."
NOTE_TOO_LONG = f"Poznámka má najviac {NOTE_MAX_CHARS} znakov — stačia jedna až tri vety."


class VersionNoteCreate(BaseModel):
    text: str

    @field_validator("text")
    @classmethod
    def _one_to_three_sentences(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError(NOTE_EMPTY)
        if len(value) > NOTE_MAX_CHARS:
            raise ValueError(NOTE_TOO_LONG)
        return value


class VersionNoteRead(BaseModel):
    id: UUID
    version_id: UUID
    text: str
    #: „Zoltán Rausch“ — ``None`` when the author's account is gone.
    author_name: Optional[str] = None
    created_at: datetime
