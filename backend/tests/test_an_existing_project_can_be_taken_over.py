"""Prevzatie existujúceho projektu — zadám priečinok, zvyšok si systém prečíta (ICCINT-85).

Prevzatie sa dovtedy robilo formulárom pre *zakladanie*: manažér musel vedieť a ručne prepísať porty,
adresu repozitára, typ aj spôsob prihlasovania. Keď sa pomýlil, evidencia začala tvrdiť niečo iné, než
je na disku — a projekt beží podľa disku, nie podľa evidencie. Pritom to všetko na disku UŽ JE.

Director 09.09.2026: *„zadám len názov projektu — napríklad nex-manager — a všetko ostatné urobí systém.“*

**Zásada, ktorú tieto stráže držia:** čo je na disku, to sa prečíta; čo sa prečítať nedá, na to sa opýta;
**nič sa nevymýšľa**. Uhádnutý údaj by vyzeral ako zistený, a to je horšie než prázdne políčko.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.services.project_adoption import discover


def _project(tmp_path: Path, name: str, compose: str | None = None, charter: str | None = None) -> Path:
    root = tmp_path / name
    root.mkdir()
    if charter is not None:
        (root / "CLAUDE.md").write_text(charter, encoding="utf-8")
    if compose is not None:
        (root / "docker-compose.yml").write_text(compose, encoding="utf-8")
    return root


# ── porty: tvary, ktoré sa v NAŠICH projektoch naozaj vyskytujú ────────────────


def test_ports_are_read_the_way_our_projects_actually_write_them(tmp_path) -> None:
    """⚠️ Zmerané 09.09.2026 na štyroch skutočných projektoch — a ani jeden tvar nestačí sám.

    nex-manager: služby ``backend``/``frontend``/``db``, vnútorné porty 8000/80/5432.
    nex-studio: tie isté mená, ale vnútorné porty 9176/9177 — podľa portu by sa rola neurčila.
    nex-payables: frontend sa volá ``web`` a väzba je ``"127.0.0.1:10220:8000"`` s poznámkou za ňou —
    podľa mena by sa rola neurčila a trojdielny tvar by rozobral zle.
    """
    root = _project(
        tmp_path,
        "vzor",
        compose="""
services:
  db:
    ports:
      - "127.0.0.1:10222:5432"   # len lokálne
  backend:
    ports:
      - "127.0.0.1:10220:8000"
  web:
    ports:
      - "10221:80"
""",
    )
    found = discover(root, "vzor")
    assert (found.backend_port, found.frontend_port, found.db_port) == (10220, 10221, 10222)


def test_a_service_whose_inner_port_is_unusual_is_still_recognised(tmp_path) -> None:
    """NEX Studio má vnútri porty 9176/9177 — rola sa určí podľa MENA služby, nie podľa čísla."""
    root = _project(
        tmp_path,
        "studio",
        compose="""
services:
  backend:
    ports: ["9176:9176"]
  frontend:
    ports: ["9177:9177"]
  db:
    ports: ["9178:5432"]
""",
    )
    found = discover(root, "studio")
    assert (found.backend_port, found.frontend_port, found.db_port) == (9176, 9177, 9178)


def test_a_binding_it_does_not_understand_is_skipped_not_guessed(tmp_path) -> None:
    """Nezrozumiteľný tvar sa preskočí a ohlási ako nedopovedaný. Vymyslené číslo portu by bolo
    horšie než prázdne políčko — evidencia by tvrdila niečo, čo na disku nie je."""
    root = _project(tmp_path, "divny", compose="services:\n  backend:\n    ports: ['tramtarara']\n")
    found = discover(root, "divny")
    assert found.backend_port is None
    assert any("port backendu" in u for u in found.unresolved)


# ── názov ─────────────────────────────────────────────────────────────────────


def test_the_name_comes_from_the_charter_not_from_package_json(tmp_path) -> None:
    """⚠️ ``package.json`` sa na názov použiť NEDÁ — zmerané: nex-manager aj nex-payables tam majú
    doslova ``"name": "frontend"``. Charta je jediné miesto, kde projekt nesie svoje ľudské meno."""
    root = _project(tmp_path, "nex-manager", charter="# NEX Manager — Univerzálny CLAUDE.md\n\nText.\n")
    assert discover(root, "nex-manager").name == "NEX Manager", "chvost je názov dokumentu, nie projektu"


def test_a_folder_without_a_charter_says_so(tmp_path) -> None:
    root = _project(tmp_path, "holy")
    found = discover(root, "holy")
    assert found.name is None
    assert any("názov projektu" in u for u in found.unresolved)


# ── čo sa prečítať nedá ───────────────────────────────────────────────────────


def test_the_login_method_is_never_guessed(tmp_path) -> None:
    """⚠️ Toto je jadro zásady. Spôsob prihlasovania sa z disku spoľahlivo zistiť nedá, a uhádnutá
    hodnota by v prehľade vyzerala ako zistený údaj — manažér by ju odklikol bez pozretia."""
    root = _project(tmp_path, "hocico", charter="# Hocičo\n")
    found = discover(root, "hocico")
    assert any("spôsob prihlasovania" in u for u in found.unresolved)


def test_it_says_where_each_value_came_from(tmp_path) -> None:
    """Prevzatie je zápis do evidencie, ktorý má sedieť s realitou — manažér musí PRED potvrdením
    vidieť, odkiaľ sa čo vzalo, nie sa to dozvedieť potom."""
    root = _project(
        tmp_path,
        "vzor2",
        charter="# Vzor\n",
        compose="services:\n  backend:\n    ports: ['10300:8000']\n",
    )
    found = discover(root, "vzor2")
    assert any("CLAUDE.md" in n for n in found.notes)
    assert any("docker-compose.yml" in n for n in found.notes)


# ── trasy ─────────────────────────────────────────────────────────────────────


def test_the_endpoints_are_mounted_where_the_cockpit_calls_them() -> None:
    """⚠️ Tá istá stráž, ktorá pri ICCINT-78 chýbala a stála jedno nasadenie navyše.

    A navyše poradie: ``/adoptable`` je konkrétna cesta a musí stáť PRED ``/{project_id}``, inak by ju
    tá druhá zožrala ako identifikátor projektu.
    """
    from backend.main import app

    paths = [getattr(route, "path", "") for route in app.routes]
    assert "/api/v1/projects/adoptable" in paths
    assert "/api/v1/projects/adoptable/{slug}" in paths
    assert paths.index("/api/v1/projects/adoptable") < paths.index("/api/v1/projects/{project_id}"), (
        "konkrétnu cestu zožerie tá s identifikátorom"
    )


@pytest.mark.parametrize("slug", ["nex-manager", "nex-payables", "nex-studio", "nex-studio-visual"])
def test_it_reads_the_real_projects_on_this_machine(slug: str) -> None:
    """⚠️ Skúška proti SKUTOČNÝM projektom, nie proti vymyslenému vzoru.

    Presne tie štyri čakajú na prevzatie. Vymyslený vzor by potvrdil len moju predstavu o tom, ako
    tie súbory vyzerajú — a práve tá predstava bola pri portoch dvakrát nesprávna. Mimo ANDROSu sa
    preskočí s uvedeným dôvodom, nie potichu prejde.
    """
    root = Path("/opt/projects") / slug
    if not (root / "docker-compose.yml").is_file():
        pytest.skip(f"{slug} nie je na tomto stroji — nekontrolované")
    found = discover(root, slug)
    assert found.name, f"{slug}: názov sa nepodarilo prečítať"
    assert found.backend_port and found.frontend_port and found.db_port, f"{slug}: porty sa nenašli"
    assert found.repo_url, f"{slug}: repozitár sa nenašiel"
