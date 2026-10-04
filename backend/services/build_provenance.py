"""Z ktorej zmeny (commit) sa obraz projektu stavia — jedno miesto pre každú stavbu, ktorú kokpit spúšťa (ICCINT-166).

**Čo chýbalo.** Kokpit stavia obraz projektu na štyroch miestach — skúška spustenia vo Verifikácii, nasadenie
(testovacie aj ostré, miestne aj na cudzí stroj), skúška čerstvo založeného projektu a ručný ``uat-deploy.py`` —
a stavbe odovzdával len verziu (``APP_VERSION``, ``VITE_APP_VERSION``). Číslo zmeny nikde. Projekt, ktorý chce
vedieť, z čoho bol jeho obraz postavený, ho nemal odkiaľ vziať: ``.git`` do kontextu stavby nepatrí. Zmerané
04.10.2026 na dedo-home: pripína ku zmene zoznam zámkov aj dôkaz v obraze (jeho Š§18.4), a jeho skúška spustenia
cez kokpit by sa preto nezostavila nikdy — ani po tom, čo by opravil všetko ostatné.

**Ako.** Číslo zmeny sa neberie z mena projektu ani z priečinka, kde „asi" leží, ale z toho, **čo sa naozaj
stavia**: z kontextov stavby v compose, ktorý ide hore. Git repozitára, v ktorom tie kontexty ležia, povie
``HEAD``. Výsledok ide ako :data:`APP_COMMIT_ENV` do prostredia príkazu, ktorý stavia — vedľa ``APP_VERSION``,
rovnakou cestou.

**Kedy sa nepovie nič.** Keď kontext stavby nie je, keď kontexty ležia v dvoch repozitároch (aj vnorených — tam
git nezlyhá, ale hlási „čisté" za kód z cudzej zmeny) alebo keď git zlyhá (aj na vzdialenom kontexte, ktorý na
disku nie je), premenná sa **nenastaví**. Projekt, ktorý ju potrebuje, potom zlyhá zatvorený vlastnou
hláškou — to je lepšie než číslo, ktoré by kokpit vymyslel.

**Kontext so zmenami, ktoré v zmene nie sú** (upravené aj nesledované súbory; ``.gitignore`` sa rešpektuje),
dostane príponu :data:`DIRTY_SUFFIX`. Obraz nesmie tvrdiť zmenu, ktorú neobsahuje.

**Zmluva pre projekt.** Premenná je k dispozícii **pri stavbe**. Povinná premenná v compose (``${X:?…}``) sa však
vyžaduje pri **každom** príkaze nad ním (``ps``, ``exec``, ``logs``, ``down``), nielen pri stavbe — projekt ju má
preto prevziať ako ``${APP_COMMIT:-}`` a zlyhať zatvorený až pri stavbe obrazu (v Dockerfile).
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger(__name__)

#: Meno premennej, pod ktorou kokpit stavbe obrazu podáva číslo zmeny.
APP_COMMIT_ENV = "APP_COMMIT"

#: Prípona čísla zmeny, keď kontext stavby obsahuje niečo, čo v tej zmene nie je (konvencia ``git describe --dirty``).
DIRTY_SUFFIX = "-dirty"

_GIT_TIMEOUT = 15


def _git(cwd: Path, *args: str) -> Optional[str]:
    """Výstup ``git -C <cwd> <args>``, alebo ``None``, keď git zlyhá. Nikdy nevyhodí výnimku."""
    try:
        done = subprocess.run(
            ["git", "-C", str(cwd), *args],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout if done.returncode == 0 else None


def build_contexts(compose: Path) -> Optional[list[Path]]:
    """Každý kontext stavby v *compose*, rozriešený tak, ako ho rozrieši compose (relatívne k priečinku súboru),
    v poradí služieb. ``None``, keď sa compose nedá prečítať; prázdny zoznam = nič nestavia (len hotové obrazy)."""
    try:
        data = yaml.safe_load(compose.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return None
    services = data.get("services") if isinstance(data, dict) else None
    if not isinstance(services, dict):
        return []
    contexts: list[Path] = []
    for svc in services.values():
        if not isinstance(svc, dict) or "build" not in svc:
            continue
        build = svc["build"]
        if isinstance(build, dict):
            context = str(build.get("context", "."))
        elif isinstance(build, str):
            context = build
        else:
            return None
        path = Path(context)
        contexts.append((path if path.is_absolute() else compose.parent / path).resolve())
    return contexts


def build_commit(compose: Path) -> Optional[str]:
    """Zmena, z ktorej sa obrazy *compose* stavajú; ``None``, keď sa to povedať nedá (pozri hlavičku modulu)."""
    contexts = build_contexts(compose)
    if not contexts:
        return None
    toplevels = [_git(context, "rev-parse", "--show-toplevel") for context in contexts]
    if any(toplevel is None for toplevel in toplevels):
        return None
    if len({toplevel.strip() for toplevel in toplevels}) != 1:
        # Aj vnorený repozitár: ``git status`` vonkajšieho naň nevidí a hlási „čisté" za kód z cudzej zmeny.
        return None
    repo = Path(toplevels[0].strip())
    head = (_git(repo, "rev-parse", "HEAD") or "").strip()
    if not head:
        return None
    changes = _git(repo, "status", "--porcelain", "--", *sorted({str(c) for c in contexts}))
    if changes is None:
        return None
    return head + (DIRTY_SUFFIX if changes.strip() else "")


def build_env(compose: Path) -> dict[str, str]:
    """``{APP_COMMIT: <zmena>}`` pre prostredie príkazu, ktorý stavia obrazy *compose* — alebo ``{}``."""
    commit = build_commit(compose)
    if commit is None:
        logger.warning(
            "build provenance: z %s sa nedá povedať, z ktorej zmeny sa stavia — APP_COMMIT nenastavené", compose
        )
        return {}
    return {APP_COMMIT_ENV: commit}
