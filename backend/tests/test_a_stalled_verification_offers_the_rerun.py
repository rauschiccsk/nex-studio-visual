"""Keď zlyhá overovací beh, kokpit ponúkne jeho zopakovanie (ICCINT-109).

**Čo sa stalo.** Verifikácia NEX Managera 1.2.0 uviazla, lebo Audítor nevrátil verdikt. Kokpit napísal
„Usmerni (Uprav) alebo over znova“ — ale **tlačidlo „over znova“ na obrazovke nebolo**. Manažérovi
zostalo jediné: „Uprav“. To pošle OPRAVNÉHO agenta hľadať chybu, ktorá neexistuje: agent spustil celú
previerku (304 kontrol appky, 412 servera), našiel ju zelenú a musel sa spýtať „čo mám opraviť?“.
Minul sa beh agenta a stavba uviazla o úroveň nižšie.

⚠️ **Engine to vedel celý čas.** ``apply_action`` má pre presne tento prípad vetvu (``_verif_stall``) a
rozhranie má hotový pruh s tlačidlom „Znova spustiť overenie“, ktorý sa ukáže, len keď backend tú akciu
ponúkne. Neponúkal ju nikdy — funkcia bola postavená od začiatku do konca a bola **nedosiahnuteľná**.

To je horší druh chyby než chýbajúca funkcia: všetko je hotové a nikto sa k tomu nedostane.
"""

from __future__ import annotations

import pytest

from backend.services import orchestrator


class _Stav:
    """Najmenší stav, aký ``determine_available_actions`` potrebuje (je state-only)."""

    def __init__(self, stage: str, status: str, block_reason: str | None) -> None:
        self.current_stage = stage
        self.status = status
        self.block_reason = block_reason
        self.flow_type = "new_version"
        self.current_actor = "auditor"
        self.mode = None
        self.is_regate = False
        self.pending_vizual_signoff = False
        self.iteration = 0


@pytest.mark.parametrize("dovod", orchestrator.VERIF_STALL_BLOCK_REASONS)
def test_a_failed_verification_run_can_be_repeated(dovod: str) -> None:
    """⚠️ Jadro nálezu: pri každom spôsobe, akým overovací beh zlyhá, musí byť čo kliknúť."""
    akcie = orchestrator.determine_available_actions(_Stav("verifikacia", "blocked", dovod))

    assert "overit_bez_opravy" in akcie, (
        f"pri {dovod} sa zopakovanie overenia neponúka — Manažérovi zostane len „Uprav“, čo pošle "
        "opravného agenta hľadať chybu, ktorá neexistuje"
    )


def test_an_agent_question_is_not_a_failed_run() -> None:
    """⚠️ Keď sa agent PÝTA, zopakovať beh je nezmysel — treba mu odpovedať.

    Bez tohto tvrdenia by stačilo ponúkať to tlačidlo vždy, keď je niečo zablokované, a Manažér by
    ním prepisoval otázky namiesto toho, aby na ne odpovedal.
    """
    akcie = orchestrator.determine_available_actions(_Stav("verifikacia", "blocked", "agent_question"))

    assert "overit_bez_opravy" not in akcie
    assert "answer" in akcie, "na otázku sa musí dať odpovedať"


def test_a_failure_in_another_phase_does_not_offer_it() -> None:
    """Zopakovať sa dá OVEROVANIE — nie ktorákoľvek fáza. Inak by tlačidlo sľubovalo, čo neurobí."""
    akcie = orchestrator.determine_available_actions(_Stav("programovanie", "blocked", "agent_error"))

    assert "overit_bez_opravy" not in akcie


def test_the_message_names_the_button_that_is_actually_there() -> None:
    """⚠️ Druhá polovica nálezu: hláška radila „over znova“, čo sa na obrazovke nevolá nijako.

    Text, ktorý posiela človeka hľadať neexistujúce tlačidlo, je horší než žiadna rada — Director
    podľa nej klikol jediné, čo videl, a stavba tým uviazla.
    """
    import inspect

    zdroj = inspect.getsource(orchestrator)
    popiska = "Znova spustiť overenie"  # presne to, čo je na tlačidle v ReverifyNoFixBar

    assert "alebo over znova" not in zdroj, "niekde sa stále radí akcia, ktorá sa tak nevolá"
    assert zdroj.count(popiska) >= 2, "hlášky pri zablokovanej Verifikácii nemenujú skutočné tlačidlo"


def test_the_frontend_bar_keys_on_what_the_backend_offers() -> None:
    """Pruh s tlačidlom sa smie ukázať IBA vtedy, keď backend tú akciu naozaj ponúka.

    Inak by sa vrátil ten istý problém opačne: tlačidlo na obrazovke, ktoré engine odmietne.
    """
    import pathlib

    bar = pathlib.Path("frontend/src/components/riadiace/ReverifyNoFixBar.tsx")
    if not bar.is_file():
        pytest.skip("rozhranie nie je v tomto strome — nekontrolované")

    text = bar.read_text(encoding="utf-8")

    assert 'available_actions?.includes("overit_bez_opravy")' in text
