"""Uloženie Zadania nesmie ticho zahodiť to, čo už na disku je (ICCINT-71).

07.09.2026, nex-productcatalogs v0.2.0: do `customer-requirements.md` bola vopred napísaná zákaznícka
špecifikácia — 71 riadkov, sedem sekcií (rozhodnutie o nočnom sťahovaní, poistka „pri zlyhaní platí
posledný dobrý súbor", zákaz dostať cenník do histórie projektu, upozornenie že odkaz je tajomstvo).
Director potom založil verziu a do Zadania napísal jednu vetu. Kokpit ju zapísal cez ňu:
`1 insertion(+), 71 deletions(-)`. Agent dostal 51 znakov namiesto 3 380.

Nebolo to prehliadnutie, ale zapísaný predpoklad: „the version's spec directory does not exist yet at
this point in the flow". Predpoklad sa nikdy neoveroval. Teraz sa overuje.
"""

from __future__ import annotations

import uuid

import pytest

from backend.db.models.foundation import User
from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.services import version as version_service


def _seed(db_session, tmp_path, monkeypatch, *, slug: str) -> Version:
    monkeypatch.setattr(version_service, "_PROJECTS_ROOT", tmp_path)
    owner = User(
        id=uuid.uuid4(),
        username=f"z-{uuid.uuid4().hex[:8]}",
        email=f"{uuid.uuid4().hex[:8]}@example.test",
        password_hash="x",  # noqa: S106 — nenulový stĺpec, nie tajomstvo
        role="ri",
    )
    project = Project(
        id=uuid.uuid4(),
        created_by=owner.id,
        name="zadanie-test",
        slug=slug,
        type="web",
        auth_mode="password",
        description="projekt na overenie zápisu Zadania",
        backend_port=10900,
        frontend_port=10901,
        db_port=10902,
    )
    version = Version(id=uuid.uuid4(), project_id=project.id, version_number="0.2.0", status="planned")
    db_session.add_all([owner, project, version])
    db_session.flush()
    return version


def test_it_refuses_to_write_over_a_different_zadanie(db_session, tmp_path, monkeypatch) -> None:
    """Jadro nálezu. Bez tohto tvrdenia sa 71 riadkov stratí a nikto sa to nedozvie."""
    version = _seed(db_session, tmp_path, monkeypatch, slug="odmietne")
    cesta = tmp_path / "odmietne" / "docs" / "specs" / "versions" / "v0.2.0"
    cesta.mkdir(parents=True)
    (cesta / "customer-requirements.md").write_text("# Špecifikácia\n\nSedemdesiat riadkov práce.\n", encoding="utf-8")

    with pytest.raises(version_service.ZadanieWouldBeOverwritten) as odmietnuté:
        version_service.write_zadanie(db_session, version.id, "Jedna veta.")

    # Obsah musí PRÍSŤ SO SEBOU — Manažér sa má rozhodnúť nad tým, čo tam je, nie nad opisom toho.
    assert "Sedemdesiat riadkov práce." in odmietnuté.value.existing
    assert (cesta / "customer-requirements.md").read_text(encoding="utf-8").startswith("# Špecifikácia")


def test_the_same_text_saved_twice_is_not_an_overwrite(db_session, tmp_path, monkeypatch) -> None:
    """Uložiť to isté znova musí ostať nečinnosťou, nie chybou — inak sa stráž stane otravou a obíde sa."""
    version = _seed(db_session, tmp_path, monkeypatch, slug="rovnake")
    version_service.write_zadanie(db_session, version.id, "Rovnaký text.")
    version_service.write_zadanie(db_session, version.id, "Rovnaký text.\n")


def test_an_explicit_decision_still_replaces(db_session, tmp_path, monkeypatch) -> None:
    """Stráž nie je zámok. Keď Manažér vidí, čo tam je, a rozhodne sa to nahradiť, nahradí sa."""
    version = _seed(db_session, tmp_path, monkeypatch, slug="nahradi")
    version_service.write_zadanie(db_session, version.id, "Pôvodné.")

    version_service.write_zadanie(db_session, version.id, "Nové.", replace_existing=True)

    cesta = tmp_path / "nahradi" / "docs" / "specs" / "versions" / "v0.2.0" / "customer-requirements.md"
    assert cesta.read_text(encoding="utf-8") == "Nové."


def test_an_empty_file_is_not_something_to_protect(db_session, tmp_path, monkeypatch) -> None:
    """Prázdny súbor nie je práca. Chrániť ho by znamenalo pýtať sa na niečo, čo nikoho nezaujíma."""
    version = _seed(db_session, tmp_path, monkeypatch, slug="prazdny")
    cesta = tmp_path / "prazdny" / "docs" / "specs" / "versions" / "v0.2.0"
    cesta.mkdir(parents=True)
    (cesta / "customer-requirements.md").write_text("   \n\n", encoding="utf-8")

    version_service.write_zadanie(db_session, version.id, "Prvé zadanie.")

    assert (cesta / "customer-requirements.md").read_text(encoding="utf-8") == "Prvé zadanie."
