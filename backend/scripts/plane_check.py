"""Is the cockpit's door to Plane open? (DEV-117)

    python -m backend.scripts.plane_check [--project SLUG] [--create-test-ticket]

Says whose key the cockpit holds (the account's e-mail), that the DEV register is visible with its columns and
labels, and which module a project's tickets go to. With ``--create-test-ticket`` it founds one ticket in that
module under the cockpit's identity and reads it back — the live proof that the door works. The key is never printed.
"""

from __future__ import annotations

import argparse
import sys

from sqlalchemy import select

from backend.db.models.projects import Project
from backend.db.session import SessionLocal
from backend.services import plane_tickets
from backend.services.plane_gateway import PlaneError, PlaneGateway


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--project", help="slug projektu kokpitu, ktorého modul sa má ukázať")
    parser.add_argument("--create-test-ticket", action="store_true", help="založ v module projektu skúšobný tiket")
    args = parser.parse_args(argv)

    gateway = PlaneGateway()
    try:
        print(f"kľúč patrí kontu: {gateway.who_am_i()}")
        project = gateway.project()
        print(f"evidencia: {project['identifier']} ({project.get('name', '')})")
        print(f"stĺpce: {', '.join(sorted(gateway.states()))}")
        print(f"štítkov: {len(gateway.labels())}, modulov: {len(gateway.modules())}")
        if not args.project:
            return 0
        with SessionLocal() as db:
            cockpit_project = db.execute(select(Project).where(Project.slug == args.project)).scalar_one_or_none()
            if cockpit_project is None:
                print(f"projekt {args.project} v kokpite nie je", file=sys.stderr)
                return 1
            module = plane_tickets.module_for(db, cockpit_project, gateway)
            print(f"modul projektu: {module['name']} ({module['id']})")
            if args.create_test_ticket:
                ref = plane_tickets.file_ticket(
                    db,
                    gateway,
                    project=cockpit_project,
                    user=None,
                    source="kokpit",
                    title="Kokpit: skúšobný tiket — overenie prepojenia s Plane (DEV-117)",
                    description="Skúšobný tiket, ktorým kokpit overil, že zapisuje do Plane pod vlastnou identitou "
                    "a zápis číta späť. Po overení sa zruší.",
                    labels=("Typ: Task",),
                    doer="Andros Dedo",
                )
                print(f"založený a prečítaný späť: {ref.identifier} {ref.url}")
            db.commit()
    except PlaneError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
