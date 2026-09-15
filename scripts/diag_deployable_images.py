"""ICCINT-137 — dá sa inštalácia po povýšení verzie vôbec nasadiť?

Nasadenie spúšťa ``docker compose up -d --build``. ``--build`` však postaví len službu, ktorá má
``build:``. Keď ju nemá — a to je prípad každej PREVZATEJ inštalácie s ručne pripnutými obrazmi —
compose obraz iba hľadá: lokálne, inak v registri. Register nemáme, takže povýšená značka znamená
``pull access denied`` a nasadenie padne.

Tento nástroj odpovie na tú otázku PRED nasadením: koľko obrazov by po povýšení nemalo ani
``build:``, ani lokálnu predlohu.

    poetry run python scripts/diag_deployable_images.py /opt/uat/mager/nex-inbox v1.5.2 nex-inbox
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from backend.services import uat_provisioner


def main() -> int:
    if len(sys.argv) != 4:
        print(__doc__)
        return 2
    instalacia, verzia, slug = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
    novy = uat_provisioner.render_version_bump(instalacia, version=verzia, project_slug=slug)

    chybaju = []
    for meno, svc in (novy.get("services") or {}).items():
        obraz = svc.get("image")
        if not isinstance(obraz, str) or svc.get("build"):
            continue  # so ``build:`` si ho ``up --build`` postaví sám
        if subprocess.run(["docker", "image", "inspect", obraz], capture_output=True).returncode != 0:
            chybaju.append(f"{meno}={obraz}")

    print(
        f"{instalacia} @ {verzia}: obrazov bez build: aj bez lokálnej predlohy: {len(chybaju)}"
        + (f" → {', '.join(chybaju)}" if chybaju else "")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
