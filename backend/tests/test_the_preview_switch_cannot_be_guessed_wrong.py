"""Dohoda o zapnutí živého náhľadu je napísaná, nie tipovaná (ICCINT-93).

**Čo sa stalo.** Pieskovisko náhľadu nastavovalo appke ``VITE_PREVIEW=1``, ale nikde nebolo napísané,
akú hodnotu má appka očakávať. Štyri projekty tipli pravdivostnú kontrolu a náhľad im fungoval;
NEX Manager tipol ``=== "true"`` a náhľad sa mu vôbec nezapol.

⚠️ **A prejavilo sa to ako niečo úplne iné.** Predstierané odpovede sa nezapli, appka sa pýtala
skutočného servera, ten v náhľade nie je — a Manažér dostal PRIHLASOVACIU STENU a hlásil „nepoznám
prihlasovacie údaje“. Skutočná príčina bol preklep v podmienke. Zmerané 09.09.2026 na NEX Manager 1.1.0;
zastavilo to prácu na hodinu.

**Ako sa to rieši.** Nie odporúčaním, ale hodnotou, pri ktorej sa tipnúť nedá: ``true`` je pravdivé
a zároveň sa rovná slovu ``"true"``. Neexistuje rozumná kontrola, ktorá by prešla pri ``1`` a zlyhala
pri ``true`` — trieda chyby tým zaniká.

Druhá stráž číta SKUTOČNÉ projekty na disku a porovnáva ich podmienku s tým, čo pieskovisko posiela.
Je to tá istá myšlienka ako pri stráži nad ``init.sh``: dohoda medzi dvoma repozitármi sa overuje
proti nim samým, nie proti kópii.
"""

from __future__ import annotations

import pathlib
import re

import pytest

from backend.services import vizual_sandbox


def _flag_value() -> str:
    """Hodnota, ktorú pieskovisko appke naozaj pošle — čítaná z argv, nie z konštanty vedľa."""
    argv = vizual_sandbox.build_run_argv(slug="vzor", frontend_host_path=pathlib.Path("/tmp/vzor"))
    for arg in argv:
        if isinstance(arg, str) and arg.startswith("VITE_PREVIEW="):
            return arg.split("=", 1)[1]
    raise AssertionError("pieskovisko appke vôbec nepovie, že ide o náhľad")


def test_the_value_satisfies_both_ways_an_app_may_check_it() -> None:
    """⚠️ Jadro ICCINT-93: hodnota musí prejsť aj pravdivostnou kontrolou, aj porovnaním so slovom.

    ``1`` prejde len prvou — a práve na tom sa náhľad NEX Managera nezapol.
    """
    value = _flag_value()

    assert value, "prázdna hodnota náhľad nezapne ani pravdivostnou kontrolou"
    assert value == "true", (
        f"pieskovisko posiela {value!r}; appka, ktorá porovnáva so slovom „true“, náhľad nezapne "
        "a Manažér dostane prihlasovaciu stenu namiesto appky"
    )


FRONTENDS = pathlib.Path("/opt/projects")


@pytest.mark.parametrize("slug", ["nex-manager", "nex-productcatalogs", "nex-shopify", "nex-websites"])
def test_the_real_apps_accept_what_the_sandbox_sends(slug: str) -> None:
    """⚠️ Dohoda sa overuje proti SKUTOČNÝM appkám, nie proti vymyslenému vzoru.

    Práve toto by bolo chytilo NEX Manager hneď — mal ``=== "true"``, kým pieskovisko posielalo ``1``.
    Mimo ANDROSu sa preskočí s uvedeným dôvodom, nie potichu prejde.
    """
    main = FRONTENDS / slug / "frontend" / "src" / "main.tsx"
    if not main.is_file():
        pytest.skip(f"{slug} nie je na tomto stroji — nekontrolované")

    text = main.read_text(encoding="utf-8")
    if "VITE_PREVIEW" not in text:
        pytest.skip(f"{slug} živý náhľad nepoužíva")

    posiela = _flag_value()
    for porovnanie in re.findall(r'VITE_PREVIEW\s*(?:!==|===)\s*"([^"]*)"', text):
        assert porovnanie == posiela, (
            f"{slug} porovnáva príznak náhľadu so {porovnanie!r}, ale pieskovisko posiela {posiela!r} — "
            "náhľad sa nezapne a Manažér uvidí prihlasovaciu obrazovku"
        )
