"""Who answers for a ticket in Plane — the person of the „Zodpovedný“ group of the DEV register (DEV-121, DEV-360).

Every ticket the cockpit founds (Poradca, Nápady, Poznámky, Plane tikety) carries the label of the person who answers
for it: the person the Manažér set at the account, else the one the account's names find. Until DEV-455 this lived
with the Zásobník's tickets; the Zásobník is gone, the rule stays.
"""

from __future__ import annotations

from typing import Optional

from backend.db.models.foundation import User
from backend.services import plane_tickets
from backend.services.plane_gateway import PlaneGateway, name_key

RESPONSIBLE_GROUP = "Zodpovedný"
TICKET_TYPE_GROUP = "Typ"


def gateway() -> PlaneGateway:
    """The cockpit's door to Plane (:func:`plane_tickets.gateway`) — one per request; tests replace it."""
    return plane_tickets.gateway()


def responsible_people(plane: PlaneGateway) -> list[str]:
    """The people of the „Zodpovedný“ group in the DEV register, by the names its labels carry."""
    prefix = f"{RESPONSIBLE_GROUP}: "
    return sorted(
        label["name"][len(prefix) :] for label in plane.labels() if (label.get("name") or "").startswith(prefix)
    )


def responsible_of(user: Optional[User], people: list[str]) -> Optional[str]:
    """Who answers for the account's tickets: the person the Manažér set at the account (DEV-360), else the person
    the account's names find — full name, first name, username. A person set and no longer in the group is nobody:
    a name would quietly pick someone else."""
    if user is None:
        return None
    by_key = {name_key(name): name for name in people}
    if user.plane_responsible:
        return by_key.get(name_key(user.plane_responsible))
    return responsible_by_name(user, by_key)


def responsible_by_name(user: User, by_key: dict[str, str]) -> Optional[str]:
    """The person of the group the account's names find, by :func:`name_key` of each name; ``None`` when none."""
    full = " ".join(part for part in (user.first_name, user.last_name) if part)
    for candidate in (full, user.first_name or "", user.username or ""):
        if candidate and name_key(candidate) in by_key:
            return by_key[name_key(candidate)]
    return None
