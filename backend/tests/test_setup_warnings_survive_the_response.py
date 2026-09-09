"""Čo sa pri zakladaní nedorobilo, musí byť vidieť aj potom (ICCINT-88).

**Čo sa dialo.** Pri prevzatí projektu kokpit zostavil vetu o tom, čo sa zámerne vynechalo — CI,
ochrana vetvy, skúšobné spustenie — a nikto ju neuvidel. Žila len v odpovedi na založenie, a dialóg
prevzatia po úspechu odchádza na stránku projektu, takže odpoveď zanikla skôr, než sa dala prečítať.
Zmerané 09.09.2026 pri prevzatí NEX Managera.

**Prečo na tom záleží.** Správa, ktorú nikto neprečíta, je to isté ako ticho — a práve tomu má
brániť. Manažér nemá ako vedieť, že prevzatý projekt si CI a ochranu vetvy drží vlastnú.

⚠️ **Prečo sa to meria cez HTTP a nie na zberači.** Stráž vedľa (``test_branch_protection_warning``)
overuje, že veta z kroku vylezie do zoznamu — a tá prechádzala aj vtedy, keď sa zoznam o chvíľu nato
zahodil. Diera nebola v tom, či veta vznikne, ale v tom, či prežije odpoveď. Preto sa tu projekt
naozaj založí a potom sa načíta znova, presne ako to robí stránka projektu.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from backend.core.security import get_current_user, require_shu_or_above
from backend.db.models.foundation import User

#: Veta, ktorú kokpit pri prevzatí zostaví. Kontroluje sa jej podstatné slovo, nie celé znenie —
#: stráž má merať, či správa prežije, nie či ju niekto preštylizoval.
PREVZATIE = "prevzatý"


@pytest.fixture(autouse=True)
def _bez_githubu():
    """Zakladanie sa nemá pýtať skutočného GitHubu — meriame, čo sa stane s vetou, nie sieť."""
    with (
        patch("backend.services.github_validation.validate_github_repo", return_value=True),
        patch("backend.services.github_validation.create_github_repo", return_value=None),
    ):
        yield


@pytest.fixture()
def zakladatel(db_session) -> User:
    user = User(
        username="zakladatel-88",
        email="zakladatel-88@test.local",
        password_hash="x",  # noqa: S106 — nenulový stĺpec, nie tajomstvo
        role="ri",
    )
    db_session.add(user)
    db_session.flush()
    return user


def test_what_adoption_skipped_is_still_there_when_the_page_loads(client, zakladatel, tmp_path) -> None:
    """⚠️ Jadro nálezu: to, čo sa pri prevzatí zámerne nespravilo, musí prežiť odpoveď na založenie.

    Toto je presne tá cesta, ktorá mlčala. Odpoveď vetu niesla, ale dialóg prevzatia po úspechu
    odchádza na stránku projektu — takže ju nikto nestihol prečítať a stránka ju nemala odkiaľ vziať.

    Priečinok je skutočný a neprázdny, lebo práve podľa toho kokpit prevzatie rozpozná: čo v ňom už
    je, to sme tam nedali my, takže sa doň nescaffolduje ani netlačí.
    """
    priecinok = tmp_path / "prevzaty-projekt"
    priecinok.mkdir()
    (priecinok / "CLAUDE.md").write_text("# vlastné pravidlá\n", encoding="utf-8")

    client.app.dependency_overrides[get_current_user] = lambda: zakladatel
    client.app.dependency_overrides[require_shu_or_above] = lambda: zakladatel
    # ⚠️ Evidencia portov musí v tejto stráži USPIEŤ. Zápis do nej beží zámerne až po commite a má
    # vlastný zápis upozornenia — takže kým padá (a v skúšobnom prostredí padá vždy), jeho zápis
    # zakryje ten, ktorý sa tu meria, a stráž prejde aj s odstránenou opravou. Zmerané pri písaní.
    with patch("backend.services.port_registry.record_allocation", return_value=None):
        resp = client.post(
            "/api/v1/projects",
            json={
                "name": "Prevzatý projekt ICCINT-88",
                "slug": "prevzaty-projekt-88",
                "type": "standard",
                "auth_mode": "password",
                "description": "projekt na overenie, že sa upozornenia nestratia",
                "source_path": str(priecinok),
                "backend_port": 10940,
                "frontend_port": 10941,
                "db_port": 10942,
            },
        )
    assert resp.status_code == 201, resp.text
    vytvoreny = resp.json()
    assert any(PREVZATIE in v for v in vytvoreny["setup_warnings"]), (
        f"odpoveď na prevzatie mlčí o tom, čo sa vynechalo: {vytvoreny['setup_warnings']}"
    )

    # A teraz to, čo urobí stránka projektu pri načítaní — čítanie nanovo, bez pamäte na odpoveď.
    znovu = client.get(f"/api/v1/projects/{vytvoreny['id']}")
    assert znovu.status_code == 200, znovu.text
    # ⚠️ Obsah, nie len zhoda. Prvé znenie tejto stráže porovnávalo iba načítané s odpoveďou — a to
    # prejde aj vtedy, keď sú OBA prázdne, teda presne v tom stave, ktorý mal nález popisovať.
    assert any(PREVZATIE in v for v in znovu.json()["setup_warnings"]), (
        "upozornenie zaniklo v odpovedi na založenie — stránka projektu ho nemá odkiaľ prečítať "
        f"a Manažér sa o vynechaných krokoch nedozvie nikdy (načítané: {znovu.json()['setup_warnings']})"
    )
    assert znovu.json()["setup_warnings"] == vytvoreny["setup_warnings"], (
        "odpoveď na založenie a načítaná stránka hovoria o tom istom projekte niečo iné"
    )


def test_a_project_where_nothing_was_skipped_stays_quiet(client, db_session, zakladatel) -> None:
    """Prázdny zoznam je bežný prípad a nesmie sa z neho stať panel s ničím.

    Bez tohto tvrdenia by stráž vyššie prešla aj vtedy, keby sa k projektu zapisovalo čokoľvek.
    """
    from backend.db.models.projects import Project

    projekt = Project(
        created_by=zakladatel.id,
        name="Bezchybný projekt ICCINT-88",
        slug="bezchybny-projekt-88",
        type="standard",
        auth_mode="password",
        description="nič sa nevynechalo",
        backend_port=10950,
        frontend_port=10951,
        db_port=10952,
    )
    db_session.add(projekt)
    db_session.flush()

    client.app.dependency_overrides[get_current_user] = lambda: zakladatel
    znovu = client.get(f"/api/v1/projects/{projekt.id}")

    assert znovu.status_code == 200, znovu.text
    assert znovu.json()["setup_warnings"] == [], "projekt, kde všetko prebehlo, si nemá čo pamätať"
