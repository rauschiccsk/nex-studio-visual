"""Spoločný pomocník stráží prevzatia: čím sa vykreslí predpis, ktorý by sa zapísal (ICCINT-151).

Náhľad prevzatia sa od 24.09.2026 nedá položiť bez tejto odpovede — a keby si ju každý súbor stráží
skladal po svojom, rozišli by sa. Jedno miesto, jeden tvar.
"""

from __future__ import annotations

from pathlib import Path

from backend.services import instance_adoption as ia

#: Najmenší zdrojový projekt, z ktorého sa dá vykresliť: databáza, backend, frontend.
ZAKLADNY_ZDROJ = """services:
  db:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: demo
      POSTGRES_PASSWORD: x
      POSTGRES_DB: demo
  backend:
    build: ./backend
  frontend:
    build: ./frontend
"""


def render_request(
    tmp_path: Path,
    *,
    source: str = ZAKLADNY_ZDROJ,
    slug: str = "icc-uat",
    project: str = "nex-demo",
    environment: str = "uat",
    customer_slug: str = "icc",
    app: str = "demo",
) -> ia.RenderRequest:
    """Zdrojový projekt v ``tmp_path`` + požiadavka na vykreslenie, ktorá naň ukazuje.

    ``source`` je predpis zdrojového projektu. Keď sa stráž pýta na konkrétnu inštaláciu, dá sa sem
    poslať ten istý text — inštalácia z toho projektu kedysi vznikla, takže vykreslenie bude blízke
    a v hláškach zostane len to, čo sa naozaj rozchádza.
    """
    proj = tmp_path / "projects" / project
    proj.mkdir(parents=True, exist_ok=True)
    (proj / "docker-compose.yml").write_text(source, encoding="utf-8")
    return ia.RenderRequest(
        project_path=proj,
        slug=slug,
        project=project,
        environment=environment,
        customer_slug=customer_slug,
        app=app,
    )
