"""DEV-151 — „Kde sme skončili“: a note at a version, why the development stands and what next, for the people.

Director 20.10.2026: „Aby v prípade, že vývoj projektu z nejakej príčiny nedokončíme (príde niečo dôležitejšie) mali
by sme presnú informáciu o tom, kde a ako pokračovať v projekte.“ To the plan: „Súhlasím“. A note carries its author
and time, everyone who may work on the project reads it, older notes stay, the Prehľad and Poradca show the newest —
and the agent that builds never gets it: it is no instruction.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select

from backend.core.security import get_current_user
from backend.db.models.foundation import User
from backend.db.models.pipeline import PipelineState
from backend.db.models.tasks import Epic, Feat, Task
from backend.db.models.version_note import VersionNote
from backend.db.models.versions import Version
from backend.main import app
from backend.schemas.version_note import NOTE_EMPTY, NOTE_TOO_LONG
from backend.services import claude_agent, orchestrator, standing
from tests.test_poradca_tools_db import world  # noqa: F401 — the fixture is used by name
from tests.test_slovak_without_diacritics_is_caught_in_the_task import ENGLISH_TEST, _agent, _build

STOPPED = "Čakáme na Tibora, ktorý doplní zoznam dodávateľov; potom pokračovať úlohou 1.1.4."


def _person(db, first="Zoltán", last="Rausch") -> User:
    name = f"u-{uuid.uuid4().hex[:8]}"
    user = User(
        username=name, email=f"{name}@example.com", password_hash="x", role="ri", first_name=first, last_name=last
    )
    db.add(user)
    db.flush()
    return user


def _version_of(db, owner: User) -> Version:
    from backend.db.models.projects import Project

    project = Project(
        name="NEX Manager",
        slug=f"p-{uuid.uuid4().hex[:8]}",
        type="standard",
        auth_mode="password",
        description="d",
        created_by=owner.id,
    )
    db.add(project)
    db.flush()
    version = Version(project_id=project.id, version_number="1.3.0")
    db.add(version)
    db.flush()
    db.add(
        PipelineState(
            version_id=version.id,
            flow_type="new_version",
            current_stage="programovanie",
            current_actor="ai_agent",
            status="paused",
            next_action="x",
        )
    )
    db.flush()
    db.commit()
    return version


def test_a_note_is_kept_with_its_author_and_time_and_the_older_ones_stay(client, db_session):
    owner = _person(db_session)
    version = _version_of(db_session, owner)
    app.dependency_overrides[get_current_user] = lambda: owner

    first = client.post(f"/api/v1/versions/{version.id}/notes", json={"text": "  Prvá poznámka.  "})
    second = client.post(f"/api/v1/versions/{version.id}/notes", json={"text": STOPPED})
    listed = client.get(f"/api/v1/versions/{version.id}/notes")

    assert first.status_code == 201, first.text
    assert (second.json()["text"], second.json()["author_name"]) == (STOPPED, "Zoltán Rausch")
    assert [n["text"] for n in listed.json()] == [STOPPED, "Prvá poznámka."]


def test_an_empty_or_long_note_is_refused_with_a_sentence(client, db_session):
    owner = _person(db_session)
    version = _version_of(db_session, owner)
    app.dependency_overrides[get_current_user] = lambda: owner

    empty = client.post(f"/api/v1/versions/{version.id}/notes", json={"text": "   "})
    long = client.post(f"/api/v1/versions/{version.id}/notes", json={"text": "x" * 601})

    assert (empty.status_code, long.status_code) == (422, 422)
    assert NOTE_EMPTY in empty.text and NOTE_TOO_LONG in long.text


def test_someone_who_may_not_work_on_the_project_neither_reads_nor_writes(client, db_session):
    owner, stranger = _person(db_session), _person(db_session, "Iný", "Človek")
    version = _version_of(db_session, owner)
    app.dependency_overrides[get_current_user] = lambda: stranger

    read = client.get(f"/api/v1/versions/{version.id}/notes")
    written = client.post(f"/api/v1/versions/{version.id}/notes", json={"text": "Cudzia poznámka."})

    assert read.status_code in (403, 404) and written.status_code in (403, 404)
    assert db_session.execute(select(VersionNote).where(VersionNote.version_id == version.id)).first() is None


def test_the_prehlad_shows_the_newest_note(db_session):
    owner = _person(db_session)
    version = _version_of(db_session, owner)
    db_session.add(VersionNote(version_id=version.id, author_id=owner.id, text="Staršia."))
    db_session.flush()
    db_session.add(VersionNote(version_id=version.id, author_id=owner.id, text=STOPPED))
    db_session.flush()

    shown = [p for p in standing.project_standing(db_session, owner) if p.open_versions]
    note = shown[0].open_versions[0].last_note

    assert (note.text, note.author_name) == (STOPPED, "Zoltán Rausch")


async def test_poradca_reads_the_newest_note(world):  # noqa: F811
    db, version = world["db"], world["version"]
    author = _person(db)
    db.add(
        PipelineState(
            version_id=version.id,
            flow_type="new_version",
            current_stage="programovanie",
            current_actor="ai_agent",
            status="paused",
            next_action="x",
        )
    )
    db.add(VersionNote(version_id=version.id, author_id=author.id, text=STOPPED))
    db.flush()

    out = await world["tools"].stavba({})

    assert f"Kde sme skončili (poznámka ľudí): „{STOPPED}“ — Zoltán Rausch, " in out


async def test_the_agent_that_builds_never_gets_the_note(db_session, monkeypatch, tmp_path):
    projects = tmp_path / "projects"
    monkeypatch.setattr(claude_agent, "PROJECTS_ROOT", projects)
    version, work = _build(db_session, projects)
    author = _person(db_session)
    db_session.add(VersionNote(version_id=version.id, author_id=author.id, text=STOPPED))
    db_session.flush()
    prompts = _agent(monkeypatch, work, [ENGLISH_TEST])

    await orchestrator.run_dispatch(db_session, version.id)

    assert prompts, "the build ran no turn — the guard would guard nothing"
    assert not [p for p in prompts if "Tibora" in p or "Kde sme skončili" in p]
    task = db_session.execute(
        select(Task)
        .join(Feat, Feat.id == Task.feat_id)
        .join(Epic, Epic.id == Feat.epic_id)
        .where(Epic.version_id == version.id)
    ).scalar_one()
    assert task.status == "done"
