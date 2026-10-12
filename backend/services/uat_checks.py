"""What the manager tried in UAT, what does not work, and what an acceptance records about it (DEV-91).

12.10.2026 the Director, with the UAT test guide (DEV-90): tick off each change he tried, see at the acceptance how much
of the version he tested, and turn a change that does not work into a fast fix. The changes to try are the sections of
the version's test guide — for a version from before the guide, the changes of its release note. One rule, read by the
UAT panel, the acceptance prompt and the acceptance record alike (:func:`progress`).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db.models.foundation import User
from backend.db.models.uat_check import BROKEN, TESTED, UatCheck
from backend.db.models.versions import Version
from backend.services import delivery_statement, uat_guide

STATUSES = (TESTED, BROKEN)

UNKNOWN_ITEM = "Takú zmenu návod tejto verzie nemá — obnov stránku."


@dataclass
class Check:
    item: str
    status: str
    note: Optional[str]
    checked_by: Optional[str]
    updated_at: datetime


@dataclass
class Progress:
    """The changes of a version and what the manager recorded for each, in the guide's order."""

    items: list[str] = field(default_factory=list)
    checks: dict[str, Check] = field(default_factory=dict)

    @property
    def tested(self) -> int:
        return sum(1 for i in self.items if i in self.checks and self.checks[i].status == TESTED)

    @property
    def untested(self) -> list[str]:
        return [i for i in self.items if i not in self.checks]

    @property
    def broken(self) -> list[str]:
        return [i for i in self.items if i in self.checks and self.checks[i].status == BROKEN]


def items(repo: Path, version_number: str, sha: Optional[str]) -> list[str]:
    """The changes to try — the guide's sections, else the release note's changes, of the commit that was verified."""
    found = uat_guide.for_commit(repo, version_number, sha)
    return [s.title for s in found.guide.sections] if found.source == uat_guide.GUIDE else list(found.changes)


def items_for(db: Session, version: Version, repo: Path) -> list[str]:
    sha, _source = delivery_statement.delivered_commit(db, version, repo)
    return items(repo, version.version_number, sha)


def _person(user: Optional[User]) -> Optional[str]:
    if user is None:
        return None
    name = " ".join(p for p in (user.first_name, user.last_name) if p)
    return name or user.username


def progress(db: Session, customer_id: uuid.UUID, version: Version, changes: list[str]) -> Progress:
    rows = db.execute(
        select(UatCheck, User)
        .outerjoin(User, User.id == UatCheck.checked_by)
        .where(UatCheck.customer_id == customer_id, UatCheck.version_id == version.id)
    ).all()
    checks = {
        row.item: Check(
            item=row.item, status=row.status, note=row.note, checked_by=_person(user), updated_at=row.updated_at
        )
        for row, user in rows
    }
    return Progress(items=list(changes), checks={k: v for k, v in checks.items() if k in changes})


def record(
    db: Session,
    customer_id: uuid.UUID,
    version: Version,
    changes: list[str],
    item: str,
    status: Optional[str],
    note: Optional[str],
    user_id: Optional[uuid.UUID],
) -> None:
    """Record what the manager found for one change; ``status=None`` takes the record back. Raises
    :class:`ValueError` for a change the version's guide does not have or an unknown status."""
    if item not in changes:
        raise ValueError(UNKNOWN_ITEM)
    if status is not None and status not in STATUSES:
        raise ValueError(f"Neznámy stav {status!r}.")
    row = db.execute(
        select(UatCheck).where(
            UatCheck.customer_id == customer_id, UatCheck.version_id == version.id, UatCheck.item == item
        )
    ).scalar_one_or_none()
    if status is None:
        if row is not None:
            db.delete(row)
            db.flush()
        return
    text = (note or "").strip() or None
    if row is None:
        db.add(
            UatCheck(
                customer_id=customer_id,
                version_id=version.id,
                item=item,
                status=status,
                note=text,
                checked_by=user_id,
            )
        )
    else:
        row.status, row.note, row.checked_by = status, text, user_id
    db.flush()


def acceptance_detail(found: Progress) -> str:
    """The sentence the acceptance event keeps about what was tested — readable in the deploy history."""
    if not found.items:
        return "UAT akceptované — PROD otvorené. Verzia nemá zoznam zmien na skúšku."
    parts = [f"UAT akceptované — PROD otvorené. Otestované {found.tested} z {len(found.items)}."]
    if found.untested:
        parts.append("Neotestované: " + "; ".join(found.untested) + ".")
    if found.broken:
        parts.append("Nefunguje: " + "; ".join(found.broken) + ".")
    return " ".join(parts)
