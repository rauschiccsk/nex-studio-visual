"""Generátor poznámok ustúpi textu, ktorý niekto napísal (ICCINT-96).

**Čo sa dialo.** Zmerané 09.09.2026 na NEX Manager 1.1.0: AI Agent napísal zákaznícku poznámku
k vydaniu (2129 znakov), Manažér schválil fázu — a NEX Studio ju **v tej istej sekunde** prepísalo
svojím zoznamom z evidencie úloh (631 znakov) a nechalo ju v pracovnom strome nezapísanú.

Nezávislá previerka to našla ako rozrobenú prácu a stavbu **dvakrát** zablokovala. Agent to poslušne
vrátil a pri ďalšej bráne sa to prepísalo znova — bojoval s kokpitom a vyhrať nemohol. Práve preto sa
nález opakoval a tretia oprava tou istou cestou by nepomohla.

**Rozhodnutie Directora 09.09.2026:** *„generátor má ustúpiť, keď si projekt poznámky napísal sám.“*
Generovaný zoznam je záchranná sieť pre projekt, ktorý si poznámky nepíše — nie pán nad textom, ktorý
niekto napísal naschvál.
"""

from __future__ import annotations

import uuid as _uuid

from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.services import release_note_writer


def _version(db) -> tuple[Version, str]:
    suffix = _uuid.uuid4().hex[:8]
    from backend.db.models.foundation import User

    user = User(username=f"p_{suffix}", email=f"p_{suffix}@t.local", password_hash="x", role="ri")  # noqa: S106
    db.add(user)
    db.flush()
    project = Project(
        name=f"Poznamky {suffix}",
        slug=f"poznamky-{suffix}",
        type="standard",
        auth_mode="password",
        description="Poznámky k vydaniu.",
        created_by=user.id,
        source_path=None,
    )
    db.add(project)
    db.flush()
    version = Version(project_id=project.id, version_number="1.1.0", status="active")
    db.add(version)
    db.flush()
    return version, project.slug


def test_a_note_somebody_wrote_is_left_alone(db_session, tmp_path) -> None:
    """⚠️ Jadro ICCINT-96 — bez toho agent bojuje s kokpitom a stavba sa točí dokola."""
    version, _ = _version(db_session)
    napisane = "# Čo je nové vo v1.1.0\n\nAppku si pripnete na plochu ako program.\n"
    notes = release_note_writer.version_notes_dir(tmp_path, "1.1.0")
    notes.mkdir(parents=True, exist_ok=True)
    (notes / "RELEASE_NOTES.md").write_text(napisane, encoding="utf-8")

    written = release_note_writer.write_release_note(db_session, version.id, tmp_path)

    assert written is None, "generátor prepísal text, ktorý niekto napísal"
    assert (notes / "RELEASE_NOTES.md").read_text(encoding="utf-8") == napisane


def test_our_own_generated_note_is_still_refreshed(db_session, tmp_path) -> None:
    """Druhá strana: záchranná sieť musí ďalej fungovať. Projekt, ktorý si poznámky nepíše, ich má
    dostať — a pri ďalšej zmene aktuálne."""
    version, _ = _version(db_session)

    prve = release_note_writer.write_release_note(db_session, version.id, tmp_path)
    assert prve is not None
    assert release_note_writer.GENERATED_MARKER in prve.read_text(encoding="utf-8")

    druhe = release_note_writer.write_release_note(db_session, version.id, tmp_path)
    assert druhe is not None, "vlastnú generovanú poznámku prestal generátor obnovovať"


def test_a_generated_note_from_before_this_change_is_recognised(db_session, tmp_path) -> None:
    """Poznámky spred tejto zmeny podpis nemajú. Nesmú sa tváriť ako cudzí text, inak by generátor
    stíchol tam, kde stíchnuť nemá."""
    version, _ = _version(db_session)
    notes = release_note_writer.version_notes_dir(tmp_path, "1.1.0")
    notes.mkdir(parents=True, exist_ok=True)
    bez_podpisu = release_note_writer.render_release_note(db_session, version).replace(
        release_note_writer.GENERATED_MARKER, ""
    )
    (notes / "RELEASE_NOTES.md").write_text(bez_podpisu, encoding="utf-8")

    assert release_note_writer.write_release_note(db_session, version.id, tmp_path) is not None


def test_an_empty_file_is_not_treated_as_somebody_s_work(db_session, tmp_path) -> None:
    """Prázdny súbor nie je čo chrániť — a keby sa tak bral, projekt by ostal bez poznámok navždy."""
    version, _ = _version(db_session)
    notes = release_note_writer.version_notes_dir(tmp_path, "1.1.0")
    notes.mkdir(parents=True, exist_ok=True)
    (notes / "RELEASE_NOTES.md").write_text("   \n", encoding="utf-8")

    assert release_note_writer.write_release_note(db_session, version.id, tmp_path) is not None


def test_a_released_version_is_still_never_regenerated(db_session, tmp_path) -> None:
    """Poistka, ktorá tu bola pred touto zmenou, musí zostať: vydaná verzia je historický záznam."""
    version, _ = _version(db_session)
    version.status = "released"
    db_session.flush()

    assert release_note_writer.write_release_note(db_session, version.id, tmp_path) is None
