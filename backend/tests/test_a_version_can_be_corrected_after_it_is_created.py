"""Názov, číslo aj dátum verzie sa dajú opraviť aj po založení (ICCINT-100).

**Čo chýbalo.** Tie tri polia sa dali zadať iba pri zakladaní. Potom už nikde: stránka verzie ich len
vypisovala a ``updateVersion`` volalo jediné miesto v celom rozhraní — formulár novej verzie pri
druhom pokuse o uloženie. Director si názov NEX Manager 1.1.0 zmenil, zmena sa vtedy ticho zahodila
(ICCINT-91), a keď ju chcel po dokončení stavby opraviť, **nemal ako**: „Nemám možnosť (aspoň som
nenašiel) ako premenovať verziu.“ Nenašiel preto, že tam nebola.

Je to tá istá diera, ktorú pre PROJEKTY zavrel ICCINT-7. Hodnota, ktorú sa človek pomýli raz a nesie
ju navždy, je diera v samostatnosti kokpitu — opraviť sa dala len zásahom do databázy.

⚠️ **Číslo verzie je výnimka a má svoj dôvod.** Podľa neho sa volá priečinok s dokumentmi
(``docs/specs/versions/v<číslo>/``), takže premenovanie verzie, ktorá už svoje dokumenty má, by
kokpit odviedlo na prázdny priečinok a hotová práca by ostala ležať pod starým číslom. Zamyká sa
preto — ale **s vetou, prečo**, nie ticho: pole, ktoré sa dá písať a nič nerobí, je horšie než
zamknuté (to je celé jadro ICCINT-91).
"""

from __future__ import annotations

import uuid

import pytest

from backend.db.models.foundation import User
from backend.db.models.pipeline import PipelineState
from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.schemas.version import VersionUpdate
from backend.services import version as version_service


def _seed(db_session, tmp_path, monkeypatch, *, slug: str) -> Version:
    monkeypatch.setattr(version_service, "_PROJECTS_ROOT", tmp_path)
    owner = User(
        id=uuid.uuid4(),
        username=f"v-{uuid.uuid4().hex[:8]}",
        email=f"{uuid.uuid4().hex[:8]}@example.test",
        password_hash="x",  # noqa: S106 — nenulový stĺpec, nie tajomstvo
        role="ri",
    )
    project = Project(
        id=uuid.uuid4(),
        created_by=owner.id,
        name=f"projekt-{slug}",
        slug=slug,
        type="web",
        auth_mode="password",
        description="projekt na overenie úprav verzie",
        backend_port=10960,
        frontend_port=10961,
        db_port=10962,
    )
    version = Version(
        id=uuid.uuid4(),
        project_id=project.id,
        version_number="1.1.0",
        name="Inštalovateľná appka",
        status="planned",
    )
    db_session.add_all([owner, project, version])
    db_session.flush()
    return version


def test_the_name_can_be_corrected_after_the_build_is_finished(db_session, tmp_path, monkeypatch) -> None:
    """⚠️ Jadro nálezu: presne to, čo Director chcel a nemal ako urobiť."""
    version = _seed(db_session, tmp_path, monkeypatch, slug="premenuj")

    upravena = version_service.update(db_session, version.id, VersionUpdate(name="Inštalovateľná aplikácia PWA"))

    assert upravena.name == "Inštalovateľná aplikácia PWA"
    assert upravena.version_number == "1.1.0", "premenovanie sa nemalo dotknúť čísla verzie"


def test_the_number_can_still_be_fixed_while_nothing_stands_on_it(db_session, tmp_path, monkeypatch) -> None:
    """Preklep v čísle sa musí dať opraviť — dovtedy, kým podľa neho nič nevzniklo."""
    version = _seed(db_session, tmp_path, monkeypatch, slug="oprav-cislo")

    assert version_service.version_number_lock_reason(db_session, version.id) is None
    upravena = version_service.update(db_session, version.id, VersionUpdate(version_number="1.2.0"))

    assert upravena.version_number == "1.2.0"


def test_the_number_locks_once_the_documents_exist(db_session, tmp_path, monkeypatch) -> None:
    """⚠️ Premenovanie verzie, ktorá už dokumenty má, by ich nechalo ležať pod starým číslom."""
    version = _seed(db_session, tmp_path, monkeypatch, slug="ma-dokumenty")
    (tmp_path / "ma-dokumenty" / "docs" / "specs" / "versions" / "v1.1.0").mkdir(parents=True)

    dovod = version_service.version_number_lock_reason(db_session, version.id)

    assert dovod is not None, "číslo sa dá premenovať aj vtedy, keď podľa neho už ležia dokumenty"
    assert "priečinok" in dovod, f"dôvod nehovorí, o čo ide: {dovod!r}"
    with pytest.raises(ValueError, match="priečinok"):
        version_service.update(db_session, version.id, VersionUpdate(version_number="9.9.9"))


def test_the_number_locks_once_the_build_has_started(db_session, tmp_path, monkeypatch) -> None:
    """Bežiaci agent sa na to číslo odvoláva — meniť ho pod ním sa nesmie."""
    version = _seed(db_session, tmp_path, monkeypatch, slug="bezi-stavba")
    db_session.add(
        PipelineState(
            version_id=version.id,
            flow_type="new_version",
            current_stage="programovanie",
            current_actor="ai_agent",
            status="agent_working",
            mode=None,
        )
    )
    db_session.flush()

    dovod = version_service.version_number_lock_reason(db_session, version.id)

    assert dovod is not None, "číslo sa dá meniť aj počas bežiacej stavby"
    assert "stavba" in dovod, f"dôvod nehovorí, o čo ide: {dovod!r}"


def test_the_lock_never_stops_the_name_or_the_date(db_session, tmp_path, monkeypatch) -> None:
    """⚠️ Zámok sa smie týkať IBA čísla.

    Bez tohto tvrdenia by stráže vyššie prešli aj vtedy, keby zámok zamkol celú verziu — a Director by
    bol presne tam, kde bol: s názvom, ktorý sa nedá opraviť.
    """
    version = _seed(db_session, tmp_path, monkeypatch, slug="zamok-len-cislo")
    (tmp_path / "zamok-len-cislo" / "docs" / "specs" / "versions" / "v1.1.0").mkdir(parents=True)

    upravena = version_service.update(
        db_session, version.id, VersionUpdate(name="Nový názov", target_date="2026-12-31")
    )

    assert upravena.name == "Nový názov"
    assert str(upravena.target_date) == "2026-12-31"


def test_the_reason_is_a_sentence_not_a_flag(db_session, tmp_path, monkeypatch) -> None:
    """Zamknuté pole bez dôvodu je pole, ktoré nejde — a človek hľadá chybu u seba."""
    version = _seed(db_session, tmp_path, monkeypatch, slug="dovod-vetou")
    (tmp_path / "dovod-vetou" / "docs" / "specs" / "versions" / "v1.1.0").mkdir(parents=True)

    dovod = version_service.version_number_lock_reason(db_session, version.id)

    assert isinstance(dovod, str) and len(dovod.split()) >= 8, f"dôvod nie je vysvetlenie, ale odkaz na nič: {dovod!r}"


def test_the_endpoints_are_mounted_where_the_cockpit_calls_them() -> None:
    """Cesta napísaná inak, než ju volá obrazovka, je 404 — a to sa už raz stalo (ICCINT-78)."""
    from backend.main import app

    cesty = {getattr(r, "path", "") for r in app.routes}

    assert "/api/v1/versions/{version_id}/nastavenia" in cesty
    assert "/api/v1/versions/{version_id}" in cesty
