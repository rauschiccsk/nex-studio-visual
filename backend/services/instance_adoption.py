"""Prevzatie ručne písanej inštalácie — z kokpitu, nie z terminálu (ICCINT-102).

**Čo tomu predchádzalo.** Provisioner odmieta zapisovať do priečinka, ktorý sám nevygeneroval
(:func:`uat_provisioner.assert_writable_instance_dir`). Na ANDROSe je trinásť takých inštalácií — okrem
iného ostrá pošta MÁGERSTAVU a spoločný Traefik, cez ktorý smerujú desiatky kontajnerov. Poistka je
správna a zostáva: klik na „Nasadiť“ sa k cudziemu priečinku nesmie dostať nikdy.

**Čo bolo zlé.** Jediná cesta ďalej viedla cez terminál (``uat-deploy.py --adopt``). Director
10.09.2026: *„Pracujeme nad vývojovým ekosystémom, ktorý má zabezpečiť KOMPLETNÝ workflow od návrhu až
po nasadenie na UAT a na PROD. Také terminálové príkazy nie sú pre mňa riešenie.“* Terminál navyše
nezapíše do evidencie kokpitu **nič** — takže dovtedajší „bezpečnejší“ spôsob bol ten, po ktorom
nezostala stopa.

**Prečo sa pôvodná obava dá splniť inak.** Rozhodnutie z 28.07.2026 stálo na tom, že vpísaním hlavičky
sa **zničí jediný dôkaz, že súbor bol písaný ručne**. Tu sa nič nezničí: ručné súbory sa najprv
**odložia** vedľa ako ``.pre-nex-studio`` (tá istá liečba ako pri prevzatí projektu, ICCINT-87), takže
dôkaz zostáva a krok sa dá vrátiť.

**Čo zostáva nedotknuté.** Táto cesta je oddelená od :mod:`backend.services.deploy`; tá o
``allow_overwrite`` naďalej nevie a stráž nad ňou (``test_cockpit_deploy_button_never_passes_the_override``)
platí bez zmeny. Prevzatie je vlastné rozhodnutie s vlastnou obrazovkou, vlastným potvrdením a vlastným
záznamom — nikdy vedľajší účinok kliknutia na „Nasadiť“.

**Nikdy hromadne.** Jedno volanie = jedna inštalácia. Director 28.07.2026 to zamietol po troch
nezávislých previerkach a to platí ďalej.
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import NamedTuple, Optional

from backend.services import uat_provisioner
from backend.services.deploy import RunnerResult, _prod_url

#: Prípona, pod ktorou sa ručná práca odkladá. Rovnaká ako pri prevzatí projektu (ICCINT-87), aby sa
#: „čo je toto za súbor“ nemuselo lúštiť dvakrát.
SET_ASIDE_SUFFIX = ".pre-nex-studio"

#: Súbory, ktoré ručne písané nasadenie nesie a ktoré sa preto odkladajú.
HAND_AUTHORED_FILES = ("docker-compose.yml", ".env")


class AdoptionPreview(NamedTuple):
    """Čo prevzatie urobí — VOPRED, aby sa Manažér rozhodoval z faktov, nie z odhadu."""

    #: Priečinok, o ktorý ide.
    instance_dir: str
    #: Existuje vôbec?
    exists: bool
    #: Je už náš (nesie hlavičku provisionera)? Potom niet čo preberať.
    already_ours: bool
    #: Súbory, ktoré sa odložia bokom (a pod akým menom).
    set_aside: list[tuple[str, str]]
    #: Súbory v priečinku, ktorých sa prevzatie nedotkne.
    untouched: list[str]
    #: Čo z toho priečinka práve beží — najsilnejší dôkaz, že to nie je opustený zvyšok.
    running_containers: list[str]
    #: Text, ktorý musí Manažér odpísať, aby sa prevzatie vykonalo. Zámerne ``<zákazník>/<projekt>``,
    #: nie holý názov priečinka: samotné „nex-manager“ je rovnaké pre troch zákazníkov, takže odpísať
    #: sa dá bez pozerania sa na to, ktorého inštalácia to je.
    confirmation_phrase: str


def instance_dir_for(*, environment: str, customer_slug: str, full_project_slug: str) -> Path:
    """Priečinok inštalácie — počítaný ROVNAKO ako v :func:`uat_provisioner.provision_uat`.

    ⚠️ Keby si tieto dve miesta cestu počítali každé po svojom, náhľad by ukazoval jeden priečinok a
    prevzatie by siahlo na druhý. Preto sa berú tie isté korene z toho istého modulu.
    """
    root = uat_provisioner.PROD_ROOT if environment == "prod" else uat_provisioner.UAT_ROOT
    return root / customer_slug / full_project_slug


def _fraza(instance_dir: Path) -> str:
    """``<zákazník>/<projekt>`` — to, čo Manažér odpíše, aby prevzatie potvrdil."""
    return f"{instance_dir.parent.name}/{instance_dir.name}"


def _running_containers(instance_dir: Path) -> list[str]:
    """Kontajnery, ktoré bežia PRÁVE z tohto priečinka (podľa ich vlastného popisu, nie podľa mena).

    Best-effort: keď sa Docker opýtať nedá, vráti prázdny zoznam — náhľad je vtedy chudobnejší, ale
    nikdy nesmie zlyhať celé prevzatie preto, že sa nepodaril doplnkový údaj.
    """
    import subprocess

    try:
        vysledok = subprocess.run(  # noqa: S603 — pevné argumenty, žiadny vstup od používateľa
            [
                "docker",
                "ps",
                "--format",
                '{{.Names}}\t{{.Label "com.docker.compose.project.working_dir"}}',
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    ciel = str(instance_dir)
    mena = []
    for riadok in vysledok.stdout.splitlines():
        meno, _, adresar = riadok.partition("\t")
        if adresar.strip() == ciel:
            mena.append(meno.strip())
    return sorted(mena)


def preview(instance_dir: Path) -> AdoptionPreview:
    """Čo by prevzatie urobilo — bez toho, aby sa čokoľvek zmenilo."""
    if not instance_dir.is_dir():
        return AdoptionPreview(
            instance_dir=str(instance_dir),
            exists=False,
            already_ours=False,
            set_aside=[],
            untouched=[],
            running_containers=[],
            confirmation_phrase=_fraza(instance_dir),
        )

    compose = instance_dir / "docker-compose.yml"
    already_ours = compose.exists() and uat_provisioner.is_provisioner_generated(compose)

    odlozi, nedotkne = [], []
    for polozka in sorted(p.name for p in instance_dir.iterdir()):
        if polozka in HAND_AUTHORED_FILES:
            odlozi.append((polozka, polozka + SET_ASIDE_SUFFIX))
        elif not polozka.endswith(SET_ASIDE_SUFFIX):
            nedotkne.append(polozka)

    return AdoptionPreview(
        instance_dir=str(instance_dir),
        exists=True,
        already_ours=already_ours,
        set_aside=[] if already_ours else odlozi,
        untouched=nedotkne,
        running_containers=_running_containers(instance_dir),
        confirmation_phrase=_fraza(instance_dir),
    )


def set_aside_hand_authored(instance_dir: Path) -> list[str]:
    """Odlož ručnú prácu bokom, kým ju prepíšeme (ICCINT-102).

    ⚠️ **Kopíruje, nepresúva.** Pôvodný súbor musí na mieste zostať až do chvíle, keď ho provisioner
    naozaj prepíše: keby sme ho odsunuli a render potom zlyhal, priečinok by ostal prázdny a bežiaca
    inštalácia bez svojho popisu. Kópia je lacná a nikoho neohrozí.

    ⚠️ **Existujúcu zálohu neprepisuje.** Druhé prevzatie by inak uložilo vedľa NAŠU vlastnú kópiu a
    originál by zmizol — presne tá chyba, ktorú pri chartách zavrel ICCINT-87.

    Vracia mená vytvorených záloh (prázdny zoznam = nebolo čo odkladať).
    """
    vytvorene = []
    for meno in HAND_AUTHORED_FILES:
        zdroj = instance_dir / meno
        if not zdroj.is_file():
            continue
        zaloha = instance_dir / (meno + SET_ASIDE_SUFFIX)
        if zaloha.exists():
            continue
        shutil.copy2(zdroj, zaloha)
        vytvorene.append(zaloha.name)
    return vytvorene


async def adopting_deploy_runner(
    *,
    project_slug: str,
    uat_slug: str,
    version_number: str,
    force_fresh: bool,
    admin_password: Optional[str] = None,
) -> tuple[bool, str, Optional[str]]:
    """Vykonávateľ nasadenia, ktorý smie prepísať ručne písaný priečinok — a najprv ho odloží.

    Má rovnaký tvar ako ``deploy._default_deploy_runner``, takže sa dá odovzdať do
    ``deploy.deploy(deploy_runner=…)`` a celý zvyšok (brány, evidencia, zápis kto/kedy/čo) zostáva
    nezmenený. Rozdiely sú presne dva: **odloží ručné súbory** a **prejde poistkou**.

    ⚠️ ``allow_overwrite=True`` je tu, nie v :mod:`backend.services.deploy`. Tá o ňom nesmie vedieť —
    inak by sa klik na „Nasadiť“ mohol dostať k živému cudziemu nasadeniu.

    ⚠️ ``rotate_secrets`` zostáva na ``force_fresh`` (predvolene False). Prevzatie musí tajomstvá
    ZACHOVAŤ: databáza v tom priečinku beží so starým heslom a čerstvé tajomstvá by ju odrezali.
    """
    from backend.services import orchestrator

    is_prod = uat_slug.endswith("-prod")
    environment = "prod" if is_prod else "uat"
    customer_slug = uat_slug.removesuffix("-prod") if is_prod else uat_slug.removesuffix("-uat")
    app = uat_provisioner.derive_uat_slug(project_slug)

    instance_dir = instance_dir_for(
        environment=environment, customer_slug=customer_slug, full_project_slug=project_slug
    )
    odlozene = await asyncio.to_thread(set_aside_hand_authored, instance_dir)

    def _provision() -> uat_provisioner.ProvisionResult:
        return uat_provisioner.provision_uat(
            project_slug,
            uat_slug,
            version=version_number,
            rotate_secrets=force_fresh,
            environment=environment,
            customer_slug=customer_slug,
            app=app,
            full_project_slug=project_slug,
            admin_password=admin_password,
            allow_overwrite=True,
        )

    try:
        result = await asyncio.to_thread(_provision)
    except (FileNotFoundError, ValueError) as exc:
        return RunnerResult(False, f"provision failed: {exc}", None)

    if is_prod:
        ok, detail = await orchestrator._run_prod_deploy(
            project_slug, customer_slug, app, project_slug, version_number=version_number
        )
        url = _prod_url(customer_slug, app) if result.fe_service else None
    else:
        ok, detail = await orchestrator._run_uat_deploy(
            project_slug,
            uat_slug,
            environment="uat",
            customer_slug=customer_slug,
            app=app,
            full_project_slug=project_slug,
            version_number=version_number,
        )
        url = result.url

    if odlozene:
        detail = f"{detail} Ručné súbory odložené: {', '.join(odlozene)}."
    return RunnerResult(ok, detail, url, list(result.warnings))
