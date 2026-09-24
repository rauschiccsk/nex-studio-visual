"""Priečinok inštalácie na CUDZOM stroji — čítanie a zápis cez Docker (ICCINT-151).

**Prečo to existuje.** Ostrá prevádzka zákazníka môže bežať na jeho vlastnom serveri. Kokpit tam
nemá shell — jeho kľúč je obmedzený výhradne na Docker (``command="docker system dial-stdio"``),
a to je zámer: nasadzovanie nemá byť diera, cez ktorú sa dá na cudzí stroj čokoľvek spustiť. Súbory
sa preto čítajú aj zapisujú tak, že sa priečinok pripojí do krátko žijúceho kontajnera.

**Prečo sa predpis musí zapísať aj na cieľ.** Kokpit ho dovtedy vykresľoval len u seba a poistka
proti rozchodu (v4.40.6) potom odmietala nasadenie navždy — s hláškou *„najprv jeho stav prevezmi"*,
hoci prevzatie práve prebehlo. Zmerané 24.09.2026 na MÁGERSTAVE. Súbor na cieli je navyše to, čo
vidí každý, kto sa na ten server pozrie; keby starol, budúci ručný zásah by vychádzal z nesprávneho
predpisu — presne ten incident, ktorý tento tiket otvoril.

⚠️ **Obsah súborov ide cez štandardný vstup, nikdy cez argumenty príkazu.** V ``.env`` sú tajomstvá
a argumenty procesu vidí na cieľovom stroji ktokoľvek (``ps``). Preto sa posiela tar na stdin.
"""

from __future__ import annotations

import io
import os
import subprocess
import tarfile
from collections.abc import Mapping
from pathlib import Path
from typing import Optional

#: Obraz, v ktorom sa na cieli čítajú a zapisujú súbory. Malý a všade dostupný; nič z neho nebeží ďalej.
IMAGE = "alpine:3.20"

#: Vlastník súborov na cieli — ten istý, aký dostávajú priečinky pre dáta.
UID = GID = 1000


def docker_env(deploy_host: Optional[str]) -> Optional[dict[str, str]]:
    """Prostredie, v ktorom Docker hovorí s cieľovým strojom. ``None`` = tento stroj."""
    ciel = (deploy_host or "").strip()
    if not ciel:
        return None
    env = dict(os.environ)
    env["DOCKER_HOST"] = f"ssh://{ciel}"
    return env


def _read_cmd(instance_dir: Path, filename: str) -> list[str]:
    """⚠️ ``--mount … readonly``, nie ``-v``: krátky zápis pri neexistujúcej ceste priečinok na cieli
    VYROBÍ, a čítanie po sebe na cudzom stroji nesmie nechať nič."""
    return [
        "docker",
        "run",
        "--rm",
        "--mount",
        f"type=bind,source={instance_dir},target=/target,readonly",
        IMAGE,
        "cat",
        f"/target/{filename}",
    ]


def read_text(
    instance_dir: Path, filename: str, *, deploy_host: str, timeout: int = 120
) -> tuple[Optional[str], Optional[str]]:
    """Súbor z priečinka inštalácie na cieli → ``(text, chyba)``. Práve jedno z nich je ``None``.

    ``(None, None)`` znamená, že tam ten súbor (ani priečinok) nie je — to je bežný stav pred prvým
    nasadením, nie porucha. Nečitateľný cieľ je naopak dôvod zastaviť: nasadzovať práve vtedy, keď
    o cieli nič nevieme, je to najhoršie možné poradie.
    """
    try:
        vysledok = subprocess.run(  # noqa: S603 — pevné argumenty, žiadny vstup od používateľa
            _read_cmd(instance_dir, filename),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=docker_env(deploy_host),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"na cieľový stroj sa nedalo pozrieť: {exc}"
    if vysledok.returncode == 0:
        return vysledok.stdout, None
    vystup = (vysledok.stdout + vysledok.stderr).strip()
    if "No such file" in vystup or "no such file" in vystup or "bind source path does not exist" in vystup:
        return None, None
    return None, f"súbor {filename} sa na cieli nedal prečítať: {vystup[:200]}"


def _tar_payload(files: Mapping[str, tuple[str, int]]) -> bytes:
    """Súbory ako tar — obsah ide na štandardný vstup, práva nesie samotný tar.

    ``files`` je ``{meno: (obsah, práva)}``. Tar namiesto ``echo`` do príkazu zámerne: obsah ``.env``
    je tajomstvo a argumenty procesu vidí na cieli ktokoľvek.
    """
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for meno, (obsah, prava) in files.items():
            data = obsah.encode("utf-8")
            info = tarfile.TarInfo(meno)
            info.size = len(data)
            info.mode = prava
            info.uid, info.gid = UID, GID
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _write_cmd(instance_dir: Path) -> list[str]:
    """Príkaz, ktorý na cieli rozbalí tar zo štandardného vstupu do priečinka inštalácie.

    Tu je ``-v`` správne: priečinok pri prvom nasadení ešte neexistuje a zápis ho vyrobiť MÁ.
    Vlastníka priečinka doženieme hneď — inak by patril rootovi a kontajnery appky by doň nevideli.
    """
    return [
        "docker",
        "run",
        "--rm",
        "-i",
        "-v",
        f"{instance_dir}:/target",
        IMAGE,
        "sh",
        "-c",
        f"mkdir -p /target && chown {UID}:{GID} /target && cd /target && tar x",
    ]


def write_files(
    instance_dir: Path, files: Mapping[str, tuple[str, int]], *, deploy_host: str, timeout: int = 180
) -> Optional[str]:
    """Zapíš súbory do priečinka inštalácie na cieli. Vráti dôvod zlyhania, alebo ``None``.

    ⚠️ Volajúci nesmie výsledok ignorovať: keď zápis neprejde a nasadenie pokračuje, na cieli zostane
    starý predpis a poistka proti rozchodu ho zastaví až o krok neskôr — s hláškou o rozchode, ktorý
    v skutočnosti spôsobilo toto zlyhanie.
    """
    if not files:
        return None
    try:
        vysledok = subprocess.run(  # noqa: S603 — pevné argumenty; obsah ide cez stdin, nie cez argv
            _write_cmd(instance_dir),
            input=_tar_payload(files),
            capture_output=True,
            timeout=timeout,
            check=False,
            env=docker_env(deploy_host),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return f"na cieľový stroj sa nedalo zapísať: {exc}"
    if vysledok.returncode != 0:
        vystup = (vysledok.stdout + vysledok.stderr).decode("utf-8", "replace").strip()
        return f"zápis na cieľový stroj zlyhal: {vystup[:200]}"
    return None
