"""Prevzatie existujúceho projektu — prečítaj, čo je na disku (ICCINT-85).

**Prečo to vzniklo.** Prevzatie sa dovtedy robilo formulárom pre *zakladanie*: manažér musel vedieť
a ručne prepísať porty, adresu repozitára, typ aj spôsob prihlasovania. Keď sa v niečom pomýlil,
evidencia začala tvrdiť niečo iné, než je na disku — a projekt beží podľa disku, nie podľa evidencie.
Pritom to všetko na disku UŽ JE. Formulár sa pýtal na odpovede, ktoré si vie sám prečítať.

Director 09.09.2026: *„zadám len názov projektu — napríklad nex-manager — a všetko ostatné urobí
systém.“*

**Zásada, ktorá tento modul drží pohromade:** čo je na disku, to sa prečíta; čo sa prečítať nedá,
to sa vypíše ako nedopovedané a opýta sa naň; **nič sa nevymýšľa**. Uhádnutý spôsob prihlasovania by
bol horší než prázdne políčko — vyzeral by ako zistený údaj.

**Prečo sa neháda podľa mena služby ani podľa portu samotného.** Zmerané 09.09.2026 na štyroch
skutočných projektoch: nex-manager má služby ``backend`` / ``frontend`` / ``db`` a vnútorné porty
8000 / 80 / 5432; nex-studio má tie isté mená, ale vnútorné porty 9176 / 9177; nex-payables volá
frontend ``web`` a väzbu píše ako ``"127.0.0.1:10220:8000"`` s poznámkou za ňou. Ani meno, ani port
samo osebe teda nestačí — berie sa meno, a keď nepomôže, vnútorný port.
"""

from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger(__name__)

#: Mená služieb, pod ktorými naše projekty vedú tri role. ``web`` je tam kvôli nex-payables.
_SERVICE_ROLES = {
    "backend": "backend",
    "api": "backend",
    "frontend": "frontend",
    "web": "frontend",
    "db": "db",
    "database": "db",
    "postgres": "db",
}

#: Vnútorné porty, podľa ktorých sa rola dá rozoznať, keď meno služby nepomohlo.
_PORT_ROLES = {5432: "db", 8000: "backend", 8080: "backend", 80: "frontend", 443: "frontend"}


def _latest_version_on_disk(root: Path) -> tuple[Optional[str], Optional[str]]:
    """Posledná verzia, ktorú o sebe projekt hovorí — z priečinkov ``docs/specs/versions/vX.Y.Z`` (ICCINT-89).

    **Prečo to treba vedieť.** Zakladanie projektu vyrába novej verzii záznam „0.1.0 — Initial prototype“.
    Pri PREVZATÍ je to výmysel: projekt svoju históriu má. Zmerané 09.09.2026 na NEX Managerovi — appka
    bežala ako 1.0.99 a v ``docs/specs/versions/`` mala ``v0.1.0`` aj ``v1.0.0`` s poznámkami k vydaniu,
    zatiaľ čo kokpit ukazoval jedinú „plánovanú“ 0.1.0 a ponúkal „Pridať verziu 0.2.0“.

    ⚠️ **A nebolo to len nepekné číslo.** Priečinok ``docs/specs/versions/v0.1.0/`` v tom projekte UŽ
    EXISTOVAL a mal vlastný obsah. Keby sa tá vymyslená verzia rozbehla, písalo by sa do cudzích
    dokumentov.

    Berú sa iba priečinky s číslom v tvare ``vX.Y.Z`` a vracia sa najvyššie. Keď sa nenájde nič, vráti sa
    ``None`` — a vtedy sa nič nevymýšľa, len sa to povie.
    """
    versions_dir = root / "docs" / "specs" / "versions"
    if not versions_dir.is_dir():
        return None, None
    found: list[tuple[tuple[int, int, int], str]] = []
    try:
        entries = list(versions_dir.iterdir())
    except OSError:
        return None, None
    for entry in entries:
        if not entry.is_dir():
            continue
        m = re.fullmatch(r"v(\d+)\.(\d+)\.(\d+)", entry.name)
        if m:
            found.append(((int(m.group(1)), int(m.group(2)), int(m.group(3))), entry.name[1:]))
    if not found:
        return None, None
    found.sort()
    latest = found[-1][1]
    return latest, f"posledná verzia {latest} z priečinkov v docs/specs/versions/"


@dataclass
class Discovered:
    """Čo sa o projekte dalo prečítať z disku — a čo nie.

    ``unresolved`` je rovnako dôležité ako zvyšok: je to zoznam otázok, ktoré musí dostať manažér,
    lebo na ne disk odpoveď nemá. ``notes`` hovorí, ODKIAĽ sa čo vzalo, aby sa dalo pred potvrdením
    skontrolovať — prevzatie je zápis do evidencie, ktorý má sedieť s realitou.
    """

    slug: str
    source_path: str
    name: Optional[str] = None
    description: Optional[str] = None
    repo_url: Optional[str] = None
    backend_port: Optional[int] = None
    frontend_port: Optional[int] = None
    db_port: Optional[int] = None
    #: Posledná verzia, ktorú o sebe projekt hovorí (ICCINT-89). ``None``, keď sa nedá zistiť — vtedy sa
    #: nevymýšľa žiadna.
    latest_version: Optional[str] = None
    unresolved: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _name_from_charter(root: Path) -> tuple[Optional[str], Optional[str]]:
    """Názov projektu z prvého nadpisu v ``CLAUDE.md``.

    **Nie z ``package.json``** — zmerané 09.09.2026: nex-manager aj nex-payables tam majú doslova
    ``"name": "frontend"``. Charta je jediné miesto, kde projekt nesie svoje ľudské meno.

    Chvost typu „— Univerzálny CLAUDE.md“ sa odreže: je to názov dokumentu, nie projektu.
    """
    charter = root / "CLAUDE.md"
    try:
        text = charter.read_text(encoding="utf-8")
    except OSError:
        return None, None
    for line in text.splitlines():
        if line.startswith("# "):
            nadpis = line[2:].strip()
            nazov = re.split(r"\s+[—–-]\s+", nadpis, maxsplit=1)[0].strip()
            return (nazov or None), f"názov z prvého nadpisu v CLAUDE.md ({nadpis!r})"
    return None, None


def _owner_repo(url: str) -> Optional[str]:
    """Z adresy gitu vytiahni tvar ``vlastník/repozitár``, aký si kokpit ukladá.

    ⚠️ Toto stálo jedno nasadenie navyše. Prvá verzia prevzatia vracala adresu tak, ako ju vypísal git
    (``https://github.com/rauschiccsk/nex-manager.git``), lenže zakladanie projektu čaká ``owner/repo``
    — tak to má aj v popise poľa a tak to majú uložené všetky tri existujúce projekty. Prevzatie preto
    skončilo na ``Invalid repository format`` a Manažér videl len holé „Unprocessable Entity“.

    Znesie oba tvary, ktoré git v našich projektoch vypisuje — ``https://host/owner/repo.git`` aj
    ``git@host:owner/repo.git``. Tvar, ktorému nerozumie, vráti ``None``: prázdne pole a otázka sú
    lepšie než adresa v tvare, ktorý zakladanie odmietne.
    """
    text = url.strip().removesuffix(".git")
    m = re.search(r"[:/]([^/:]+)/([^/]+)$", text)
    if not m:
        return None
    return f"{m.group(1)}/{m.group(2)}"


def _repo_from_git(root: Path) -> tuple[Optional[str], Optional[str]]:
    """Adresa repozitára z nastavenia gitu. Neexistujúci alebo nenastavený git nie je chyba —
    projekt bez vzdialeného repozitára je legitímny, len sa naň potom treba opýtať."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None, None
    url = (proc.stdout or "").strip()
    if proc.returncode != 0 or not url:
        return None, None
    repo = _owner_repo(url)
    if repo is None:
        return None, None
    return repo, f"repozitár z nastavenia gitu ({url})"


def _host_port(binding: object) -> Optional[tuple[int, int]]:
    """Rozober jednu väzbu portu na (port na stroji, port v kontajneri).

    Znesie všetky tvary, ktoré sa v našich projektoch naozaj vyskytujú: ``"10211:80"``,
    ``"127.0.0.1:10220:8000"`` aj poznámku za nimi. Tvar, ktorému nerozumie, sa preskočí —
    lepšie žiadny údaj než vymyslený.
    """
    if isinstance(binding, dict):  # dlhý zápis: {published: 10211, target: 80}
        try:
            return int(binding["published"]), int(binding["target"])
        except (KeyError, TypeError, ValueError):
            return None
    text = str(binding).split("#", 1)[0].strip().strip('"').strip("'")
    parts = text.split(":")
    if len(parts) < 2:
        return None
    try:
        return int(parts[-2]), int(parts[-1].split("/")[0])
    except ValueError:
        return None


def _ports_from_compose(root: Path) -> tuple[dict[str, int], list[str]]:
    """Porty z ``docker-compose.yml`` podľa mena služby, a keď to nepomôže, podľa vnútorného portu."""
    compose = root / "docker-compose.yml"
    if not compose.is_file():
        return {}, []
    try:
        data = yaml.safe_load(compose.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        logger.warning("compose sa nedá prečítať pre %s: %s", root, exc)
        return {}, []
    services = data.get("services")
    if not isinstance(services, dict):
        return {}, []

    najdene: dict[str, int] = {}
    notes: list[str] = []
    for meno, telo in services.items():
        if not isinstance(telo, dict):
            continue
        for binding in telo.get("ports") or []:
            rozobrate = _host_port(binding)
            if rozobrate is None:
                continue
            na_stroji, v_kontajneri = rozobrate
            rola = _SERVICE_ROLES.get(str(meno).lower()) or _PORT_ROLES.get(v_kontajneri)
            if rola and rola not in najdene:
                najdene[rola] = na_stroji
                notes.append(f"{rola}: port {na_stroji} zo služby „{meno}“ v docker-compose.yml")
    return najdene, notes


def discover(root: Path, slug: str) -> Discovered:
    """Prečítaj z disku všetko, čo sa o projekte prečítať dá."""
    found = Discovered(slug=slug, source_path=str(root))

    nazov, poznamka = _name_from_charter(root)
    if nazov:
        found.name = nazov
        found.notes.append(poznamka or "")
    else:
        found.unresolved.append("názov projektu — v priečinku nie je CLAUDE.md s nadpisom")

    repo, poznamka = _repo_from_git(root)
    if repo:
        found.repo_url = repo
        found.notes.append(poznamka or "")
    else:
        found.unresolved.append("adresa repozitára — git tu nemá nastavený „origin“")

    porty, poznamky = _ports_from_compose(root)
    found.backend_port = porty.get("backend")
    found.frontend_port = porty.get("frontend")
    found.db_port = porty.get("db")
    found.notes.extend(poznamky)
    for rola, popis in (("backend", "backendu"), ("frontend", "frontendu"), ("db", "databázy")):
        if rola not in porty:
            found.unresolved.append(f"port {popis} — v docker-compose.yml sa nenašiel")

    verzia, poznamka = _latest_version_on_disk(root)
    if verzia:
        found.latest_version = verzia
        found.notes.append(poznamka or "")
    else:
        found.unresolved.append("posledná verzia — v docs/specs/versions/ sa nenašla; prvú si založíš sám")

    # Spôsob prihlasovania sa z disku spoľahlivo prečítať NEDÁ a hádať sa nesmie: uhádnutá hodnota
    # by vyzerala ako zistený údaj. Nech ju manažér vidí a potvrdí.
    found.unresolved.append("spôsob prihlasovania — z disku sa nedá zistiť, potvrď ho")
    return found
