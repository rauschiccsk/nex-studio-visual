"""Reštart inštalácie prinesie presne tú verziu, ktorá sa nasadila (ICCINT-103).

**Čo sa dialo.** Vyrenderovaný compose označoval postavené obrazy ``:latest``. Po ľubovoľnom ďalšom
zostavení preto tá istá inštalácia po reštarte nabehla na INÝ kód — bez toho, aby ktokoľvek čokoľvek
nasadil. Pri UAT je to nepríjemné; pri PROD by to bolo vážne.

**Ako sa to našlo.** Pri prevzatí UAT MÁGERSTAVU 10.09.2026. Ručne písaný compose mal obrazy zámerne
pripnuté (``nexmanager-backend:1.0.0``) a v hlavičke k tomu vetu „PINNED images (byte-identické
s v1.0.0 buildom)“. Prevzatie to **ticho zrušilo** — pripnutie bolo vedomé rozhodnutie autora toho
súboru a nikde sa nepovedalo, že padlo.

⚠️ Toto je stráž nad tým, čo skončí v priečinku u zákazníka — nie nad tým, čo si o tom myslí kód.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.services import uat_provisioner as u

PROJEKT = Path("/opt/projects/nex-productcatalogs")


def _compose(version):
    if not (PROJEKT / "docker-compose.yml").is_file():
        pytest.skip("vzorový projekt nie je na tomto stroji — nekontrolované")
    source = u.load_source_compose(PROJEKT)
    return u.build_uat_compose(
        slug="acme-app",
        project="nex-productcatalogs",
        project_path=PROJEKT,
        source=source,
        roles=u.identify_service_roles(source["services"]),
        db_user="app",
        db_name="app",
        customer_slug="acme",
        app="app",
        version=version,
    )


def _postavene(compose) -> dict[str, str]:
    """Iba služby, ktoré sa STAVAJÚ — cudzie obrazy (postgres) sa označovať nesmú."""
    return {n: s["image"] for n, s in compose["services"].items() if s.get("build") is not None}


def test_built_images_carry_the_deployed_version(tmp_path) -> None:
    """⚠️ Jadro nálezu: v súbore u zákazníka musí byť číslo verzie, nie ``latest``."""
    obrazy = _postavene(_compose("1.1.0"))

    assert obrazy, "vo vzorovom projekte sa nič nestavia — stráž by nemerala nič"
    for meno, obraz in obrazy.items():
        assert obraz.endswith(":1.1.0"), f"služba {meno} beží na {obraz} — reštart môže priniesť iný kód"


def test_two_versions_never_share_a_tag() -> None:
    """Bez toho by pripnutie bolo len ozdoba: dve rôzne verzie pod jedným menom = ten istý problém."""
    prve = _postavene(_compose("1.1.0"))
    druhe = _postavene(_compose("1.2.0"))

    assert set(prve) == set(druhe)
    for meno in prve:
        assert prve[meno] != druhe[meno], f"{meno} má pri oboch verziách rovnaké označenie {prve[meno]}"


def test_images_that_are_not_built_are_left_alone() -> None:
    """⚠️ Cudzie obrazy (postgres) sa označovať NESMÚ — inak by compose ukazoval na obraz, čo neexistuje.

    Bez tohto tvrdenia by stráže vyššie prešli aj vtedy, keby sa označovalo všetko.
    """
    compose = _compose("1.1.0")
    cudzie = {n: s["image"] for n, s in compose["services"].items() if s.get("build") is None and "image" in s}

    assert cudzie, "vo vzorovom projekte niet cudzieho obrazu — stráž by nemerala nič"
    for meno, obraz in cudzie.items():
        assert not obraz.endswith(":1.1.0"), f"cudziemu obrazu {meno} sa prepísalo označenie na {obraz}"


def test_without_a_version_nothing_changes() -> None:
    """Staršie volania (a skúšky) musia fungovať ako doteraz — inak by oprava rozbila okolie."""
    for chybajuca in (None, ""):
        obrazy = _postavene(_compose(chybajuca))
        for meno, obraz in obrazy.items():
            assert obraz.endswith(":latest"), f"{meno} bez zadanej verzie dostal {obraz}"


@pytest.mark.parametrize(
    ("zadane", "ocakavane"),
    [
        ("1.1.0", "1.1.0"),
        ("v1.2.0", "v1.2.0"),
        ("1.0.0-rc.1", "1.0.0-rc.1"),
        ("../uteka", "_uteka"),
        (".bodka", "bodka"),
    ],
)
def test_the_tag_is_something_docker_accepts(zadane: str, ocakavane: str) -> None:
    """Číslo verzie chodí zvonku a končí v označení obrazu — nesmie z neho spraviť neplatný názov."""
    assert u._image_tag(zadane) == ocakavane


def test_the_build_section_stays_so_the_tagged_image_gets_built() -> None:
    """⚠️ Pripnutie bez ``build`` by bolo horšie než ``latest``: compose by ukazoval na obraz, ktorý
    nikto nepostaví, a nasadenie by padlo na „image not found“."""
    for meno, svc in _compose("1.1.0")["services"].items():
        if svc.get("image", "").endswith(":1.1.0"):
            assert svc.get("build") is not None, f"{meno} má pripnuté označenie, ale nemá sa z čoho postaviť"


def test_the_version_actually_reaches_the_renderer(tmp_path) -> None:
    """⚠️ Spoj, nie okolie spoja.

    Stráže vyššie volajú ``build_uat_compose`` priamo, takže by prešli aj vtedy, keby
    ``provision_uat`` verziu ďalej NEODOVZDAL — a v priečinku u zákazníka by aj tak stálo ``latest``.
    Presne túto dieru som mal dnes už raz (``adopting_deploy_runner``), preto sa tu meria celá cesta:
    vyrenderuje sa naozaj a prečíta sa, čo v tom súbore skončilo.
    """
    if not (PROJEKT / "docker-compose.yml").is_file():
        pytest.skip("vzorový projekt nie je na tomto stroji — nekontrolované")

    u.provision_uat(
        "nex-productcatalogs",
        "skuska-uat",
        version="7.7.7",
        uat_root=tmp_path,
        customer_slug="skuska",
        app="app",
        full_project_slug="nex-productcatalogs",
    )
    napisane = (tmp_path / "skuska" / "nex-productcatalogs" / "docker-compose.yml").read_text(encoding="utf-8")

    assert ":7.7.7" in napisane, "verzia sa do vyrenderovaného súboru nedostala — cesta je prerušená"
    assert f"{u.GENERATED_BY_MARKER}" in napisane, "vyrenderovaný súbor nenesie hlavičku provisionera"


# ── appka hovorí o sebe nasadenú verziu (ICCINT-104) ──────────────────────────


def test_the_app_reports_the_version_that_was_deployed(tmp_path) -> None:
    """⚠️ Jadro ICCINT-104: appka nesmie o sebe hovoriť číslo z čias, keď niekto písal `.env.example`.

    Zmerané 10.09.2026 na UAT MÁGERSTAVU po nasadení 1.1.0: frontend hlásil 1.1.0 správne, ale
    ``VITE_APP_VERSION`` v kontajneri zostalo ``1.0.0``. Dve časti tej istej appky sa hlásili rôznymi
    číslami — pri hľadaní chyby je to presne ten údaj, ktorý zavedie na nesprávnu stopu.
    """
    napisane = u.generate_uat_env(
        slug="skuska-uat",
        project="nex-manager",
        version="1.1.0",
        services={},
        be_service=None,
        db_service=None,
        source_env_example={"VITE_APP_VERSION": "1.0.0", "APP_VERSION": "0.9.0"},
        db_user="app",
        db_name="app",
        shared_db_password="x",  # noqa: S106 — skúšobná hodnota, nie tajomstvo
        preserved_secrets={},
    )

    assert "VITE_APP_VERSION=1.1.0" in napisane, "appka o sebe hovorí verziu z .env.example, nie nasadenú"
    assert "APP_VERSION=1.1.0" in napisane
    assert "1.0.0" not in napisane and "0.9.0" not in napisane, "stará hodnota v súbore ostala"


def test_a_version_variable_is_never_invented(tmp_path) -> None:
    """Appke, ktorá o svojej verzii nehovorí, sa nastavenie nedopĺňa — nevymýšľame jej premenné."""
    napisane = u.generate_uat_env(
        slug="skuska-uat",
        project="nex-manager",
        version="1.1.0",
        services={},
        be_service=None,
        db_service=None,
        source_env_example={"SMTP_HOST": "posta.local"},
        db_user="app",
        db_name="app",
        shared_db_password="x",  # noqa: S106 — skúšobná hodnota, nie tajomstvo
        preserved_secrets={},
    )

    assert "APP_VERSION=" not in napisane, "appke sa dopísala premenná, ktorú sama nedeklaruje"
    assert "VITE_APP_VERSION=" not in napisane


def test_a_contract_version_is_left_alone(tmp_path) -> None:
    """⚠️ ``API_VERSION`` je číslo zmluvy rozhrania, nie appky — prepísať ho by bola väčšia chyba.

    Bez tohto tvrdenia by stráže vyššie prešli aj vtedy, keby sa prepisovalo všetko, čo končí na
    ``_VERSION`` — a nasadenie by appke ticho zmenilo cestu rozhrania.
    """
    napisane = u.generate_uat_env(
        slug="skuska-uat",
        project="nex-manager",
        version="1.1.0",
        services={},
        be_service=None,
        db_service=None,
        source_env_example={"API_VERSION": "v1", "NODE_VERSION": "20"},
        db_user="app",
        db_name="app",
        shared_db_password="x",  # noqa: S106 — skúšobná hodnota, nie tajomstvo
        preserved_secrets={},
    )

    assert "API_VERSION=v1" in napisane, "prepísalo sa číslo zmluvy rozhrania"
    assert "NODE_VERSION=20" in napisane, "prepísala sa verzia behového prostredia"
