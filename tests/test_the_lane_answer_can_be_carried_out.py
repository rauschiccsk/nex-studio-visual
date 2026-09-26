"""Keď sa obaja zhodnú, že práca nepatrí na rýchlu dráhu, dá sa to aj vykonať (ICCINT-139).

**Čo tomu predchádzalo.** AI Agent na začiatku stavby NEX Inbox 1.5.2 napísal:

    „Rozsah práce presahuje rýchlu opravu: tri zo štyroch bodov menia správanie popísané
     v Špecifikácii a dva z nich vyžadujú prestavbu údajov v ostrej prevádzke so 117 skutočnými
     faktúrami. Odporúčam postaviť v1.5.2 ako riadnu verziu a žiadam o potvrdenie."

Manažér potvrdil. Stavba **aj tak dobehla ako rýchla oprava** — dráha sa nastaví raz, pri spustení,
a nikde inde sa nemení. Agent sa smel spýtať, Manažér smel odpovedať, a odpoveď nemala kam ísť.

1.5.2 tak niesla migráciu databázy cez ĽAHKÚ kontrolu Audítora (»oprava funguje + nič sa nerozbilo«)
namiesto plnej — a jeden zo štyroch bodov svoj účel nesplnil.

⚠️ **Nie je to výnimka.** Zmerané 26.09.2026: 16 stavieb rýchlou dráhou proti 8 riadnym verziám;
posledných šesť verzií NEX Inboxu šlo všetkých šesť rýchlou dráhou.

⚠️ Čo tieto stráže NEDOVOLIA zmeniť:

1. **Prenesie sa ZADANIE, nie len číslo.** Nová verzia bez brífu by bola prázdny priečinok a Manažér
   by text prepisoval ručne — presne to, čomu sa vyhýbame.
2. **Dráha sa NEPREPÍNA za behu.** Prepnúť ju uprostred by znamenalo domýšľať, ktoré už prebehnuté
   fázy ešte platia. Stará stavba sa pozastaví a povie, KAM práca pokračuje.
3. **Nedá sa to uprostred ťahu.** Stavba, v ktorej práve pracuje agent, sa neprenáša.
4. **Nová verzia ide RIADNOU dráhou.** Keby vznikla zas ako rýchla oprava, celá akcia by bola
   ozdoba.
"""

from __future__ import annotations

import uuid
from typing import Optional

import pytest
from sqlalchemy import select

from backend.db.models.foundation import User
from backend.db.models.pipeline import PipelineMessage, PipelineState
from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.services import orchestrator

BRIEF = (
    "Rozsah presahuje rýchlu opravu: tri body menia správanie zo Špecifikácie a dva žiadajú "
    "prestavbu údajov v ostrej prevádzke so 117 faktúrami."
)


def _user(db) -> User:
    u = User(
        username=f"u_{uuid.uuid4().hex[:8]}",
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        password_hash="x",
        role="ri",
    )
    db.add(u)
    db.flush()
    return u


def _rychla_oprava(db, *, status: str = "blocked", cislo: str = "1.5.2") -> tuple[Project, Version, PipelineState]:
    """Stavba na rýchlej dráhe, zaseknutá na otázke agenta — presne prípad z tiketu."""
    autor = _user(db)
    project = Project(
        name=f"P {uuid.uuid4().hex[:8]}",
        slug=f"p-{uuid.uuid4().hex[:8]}",
        type="standard",
        auth_mode="password",
        description="d",
        created_by=autor.id,
        source_path=None,
    )
    db.add(project)
    db.flush()
    version = Version(project_id=project.id, version_number=cislo, name="Rýchla oprava")
    db.add(version)
    db.flush()
    state = PipelineState(
        version_id=version.id,
        flow_type="fast_fix",
        current_stage="priprava",
        current_actor="ai_agent",
        status=status,
        next_action="Čaká na Manažéra.",
        mode="conversation",
    )
    db.add(state)
    db.flush()
    # Štartovacia správa nesie zadanie — tak, ako ho nesie rýchla oprava spustená z kokpitu.
    db.add(
        PipelineMessage(
            version_id=version.id,
            stage="priprava",
            author="manazer",
            recipient="ai_agent",
            kind="kickoff",
            content=BRIEF,
            status="delivered",
            payload={"flow_type": "fast_fix", "phase": "priprava", "directive": BRIEF},
        )
    )
    db.flush()
    return project, version, state


def _nova_verzia(db, project: Project, okrem: Version) -> Optional[Version]:
    return db.execute(
        select(Version).where(Version.project_id == project.id, Version.id != okrem.id)
    ).scalar_one_or_none()


# ── 1. odpoveď sa dá vykonať ──────────────────────────────────────────────────


class TestOdpovedSaDaVykonat:
    @pytest.mark.asyncio
    async def test_the_work_moves_to_a_proper_version(self, db_session):
        """⚠️ Jadro tiketu: dnes sa to nedá vôbec."""
        project, version, _state = _rychla_oprava(db_session)

        await orchestrator.apply_action(db_session, version_id=version.id, action="na_riadnu_verziu")

        nova = _nova_verzia(db_session, project, version)
        assert nova is not None, "nová verzia nevznikla"
        assert nova.version_number == "1.6.0", nova.version_number

    @pytest.mark.asyncio
    async def test_the_new_version_carries_the_same_brief(self, db_session):
        """⚠️ Prenesie sa ZADANIE, nie len číslo. Inak by Manažér text prepisoval ručne."""
        from backend.services import version as version_service

        project, version, _state = _rychla_oprava(db_session)

        await orchestrator.apply_action(db_session, version_id=version.id, action="na_riadnu_verziu")

        nova = _nova_verzia(db_session, project, version)
        assert BRIEF in version_service.read_zadanie(db_session, nova.id)

    @pytest.mark.asyncio
    async def test_the_new_version_is_not_a_fast_fix_again(self, db_session):
        """⚠️ Keby nová verzia vznikla zas ako rýchla oprava, celá akcia by bola ozdoba."""
        project, version, _state = _rychla_oprava(db_session)

        await orchestrator.apply_action(db_session, version_id=version.id, action="na_riadnu_verziu")

        nova = _nova_verzia(db_session, project, version)
        stav_novej = db_session.execute(
            select(PipelineState).where(PipelineState.version_id == nova.id)
        ).scalar_one_or_none()
        # Buď ešte nezačala (koncept), alebo beží riadnou dráhou — nikdy nie rýchlou.
        assert stav_novej is None or stav_novej.flow_type == "new_version", stav_novej


# ── 2. stará stavba to povie ──────────────────────────────────────────────────


class TestStaraStavbaToPovie:
    @pytest.mark.asyncio
    async def test_the_old_build_is_paused_not_silently_left_running(self, db_session):
        """Rozbehnutá rýchla oprava sa nesmie ticho dokončiť popri novej verzii."""
        _project, version, state = _rychla_oprava(db_session)

        await orchestrator.apply_action(db_session, version_id=version.id, action="na_riadnu_verziu")

        assert state.status == "paused", state.status

    @pytest.mark.asyncio
    async def test_the_old_build_records_where_the_work_continues(self, db_session):
        """⚠️ Kto otvorí starú stavbu o mesiac, musí sa dozvedieť, kam sa práca presunula — inak to
        vyzerá ako opustená stavba."""
        _project, version, _state = _rychla_oprava(db_session)

        await orchestrator.apply_action(db_session, version_id=version.id, action="na_riadnu_verziu")

        spravy = (
            db_session.execute(select(PipelineMessage).where(PipelineMessage.version_id == version.id)).scalars().all()
        )
        posledna = spravy[-1]
        assert "1.6.0" in posledna.content, posledna.content
        assert (posledna.payload or {}).get("prenesene_do"), posledna.payload


# ── 3. kedy sa to NESMIE ──────────────────────────────────────────────────────


class TestKedyToNejde:
    @pytest.mark.asyncio
    async def test_a_build_mid_turn_is_not_transferred(self, db_session):
        """⚠️ Uprostred ťahu agenta nie. Založiť vedľa druhú verziu z toho istého zadania, kým prvá
        pracuje, by znamenalo dve stavby na tej istej práci."""
        _project, version, _state = _rychla_oprava(db_session, status="agent_working")

        with pytest.raises(orchestrator.OrchestratorError):
            await orchestrator.apply_action(db_session, version_id=version.id, action="na_riadnu_verziu")

    @pytest.mark.asyncio
    async def test_a_proper_version_has_nothing_to_transfer(self, db_session):
        """Na riadnej dráhe tá akcia nedáva zmysel a nesmie sa ponúkať."""
        _project, version, state = _rychla_oprava(db_session)
        state.flow_type = "new_version"
        db_session.flush()

        with pytest.raises(orchestrator.OrchestratorError):
            await orchestrator.apply_action(db_session, version_id=version.id, action="na_riadnu_verziu")

    @pytest.mark.asyncio
    async def test_the_action_is_offered_only_on_the_fast_lane(self, db_session):
        """Obrazovka ponúka len to, čo backend dovolí — inak by tlačidlo svietilo a nič nerobilo."""
        _project, _version, state = _rychla_oprava(db_session)

        assert "na_riadnu_verziu" in orchestrator.determine_available_actions(state)

        state.flow_type = "new_version"
        assert "na_riadnu_verziu" not in orchestrator.determine_available_actions(state)
