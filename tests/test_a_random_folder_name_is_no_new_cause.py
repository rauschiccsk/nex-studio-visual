"""DEV-381 — a random temp folder in a failure's detail does not make the same cause look like a new one.

13.11.2026, NEX Manager 1.6.0: the Verifikácia failed twice on the same deploy rehearsal, and the second card said
„Oproti minulému kolu je to INÁ príčina — tá predošlá je vyriešená. Slučka sa teda pohla dopredu …“. The two details
differed only in the name of the rehearsal's temp folder (``tempfile.mkdtemp`` — „…-yqkrgspf“ against „…-nc8npm6b“),
which the fingerprint of a failure did not blank. Director, on the finding and the ticket: „Súhlasím“; on doing it
next: „Áno súhlasím“.
"""

from __future__ import annotations

from backend.services import orchestrator as O

DETAIL = (
    "Skúška nasadenia: kokpit nevedel zapísať predpis inštalácie (Priečinok „/tmp/nex-manager-skuska-nasadenia-{}/"
    "zakaznik-andros/andros/nex-manager“ už obsahuje nasadenie, ktoré NEX Studio nevygenerovalo (súbor "
    "docker-compose.yml nemá hlavičku generovanú provisionerom)."
)


def _signature(detail: str) -> str:
    return O._failure_signature({"technical_detail": detail})


def test_the_same_cause_in_another_temp_folder_is_the_same_cause():
    assert _signature(DETAIL.format("yqkrgspf")) == _signature(DETAIL.format("nc8npm6b"))


def test_what_follows_the_temp_folder_still_tells_two_causes_apart():
    other = DETAIL.format("nc8npm6b").replace("zakaznik-andros/andros", "zakaznik-icc/icc")

    assert _signature(DETAIL.format("yqkrgspf")) != _signature(other)


def test_a_folder_outside_the_temp_dir_is_kept():
    """Only a temp folder is volatile — a real installation's path names the installation."""
    assert _signature("Priečinok /opt/uat/icc/nex-manager chýba") != _signature(
        "Priečinok /opt/uat/mager/nex-manager chýba"
    )
