"""Converts the Zásobník's requirements without a ticket into project tickets in Plane (DEV-455).

The Zásobník is gone; its open and included requirements that have no ticket would otherwise be lost to Plane. Each
becomes a ticket of its project's module (title, description, „Typ: Task“, the responsible person of the account that
runs the conversion and the work kind); an included one goes into its version (``version_tickets``) — the version
then hands it to the Príprava and closes it after PROD as every ticket put in on the Plane tikety tab.

Without ``--apply`` it only prints the list and writes nothing — the Director reviews it first. The work kind of each
requirement comes from ``--druh-file`` (JSON ``{"<project slug>/REQ-<n>": "fix" | "gift" | "change"}``), else
``--druh``::

    docker exec nex-studio-visual-prod-backend-1 python -m backend.scripts.zasobnik_to_tickets
    docker exec nex-studio-visual-prod-backend-1 python -m backend.scripts.zasobnik_to_tickets --apply \
        --konto <username> --druh change --druh-file /tmp/druhy.json
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db.models.backlog import BacklogItem
from backend.db.models.foundation import User
from backend.db.models.projects import Project
from backend.db.models.version_tickets import VersionTicket
from backend.db.models.versions import Version
from backend.db.session import SessionLocal
from backend.services import plane_responsible, plane_tickets
from backend.services.plane_gateway import PlaneGateway

WORK_KINDS = ("fix", "gift", "change")


@dataclass(frozen=True)
class Pending:
    item_id: object
    key: str  # „<slug>/REQ-<n>“
    project: str
    status: str
    version: Optional[str]
    title: str


def pending(db: Session) -> list[Pending]:
    """The open and included requirements without a ticket, by project and number."""
    rows = db.execute(
        select(BacklogItem, Project, Version)
        .join(Project, Project.id == BacklogItem.project_id)
        .outerjoin(Version, Version.id == BacklogItem.version_id)
        .where(BacklogItem.plane_issue_id.is_(None), BacklogItem.status.in_(("open", "included")))
        .order_by(Project.slug, BacklogItem.number)
    ).all()
    return [
        Pending(
            item.id,
            f"{project.slug}/REQ-{item.number}",
            project.name,
            item.status,
            version.version_number if version is not None and item.status == "included" else None,
            item.title,
        )
        for item, project, version in rows
    ]


def convert(db: Session, plane: PlaneGateway, user: User, entry: Pending, work_kind: str) -> str:
    """One requirement → its ticket (and its version's link when it was included); the ticket's identifier."""
    item = db.get(BacklogItem, entry.item_id)
    project = db.get(Project, item.project_id)
    responsible = plane_responsible.responsible_of(user, plane_responsible.responsible_people(plane))
    if responsible is None:
        raise SystemExit(f"Konto {user.username} nemá zodpovedného v Plane — prevod sa nezačal.")
    ref = plane_tickets.file_ticket(
        db,
        plane,
        project=project,
        user=user,
        source="zasobnik",
        title=item.title[:500],
        description=(item.description or item.title)
        + f"\n\nPrevedené zo Zásobníka kokpitu (REQ-{item.number}) pri jeho zrušení (DEV-455).",
        labels=(
            f"{plane_responsible.TICKET_TYPE_GROUP}: Task",
            f"{plane_responsible.RESPONSIBLE_GROUP}: {responsible}",
        ),
        doer=responsible,
        work_kind=work_kind,
    )
    item.plane_issue_id, item.plane_identifier, item.plane_url = ref.id, ref.identifier, ref.url
    if item.status == "included" and item.version_id is not None:
        db.add(
            VersionTicket(
                version_id=item.version_id,
                plane_issue_id=ref.id,
                plane_identifier=ref.identifier,
                plane_url=ref.url,
                title=item.title[:500],
                text=item.description or "",
                added_by=user.id,
                delivered_at=item.delivered_at,
            )
        )
    db.flush()
    return ref.identifier


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Prevod požiadaviek Zásobníka bez tiketu na tikety projektu (DEV-455)."
    )
    parser.add_argument("--apply", action="store_true", help="založiť tikety (bez neho len zoznam)")
    parser.add_argument("--konto", help="konto, v mene ktorého sa tikety založia (jeho zodpovedný v Plane)")
    parser.add_argument("--druh", choices=WORK_KINDS, help="druh práce pre požiadavky, ktoré súbor nemenuje")
    parser.add_argument("--druh-file", type=Path, help='JSON {"<slug>/REQ-<n>": "fix|gift|change"}')
    args = parser.parse_args(argv)
    kinds: dict[str, str] = json.loads(args.druh_file.read_text(encoding="utf-8")) if args.druh_file else {}
    with SessionLocal() as db:
        found = pending(db)
        for entry in found:
            where = f"vo verzii {entry.version}" if entry.version else "otvorená"
            kind = kinds.get(entry.key, args.druh or "—")
            print(f"{entry.key}\t{where}\t{kind}\t{entry.title}")
        print(f"Spolu: {len(found)}")
        if not args.apply:
            return 0
        unknown = [e.key for e in found if kinds.get(e.key, args.druh) not in WORK_KINDS]
        if unknown or not args.konto:
            raise SystemExit("Chýba konto alebo druh práce pre: " + ", ".join(unknown or ["--konto"]))
        user = db.execute(select(User).where(User.username == args.konto)).scalar_one()
        plane = plane_tickets.gateway()
        for entry in found:
            identifier = convert(db, plane, user, entry, kinds.get(entry.key, args.druh))
            db.commit()
            print(f"{entry.key} → {identifier}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
