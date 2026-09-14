"""ICCINT-126 — pozastavená stavba povie, PREČO stojí.

Director 14.09.2026 na NEX Inbox v1.5.0, fáza Verifikácia, vlastnými slovami:
*„Spravil som všetko podľa tvojho pokynu a nezbadal som, že tlačidlo nad plánom úloh zmenilo text."*
Stavba stála a nikto nevedel prečo.

Porovnanie, ktoré to vysvetľuje — tri stavy, všetky vyžadujú kliknutie Manažéra:

  decision_needed    vlastný pruh cez celú šírku, červený
  framework_issue    vlastný pruh
  paused             štítok v stavovom prúžku + poznámka v bočnej lište + ZMENENÝ TEXT NA TLAČIDLE

Stav, ktorý rovnako potrebuje ľudské kliknutie, bol zobrazený najmenej nápadne zo všetkých troch.

⚠️ Neriešime to zrušením tej brány. Druhé potvrdenie pri usmernenej oprave má zmysel — Manažér tam
posiela vlastný pokyn a má dostať možnosť si ho ešte pozrieť. Chyba nie je v tom, že sa čaká. Chyba
je, že sa to nedá zbadať.
"""

from __future__ import annotations

import pytest

from backend.services import orchestrator


@pytest.mark.asyncio
async def test_a_guided_fix_pause_says_it_is_waiting_for_the_manager(db_session, monkeypatch):
    """Toto je VÝZVA — stavba je pripravená a čaká na jedno kliknutie."""
    from tests.test_orchestrator_v2_verifikacia import _make_version, _seed_verifikacia

    version, _ = _make_version(db_session, project_dial="plna")
    state = _seed_verifikacia(db_session, version.id, iteration=1)
    state.current_stage = "programovanie"
    db_session.flush()

    novy = await orchestrator._route_manazer_fix_to_ai_agent(db_session, state, comment="Oprav to inak.")

    assert novy.status == "paused"
    assert novy.pause_reason == "fix_ready", (
        "pozastavenie čakajúce na Manažéra sa nedá odlíšiť od prekročeného stropu — "
        "a práve ten rozdiel Director 14.09.2026 nezbadal"
    )


def test_a_pause_reason_never_outlives_the_pause(db_session):
    """Tá istá disciplína ako pri ``block_reason``: dôvod platí len počas pozastavenia. Zastaraný
    dôvod je horší než žiadny — pruh by sa ukázal nad stavbou, ktorá už beží."""
    from tests.test_orchestrator_v2_verifikacia import _make_version, _seed_verifikacia

    version, _ = _make_version(db_session, project_dial="plna")
    state = _seed_verifikacia(db_session, version.id, iteration=1)
    state.status = "paused"
    state.pause_reason = "fix_ready"
    db_session.flush()

    state.status = "agent_working"
    db_session.flush()

    assert state.pause_reason is None


def test_the_three_pause_reasons_are_distinct():
    """Tri dôvody, tri rôzne veci. Prvý je výzva, druhý prekážka, tretí Manažér sám vie."""
    assert orchestrator.PAUSE_REASONS == ("fix_ready", "token_limit", "manazer")
