"""How the cockpit names a person on screen — first and last name, else the user name (one place, DEV-151)."""

from __future__ import annotations

from typing import Optional

from backend.db.models.foundation import User


def person_name(user: Optional[User]) -> Optional[str]:
    """„Zoltán Rausch“, else the user name; ``None`` when there is no person (a deleted account)."""
    if user is None:
        return None
    full = " ".join(part for part in (user.first_name, user.last_name) if part)
    return full or user.username
