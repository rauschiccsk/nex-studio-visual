"""Ostré nasadenie ide na stroj zákazníka, testovacie zostáva tu (ICCINT-151).

**Čo tomu predchádzalo.** Ostrá prevádzka MÁGERSTAVU beží na vlastnom serveri (MAGER) a nasadzovala
sa tam rukami. Director 24.09.2026: *„Žiadne ručné nasadenia už nebudú. Má to fungovať presne tak
ako nasadzujem UAT, len nasadenie UAT-u robíme na ANDROS Serveri a PROD ide na MAGER Server."*

Docker to vie sám — cez ``DOCKER_HOST=ssh://<stroj>``. ``_run_uat_deploy`` cieľ prijímať vedel od
v4.40.6, ale **nemal ho od koho dostať**: ``_run_prod_deploy`` ho neposielal ďalej a vykonávateľ
nasadenia o ňom nevedel. Celá cesta na iný server sa končila na tomto jedinom mieste a ostré
nasadenie by ticho zbehlo na stroji kokpitu.

⚠️ Čo tieto stráže NEDOVOLIA zmeniť: cieľ musí prejsť celou cestou od zákazníka až k nasadeniu, a
testovacia inštalácia sa naň NIKDY nesmie zviezť — údaj sa volá ``prod_host`` a týka sa ostrej
prevádzky. Keby ho prevzala aj testovacia, prvá skúška zákazníka by mu zasiahla do vlastného servera.
"""

from __future__ import annotations

import inspect

import pytest

from backend.services import deploy as deploy_service
from backend.services import instance_adoption, orchestrator


def _zachyt(monkeypatch) -> list[dict]:
    """Zapamätá si, s čím sa volalo nasadenie — a nespustí nič."""
    videne: list[dict] = []

    async def _fake_prod(project_slug, customer_slug, app, full_project_slug, version_number=None, deploy_host=None):
        videne.append({"kde": "prod", "deploy_host": deploy_host, "customer_slug": customer_slug})
        return True, "OK"

    async def _fake_uat(*args, **kwargs):
        videne.append({"kde": "uat", "deploy_host": kwargs.get("deploy_host")})
        return True, "OK"

    monkeypatch.setattr(orchestrator, "_run_prod_deploy", _fake_prod)
    monkeypatch.setattr(orchestrator, "_run_uat_deploy", _fake_uat)
    return videne


def _bez_zapisu(monkeypatch) -> None:
    """Generátor sa v týchto stráženiach nespúšťa — ide o to, kam sa nasadzuje, nie čo sa vykreslí."""

    class _Vysledok:
        fe_service = "frontend"
        warnings: list[str] = []

    monkeypatch.setattr(instance_adoption.uat_provisioner, "provision_uat", lambda *a, **k: _Vysledok())
    monkeypatch.setattr(deploy_service.uat_provisioner, "provision_uat", lambda *a, **k: _Vysledok())


# ── 1. cieľ prejde celou cestou ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_prod_deploy_carries_the_target_machine(monkeypatch) -> None:
    """⚠️ Jadro veci: bez tohto sa ostrá prevádzka nasadí na stroj kokpitu."""
    videne = _zachyt(monkeypatch)
    _bez_zapisu(monkeypatch)

    await deploy_service._default_deploy_runner(
        project_slug="nex-manager",
        uat_slug="mager-prod",
        version_number="1.2.2",
        force_fresh=False,
        deploy_host="mager",
    )

    assert videne == [{"kde": "prod", "deploy_host": "mager", "customer_slug": "mager"}]


@pytest.mark.asyncio
async def test_the_adoption_carries_it_too(monkeypatch) -> None:
    """Prevzatie je druhá cesta k tomu istému nasadeniu — a nesmie o cieli vedieť menej."""
    videne = _zachyt(monkeypatch)
    _bez_zapisu(monkeypatch)
    monkeypatch.setattr(instance_adoption, "set_aside_hand_authored", lambda _d: [])

    await instance_adoption.adopting_deploy_runner(
        project_slug="nex-manager",
        uat_slug="mager-prod",
        version_number="1.2.2",
        force_fresh=False,
        deploy_host="mager",
    )

    assert [v["deploy_host"] for v in videne] == ["mager"]


def test_the_wrapper_passes_it_on_rather_than_dropping_it() -> None:
    """Overuje sa proti SAMOTNÉMU zdroju obalu, nie proti napodobenine — tá by potvrdila len moju
    predstavu o ňom. Presne tu sa cesta na iný server predtým končila."""
    zdroj = inspect.getsource(orchestrator._run_prod_deploy)

    assert "deploy_host" in inspect.signature(orchestrator._run_prod_deploy).parameters
    assert "deploy_host=deploy_host" in zdroj, "obal cieľ prijal a zahodil"


# ── 2. testovacia inštalácia sa na cudzí stroj nezvezie ───────────────────────


@pytest.mark.asyncio
async def test_the_uat_deploy_never_goes_to_the_customers_machine(monkeypatch) -> None:
    """⚠️ Údaj sa volá ``prod_host``. Testovacia inštalácia beží na stroji kokpitu — vždy."""
    videne = _zachyt(monkeypatch)
    _bez_zapisu(monkeypatch)

    await deploy_service._default_deploy_runner(
        project_slug="nex-manager",
        uat_slug="mager-uat",
        version_number="1.2.2",
        force_fresh=False,
        deploy_host="mager",
    )

    assert videne and videne[0]["kde"] == "uat"
    assert videne[0]["deploy_host"] is None, "skúšobná inštalácia by zasiahla do servera zákazníka"


@pytest.mark.asyncio
async def test_no_target_means_this_machine(monkeypatch) -> None:
    """Zákazník bez vlastného servera je bežný prípad — nie chyba."""
    videne = _zachyt(monkeypatch)
    _bez_zapisu(monkeypatch)

    await deploy_service._default_deploy_runner(
        project_slug="nex-manager",
        uat_slug="icc-prod",
        version_number="1.2.2",
        force_fresh=False,
    )

    assert videne[0]["deploy_host"] is None


# ── 3. údaj sa číta tam, kde je zákazník ──────────────────────────────────────


def test_the_service_reads_the_target_off_the_customer() -> None:
    """Vykonávateľ dostane hotový údaj; databázy sa pýta ten, kto zákazníka drží v ruke."""
    zdroj = inspect.getsource(deploy_service.deploy)

    assert "customer.prod_host" in zdroj, "cieľ sa od zákazníka vôbec nečíta"
    assert "deploy_host=deploy_host" in zdroj, "prečítaný cieľ sa vykonávateľovi neposiela"


def test_the_runner_seam_accepts_the_target() -> None:
    """Obe cesty k nasadeniu majú rovnaký tvar — inak by jedna z nich cieľ stratila."""
    for vykonavatel in (deploy_service._default_deploy_runner, instance_adoption.adopting_deploy_runner):
        parametre = inspect.signature(vykonavatel).parameters
        assert "deploy_host" in parametre, f"{vykonavatel.__name__} o cieli nevie"
        assert parametre["deploy_host"].default is None, "predvolene sa nasadzuje na tento stroj"
