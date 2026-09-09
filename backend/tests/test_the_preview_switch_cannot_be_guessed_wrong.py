"""Dohoda o zapnutí živého náhľadu je napísaná, nie tipovaná (ICCINT-93, ICCINT-95).

**Čo sa stalo prvýkrát.** Pieskovisko náhľadu nastavovalo appke ``VITE_PREVIEW=1``, ale nikde nebolo
napísané, akú hodnotu má appka očakávať. Štyri projekty tipli pravdivostnú kontrolu a náhľad im
fungoval; NEX Manager tipol ``=== "true"`` a náhľad sa mu vôbec nezapol.

⚠️ **A prejavilo sa to ako niečo úplne iné.** Predstierané odpovede sa nezapli, appka sa pýtala
skutočného servera, ten v náhľade nie je — a Manažér dostal PRIHLASOVACIU STENU a hlásil „nepoznám
prihlasovacie údaje“. Skutočná príčina bol preklep v podmienke. Zmerané 09.09.2026 na NEX Manager 1.1.0;
zastavilo to prácu na hodinu.

**Čo sa stalo druhýkrát.** Opravou bolo „kontroluj pravdivostne, nikdy neporovnávaj so slovom“ — a to
je len druhá polovica pravdy. Nezávislá previerka NEX Manager 1.1.0 upozornila, že v JavaScripte je
pravdivý aj reťazec ``"false"``, takže **vypnutie slovom „false“ by náhľad ZAPLO**. Nie je to
teoretické: vypínač ``VITE_LAUNCH_LIVE=false`` sa reťazcom „false“ naozaj používa, takže ten zvyk je
živý a raz to sem niekto napíše.

**Ako sa to rieši.** Výslovným zoznamom zapínacích hodnôt (``PREVIEW_ON``). Vymenované hodnoty náhľad
zapnú, čokoľvek iné — ``false``, ``0``, ``off``, prázdno — nie. Obe chyby tým zanikajú naraz.

Stráže čítajú SKUTOČNÉ súbory: hodnotu z argv pieskoviska, podmienku zo šablóny na disku a podmienky
skutočných appiek. Je to tá istá myšlienka ako pri stráži nad ``init.sh``: dohoda medzi dvoma
repozitármi sa overuje proti nim samým, nie proti kópii vedľa.
"""

from __future__ import annotations

import pathlib
import re

import pytest

from backend.services import vizual_sandbox

#: Hodnoty, ktoré náhľad zapnúť NESMÚ. ``false`` je jadro ICCINT-95, zvyšok je ten istý zvyk.
OFF_VALUES = ("false", "0", "off", "no", "")

TEMPLATE = pathlib.Path("/home/icc/knowledge/templates/claude-project/frontend-skeleton")
FRONTENDS = pathlib.Path("/opt/projects")

#: Appky, ktoré ešte kontrolujú príznak PRAVDIVOSTNE. Dnes im to nič nekazí (``VITE_PREVIEW`` sa
#: nikde nenastavuje na ``false``), ale zoznam je západka: keď sa niektorá prepíše na výslovný
#: zoznam, táto stráž spadne a zoznam sa musí skrátiť. Prázdny zoznam = hospodárstvo je zjednotené.
#:
#: ⚠️ Tieto štyri sa NEOPRAVUJÚ odtiaľto — zmeny cudzích projektov idú cez kokpit (rozhodnutie
#: Directora). Sem patrí len to, že sa o tom vie a že sa to nedá ticho zabudnúť.
APPS_STILL_ON_TRUTHINESS = ("nex-manager", "nex-productcatalogs", "nex-shopify", "nex-websites")


def _flag_value() -> str:
    """Hodnota, ktorú pieskovisko appke naozaj pošle — čítaná z argv, nie z konštanty vedľa."""
    argv = vizual_sandbox.build_run_argv(slug="vzor", frontend_host_path=pathlib.Path("/tmp/vzor"))
    for arg in argv:
        if isinstance(arg, str) and arg.startswith("VITE_PREVIEW="):
            return arg.split("=", 1)[1]
    raise AssertionError("pieskovisko appke vôbec nepovie, že ide o náhľad")


def _accepted_values(text: str) -> tuple[str, ...] | None:
    """Hodnoty, ktoré podmienka v ``main.tsx`` prijme.

    Vráti ``None``, ak appka kontroluje PRAVDIVOSTNE — vtedy prijme čokoľvek neprázdne vrátane
    ``"false"``, a práve to je nález ICCINT-95.
    """
    literals = re.findall(r'(?:VITE_PREVIEW|previewFlag)\s*===\s*"([^"]*)"', text)
    return tuple(literals) if literals else None


def test_the_sandbox_sends_a_value_from_the_agreed_list() -> None:
    """Čo pieskovisko posiela, musí byť na zozname — inak by náhľad nezaplo ani správne napísanej appke."""
    posiela = _flag_value()

    assert posiela, "prázdna hodnota náhľad nezapne nijakou kontrolou"
    assert posiela in vizual_sandbox.PREVIEW_ON, (
        f"pieskovisko posiela {posiela!r}, čo na zozname zapínacích hodnôt {vizual_sandbox.PREVIEW_ON} "
        "nie je — appka podľa šablóny náhľad nezapne a Manažér dostane prihlasovaciu stenu"
    )


def test_the_agreed_list_refuses_the_words_people_switch_things_off_with() -> None:
    """⚠️ Jadro ICCINT-95: ``false``, ``0`` ani ``off`` sa na zoznam nesmú dostať."""
    for off in OFF_VALUES:
        assert off not in vizual_sandbox.PREVIEW_ON, (
            f"{off!r} je na zozname zapínacích hodnôt — vypnutie príznaku by náhľad ZAPLO "
            "a predstierané odpovede by sa dostali do behu, kde nemajú čo robiť"
        )


def test_the_template_compares_against_the_whole_list_not_a_single_word_nor_truthiness() -> None:
    """Šablóna je rada zapísaná do KAŽDÉHO budúceho projektu — musí byť správna práve tam.

    Toto je stráž, ktorá by bola chytila oba omyly: aj porovnanie s jediným slovom (ICCINT-93),
    aj pravdivostnú kontrolu, ktorá prijme ``"false"`` (ICCINT-95).
    """
    main = TEMPLATE / "src" / "main.tsx"
    if not main.is_file():
        pytest.skip(f"šablóna nie je na tomto stroji ({main}) — nekontrolované")

    prijima = _accepted_values(main.read_text(encoding="utf-8"))

    assert prijima is not None, (
        "šablóna kontroluje príznak náhľadu PRAVDIVOSTNE — tým prijme aj reťazec „false“ a vypnutie "
        "by náhľad zaplo (ICCINT-95). Porovnaj s celým zoznamom zapínacích hodnôt."
    )
    assert sorted(prijima) == sorted(vizual_sandbox.PREVIEW_ON), (
        f"šablóna prijíma {prijima}, ale dohodnutý zoznam je {vizual_sandbox.PREVIEW_ON} — "
        "nový projekt by mal inú predstavu než pieskovisko"
    )


def test_the_template_writes_the_contract_down_where_the_app_reads_it() -> None:
    """Nestačí správna podmienka — musí byť napísané PREČO, inak ju najbližší „zjednodušovač“ zruší."""
    types = TEMPLATE / "src" / "vite-env.d.ts"
    if not types.is_file():
        pytest.skip(f"šablóna nie je na tomto stroji ({types}) — nekontrolované")

    text = types.read_text(encoding="utf-8")
    for value in vizual_sandbox.PREVIEW_ON:
        assert f"`{value}`" in text, f"zmluva pri type neuvádza zapínaciu hodnotu {value!r}"
    assert "false" in text, "zmluva pri type nevaruje pred vypnutím slovom „false“ (ICCINT-95)"


def test_the_agent_is_told_the_list_not_just_the_flag_name() -> None:
    """Agent stavia náhľad podľa zadania — ak mu povieme len meno príznaku, tipne si podmienku sám."""
    from backend.services import orchestrator

    text = pathlib.Path(orchestrator.__file__).read_text(encoding="utf-8")
    zadanie = [line for line in text.splitlines() if "PREVIEW HARNESS" in line]
    assert zadanie, "zadanie pre Vizuál už o preview harness nehovorí — stráž oslepla, nájdi novú kotvu"

    # Únikové spätné lomky sa zahodia — či je reťazec v zdroji v úvodzovkách jednoduchých alebo
    # dvojitých, rozhoduje `ruff format`, a stráž nesmie na jeho rozhodnutí stáť.
    okno = text[text.index("PREVIEW HARNESS") : text.index("PREVIEW HARNESS") + 2000].replace("\\", "")
    for value in vizual_sandbox.PREVIEW_ON:
        assert f'"{value}"' in okno, f"zadanie pre agenta neuvádza zapínaciu hodnotu {value!r}"


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
    prijima = _accepted_values(text)
    if prijima is None:
        return  # pravdivostná kontrola prijme čokoľvek neprázdne; jej vlastný nález drží stráž nižšie

    assert posiela in prijima, (
        f"{slug} prijme príznak náhľadu len ako {prijima}, ale pieskovisko posiela {posiela!r} — "
        "náhľad sa nezapne a Manažér uvidí prihlasovaciu obrazovku"
    )


def test_the_apps_still_on_truthiness_are_a_known_and_shrinking_list() -> None:
    """Západka nad zvyškom hospodárstva (ICCINT-95).

    Nie je to výnimka, ktorá by dieru zakryla — je to jej evidencia. Keď sa appka prepíše na výslovný
    zoznam, táto stráž spadne a zoznam sa musí skrátiť; nedá sa na to ticho zabudnúť.
    """
    truthiness = []
    for slug in APPS_STILL_ON_TRUTHINESS:
        main = FRONTENDS / slug / "frontend" / "src" / "main.tsx"
        if not main.is_file():
            pytest.skip(f"{slug} nie je na tomto stroji — západku nemožno poctivo zmerať")
        if _accepted_values(main.read_text(encoding="utf-8")) is None:
            truthiness.append(slug)

    assert tuple(truthiness) == APPS_STILL_ON_TRUTHINESS, (
        f"appky s pravdivostnou kontrolou sú {tuple(truthiness)}, evidencia hovorí "
        f"{APPS_STILL_ON_TRUTHINESS} — priprav zoznam do súladu (a ak sa skrátil, je to dobrá správa)"
    )
