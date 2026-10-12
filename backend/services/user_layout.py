"""How a person arranged the cockpit's screen (DEV-82) — read and change, always one's own.

The bounds are defined once, here: the screen reads them from the answer, the database checks the same numbers
(migration 120). A width outside them is refused, never moved to the nearest bound — a number the screen did not
send must not be stored.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Optional

from sqlalchemy.orm import Session

from backend.db.models.user_layout import UserLayout


@dataclass(frozen=True)
class Limit:
    min: int
    max: int
    #: The width with nothing chosen — the layout before DEV-82 (the sidebar's 14rem, the task plan's 320px column).
    default: int


LIMITS: dict[str, Limit] = {
    "sidebar_width": Limit(min=180, max=360, default=224),
    "plan_width": Limit(min=280, max=640, default=320),
}

OUT_OF_BOUNDS = "Šírka {what} musí byť od {min} do {max} px."
_WHAT = {"sidebar_width": "bočného panela", "plan_width": "Plánu úloh"}


def get(db: Session, user_id: uuid.UUID) -> Optional[UserLayout]:
    return db.get(UserLayout, user_id)


def update(db: Session, user_id: uuid.UUID, changes: dict) -> UserLayout:
    """Change only what ``changes`` names; a width of ``None`` returns to the default. Raises :class:`ValueError`
    for a width outside its bounds — nothing is stored then."""
    for key, limit in LIMITS.items():
        value = changes.get(key)
        if value is not None and not limit.min <= value <= limit.max:
            raise ValueError(OUT_OF_BOUNDS.format(what=_WHAT[key], min=limit.min, max=limit.max))
    row = db.get(UserLayout, user_id)
    if row is None:
        row = UserLayout(user_id=user_id, sidebar_collapsed=False)
        db.add(row)
    for key in ("sidebar_width", "sidebar_collapsed", "plan_width"):
        if key in changes:
            setattr(row, key, changes[key] if key != "sidebar_collapsed" else bool(changes[key]))
    db.flush()
    return row
