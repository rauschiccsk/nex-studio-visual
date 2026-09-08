"""Prácu, ktorá čaká, presuň z hlavnej slučky preč (ICCINT-74).

**Čo sa stalo.** 07.09.2026 o 19:33 zablokoval jeden ťah agenta celý kokpit. Server prestal odpovedať
na čokoľvek — Manažér nenačítal v NEX Studiu vôbec nič. Žiadna chyba, žiadna výnimka, posledný zápis
v protokole z času tesne pred zaseknutím. Jedinou stopou bola hromada zaseknutých kontrol zdravia
vnútri kontajnera: 16 po dvadsiatich minútach, 65 po štyridsiatich. Obnovilo to až reštartovanie.

**Prečo.** ``_run_vizual_round`` je ``async``, ale ``vizual_sandbox.spin_up`` je obyčajná funkcia,
ktorá spúšťa ``npm install`` (strop 600 s) a dva dockerové príkazy (po 60 s). Zavolaná priamo z ``async``
funkcie beží **na hlavnej slučke**, takže celý ten čas server nestíha odbaviť ani ``/health``. Nie je to
zamrznutie v zmysle chyby — je to normálna práca na nesprávnom vlákne.

**Prečo je to zákernejšie než pád.** Ťah, ktorý spadne nahlas, sa dá vyšetriť. Ťah, ktorý sa zahryzne
a stiahne so sebou celý kokpit, vyzerá ako výpadok siete: Manažér nemá čo nahlásiť a nemá kde hľadať.

Pravidlo, ktoré z toho platí: **z ``async`` funkcie sa nikdy nevolá priamo nič, čo čaká na proces, sieť
alebo disk.** Ide to sem — do vlákna, so stropom. Stráž
``backend/tests/test_the_cockpit_cannot_be_suffocated_by_one_turn.py`` to vynucuje strojovo, lebo na
disciplínu volajúceho sa spoľahnúť nedá: ten istý omyl sa pri ďalšej funkcii spraví znova a prejaví sa
až o mesiac ako „NEX Studio nejde“.
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable, TypeVar

T = TypeVar("T")


class BlockingWorkTimedOut(Exception):
    """Práca vo vlákne neskončila do stropu.

    Vlákno sa zabiť nedá — beží ďalej, kým sám proces neskončí. Podstatné je, že **hlavná slučka je
    voľná** a volajúci sa dozvie, že sa nedočkal, takže sa dá usadiť stav a povedať to Manažérovi.
    Bez stropu by ťah čakal navždy a v kokpite by naďalej svietilo „pracuje sa“.
    """


async def run_blocking(fn: Callable[..., T], /, *args: Any, cap: float, **kwargs: Any) -> T:
    """Spusti ``fn`` vo vlákne a čakaj naň najviac ``cap`` sekúnd.

    ``cap`` je **povinný a pomenovaný**: strop, ktorý sa dá zabudnúť, sa zabudne. Voľ ho podľa toho, čo
    tá práca reálne robí, a radšej veľkoryso — jeho úlohou nie je prerušovať poctivú prácu, ale zabrániť
    tomu, aby ťah čakal donekonečna.
    """
    try:
        return await asyncio.wait_for(asyncio.to_thread(fn, *args, **kwargs), timeout=cap)
    except TimeoutError as exc:  # asyncio.TimeoutError je od Pythonu 3.11 TimeoutError
        raise BlockingWorkTimedOut(
            f"{getattr(fn, '__qualname__', fn)} neskončila do {cap:g} s — hlavná slučka je voľná, "
            "ale výsledok tejto práce nemáme"
        ) from exc
