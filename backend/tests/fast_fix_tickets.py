"""DEV-492 — a fast fix as the Manažér makes it in tests: founded as one, a ticket put in, started from it."""

from __future__ import annotations

import itertools

from backend.db.models.version_tickets import VersionTicket
from backend.db.models.versions import Version

_NUMBERS = itertools.count(9001)


def put_ticket(db, version: Version, title: str, text: str = "") -> VersionTicket:
    """A ticket of the project put into ``version`` on the „Plane tikety“ tab (no Plane — the link alone)."""
    n = next(_NUMBERS)
    link = VersionTicket(
        version_id=version.id,
        plane_issue_id=f"issue-{n}",
        plane_identifier=f"DEV-{n}",
        plane_url=f"http://plane.test/issues/{n}",
        title=title,
        text=text,
    )
    db.add(link)
    db.flush()
    return link


def as_fast_fix(db, version: Version, title: str, text: str = "") -> VersionTicket:
    """Mark ``version`` a fast fix founded as one and put the ticket it fixes into it."""
    version.fast_fix = True
    db.flush()
    return put_ticket(db, version, title, text)
