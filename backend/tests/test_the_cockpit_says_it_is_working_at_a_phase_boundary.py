"""Kým sa schválenie Vizuálu spracúva, kokpit to povie — nemlčí (ICCINT-75).

**Čo sa dialo.** Po kliknutí na „Schváliť vizuál“ bežal ťah agenta aj tri minúty. Stav v evidencii pritom
zostával na ``vizual/awaiting_manazer`` a ``dispatch_in_flight`` na ``false``, takže obrazovka čítala
„čaká na súhlas“ a nezobrazovala nič. Director to opísal slovami *„nič sa nedeje, nefunguje to“* — agent
pritom celý čas pracoval (overené na procesoch v kontajneri, 07.09.2026, nex-productcatalogs v0.2.0).

**Prečo na tom záleží.** Ticho pri práci a ticho pri poruche vyzerajú rovnako. Manažér nemá ako rozhodnúť,
či počkať, kliknúť znova, alebo volať pomoc — a klikanie znova spúšťa ďalšie ťahy, čo stojí beh agenta
a mätie protokol.

**Čo sa zmenilo.** Kliknutie iba zapíše, že sa schválenie spracúva, a hneď sa vráti; prácu urobí ten istý
mechanizmus na pozadí, ktorý vykonáva každý iný ťah. Fáza sa posunie až tam a len ak niet rozporu.
"""

from __future__ import annotations

import inspect
import uuid as _uuid

import pytest

from backend.db.models.foundation import User
from backend.db.models.pipeline import PipelineState
from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.services import orchestrator


def _seed(db) -> tuple[Version, PipelineState]:
    suffix = _uuid.uuid4().hex[:8]
    user = User(username=f"m_{suffix}", email=f"m_{suffix}@t.local", password_hash="x", role="ri")  # noqa: S106
    db.add(user)
    db.flush()
    project = Project(
        name=f"Hranica {suffix}",
        slug=f"hranica-{suffix}",
        type="standard",
        auth_mode="password",
        description="Hranica fázy — ticho pri práci.",
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
        current_stage="vizual",
        current_actor="ai_agent",
        status="awaiting_manazer",
        mode=None,
    )
    db.add(state)
    db.flush()
    return version, state


@pytest.mark.asyncio
async def test_the_click_comes_back_at_once_and_says_work_is_running(db_session) -> None:
    """⚠️ Jadro ICCINT-75: po kliknutí musí byť OKAMŽITE vidieť, že sa pracuje.

    Nie o tri minúty, keď sa práca dokončí — vtedy už je Manažér dávno presvedčený, že je to rozbité.
    """
    version, state = _seed(db_session)

    vratene = await orchestrator.apply_action(db_session, version_id=version.id, action="schvalit", payload={})

    assert vratene.status == "agent_working", "obrazovka by ďalej čítala „čaká na súhlas“"
    assert vratene.dispatch_in_flight is True
    assert vratene.next_action, "stav bez vety je pre Manažéra to isté ticho"
    assert "spracúva" in vratene.next_action.lower()
    assert vratene.current_stage == "vizual", (
        "fáza sa nesmie posunúť skôr, než sa ukáže, či v dokumentoch nie je rozpor"
    )
    assert vratene.pending_vizual_signoff is True


@pytest.mark.asyncio
async def test_the_approval_itself_is_recorded_right_away(db_session) -> None:
    """Manažérovo rozhodnutie patrí do protokolu v okamihu, keď ho urobil — nie až keď dobehne stroj."""
    from sqlalchemy import select

    from backend.db.models.pipeline import PipelineMessage

    version, _state = _seed(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="schvalit", payload={})

    kinds = (
        db_session.execute(select(PipelineMessage.kind).where(PipelineMessage.version_id == version.id)).scalars().all()
    )
    assert "approval" in kinds


@pytest.mark.asyncio
async def test_the_heavy_work_happens_in_the_background_turn(db_session, monkeypatch) -> None:
    """Príznak sa spotrebuje na začiatku ťahu a spustí dokončenie — inak by kliknutie viselo naveky."""
    version, state = _seed(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="schvalit", payload={})

    bezalo: list[bool] = []

    async def _dokoncenie(db, st, **kw):
        bezalo.append(True)
        return st

    monkeypatch.setattr(orchestrator, "_complete_vizual_signoff", _dokoncenie)
    await orchestrator.run_dispatch(db_session, version.id)

    assert bezalo == [True], "ťah na pozadí dokončenie schválenia nespustil"
    db_session.refresh(state)
    assert state.pending_vizual_signoff is False, "príznak musí zhorieť, inak sa schválenie spracuje dvakrát"


@pytest.mark.asyncio
async def test_a_contradiction_still_stops_the_approval(db_session, monkeypatch) -> None:
    """Presun práce na pozadie nesmie prehltnúť rozpor.

    Rozpor nie je porucha, ale otázka na Manažéra: dve rozhodnutia z dvoch rôznych chvíľ. Schválenie
    vtedy zámerne neprejde — usadiť ho ticho ktorýmkoľvek smerom by ten nesúhlas pochovalo.
    """
    _version, state = _seed(db_session)

    async def _rozpor(db, st):
        return ["Košík vs. objednávka na jeden klik"]

    async def _usadenie(db, st, conflicts):
        st.status = "awaiting_manazer"
        return st

    monkeypatch.setattr(orchestrator, "_writeback_vizual_to_docs", _rozpor)
    monkeypatch.setattr(orchestrator, "_settle_vizual_conflict", _usadenie)

    vysledok = await orchestrator._complete_vizual_signoff(db_session, state)

    assert vysledok.current_stage == "vizual", "so zisteným rozporom sa fáza posunúť nesmie"
    assert vysledok.status == "awaiting_manazer"


@pytest.mark.asyncio
async def test_a_clean_fold_moves_the_build_on(db_session, monkeypatch) -> None:
    """Keď rozpor niet, Vizuál sa uzavrie a stavba ide ďalej sama — Manažér už nič klikať nemá."""
    _version, state = _seed(db_session)

    async def _bez_rozporu(db, st):
        return []

    monkeypatch.setattr(orchestrator, "_writeback_vizual_to_docs", _bez_rozporu)
    monkeypatch.setattr(orchestrator, "_commit_vizual_changes", lambda root: None)
    monkeypatch.setattr(orchestrator, "_docs_changed_in_head", lambda root: [])
    monkeypatch.setattr(orchestrator, "_repo_head", lambda root: None)

    vysledok = await orchestrator._complete_vizual_signoff(db_session, state)

    assert vysledok.current_stage == "programovanie"
    assert vysledok.status == "agent_working", "reťaz behu sa spúšťa práve stavom „pracuje sa“"


def test_the_click_does_not_carry_the_minutes_long_work_any_more() -> None:
    """⚠️ Stráž proti návratu choroby.

    Nič nebráni tomu, aby niekto tú prácu vrátil späť do ``apply_action`` — vyzerá to tam prirodzene,
    veď schválenie ju spúšťa. Preto sa priamo kontroluje, že vetva schválenia už tie minútové kroky
    nevolá, a že ich volá ťah na pozadí.
    """
    klik = inspect.getsource(orchestrator.apply_action)
    pozadie = inspect.getsource(orchestrator._complete_vizual_signoff)

    for krok in ("_writeback_vizual_to_docs", "_run_auditor_upfront_review", "_commit_vizual_changes"):
        assert krok not in klik, f"{krok} sa vrátilo do kliknutia — Manažér zase uvidí ticho"
        assert krok in pozadie, f"{krok} sa z ťahu na pozadí stratilo"
