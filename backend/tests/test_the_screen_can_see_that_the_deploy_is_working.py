"""Obrazovka vidí, že nasadenie beží a v ktorom kroku je (ICCINT-153).

**Čo tomu predchádzalo.** 25.09.2026 pri prevzatí ostrého NEX Inboxu na MAGERi videl Director
niekoľko minút tú istú obrazovku a jediné slovo na tlačidle: ``Preberám…``:

    „už niekoľko minút vidím tú istú obrazovku bez zmeny, bez informácie, že niečo sa deje.
     To treba napraviť, lebo mýli ma, neviem či skutočne niečo sa robí, alebo zamrzol systém."

Medzitým sa na server zákazníka prenášal zdrojový kód, stavali sa tri obrazy a reštartovali
kontajnery. Ja som to vedel zistiť — spýtal som sa Dockera na cieli. Z obrazovky sa to zistiť nedalo.

⚠️ Čo tieto stráže NEDOVOLIA zmeniť: záznam o behu sa musí vyčistiť aj pri ZLYHANÍ (inak by navždy
tvrdil, že sa pracuje, a ďalšie nasadenie by sa nedalo spustiť), druhé spustenie sa musí odmietnuť
(kto nevie, či sa niečo deje, klikne znovu) a čas začiatku sa pri kroku NESMIE vynulovať (inak dlhé
nasadenie navonok vyzerá ako stále čerstvé).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.services import deploy_progress as P

INSTALACIA = Path("/opt/customers/mager/nex-inbox")
INA = Path("/opt/customers/mager/nex-manager")


@pytest.fixture(autouse=True)
def _cisto():
    P._vycisti_vsetko()
    yield
    P._vycisti_vsetko()


# ── 1. čo obrazovka uvidí ─────────────────────────────────────────────────────


def test_a_started_deploy_is_visible_with_its_step() -> None:
    """⚠️ Jadro veci: bez tohto je na obrazovke len slovo na tlačidle."""
    P.zacni(INSTALACIA)

    priebeh = P.stav(INSTALACIA)

    assert priebeh is not None
    assert priebeh.krok == P.KROK_PRIPRAVA
    assert P.bezi(INSTALACIA) is True


def test_the_step_moves_as_the_deploy_moves() -> None:
    """Kroky sú tie, ktoré nasadenie naozaj robí — nie ozdoba."""
    P.zacni(INSTALACIA)
    P.krok(INSTALACIA, P.KROK_STAVBA)
    assert P.stav(INSTALACIA).krok == P.KROK_STAVBA

    P.krok(INSTALACIA, P.KROK_OVERENIE)
    assert P.stav(INSTALACIA).krok == P.KROK_OVERENIE


def test_nothing_is_reported_when_nothing_runs() -> None:
    """Prázdna odpoveď je správna odpoveď — nie výmysel o práci, ktorá nebeží."""
    assert P.stav(INSTALACIA) is None
    assert P.bezi(INSTALACIA) is False


def test_two_installations_do_not_see_each_other() -> None:
    """Zákazník má viac aplikácií; nasadenie jednej nesmie vyzerať ako nasadenie druhej."""
    P.zacni(INSTALACIA)

    assert P.bezi(INSTALACIA) is True
    assert P.bezi(INA) is False


# ── 2. čas ────────────────────────────────────────────────────────────────────


def test_the_elapsed_time_is_not_reset_by_a_step() -> None:
    """⚠️ Inak by sa „beží 8 minút" pri každom kroku vynulovalo a dlhé nasadenie by navonok vyzeralo
    ako stále čerstvé — teda presne ten údaj, kvôli ktorému to celé vzniklo, by klamal."""
    P.zacni(INSTALACIA)
    zaciatok = P.stav(INSTALACIA).zacate

    P.krok(INSTALACIA, P.KROK_STAVBA)

    assert P.stav(INSTALACIA).zacate == zaciatok


def test_the_step_has_its_own_clock() -> None:
    """Dlho visiaci KROK je iná informácia než dlho bežiace nasadenie."""
    P.zacni(INSTALACIA)
    priebeh = P.stav(INSTALACIA)

    assert priebeh.trva_sekund(priebeh.zacate + 300) == 300
    assert priebeh.krok_trva_sekund(priebeh.krok_od + 12) == 12


def test_a_repeated_start_does_not_restart_the_clock() -> None:
    """Opakované ohlásenie začiatku je bežné (viac ciest k nasadeniu) a čas nesmie posunúť."""
    P.zacni(INSTALACIA)
    zaciatok = P.stav(INSTALACIA).zacate

    P.zacni(INSTALACIA, P.KROK_STAVBA)

    assert P.stav(INSTALACIA).zacate == zaciatok
    assert P.stav(INSTALACIA).krok == P.KROK_STAVBA


# ── 3. koniec a druhé kliknutie ───────────────────────────────────────────────


def test_the_record_is_cleared_when_the_deploy_ends() -> None:
    """⚠️ Visiaci záznam by navždy tvrdil, že sa pracuje — a ďalšie nasadenie by sa nedalo spustiť."""
    P.zacni(INSTALACIA)
    P.skonci(INSTALACIA)

    assert P.bezi(INSTALACIA) is False


def test_clearing_an_unknown_installation_is_not_an_error() -> None:
    """Koniec sa volá aj po zlyhaní, ktoré nastalo skôr, než sa čokoľvek začalo."""
    P.skonci(INSTALACIA)  # nesmie spadnúť


def test_a_step_on_a_deploy_that_does_not_run_starts_nothing() -> None:
    """Hlásiť priebeh práce, ktorá nebeží, by bola nepravda."""
    P.krok(INSTALACIA, P.KROK_STAVBA)

    assert P.bezi(INSTALACIA) is False


def test_the_deploy_refuses_a_second_start_while_one_runs() -> None:
    """⚠️ Druhá polovica problému: kto nevie, či sa niečo deje, klikne znovu. Pri prevzatí ostrej
    inštalácie zákazníka je to to posledné, čo chceme. Overuje sa proti SAMOTNÉMU zdroju nasadenia."""
    import inspect

    from backend.services import deploy as D

    zdroj = inspect.getsource(D.deploy)

    assert "deploy_progress.bezi(instalacia)" in zdroj, "druhé spustenie sa neodmieta"
    assert "deploy_progress.zacni(instalacia)" in zdroj
    assert "finally:" in zdroj and "deploy_progress.skonci(instalacia)" in zdroj, (
        "záznam sa nevyčistí pri zlyhaní — zostal by visieť"
    )
