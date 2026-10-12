"""DEV-89 — quick notes: only the author reads them, a change made elsewhere is never overwritten without a word, and a
note becomes exactly one request in a project's backlog.

12.10.2026 the Director: a plain text editor for notes jotted down quickly, kept until he sorts them out properly.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select

from backend.core.security import get_current_user
from backend.db.models.backlog import BacklogItem
from backend.db.models.foundation import User
from backend.db.models.projects import Project
from backend.main import app

URL = "/api/v1/notes"


def _user(db, name, username=None):
    suffix = uuid.uuid4().hex[:8]
    user = User(username=username or f"{name}_{suffix}", email=f"{name}_{suffix}@t.sk", password_hash="x", role="ri")
    db.add(user)
    db.flush()
    return user


@pytest.fixture
def people(db_session):
    db = db_session
    maria, tibor = _user(db, "maria"), _user(db, "tibor")
    project = Project(
        name="Faktúry",
        slug=f"faktury-{uuid.uuid4().hex[:6]}",
        type="standard",
        auth_mode="password",
        description="",
        created_by=maria.id,
    )
    db.add(project)
    db.flush()
    return db, maria, tibor, project


def _as(db, user):
    user_id = user.id
    app.dependency_overrides[get_current_user] = lambda: db.get(User, user_id)


def _new(client, body=""):
    response = client.post(URL, json={"body": body})
    assert response.status_code == 201, response.text
    return response.json()


def test_a_note_is_kept_on_the_server_and_named_by_its_first_line(client, people):
    db, maria, _tibor, _project = people
    _as(db, maria)
    note = _new(client)

    saved = client.put(f"{URL}/{note['id']}", json={"body": "\n  Zavolať Tiborovi  \nohľadom faktúr", "revision": 1})

    assert saved.status_code == 200
    assert (saved.json()["title"], saved.json()["revision"]) == ("Zavolať Tiborovi", 2)
    db.expire_all()  # another computer: only what the server kept
    listed = client.get(URL).json()
    assert [(n["id"], n["body"]) for n in listed] == [(note["id"], "\n  Zavolať Tiborovi  \nohľadom faktúr")]


def test_another_person_does_not_see_read_change_or_delete_it_not_even_its_existence(client, people):
    db, maria, tibor, project = people
    _as(db, maria)
    note = _new(client, "Moje súkromné")
    client.post(f"{URL}/{note['id']}/resolve", json={"resolved": True})

    _as(db, tibor)
    assert client.get(URL).json() == []
    answers = [
        client.put(f"{URL}/{note['id']}", json={"body": "cudzie", "revision": 1}),
        client.post(f"{URL}/{note['id']}/resolve", json={"resolved": False}),
        client.delete(f"{URL}/{note['id']}"),
    ]
    assert [(a.status_code, a.json()["detail"]) for a in answers] == [(404, "Poznámka sa nenašla.")] * 3

    _as(db, maria)
    still = client.get(URL).json()[0]
    assert (still["body"], still["resolved_at"] is not None) == ("Moje súkromné", True)


def test_the_admin_account_has_no_way_in_either(client, people):
    db, maria, _tibor, _project = people
    admin = db.execute(select(User).where(User.username == "admin")).scalar_one_or_none() or _user(db, "admin", "admin")
    _as(db, maria)
    note = _new(client, "Len moje")

    _as(db, admin)

    assert client.get(URL).json() == []
    assert client.put(f"{URL}/{note['id']}", json={"body": "x", "revision": 1}).status_code == 404


def test_a_change_written_over_an_older_revision_is_refused_with_the_note_as_it_is_now(client, people):
    db, maria, _tibor, _project = people
    _as(db, maria)
    note = _new(client, "Začiatok")
    client.put(f"{URL}/{note['id']}", json={"body": "Z telefónu", "revision": 1})  # the phone saved first

    stale = client.put(f"{URL}/{note['id']}", json={"body": "Z počítača", "revision": 1})

    assert stale.status_code == 409
    assert stale.json()["detail"] == "Poznámka sa medzitým zmenila na inom zariadení."
    assert (stale.json()["note"]["body"], stale.json()["note"]["revision"]) == ("Z telefónu", 2)
    assert client.get(URL).json()[0]["body"] == "Z telefónu", "the other device's text was overwritten"
    # The person chose to keep the computer's text — written over the revision they were shown.
    assert client.put(f"{URL}/{note['id']}", json={"body": "Z počítača", "revision": 2}).json()["revision"] == 3


def test_resolved_stays_until_deleted_on_purpose_and_an_open_note_is_never_deleted(client, people):
    db, maria, _tibor, _project = people
    _as(db, maria)
    note = _new(client, "Hotovo")

    refused = client.delete(f"{URL}/{note['id']}")
    assert (refused.status_code, refused.json()["detail"]) == (
        409,
        "Natrvalo sa maže len vyriešená poznámka — najprv ju označ ako vyriešenú.",
    )
    assert client.post(f"{URL}/{note['id']}/resolve", json={"resolved": True}).json()["resolved_at"]
    assert client.post(f"{URL}/{note['id']}/resolve", json={"resolved": False}).json()["resolved_at"] is None
    client.post(f"{URL}/{note['id']}/resolve", json={"resolved": True})

    assert client.delete(f"{URL}/{note['id']}").status_code == 204
    assert client.get(URL).json() == []


def test_to_backlog_makes_exactly_one_request_and_resolves_the_note(client, people):
    db, maria, _tibor, project = people
    _as(db, maria)
    note = _new(client, "\nExport do CSV\n\nZákazník chce stĺpce ako v Exceli.\nAj s DPH.")
    before = db.execute(
        select(func.count()).select_from(BacklogItem).where(BacklogItem.project_id == project.id)
    ).scalar()

    done = client.post(f"{URL}/{note['id']}/to-backlog", json={"project_slug": project.slug})

    assert done.status_code == 200, done.text
    body = done.json()
    item = db.get(BacklogItem, uuid.UUID(body["backlog"]["id"]))
    assert (item.project_id, item.title, item.description, item.status) == (
        project.id,
        "Export do CSV",
        "Zákazník chce stĺpce ako v Exceli.\nAj s DPH.",
        "open",
    )
    assert body["resolved_at"] is not None
    assert (body["backlog"]["number"], body["backlog"]["project_slug"], body["backlog"]["project_name"]) == (
        item.number,
        project.slug,
        "Faktúry",
    )

    again = client.post(f"{URL}/{note['id']}/to-backlog", json={"project_slug": project.slug})
    assert (again.status_code, again.json()["detail"]) == (
        409,
        f"Z tejto poznámky už vznikla požiadavka REQ-{item.number}.",
    )
    after = db.execute(
        select(func.count()).select_from(BacklogItem).where(BacklogItem.project_id == project.id)
    ).scalar()
    assert after == before + 1


def test_to_backlog_refuses_an_empty_note_and_a_project_the_person_may_not_touch(client, people):
    db, maria, tibor, project = people
    _as(db, maria)
    empty = _new(client, "   \n  ")
    refused = client.post(f"{URL}/{empty['id']}/to-backlog", json={"project_slug": project.slug})
    assert (refused.status_code, refused.json()["detail"]) == (
        409,
        "Poznámka je prázdna — nie je z čoho založiť požiadavku.",
    )

    _as(db, tibor)
    own = _new(client, "Tiborova požiadavka")
    assert client.post(f"{URL}/{own['id']}/to-backlog", json={"project_slug": project.slug}).status_code == 403
    assert client.get(URL).json()[0]["backlog"] is None


def test_without_signing_in_there_are_no_notes(client):
    app.dependency_overrides.pop(get_current_user, None)
    assert client.get(URL).status_code == 401
