"""Audítor na rýchlej dráhe dostane zoznam toho, čo má overiť (ICCINT-136).

**Čo tomu predchádzalo.** Rýchla dráha má zámerne ĽAHŠIU koncovú kontrolu — smernica to výslovne
hovorí („NIE plný release oracle"). Vynechať adverzariálnu hĺbku dáva zmysel.

Vynechaný bol ale aj **zoznam deklarovaných invariantov**: smernica pre rýchlu dráhu sa vracia skôr,
než sa prehľad pokrytia vôbec poskladá. Brána pred vydaním pritom invarianty vyžaduje **na oboch
dráhach rovnako** — Audítor teda posudzoval pokrytie niečoho, čo nevidel, a dozvedel sa o tom až
z textu chyby.

To nie je menej práce. To je práca naslepo.

⚠️ Čo tieto stráže NEDOVOLIA zmeniť:

1. **Deklarované invarianty sú v smernici oboch dráh.** Bez nich Audítor nevie, čo hľadá.
2. **Hĺbka rýchlej dráhy zostáva ĽAHKÁ.** Keby sa vetvy zliali, stratili by sme dôvod, prečo rýchla
   dráha existuje — a to by bola horšia chyba než tá pôvodná.
"""

from __future__ import annotations

import pytest

from backend.services import orchestrator

DEKLAROVANY = "Faktúra sa nesmie zaúčtovať dvakrát"


@pytest.fixture()
def deklaracia(monkeypatch):
    """Verzia, ktorá deklaruje jeden invariant — to, čo má Audítor overiť."""
    monkeypatch.setattr(
        orchestrator,
        "_release_declaration_payload",
        lambda _db, _v: {
            "flagship_features": ["Import faktúr"],
            "safety_properties": [{"name": DEKLAROVANY, "risky_op": "zaúčtovanie"}],
        },
    )


class _PrazdnaDb:
    """Najmenšia databáza, aká stačí: riadna dráha si pýta commit schváleného Vizuálu.

    ⚠️ Nie je to napodobenina toho, čo meriame — meriame TEXT smernice. Toto je len to, bez čoho sa
    k nemu nedá dostať; keby som atrapu urobil bohatšou, merala by moju predstavu o databáze.
    """

    def execute(self, _q):
        return self

    def scalar_one_or_none(self):
        return None


def _smernica(flow_type: str) -> str:
    return orchestrator._verifikacia_directive(_PrazdnaDb(), None, smoke_block="", flow_type=flow_type)


def test_the_fast_lane_auditor_is_told_what_to_verify(deklaracia):
    """⚠️ Jadro tiketu. Presne toto meranie je v ňom zapísané a vracalo False."""
    assert DEKLAROVANY in _smernica("fast_fix")


def test_the_ordinary_lane_still_gets_it_too(deklaracia):
    """Opačný smer — oprava nesmie prehľad odobrať tam, kde už bol."""
    assert DEKLAROVANY in _smernica("new_version")


def test_the_fast_lane_stays_light(deklaracia):
    """⚠️ Hĺbka sa NEMENÍ. Keby sa vetvy zliali, rýchla dráha by prestala byť rýchla — a to je horšia
    chyba než tá pôvodná, lebo by zmizol dôvod, prečo tá dráha existuje."""
    rychla = _smernica("fast_fix")

    assert "ĽAHKÁ koncová kontrola" in rychla
    assert "NIE plný release oracle" in rychla
    assert "nerob plný adverzariálny audit" in rychla


def test_the_two_lanes_are_still_different(deklaracia):
    """Poistka proti zlievaniu: rýchla dráha musí zostať kratšia než riadna."""
    rychla, riadna = _smernica("fast_fix"), _smernica("new_version")

    assert rychla != riadna
    assert len(rychla) < len(riadna), (len(rychla), len(riadna))
