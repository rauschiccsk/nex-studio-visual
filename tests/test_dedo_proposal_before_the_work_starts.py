"""Dedo vie pripraviť zadanie pre prácu, ktorá sa ešte nezačala (ICCINT-152).

**Čo tomu predchádzalo.** Dedove dvere majú návrh — text, ktorý Manažér v kokpite prečíta, prípadne
upraví a jedným tlačidlom pošle ďalej. Viazal sa ale na BEŽIACU stavbu, takže sa otvoril až vtedy, keď
už zadanie netreba. 25.09.2026 prestalo na MÁGERSTAVE fungovať spúšťanie NEX Inboxu z NEX Managera,
Director požiadal *„zapíš to zadanie do kokpitu ako návrh"* — a nešlo to. Text som mu musel podať do
ruky, aby ho pri spúšťaní rýchlej opravy vložil. Presne tomu mali tie dvere zabrániť.

⚠️ Čo tieto stráže NEDOVOLIA zmeniť:

1. **Dedove dvere nespustia nič.** Zápis návrhu nesmie založiť verziu ani stavbu — inak by Dedo
   obišiel Manažéra, a to je hranica, ktorá platí od ICCINT-14.
2. **Verziu zakladá KLIK Manažéra, pod jeho účtom.** Rovnaká cesta, akú by klikol sám.
3. **Koná sa nad tým návrhom, ktorý mal Manažér pred očami** — nie nad „tým, čo je otvorené teraz".
   Inak by Dedov novší návrh podaný medzi zobrazením a kliknutím odišiel namiesto neho.
4. **Otvorený je najviac jeden** a drží to databáza, nie poradie príkazov v službe.
5. **``new_version`` nespustí stavbu** — založí koncept. Rozdiel medzi „pripravené" a „beží" je to
   jediné, čo na tej obrazovke Manažér rozhoduje.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from backend.config.settings import settings
from backend.core import dedo_auth
from backend.db.models.dedo_proposal import PROPOSED, SENT, SUPERSEDED, DedoProjectProposal
from backend.db.models.foundation import User, UserSession
from backend.db.models.pipeline import PipelineMessage, PipelineState
from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.services import auth as auth_service
from backend.services import pipeline_runner

_TOKEN = "d" * 64

_ZADANIE = (
    "NEX Manager od 1.2.0 posiela spúšťací lístok telom požiadavky, Inbox má tú adresu len na GET "
    "a odpovie „Method Not Allowed“. Prijmi na tej adrese aj POST a lístok čítaj z tela."
)


@pytest.fixture()
def dedo_token(monkeypatch) -> str:
    monkeypatch.setattr(settings, "dedo_api_token_sha256", "")
    monkeypatch.setattr(settings, "dedo_api_token", _TOKEN)
    return _TOKEN


@pytest.fixture()
def no_dispatch(monkeypatch) -> list:
    """Zachytí spustenie agenta namiesto toho, aby ho naozaj pustilo."""
    scheduled: list = []
    monkeypatch.setattr(pipeline_runner, "schedule_dispatch", lambda v, d=None: scheduled.append((v, d)))
    return scheduled


def _dedo_auth() -> dict[str, str]:
    return {dedo_auth.DEDO_TOKEN_HEADER: _TOKEN}


def _make_user(db_session, role: str = "ri") -> User:
    user = User(
        username=f"u_{uuid.uuid4().hex[:8]}",
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        password_hash="x",
        role=role,
    )
    db_session.add(user)
    db_session.flush()
    db_session.add(UserSession(user_id=user.id, token_version=0))
    db_session.flush()
    return user


def _make_project(db_session, user: User, *, so_semverom: bool = True) -> Project:
    """Projekt s jednou vydanou verziou — rýchla oprava potrebuje, z čoho povýšiť."""
    project = Project(
        name=f"P {uuid.uuid4().hex[:8]}",
        slug=f"p-{uuid.uuid4().hex[:8]}",
        type="standard",
        auth_mode="password",
        description="d",
        created_by=user.id,
        source_path=None,
    )
    db_session.add(project)
    db_session.flush()
    if so_semverom:
        db_session.add(Version(project_id=project.id, version_number="1.5.5", name="v1.5.5"))
        db_session.flush()
    return project


def _bearer(user: User) -> dict[str, str]:
    token, _ = auth_service.create_access_token(user, 0, 60)
    return {"Authorization": f"Bearer {token}"}


def _navrhni(client, project_id, *, content: str = _ZADANIE, action: str = "fast_fix"):
    return client.post(
        f"/api/v1/dedo/projects/{project_id}/proposals",
        headers=_dedo_auth(),
        json={"content": content, "proposed_action": action},
    )


def _posli(client, user, project_id, proposal_id, *, text: str = _ZADANIE):
    return client.post(
        f"/api/v1/projects/{project_id}/dedo-proposal/send",
        headers=_bearer(user),
        json={"proposal_id": str(proposal_id), "text": text},
    )


def _zamietni(client, user, project_id, proposal_id):
    return client.post(
        f"/api/v1/projects/{project_id}/dedo-proposal/reject",
        headers=_bearer(user),
        json={"proposal_id": str(proposal_id)},
    )


# ── 1. Dedo píše zadanie, keď nič nebeží ──────────────────────────────────────


class TestDedoMozeNapisatZadanie:
    def test_a_brief_can_be_written_for_a_project_with_no_build(self, client, db_session, dedo_token):
        """⚠️ Jadro veci. Presne toto 25.09.2026 nešlo."""
        user = _make_user(db_session)
        project = _make_project(db_session, user)
        db_session.commit()

        r = _navrhni(client, project.id)

        assert r.status_code == 201, r.text
        telo = r.json()
        assert telo["content"] == _ZADANIE
        assert telo["proposed_action"] == "fast_fix"
        assert telo["status"] == PROPOSED

    def test_writing_a_brief_starts_nothing(self, client, db_session, dedo_token, no_dispatch):
        """⚠️ Hranica z ICCINT-14: Dedo navrhuje, Manažér rozhoduje. Zápis nesmie založiť verziu ani
        stavbu — inak by Dedo spúšťal prácu na zákazníkovom projekte sám."""
        user = _make_user(db_session)
        project = _make_project(db_session, user)
        db_session.commit()
        verzii_pred = db_session.execute(select(Version).where(Version.project_id == project.id)).scalars().all()

        assert _navrhni(client, project.id).status_code == 201

        verzii_po = db_session.execute(select(Version).where(Version.project_id == project.id)).scalars().all()
        assert len(verzii_po) == len(verzii_pred), "návrh založil verziu"
        assert db_session.execute(select(PipelineState)).scalars().all() == [], "návrh spustil stavbu"
        assert no_dispatch == [], "návrh poslal agenta do práce"

    def test_an_unknown_project_is_refused(self, client, db_session, dedo_token):
        assert _navrhni(client, uuid.uuid4()).status_code == 404

    def test_an_unknown_verb_is_refused(self, client, db_session, dedo_token):
        """Sloveso musí byť také, ktoré Manažér v kokpite naozaj vie stlačiť."""
        user = _make_user(db_session)
        project = _make_project(db_session, user)
        db_session.commit()

        assert _navrhni(client, project.id, action="deploy").status_code == 409

    def test_a_brief_with_no_text_is_refused(self, client, db_session, dedo_token):
        user = _make_user(db_session)
        project = _make_project(db_session, user)
        db_session.commit()

        assert _navrhni(client, project.id, content="   ").status_code in (409, 422)

    def test_the_door_is_closed_without_dedos_token(self, client, db_session, dedo_token):
        user = _make_user(db_session)
        project = _make_project(db_session, user)
        db_session.commit()

        r = client.post(
            f"/api/v1/dedo/projects/{project.id}/proposals",
            headers=_bearer(user),
            json={"content": _ZADANIE, "proposed_action": "fast_fix"},
        )

        assert r.status_code == 401, "používateľský prístup otvoril Dedove dvere"


# ── 2. Najviac jeden otvorený ─────────────────────────────────────────────────


class TestNajviacJedenOtvoreny:
    def test_a_newer_brief_archives_the_older_one(self, client, db_session, dedo_token):
        """Dedo, ktorý znovu premeral a napísal presnejšie zadanie, je bežný prípad."""
        user = _make_user(db_session)
        project = _make_project(db_session, user)
        db_session.commit()
        prvy = _navrhni(client, project.id).json()

        druhy = _navrhni(client, project.id, content=_ZADANIE + " A pridaj skúšku.").json()

        otvorene = (
            db_session.execute(
                select(DedoProjectProposal).where(
                    DedoProjectProposal.project_id == project.id,
                    DedoProjectProposal.status == PROPOSED,
                )
            )
            .scalars()
            .all()
        )
        assert [str(r.id) for r in otvorene] == [druhy["id"]]
        assert db_session.get(DedoProjectProposal, uuid.UUID(prvy["id"])).status == SUPERSEDED

    def test_the_older_one_is_not_recorded_as_decided(self, client, db_session, dedo_token):
        """⚠️ Archivovaný ≠ zamietnutý. Záznam nesmie tvrdiť, že Manažér rozhodol o niečom, čo nevidel."""
        user = _make_user(db_session)
        project = _make_project(db_session, user)
        db_session.commit()
        prvy = _navrhni(client, project.id).json()
        _navrhni(client, project.id, content="novšie")

        stary = db_session.get(DedoProjectProposal, uuid.UUID(prvy["id"]))
        assert stary.status == SUPERSEDED
        assert stary.resolved_by is None


# ── 3. Manažér rozhoduje ──────────────────────────────────────────────────────


class TestManazerRozhoduje:
    def test_he_sees_the_open_brief(self, client, db_session, dedo_token):
        user = _make_user(db_session)
        project = _make_project(db_session, user)
        db_session.commit()
        navrh = _navrhni(client, project.id).json()

        r = client.get(f"/api/v1/projects/{project.id}/dedo-proposal", headers=_bearer(user))

        assert r.status_code == 200, r.text
        assert r.json()["id"] == navrh["id"]
        assert r.json()["content"] == _ZADANIE

    def test_no_brief_is_an_empty_answer_not_an_error(self, client, db_session):
        user = _make_user(db_session)
        project = _make_project(db_session, user)
        db_session.commit()

        r = client.get(f"/api/v1/projects/{project.id}/dedo-proposal", headers=_bearer(user))

        assert r.status_code == 200 and r.json() is None

    def test_his_click_starts_the_fast_fix(self, client, db_session, dedo_token, no_dispatch):
        """⚠️ Verzia vzniká až TU — pod jeho účtom, tou istou cestou, akou by formulár vyplnil sám."""
        user = _make_user(db_session)
        project = _make_project(db_session, user)
        db_session.commit()
        navrh = _navrhni(client, project.id).json()

        r = _posli(client, user, project.id, navrh["id"])

        assert r.status_code == 200, r.text
        nova = db_session.get(Version, uuid.UUID(r.json()["version_id"]))
        assert nova.version_number == "1.5.6", "rýchla oprava nepovýšila opravné číslo"
        assert (
            db_session.execute(select(PipelineState).where(PipelineState.version_id == nova.id)).scalar_one_or_none()
            is not None
        ), "stavba sa nespustila"

    def test_what_he_edited_is_what_goes_in(self, client, db_session, dedo_token, no_dispatch):
        """Text v okienku je jeho — Dedov návrh je predloha, nie príkaz."""
        user = _make_user(db_session)
        project = _make_project(db_session, user)
        db_session.commit()
        navrh = _navrhni(client, project.id).json()
        upravene = _ZADANIE + " Najprv over, či to na UAT naozaj padá."

        r = _posli(client, user, project.id, navrh["id"], text=upravene)

        assert r.status_code == 200, r.text
        # Zadanie sa overuje tam, kam SKUTOČNE ide: do štartovacej správy novej stavby, ktorú agent číta.
        kickoff = db_session.execute(
            select(PipelineMessage).where(
                PipelineMessage.version_id == uuid.UUID(r.json()["version_id"]),
                PipelineMessage.kind == "kickoff",
            )
        ).scalar_one()
        assert (kickoff.payload or {}).get("directive") == upravene, kickoff.payload

    def test_the_sent_brief_is_closed_and_points_at_its_version(self, client, db_session, dedo_token, no_dispatch):
        user = _make_user(db_session)
        project = _make_project(db_session, user)
        db_session.commit()
        navrh = _navrhni(client, project.id).json()

        r = _posli(client, user, project.id, navrh["id"])

        row = db_session.get(DedoProjectProposal, uuid.UUID(navrh["id"]))
        assert row.status == SENT
        assert row.resolved_by == user.id
        assert str(row.version_id) == r.json()["version_id"]

    def test_a_new_version_brief_prepares_but_does_not_start(self, client, db_session, dedo_token, no_dispatch):
        """⚠️ Druhé sloveso. Koncept verzie so zadaním v popise — a NIČ sa nerozbehne."""
        user = _make_user(db_session)
        project = _make_project(db_session, user)
        db_session.commit()
        navrh = _navrhni(client, project.id, action="new_version").json()

        r = _posli(client, user, project.id, navrh["id"])

        assert r.status_code == 200, r.text
        nova = db_session.get(Version, uuid.UUID(r.json()["version_id"]))
        assert nova.status == "planned"
        assert _ZADANIE in (nova.description or ""), "zadanie sa do verzie nedostalo"
        assert db_session.execute(select(PipelineState)).scalars().all() == [], "koncept spustil stavbu"
        assert no_dispatch == [], "koncept poslal agenta do práce"

    def test_he_can_decline_it(self, client, db_session, dedo_token, no_dispatch):
        user = _make_user(db_session)
        project = _make_project(db_session, user)
        db_session.commit()
        navrh = _navrhni(client, project.id).json()

        r = _zamietni(client, user, project.id, navrh["id"])

        assert r.status_code == 200, r.text
        assert db_session.get(DedoProjectProposal, uuid.UUID(navrh["id"])).status == "rejected"
        assert db_session.execute(select(PipelineState)).scalars().all() == []
        assert no_dispatch == []

    def test_a_declined_brief_is_not_offered_again(self, client, db_session, dedo_token):
        user = _make_user(db_session)
        project = _make_project(db_session, user)
        db_session.commit()
        navrh = _navrhni(client, project.id).json()
        _zamietni(client, user, project.id, navrh["id"])

        r = client.get(f"/api/v1/projects/{project.id}/dedo-proposal", headers=_bearer(user))

        assert r.json() is None


# ── 4. Koná sa nad tým, čo mal pred očami ─────────────────────────────────────


class TestKonaSaNadTymCoVidel:
    def test_acting_on_a_superseded_brief_is_refused(self, client, db_session, dedo_token, no_dispatch):
        """⚠️ Keby sa klik vyhodnocoval proti „čo je otvorené teraz", Dedov novší návrh podaný medzi
        zobrazením a kliknutím by odišiel namiesto toho, ktorý mal Manažér pred očami."""
        user = _make_user(db_session)
        project = _make_project(db_session, user)
        db_session.commit()
        stary = _navrhni(client, project.id).json()
        _navrhni(client, project.id, content="Dedo medzitým premeral a napísal iné zadanie.")

        r = _posli(client, user, project.id, stary["id"])

        assert r.status_code == 409, r.text
        assert db_session.execute(select(PipelineState)).scalars().all() == [], "odmietnutý klik spustil stavbu"

    def test_the_refusal_says_what_happened(self, client, db_session, dedo_token):
        """Hláška musí odpovedať na otázku „a čo teda". Nemenné „to nejde" zacyklí človeka."""
        user = _make_user(db_session)
        project = _make_project(db_session, user)
        db_session.commit()
        stary = _navrhni(client, project.id).json()
        _navrhni(client, project.id, content="novšie")

        r = _posli(client, user, project.id, stary["id"])

        assert "novší" in r.json()["detail"], r.text

    def test_a_brief_from_another_project_is_not_found(self, client, db_session, dedo_token):
        """Identifikátor návrhu sa overuje PROTI projektu z adresy — inak by sa dal odoslať cudzí."""
        user = _make_user(db_session)
        jeden = _make_project(db_session, user)
        druhy = _make_project(db_session, user)
        db_session.commit()
        navrh = _navrhni(client, jeden.id).json()

        assert _posli(client, user, druhy.id, navrh["id"]).status_code == 404

    def test_the_second_click_on_the_same_brief_is_refused(self, client, db_session, dedo_token, no_dispatch):
        """Dvojklik nesmie založiť dve verzie."""
        user = _make_user(db_session)
        project = _make_project(db_session, user)
        db_session.commit()
        navrh = _navrhni(client, project.id).json()
        assert _posli(client, user, project.id, navrh["id"]).status_code == 200

        r = _posli(client, user, project.id, navrh["id"])

        assert r.status_code == 409, r.text
        verzie = db_session.execute(select(Version).where(Version.project_id == project.id)).scalars().all()
        assert len(verzie) == 2, [v.version_number for v in verzie]
