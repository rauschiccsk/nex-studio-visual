"""ICCINT-135 — priradí sa posledné naviazanie poistiek k deklarovaným invariantom?

Keď vyjde 0 z N, stavba sa zacyklí na nemennom hlásení: nepriradené naviazanie sa neuplatní,
platí staršie, a text zlyhania sa nezmení, nech agent opraví čokoľvek.

Beží VNÚTRI kontajnera kokpitu a používa tie isté funkcie ako brána — nie ich napodobeninu
v SQL, ktorá by potvrdila len predstavu autora.

Do obrazu sa zámerne nekopíruje (Dockerfile nesie len to, čo appka potrebuje na beh), takže sa
najprv podstrčí dnu:

    docker cp scripts/diag_binding_match.py nex-studio-visual-prod-backend-1:/tmp/d.py \\
      && docker exec -w /app -e PYTHONPATH=/app nex-studio-visual-prod-backend-1 \\
         python /tmp/d.py nex-inbox 1.5.1
"""

from __future__ import annotations

import sys

from sqlalchemy import select

from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.db.session import SessionLocal
from backend.services import orchestrator


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    slug, verzia = sys.argv[1], sys.argv[2]
    db = SessionLocal()
    try:
        v = (
            db.execute(
                select(Version)
                .join(Project, Project.id == Version.project_id)
                .where(Project.slug == slug, Version.version_number == verzia)
            )
            .scalars()
            .one_or_none()
        )
        if v is None:
            print(f"{slug} {verzia}: taká verzia v evidencii nie je")
            return 2

        aliasy = orchestrator._declared_safety_aliases(db, v.id)
        najnovsie = next(
            (p for p in orchestrator._gate_report_payloads_newest_first(db, v.id) if p.get("safety_properties")),
            None,
        )
        poistky = (najnovsie or {}).get("safety_properties") or []
        sedia = [
            sp
            for sp in poistky
            if isinstance(sp, dict)
            and (str(sp.get("key") or "").strip() in aliasy or str(sp.get("name") or "").strip() in aliasy)
        ]
        print(
            f"{slug} {verzia}: posledné hlásenie viaže {len(poistky)} poistiek; "
            f"deklarácia ponúka {len(aliasy)} identifikátorov; priradí sa {len(sedia)}"
        )
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
