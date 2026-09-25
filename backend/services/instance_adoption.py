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
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, NamedTuple, Optional

import yaml

from backend.services import remote_instance, uat_provisioner
from backend.services.deploy import RunnerResult, _prod_url, _url_for_instance_slug

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
    #: ICCINT-130 — čo vie LEN táto inštalácia a prevzatie to prenesie do vygenerovaného súboru.
    #: Vety pre človeka, nie štruktúra: Manažér sa rozhoduje podľa toho, čo prečíta.
    carried_over: list[str]
    #: Čo by sa prevzatím STRATILO. Neprázdne = prevzatie sa neponúkne.
    blocking: list[str]
    #: Text, ktorý musí Manažér odpísať, aby sa prevzatie vykonalo. Zámerne ``<zákazník>/<projekt>``,
    #: nie holý názov priečinka: samotné „nex-manager“ je rovnaké pre troch zákazníkov, takže odpísať
    #: sa dá bez pozerania sa na to, ktorého inštalácia to je.
    confirmation_phrase: str
    #: Smie sa prevzatie vôbec ponúknuť. Zásada R16 z NEX Inboxu: akcia sa neponúka tam, kde nemôže
    #: uspieť. Úspešne vyzerajúce prevzatie, po ktorom appka oslepne, je horšie než odmietnutie.
    can_adopt: bool


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


def _running_containers(instance_dir: Path, env: Optional[dict[str, str]] = None) -> list[str]:
    """Kontajnery, ktoré bežia PRÁVE z tohto priečinka (podľa ich vlastného popisu, nie podľa mena).

    ``env`` nesie ``DOCKER_HOST`` cieľového stroja, keď inštalácia býva inde (ICCINT-151). Bez neho
    by sa náhľad pýtal tunajšieho Dockera na kontajnery, ktoré tu nebežia, a odpovedal by „nič“ —
    čo sa na obrazovke číta ako „ten priečinok je opustený“.

    Best-effort: keď sa Docker opýtať nedá, vráti prázdny zoznam — náhľad je vtedy chudobnejší, ale
    nikdy nesmie zlyhať celé prevzatie preto, že sa nepodaril doplnkový údaj.
    """
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
            env=env,
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


class InstanceState(NamedTuple):
    """Čo v priečinku inštalácie je — nezávisle od toho, na ktorom stroji ten priečinok leží.

    **Prečo to nie je len ``Path`` (ICCINT-151).** Ostrá inštalácia MÁGERSTAVU býva na MAGERi,
    kokpit beží na ANDROSe. Náhľad prevzatia čítal priečinok u seba, takže 24.09.2026 ukazoval
    júlovú kópiu, kým na cieli ležal septembrový predpis o 1 900 bajtov dlhší. Priečinok preto do
    náhľadu vstupuje ako PREČÍTANÝ STAV, nie ako cesta, a odkiaľ sa prečítal, rieši jedno miesto.
    """

    #: Priečinok na cieli existuje.
    exists: bool
    #: Mená položiek v ňom (vrátane skrytých).
    entries: list[str]
    #: Text predpisu, alebo ``None``, keď tam žiadny nie je.
    compose_text: Optional[str]
    #: Prečo sa stav nedal prečítať. Neprázdne = nevieme nič, a vtedy sa nepreberá.
    unreadable: Optional[str]
    #: Čo z toho priečinka práve beží.
    running_containers: list[str]


#: Obraz, v ktorom sa cieľ číta. Ten istý, aký na cieli vyrába priečinky — nič nové sa nesťahuje.
_READ_IMAGE = "alpine:3.20"

#: Hranica medzi výpisom priečinka a predpisom v jedinom výstupe. Dva behy by znamenali dve pripojenia
#: a dve príležitosti, aby sa stav medzi nimi zmenil.
_ODDELOVAC = "---8<--- predpis ---8<---"


def _remote_state_cmd(instance_dir: Path) -> list[str]:
    """Príkaz, ktorý na CIELI prečíta obsah priečinka aj predpis naraz.

    ⚠️ ``--mount … readonly``, nikdy ``-v``: krátky zápis pri neexistujúcej ceste priečinok na cieli
    **vyrobí**. Náhľad, ktorý po sebe na cudzom stroji nechá priečinok, už nie je náhľad. Zmerané
    24.09.2026 proti MAGERu — ``--mount`` namiesto toho zlyhá a na cieli nevznikne nič.
    """
    return [
        "docker",
        "run",
        "--rm",
        "--mount",
        f"type=bind,source={instance_dir},target=/target,readonly",
        _READ_IMAGE,
        "sh",
        "-c",
        f"ls -1A /target; echo '{_ODDELOVAC}'; cat /target/docker-compose.yml 2>/dev/null",
    ]


def _parse_remote_state(rc: int, out: str) -> InstanceState:
    """Výstup :func:`_remote_state_cmd` → :class:`InstanceState`.

    Chýbajúci priečinok a nedostupný cieľ vyzerajú oba ako nenulový koniec, ale znamenajú opak:
    prvé je „niet čo preberať“, druhé „o cieli nevieme nič“. Zliať ich by znamenalo ponúknuť
    prevzatie práve vtedy, keď sa na cieľ nedá pozrieť.
    """
    if rc != 0:
        if "bind source path does not exist" in out:
            return InstanceState(False, [], None, None, [])
        return InstanceState(True, [], None, f"na cieľový stroj sa nedalo pozrieť: {out.strip()[:200]}", [])
    zoznam, oddelovac, predpis = out.partition(_ODDELOVAC)
    if not oddelovac:
        return InstanceState(True, [], None, "výpis z cieľa sa nedá rozobrať — prevzatie zastavené", [])
    entries = sorted(r.strip() for r in zoznam.splitlines() if r.strip())
    text = predpis.lstrip("\n")
    return InstanceState(True, entries, text or None, None, [])


def _read_local_state(instance_dir: Path) -> InstanceState:
    """Stav priečinka na tomto stroji."""
    if not instance_dir.is_dir():
        return InstanceState(False, [], None, None, [])
    try:
        entries = sorted(p.name for p in instance_dir.iterdir())
    except OSError as exc:
        return InstanceState(True, [], None, f"priečinok sa nedá prečítať: {exc}", [])
    compose = instance_dir / "docker-compose.yml"
    text: Optional[str] = None
    if compose.is_file():
        try:
            text = compose.read_text(encoding="utf-8")
        except OSError as exc:
            return InstanceState(True, entries, None, f"predpis sa nedá prečítať: {exc}", [])
    return InstanceState(True, entries, text, None, _running_containers(instance_dir))


def read_instance_state(instance_dir: Path, *, deploy_host: Optional[str] = None) -> InstanceState:
    """Stav inštalácie — z tohto stroja, alebo z toho, na ktorom naozaj beží (ICCINT-151)."""
    ciel = (deploy_host or "").strip()
    if not ciel:
        return _read_local_state(instance_dir)

    # Neskorý import zámerne: ``orchestrator`` si ťahá celý nasadzovací modul a tento sa načítava
    # z neho — na úrovni súboru by z toho bol kruh.
    from backend.services.orchestrator import _docker_env_for_target

    env = _docker_env_for_target(dict(os.environ), ciel)
    try:
        vysledok = subprocess.run(  # noqa: S603 — pevné argumenty, žiadny vstup od používateľa
            _remote_state_cmd(instance_dir),
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
            env=env,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return InstanceState(True, [], None, f"na cieľový stroj sa nedalo pozrieť: {exc}", [])
    stav = _parse_remote_state(vysledok.returncode, vysledok.stdout + vysledok.stderr)
    if not stav.exists or stav.unreadable:
        return stav
    return stav._replace(running_containers=_running_containers(instance_dir, env))


class RenderRequest(NamedTuple):
    """Čím sa vykreslí predpis, ktorý by prevzatie do inštalácie zapísalo.

    Presne tie údaje, s akými :func:`adopting_deploy_runner` volá generátor — aby náhľad
    porovnával so skutočným zápisom, nie s domnienkou o ňom.
    """

    project_path: Path
    slug: str
    project: str
    environment: str
    customer_slug: str
    app: str
    #: Verzia, ktorú by nasadenie zapísalo. Náhľad ju potrebuje, lebo do stojacej inštalácie sa
    #: nasadzuje VERZIA — a predpoveď sa musí robiť tým istým kódom ako zápis.
    version: str = "v0.0.0-dev"
    #: Nastavenia inštalácie ako text (z cieľa). Rozhodujú o tom, ktoré premenné by sa dopĺňali.
    env_text: Optional[str] = None


def _labely(sluzba: dict[str, Any]) -> set[str]:
    """Labely služby ako množina ``kľúč=hodnota`` — v compose sa píšu dvomi tvarmi."""
    raw = sluzba.get("labels") or []
    if isinstance(raw, dict):
        return {f"{k}={v}" for k, v in raw.items()}
    if isinstance(raw, list):
        return {str(x) for x in raw}
    return set()


def _mnozina(sluzba: dict[str, Any], kluc: str) -> set[str]:
    """Zoznamová alebo slovníková vlastnosť služby ako množina mien."""
    raw = sluzba.get(kluc) or []
    if isinstance(raw, dict):
        return {str(k) for k in raw}
    if isinstance(raw, list):
        return {str(x) for x in raw}
    return set()


def _co_by_zmizlo(existing_text: Optional[str], rendered_text: str) -> list[str]:
    """Čo je v terajšom predpise a v tom vykreslenom by už nebolo — vetami pre človeka.

    **Prečo to nahrádza kontrolu podľa názvov (ICCINT-151).** Dovtedajšia kontrola porovnávala mená
    vlastností oproti zoznamu „toto vieme vykresliť“. Na tom zozname je ``labels`` — a tým bola pre
    ňu otázka vybavená, hoci tie KONKRÉTNE labely (smerovanie MÁGERSTAVU cez Tailscale, 25 riadkov)
    generátor zapísať nevie ani jeden. Odpovedala teda „nič sa nestratí“ na inštaláciu, ktorá by
    prišla o celú svoju adresu. Jediná odpoveď, ktorá nemôže klamať, je porovnanie s tým, čo by sa
    naozaj zapísalo.

    Hlási sa len to, čo **ubudne**. Zmena verzie obrazu je zmysel nasadenia, nie strata.
    """
    if not existing_text:
        return []
    try:
        teraz = yaml.safe_load(existing_text) or {}
        potom = yaml.safe_load(rendered_text) or {}
    except yaml.YAMLError as exc:
        return [f"predpis inštalácie sa nedá prečítať ({exc.__class__.__name__}) — čo by sa stratilo, nevieme"]
    if not isinstance(teraz, dict) or not isinstance(potom, dict):
        return ["predpis inštalácie nie je platný compose — čo by sa stratilo, nevieme"]

    straty: list[str] = []
    if teraz.get("name") and teraz.get("name") != potom.get("name"):
        straty.append(f"celá zostava by sa premenovala: „{teraz['name']}“ → „{potom.get('name')}“")

    sluzby_teraz = teraz.get("services") or {}
    sluzby_potom = potom.get("services") or {}
    if isinstance(sluzby_teraz, dict) and isinstance(sluzby_potom, dict):
        for meno in sorted(set(sluzby_teraz) - set(sluzby_potom)):
            straty.append(f"služba {meno} by zmizla")
        for meno in sorted(set(sluzby_teraz) & set(sluzby_potom)):
            a = sluzby_teraz[meno] if isinstance(sluzby_teraz[meno], dict) else {}
            b = sluzby_potom[meno] if isinstance(sluzby_potom[meno], dict) else {}
            stratene = sorted(_labely(a) - _labely(b))
            if stratene:
                ukazka = "; ".join(stratene[:3])
                straty.append(f"služba {meno} by prišla o {len(stratene)} riadkov smerovania, napríklad: {ukazka}")
            odpojene = sorted(_mnozina(a, "networks") - _mnozina(b, "networks"))
            if odpojene:
                straty.append(f"služba {meno} by sa odpojila zo siete: {', '.join(odpojene)}")
            zatvorene = sorted(_mnozina(a, "ports") - _mnozina(b, "ports"))
            if zatvorene:
                straty.append(f"služba {meno} by prestala počúvať na: {', '.join(zatvorene)}")
            if a.get("container_name") and a.get("container_name") != b.get("container_name"):
                straty.append(
                    f"služba {meno} by bežala pod iným menom kontajnera: "
                    f"„{a['container_name']}“ → „{b.get('container_name')}“"
                )

    for kluc, veta in (("networks", "sieť {} by zmizla"), ("volumes", "úložisko {} by zmizlo")):
        m_teraz, m_potom = teraz.get(kluc) or {}, potom.get(kluc) or {}
        if isinstance(m_teraz, dict) and isinstance(m_potom, dict):
            straty.extend(veta.format(meno) for meno in sorted(set(m_teraz) - set(m_potom)))
    return straty


def _vykresli(render: RenderRequest, existing_compose_text: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """Predpis, ktorý by prevzatie zapísalo → ``(text, dôvod prečo nie)``. Práve jedno je ``None``.

    Akékoľvek zlyhanie vykreslenia je dôvod prevzatie ODMIETNUŤ: keď nevieme, čo by sme zapísali,
    nevieme ani, čo by sme prepísali.
    """
    try:
        if existing_compose_text:
            # ⚠️ Inštalácia, ktorá už stojí, sa NEPREKRESĽUJE — nasadzuje sa do nej verzia (ICCINT-133).
            # Náhľad preto musí predpovedať to isté, inak hlási stratu služieb a úložísk, ktoré nikam
            # nezmiznú, a prevzatie sa zamkne samo. Presne to sa 25.09.2026 stalo NEX Inboxu.
            compose = uat_provisioner.version_bump_from_text(
                existing_compose_text,
                version=render.version,
                project_slug=render.project,
                source=uat_provisioner.load_source_compose(render.project_path),
                project_path=render.project_path,
                env_text=render.env_text,
            )
        else:
            compose = uat_provisioner.build_compose_for_instance(
                project_path=render.project_path,
                slug=render.slug,
                project=render.project,
                environment=render.environment,
                customer_slug=render.customer_slug,
                app=render.app,
                existing_compose_text=existing_compose_text,
            )
        return uat_provisioner.render_uat_compose(compose), None
    except Exception as exc:  # noqa: BLE001 — pozri docstring: nevykreslené = neprevzaté
        return None, f"predpis, ktorý by sme zapísali, sa nedá vykresliť ({exc.__class__.__name__}: {exc})"


def _facts_in_words(fakty: uat_provisioner.InstanceFacts) -> list[str]:
    """Zákaznícke údaje ako VETY. ICCINT-130.

    Náhľad dovtedy hovoril, ktoré SÚBORY sa odložia bokom — a to nie je údaj, na základe ktorého sa
    dá rozhodnúť. Manažér potrebuje vedieť, čo v novom súbore nebude, a potrebuje to v reči, ktorej
    rozumie bez toho, aby otváral compose.
    """
    vety: list[str] = []
    for sluzba, pripojenia in sorted(fakty.host_mounts.items()):
        for p in pripojenia:
            zdroj = p.split(":")[0]
            vety.append(f"pripojenie priečinka {zdroj} (služba {sluzba}) — {p}")
    for h in fakty.extra_hosts:
        vety.append(f"pevne určený hostiteľ {h}")
    for siet, podsiet in sorted(fakty.network_subnets.items()):
        vety.append(f"ručne pridelená podsieť {podsiet} pre sieť {siet}")
    return vety


def _facts_that_cannot_survive(fakty: uat_provisioner.InstanceFacts) -> list[str]:
    """Čo by sa prevzatím STRATILO — vlastnosti, ktoré provisioner nevie vykresliť.

    Tá istá zásada, ktorá práve prešla do NEX Inboxu ako R16: akcia sa neponúka tam, kde nemôže
    uspieť. Prevzatie, ktoré ohlási úspech a pritom appku oberie o vlastnosť, je horšie než
    odmietnutie — pri odmietnutí sa aspoň vie, že sa treba pozrieť.
    """
    return [
        f"služba {sluzba}: {', '.join(kluce)} — provisioner tieto vlastnosti nevykresľuje"
        for sluzba, kluce in sorted(fakty.unreproducible.items())
    ]


def preview(instance_dir: Path, *, render: RenderRequest, deploy_host: Optional[str] = None) -> AdoptionPreview:
    """Čo by prevzatie urobilo — bez toho, aby sa čokoľvek zmenilo.

    ``render`` je povinný zámerne: bez neho sa nedá zistiť, čo by sa zapísalo, a náhľad bez tejto
    odpovede už raz povedal „nič sa nestratí“ o inštalácii, ktorá by prišla o celé svoje smerovanie.
    ``deploy_host`` hovorí, na ktorom stroji inštalácia býva (prázdne = na tomto).
    """
    stav = read_instance_state(instance_dir, deploy_host=deploy_host)
    if not stav.exists:
        return AdoptionPreview(
            instance_dir=str(instance_dir),
            exists=False,
            already_ours=False,
            set_aside=[],
            untouched=[],
            running_containers=[],
            carried_over=[],
            blocking=[],
            confirmation_phrase=_fraza(instance_dir),
            can_adopt=False,
        )

    already_ours = uat_provisioner.is_provisioner_generated_text(stav.compose_text)

    odlozi, nedotkne = [], []
    for polozka in stav.entries:
        if polozka in HAND_AUTHORED_FILES:
            odlozi.append((polozka, polozka + SET_ASIDE_SUFFIX))
        elif not polozka.endswith(SET_ASIDE_SUFFIX):
            nedotkne.append(polozka)

    fakty = uat_provisioner.facts_from_compose_text(stav.compose_text)
    blokujuce = _facts_that_cannot_survive(fakty)
    if stav.unreadable:
        # Nevieme, čo na cieli je — a práve vtedy sa prepisovať nesmie.
        blokujuce.append(stav.unreadable)
    elif not already_ours:
        vykreslene, chyba = _vykresli(render, stav.compose_text)
        if chyba:
            blokujuce.append(chyba)
        else:
            blokujuce.extend(_co_by_zmizlo(stav.compose_text, vykreslene))

    return AdoptionPreview(
        instance_dir=str(instance_dir),
        exists=True,
        already_ours=already_ours,
        set_aside=[] if already_ours else odlozi,
        untouched=nedotkne,
        running_containers=stav.running_containers,
        carried_over=_facts_in_words(fakty),
        blocking=blokujuce,
        confirmation_phrase=_fraza(instance_dir),
        # Už-naša inštalácia sa nepreberá (niet čo), a inštalácia s nepreneseľnou vlastnosťou sa
        # preberať NESMIE. Obe „nie" sú tu naraz zámerne: tlačidlo sa riadi jedným údajom, nie
        # dvomi podmienkami roztrúsenými po rozhraní.
        can_adopt=not already_ours and not blokujuce,
    )


def set_aside_hand_authored(instance_dir: Path, *, deploy_host: Optional[str] = None) -> list[str]:
    """Odlož ručnú prácu bokom, kým ju prepíšeme (ICCINT-102).

    ⚠️ **Kopíruje, nepresúva.** Pôvodný súbor musí na mieste zostať až do chvíle, keď ho provisioner
    naozaj prepíše: keby sme ho odsunuli a render potom zlyhal, priečinok by ostal prázdny a bežiaca
    inštalácia bez svojho popisu. Kópia je lacná a nikoho neohrozí.

    ⚠️ **Existujúcu zálohu neprepisuje.** Druhé prevzatie by inak uložilo vedľa NAŠU vlastnú kópiu a
    originál by zmizol — presne tá chyba, ktorú pri chartách zavrel ICCINT-87.

    ⚠️ **Záloha patrí tam, kde býva ORIGINÁL** (ICCINT-151, 25.09.2026). Dovtedy sa odkladalo vždy na
    stroji kokpitu, aj keď inštalácia bežala inde — pri prevzatí MÁGERSTAVu to zlyhalo dvakrát naraz:
    na cieli nevznikla žiadna záloha a tu vznikla záloha JÚLOVEJ kópie, teda súboru, ktorý už týždne
    nebol tým živým. Poistka nesplnila ani jedno: nezachránila originál a uložila nesprávny súbor.

    Vracia mená vytvorených záloh (prázdny zoznam = nebolo čo odkladať).
    """
    if (deploy_host or "").strip():
        return _set_aside_on_target(instance_dir, deploy_host.strip())
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


def _set_aside_on_target(instance_dir: Path, deploy_host: str) -> list[str]:
    """To isté, ale na stroji, kde inštalácia naozaj beží (ICCINT-151).

    ⚠️ Nečitateľný cieľ zastaví prevzatie. Odkladanie je poistka, ktorá robí krok vratným; keby sa
    pri nedostupnom cieli len ticho preskočila, prevzatie by pokračovalo BEZ nej — a človek by sa to
    dozvedel až vtedy, keď by sa chcel vracať.
    """
    vytvorene: list[str] = []
    for meno in HAND_AUTHORED_FILES:
        obsah, chyba = remote_instance.read_text(instance_dir, meno, deploy_host=deploy_host)
        if chyba:
            raise OSError(f"odkladanie ručných súborov zlyhalo: {chyba}")
        if obsah is None:
            continue
        zaloha = meno + SET_ASIDE_SUFFIX
        existujuca, chyba = remote_instance.read_text(instance_dir, zaloha, deploy_host=deploy_host)
        if chyba:
            raise OSError(f"odkladanie ručných súborov zlyhalo: {chyba}")
        if existujuca is not None:
            continue
        prava = 0o600 if meno == ".env" else 0o664
        chyba = remote_instance.write_files(instance_dir, {zaloha: (obsah, prava)}, deploy_host=deploy_host)
        if chyba:
            raise OSError(f"odkladanie ručných súborov zlyhalo: {chyba}")
        vytvorene.append(zaloha)
    return vytvorene


async def adopting_deploy_runner(
    *,
    project_slug: str,
    uat_slug: str,
    version_number: str,
    force_fresh: bool,
    admin_password: Optional[str] = None,
    deploy_host: Optional[str] = None,
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
    odlozene = await asyncio.to_thread(
        set_aside_hand_authored, instance_dir, deploy_host=deploy_host if is_prod else None
    )

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
            deploy_host=deploy_host if is_prod else None,
        )

    try:
        result = await asyncio.to_thread(_provision)
    except (FileNotFoundError, ValueError) as exc:
        return RunnerResult(False, f"provision failed: {exc}", None)

    if is_prod:
        ok, detail = await orchestrator._run_prod_deploy(
            project_slug, customer_slug, app, project_slug, version_number=version_number, deploy_host=deploy_host
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
        # ⚠️ ``ProvisionResult`` žiadne ``url`` nemá — skladá sa z názvu inštalácie, presne ako
        # v ``deploy._default_deploy_runner``. Prvé znenie tu malo ``result.url`` a padlo to až
        # v ostrej prevádzke, po tom, čo sa priečinok už prevzal: 500 na obrazovke, nič v evidencii,
        # ale na disku hotovo. Preto sa URL berie z toho istého pomocníka ako pri bežnom nasadení.
        url = _url_for_instance_slug(f"{customer_slug}-{app}") if result.fe_service else None

    warnings = list(result.warnings)
    if odlozene:
        detail = f"{detail} Ručné súbory odložené: {', '.join(odlozene)}."
    if warnings:
        detail = f"{detail} | warnings: {'; '.join(warnings)}"
    return RunnerResult(ok, detail, url, warnings)
