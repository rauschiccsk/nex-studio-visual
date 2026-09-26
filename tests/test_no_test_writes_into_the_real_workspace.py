"""Žiadna skúška nesmie zapisovať do skutočného `/opt/projects` (ICCINT-157).

**Čo tomu predchádzalo.** Skúšky kokpitu zakladajú projekty — musia, lebo zakladanie projektov je to,
čo overujú. Zapisovali ich ale do priečinka so SKUTOČNÝMI projektmi. 10.07.2026 tam bolo **1690**
takých priečinkov a Director to zastavil; vtedy vznikla poistka, ktorá zakladanie presmeruje do
dočasného priečinka.

**Poistka kryla dve cesty z troch.** Prestavovala adresu v dvoch moduloch — lenže vlastnú kópiu
`Path("/opt/projects")` má **šesť** modulov. 26.09.2026 mi pri behu skúšok pribudlo trinásť
priečinkov `p-<hex>`; zapisoval ich `version.write_zadanie` cez svoju kópiu `_PROJECTS_ROOT`, o ktorej
poistka nevedela. Popri nich sa od júla nazbieralo 54 priečinkov `metrics-phase-<hex>` — tie unikali
druhou dierou: poistka bola v `tests/conftest.py`, kým skúška, ktorá ich vyrába, býva
v `backend/tests/`, a ten jej conftest nevidí.

⚠️ **Prečo stráž a nie zoznam.** Presnejší zoznam modulov je zas len zoznam — rozíde sa pri siedmom
module, ktorý si adresu skopíruje. Táto stráž sa nepýta, či je zoznam úplný; pýta sa, či **niektorý
modul ešte ukazuje na skutočný priečinok**. To chytí aj to, na čo sa zabudlo.
"""

from __future__ import annotations

import importlib
import pkgutil
from pathlib import Path

#: Skutočný pracovný priečinok. Do neho počas skúšok nesmie ukazovať NIČ.
SKUTOCNY = Path("/opt/projects")


def _moduly_sluzieb() -> list:
    """Všetky moduly `backend.services` — načítané, nie vymenované."""
    import backend.services as balik

    nacitane = []
    for info in pkgutil.iter_modules(balik.__path__):
        try:
            nacitane.append(importlib.import_module(f"backend.services.{info.name}"))
        except Exception:  # noqa: BLE001 — modul, ktorý sa nedá načítať, nemá ako zapisovať
            continue
    return nacitane


def _kopie_skutocnej_cesty() -> list[str]:
    """Kde všade ešte počas behu skúšok leží `Path("/opt/projects")`."""
    najdene = []
    for modul in _moduly_sluzieb():
        for meno in dir(modul):
            if "PROJECTS_ROOT" not in meno.upper():
                continue
            hodnota = getattr(modul, meno, None)
            if isinstance(hodnota, Path) and hodnota == SKUTOCNY:
                najdene.append(f"{modul.__name__}.{meno}")
    return sorted(najdene)


def test_no_module_still_points_at_the_real_workspace():
    """⚠️ Jadro tiketu. Kým hociktorý modul ukazuje na `/opt/projects`, skúška doň môže zapísať —
    a nikto sa to nedozvie, kým si niekto nevšimne priečinky, ktoré tam nepatria.

    Toto je stráž, nie zoznam: nekontroluje, či je izolácia vymenovaná úplne, ale či po nej niečo
    zostalo. Preto chytí aj modul, ktorý pribudne zajtra.
    """
    zostali = _kopie_skutocnej_cesty()

    assert zostali == [], (
        "Tieto moduly počas skúšok stále ukazujú na skutočný /opt/projects, takže doň môžu zapísať: "
        + ", ".join(zostali)
    )


def test_the_isolation_actually_moved_the_path_somewhere_else():
    """Opačný smer — aby stráž vyššie nemohla prejsť tým, že adresa zmizne úplne.

    Bez tohto by prešla aj vtedy, keby izolácia modulom adresu vymazala (`None`), čo by bolo horšie:
    zápis by padol na nezrozumiteľnej chybe namiesto toho, aby šiel do dočasného priečinka.
    """
    import backend.services.claude_agent as ca
    import backend.services.version as v

    for meno, hodnota in (
        ("claude_agent.PROJECTS_ROOT", ca.PROJECTS_ROOT),
        ("version._PROJECTS_ROOT", v._PROJECTS_ROOT),
    ):
        assert isinstance(hodnota, Path), f"{meno} nie je cesta: {hodnota!r}"
        assert hodnota != SKUTOCNY, f"{meno} ukazuje na skutočný priečinok"
        assert hodnota.is_dir(), f"{meno} ukazuje na niečo, čo neexistuje: {hodnota}"
