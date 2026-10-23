"""Kde nasadenie práve je — aby človek rozoznal prácu od zamrznutia (ICCINT-153).

**Čo tomu predchádzalo.** 25.09.2026 pri prevzatí ostrého NEX Inboxu na MAGERi videl Director
niekoľko minút tú istú obrazovku a jediné slovo na tlačidle: ``Preberám…``. Jeho slová:

    „už niekoľko minút vidím tú istú obrazovku bez zmeny, bez informácie, že niečo sa deje.
     To treba napraviť, lebo mýli ma, neviem či skutočne niečo sa robí, alebo zamrzol systém."

Medzitým sa na server zákazníka prenášal zdrojový kód, stavali sa tri obrazy a reštartovali
kontajnery ostrej prevádzky. Ja som to vedel zistiť — spýtal som sa Dockera na cieli. Z obrazovky sa
to zistiť nedalo, a to je presne tá diera, ktorú má kokpit odstraňovať, nie vytvárať.

⚠️ **Druhá polovica toho problému: kto nevie, či sa niečo deje, klikne znovu.** Pri prevzatí ostrej
inštalácie zákazníka je to to posledné, čo chceme — preto sa tu drží aj to, že nasadenie BEŽÍ.

**Prečo v pamäti a nie v databáze.** Je to okamžitý stav bežiacej práce, nie záznam — po reštarte
kokpitu už žiadne nasadenie nebeží, takže prázdna pamäť je správna odpoveď. Rovnaký predpoklad ako
pri :mod:`backend.services.pipeline_ws`: NEX Studio beží v jednom procese.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from backend.core.test_seam import for_tests

#: Kroky, ktoré nasadenie naozaj robí — v poradí, v akom prebiehajú. Text je pre človeka na obrazovke.
KROK_PRIPRAVA = "pripravujem predpis a nastavenia"
#: DEV-68: the build runs while the installation is untouched; the outage starts only with the next step.
KROK_STAVBA = "prenášam zdrojový kód a staviam obrazy — bežiaca inštalácia sa zatiaľ nemení"
#: DEV-116: the database is backed up after the build, before anything is replaced.
KROK_ZALOHA = "zálohujem databázu — bežiaca inštalácia sa zatiaľ nemení"
KROK_SPUSTENIE = "spúšťam novú verziu — aplikácia je teraz chvíľu nedostupná"
KROK_MIGRACIA = "migrujem databázu"
KROK_OVERENIE = "overujem, že aplikácia odpovedá"
KROK_UPRATOVANIE = "upratujem po sebe"


@dataclass(frozen=True)
class Priebeh:
    """Čo sa práve deje s jednou inštaláciou."""

    krok: str
    #: Kedy sa začalo CELÉ nasadenie — z toho sa počíta, ako dlho už beží.
    zacate: float
    #: Kedy sa začal TENTO krok. Dlho visiaci krok je iná informácia než dlho bežiace nasadenie.
    krok_od: float

    def trva_sekund(self, teraz: Optional[float] = None) -> int:
        return max(0, int((teraz if teraz is not None else time.time()) - self.zacate))

    def krok_trva_sekund(self, teraz: Optional[float] = None) -> int:
        return max(0, int((teraz if teraz is not None else time.time()) - self.krok_od))


_zamok = threading.Lock()
_bezi: dict[str, Priebeh] = {}


def _kluc(instance_dir: Path) -> str:
    """Priečinok inštalácie. Obe strany si ho vedia spočítať rovnako — obrazovka aj nasadenie."""
    return str(instance_dir)


def zacni(instance_dir: Path, krok: str = KROK_PRIPRAVA) -> None:
    """Nasadenie sa začalo. Opakované volanie čas začiatku NEPREPÍŠE — inak by sa „beží 8 minút"
    pri každom kroku vynulovalo a dlhé nasadenie by navonok vyzeralo ako stále čerstvé."""
    teraz = time.time()
    with _zamok:
        predosle = _bezi.get(_kluc(instance_dir))
        _bezi[_kluc(instance_dir)] = Priebeh(krok=krok, zacate=predosle.zacate if predosle else teraz, krok_od=teraz)


def krok(instance_dir: Path, krok: str) -> None:
    """Posuň na ďalší krok. Keď nasadenie nebeží, nezačína ho — hlásiť priebeh práce, ktorá nebeží,
    by bola nepravda."""
    teraz = time.time()
    with _zamok:
        predosle = _bezi.get(_kluc(instance_dir))
        if predosle is None:
            return
        _bezi[_kluc(instance_dir)] = Priebeh(krok=krok, zacate=predosle.zacate, krok_od=teraz)


def skonci(instance_dir: Path) -> None:
    """Nasadenie skončilo — úspechom aj neúspechom. ⚠️ Musí sa zavolať v OBOCH prípadoch: záznam,
    ktorý zostane visieť, by navždy tvrdil, že sa pracuje, a ďalšie nasadenie by sa nedalo spustiť."""
    with _zamok:
        _bezi.pop(_kluc(instance_dir), None)


def stav(instance_dir: Path) -> Optional[Priebeh]:
    """Čo sa s touto inštaláciou deje, alebo ``None``, keď nič."""
    with _zamok:
        return _bezi.get(_kluc(instance_dir))


def bezi(instance_dir: Path) -> bool:
    """Beží pre túto inštaláciu nasadenie? Podľa toho sa druhé spustenie odmietne."""
    return stav(instance_dir) is not None


def kolko_bezi() -> int:
    """Koľko nasadení práve beží — reštart kokpitu by ich prerušil (DEV-193, ``/health/busy``)."""
    with _zamok:
        return len(_bezi)


@for_tests
def _vycisti_vsetko() -> None:
    """Len pre skúšky — pamäť je procesová a medzi skúškami by sa prenášala."""
    with _zamok:
        _bezi.clear()
