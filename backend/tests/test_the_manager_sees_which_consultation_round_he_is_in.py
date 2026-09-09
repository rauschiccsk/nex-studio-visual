"""Manažér vidí, koľké kolo previerky beží a koľko ich ešte môže prísť (ICCINT-72).

**Čo chýbalo.** Strop existuje a funguje — je to tá istá konštanta ako pri overovacej slučke
(``AUDITOR_LOOP_MAX = 5``) a na nex-productcatalogs v0.2.0 zabral presne tak, ako má. Neviditeľné bolo
**počítadlo**: kolá 1 až 4 ohlásili iba „Auditor našiel medzeru — spúšťa sa konzultácia s Manažérom“
a číslo sa objavilo až v okamihu eskalácie, teda keď už bolo po všetkom.

**Prečo na tom záleží.** Zmerané na v0.2.0: päť kôl za dva a pol hodiny, 23 rozhodnutí. Manažér pri
kartách nemal ako vedieť, či je na začiatku, alebo pred posledným kolom — a teda ani či sa oplatí čakať
ďalšie, alebo má rozhodnúť sám. „Rozhodnutie 3 z 5“ na karte hovorí o rozhodnutiach v TOMTO kole, nie
o kolách; ľahko sa to zamení a znie to ako koniec.

*(Poznámka k pôvodnému zneniu tiketu: tvrdil som, že predbežná previerka strop nemá. Nebola to pravda —
hľadal som ho vnútri ``_run_auditor_upfront_review``, kde nie je, a zo záporného nálezu jedného hľadania
som usúdil, že nie je nikde.)*
"""

from __future__ import annotations

import uuid as _uuid

import pytest
from sqlalchemy import select

from backend.db.models.foundation import User
from backend.db.models.pipeline import PipelineMessage, PipelineState
from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.services import orchestrator
from backend.services.pipeline_status import PipelineStatusBlock


def _seed(db) -> tuple[Version, PipelineState]:
    suffix = _uuid.uuid4().hex[:8]
    user = User(username=f"k_{suffix}", email=f"k_{suffix}@t.local", password_hash="x", role="ri")  # noqa: S106
    db.add(user)
    db.flush()
    project = Project(
        name=f"Kolo {suffix}",
        slug=f"kolo-{suffix}",
        type="standard",
        auth_mode="password",
        description="Počítadlo kôl konzultácie.",
        created_by=user.id,
        source_path=None,
    )
    db.add(project)
    db.flush()
    version = Version(project_id=project.id, version_number="0.1.0", status="active")
    db.add(version)
    db.flush()
    state = PipelineState(
        version_id=version.id,
        flow_type="new_version",
        current_stage="navrh",
        current_actor="ai_agent",
        status="agent_working",
        mode=None,
    )
    db.add(state)
    db.flush()
    return version, state


def _cards(n: int) -> PipelineStatusBlock:
    return PipelineStatusBlock(
        stage="navrh",
        kind="consultation",
        summary="Rozhodnutia.",
        awaiting="manazer",
        consultation={
            "id": f"c-{n}",
            "source": "auditor_upfront",
            "decisions": [
                {
                    "key": f"d{i}",
                    "question": f"Otázka {i}?",
                    "options": [
                        {"id": "a", "label": "Áno", "recommended": True},
                        {"id": "b", "label": "Nie"},
                    ],
                }
                for i in range(n)
            ],
        },
    )


def _stub_turn(db, version, block: PipelineStatusBlock):
    """Atrapa ťahu agenta, ktorá aj ZAPÍŠE správu s kartami — presne to robí skutočný
    ``invoke_agent_with_parse_retry``. Bez toho zápisu by sa meralo niečo, čo v systéme nikdy nevznikne."""

    async def _turn(*a, **k):
        orchestrator._record_message(
            db,
            version_id=version.id,
            stage="navrh",
            author="ai_agent",
            recipient="manazer",
            kind="consultation",
            content=block.summary,
            payload={"consultation": block.consultation.model_dump()},
        )
        return block

    return _turn


def _latest_consultation_payload(db, version_id) -> dict:
    row = db.execute(
        select(PipelineMessage.payload)
        .where(PipelineMessage.version_id == version_id, orchestrator._carries_decision_queue())
        .order_by(PipelineMessage.seq.desc())
        .limit(1)
    ).scalar_one()
    return row["consultation"]


@pytest.mark.asyncio
async def test_the_first_round_says_it_is_the_first(db_session, monkeypatch) -> None:
    """⚠️ Jadro ICCINT-72: číslo kola musí byť na kartách, kde Manažér sedí — nie až pri eskalácii."""
    version, state = _seed(db_session)

    monkeypatch.setattr(orchestrator, "invoke_agent_with_parse_retry", _stub_turn(db_session, version, _cards(2)))
    await orchestrator._settle_for_consultation(db_session, state, source="auditor_upfront", verdict=None)

    consultation = _latest_consultation_payload(db_session, version.id)
    assert consultation["round"] == 1
    assert consultation["round_max"] == orchestrator.AUDITOR_LOOP_MAX
    assert "kolo 1 z" in state.next_action


@pytest.mark.asyncio
async def test_the_round_counts_up_with_every_consultation_about_the_same_thing(db_session, monkeypatch) -> None:
    """Kolá o TEJ ISTEJ veci sa počítajú; číslo pochádza z toho istého počítadla ako strop, takže sa
    s ním nemôže rozísť."""
    version, state = _seed(db_session)

    monkeypatch.setattr(orchestrator, "invoke_agent_with_parse_retry", _stub_turn(db_session, version, _cards(1)))
    for ocakavane in (1, 2, 3):
        await orchestrator._settle_for_consultation(db_session, state, source="auditor_upfront", verdict=None)
        assert _latest_consultation_payload(db_session, version.id)["round"] == ocakavane


@pytest.mark.asyncio
async def test_a_consultation_about_something_else_starts_from_one(db_session, monkeypatch) -> None:
    """Strop platí na jeden spor, nie na verziu — a rovnako sa počíta aj to, čo Manažér číta.

    Keby sa kolá počítali cez celú verziu, jeden vyčerpaný spor by Manažérovi ukázal „kolo 6 z 5“ pri
    úplne inej otázke.
    """
    version, state = _seed(db_session)

    monkeypatch.setattr(orchestrator, "invoke_agent_with_parse_retry", _stub_turn(db_session, version, _cards(1)))
    await orchestrator._settle_for_consultation(db_session, state, source="auditor_upfront", verdict=None)
    await orchestrator._settle_for_consultation(db_session, state, source="auditor_upfront", verdict=None)

    ine = _cards(1)
    ine.consultation.source = "verifikacia_fix"
    monkeypatch.setattr(orchestrator, "invoke_agent_with_parse_retry", _stub_turn(db_session, version, ine))
    await orchestrator._settle_for_consultation(db_session, state, source="verifikacia_fix", verdict=None)

    assert _latest_consultation_payload(db_session, version.id)["round"] == 1


def test_the_counter_behind_the_cap_and_the_counter_behind_the_words_are_the_same_one() -> None:
    """Dve nezávislé počítania toho istého by sa raz rozišli a Manažér by čítal číslo, ktoré neplatí."""
    import inspect

    src = inspect.getsource(orchestrator._settle_for_consultation)
    assert "_consult_rounds_used(" in src, "strop si počíta kolá po svojom"
    assert src.count("_carries_decision_queue()") <= 1, (
        "kolá sa počítajú na dvoch miestach — raz pre strop, raz pre text"
    )


# ── Kolá opráv po Verifikácii (ICCINT-97) ─────────────────────────────────────
#
# Tá istá diera, len pri druhom druhu konzultácie — a ICCINT-72 sa jej nedotkol. Číslo kola sa dopĺňa
# v ``_settle_for_consultation``, ale karty z Verifikácie vznikajú inou cestou
# (``_build_fix_consultation``), ktorá cez ňu nejde. Manažér tak prešiel 09.09.2026 na NEX Manager 1.1.0
# šesť kôl a nikde nestálo, koľké to je.
#
# ⚠️ Strop tu ZÁMERNE nie je. ``AUDITOR_LOOP_MAX`` ohraničuje samočinnú slučku agent↔Auditor; keď na
# kartu odpovie človek, počítadlo sa nuluje, a to je správne — človek riadi, nie stroj beží naprázdno.
# Napísať na kartu strop, ktorý neplatí, by bola lož. Ukazuje sa preto číslo bez „z piatich“.


def _zapis_kolo_opravy(db, version_id) -> None:
    """Zapíš jedno KOLO opravy po Verifikácii — takú správu, akú počítadlo naozaj počíta."""
    orchestrator._record_message(
        db,
        version_id=version_id,
        stage="verifikacia",
        author="ai_agent",
        recipient="manazer",
        kind="consultation",
        content="Verifikácia našla chybu.",
        payload={
            "consultation": {
                "id": f"verifikacia-fix-{_uuid.uuid4().hex[:6]}",
                "source": "verifikacia_fix",
                "decisions": [
                    {
                        "key": "verifikacia_fix_next",
                        "question": "Ako chceš pokračovať?",
                        "options": [{"id": "fix_it", "label": "Nechaj to opraviť", "recommended": True}],
                    }
                ],
            }
        },
    )


def test_the_first_fix_round_after_verification_says_it_is_the_first(db_session) -> None:
    """⚠️ Jadro ICCINT-97: karta z Verifikácie musí niesť číslo kola tak ako karta z previerky."""
    version, state = _seed(db_session)

    karta = orchestrator._build_fix_consultation(db_session, version.id, state)

    assert karta.round == 1, "karta z Verifikácie o čísle kola mlčí — Manažér nevie, koľké to je"
    assert karta.round_max is None, "karta z Verifikácie tvrdí strop, ktorý pri kolách riadených človekom neplatí"


def test_every_answered_round_counts_even_though_nothing_caps_it(db_session) -> None:
    """Kolá riadené človekom sa počítajú ďalej — strop ich nenuluje, lebo žiadny nie je."""
    version, state = _seed(db_session)

    for ocakavane in (1, 2, 3, 4):
        karta = orchestrator._build_fix_consultation(db_session, version.id, state)
        assert karta.round == ocakavane, f"čakal som {ocakavane}. kolo, karta hovorí {karta.round}"
        _zapis_kolo_opravy(db_session, version.id)


def test_from_the_third_round_it_says_out_loud_how_many_there_have_been(db_session) -> None:
    """Číslo na karte je pre toho, kto sa naň pozrie; veta je pre toho, kto len klikne ĎALEJ.

    Kolá nie sú zadarmo — každé stojí beh AI Agenta aj beh Auditora. Od tretieho kola sa to preto
    povie rovno vo vysvetlení, spolu s tým, že rozhodnutie pokračovať zostáva na Manažérovi.
    """
    version, state = _seed(db_session)
    for _ in range(2):
        _zapis_kolo_opravy(db_session, version.id)

    karta = orchestrator._build_fix_consultation(db_session, version.id, state)
    vysvetlenie = karta.decisions[0].explanation or ""

    assert karta.round == 3
    assert "3. kolo opráv" in vysvetlenie, f"tretie kolo sa nikde nepovie: {vysvetlenie!r}"
    assert "beh AI Agenta" in vysvetlenie, "nepovie sa, že kolo niečo stojí"


def test_the_first_two_rounds_stay_short(db_session) -> None:
    """Prvé dve kolá sú bežná práca — pripomínať pri nich cenu by bolo otravné a zbytočné.

    Bez tohto tvrdenia by stráž vyššie prešla aj vtedy, keby sa tá veta lepila na každú kartu.
    """
    version, state = _seed(db_session)

    karta = orchestrator._build_fix_consultation(db_session, version.id, state)

    assert "kolo opráv" not in (karta.decisions[0].explanation or ""), (
        "cena kola sa pripomína už v prvom kole — tam ešte niet čo zvažovať"
    )
