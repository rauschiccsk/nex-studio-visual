"""Rozhranie Poradcu — prístup a tvar odpovedí (ICCINT-167).

Prístup ako všade v kokpite: k projektu vlastník alebo účet admin; k rozhovoru jeho autor alebo admin.
Cudzí rozhovor je 404 — o jeho existencii sa iný človek nedozvie. Beh otázky tu nahrádza atrapa;
skutočný beh skúša ``test_poradca_runner``.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.routes import poradca as poradca_routes
from backend.core.security import get_current_user
from backend.db.models.foundation import User
from backend.db.models.poradca import PoradcaMessage
from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.db.session import get_db
from backend.schemas.poradca import PoradcaStatus
from backend.services.poradca import runner


def _user(db: Any, username: str | None = None) -> User:
    u = User(
        username=username or f"u_{uuid.uuid4().hex[:8]}",
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        password_hash="x",
        role="ri",
    )
    db.add(u)
    db.flush()
    return u


def _project(db: Any, owner: User) -> Project:
    p = Project(
        name=f"Demo {uuid.uuid4().hex[:8]}",
        slug=f"demo-{uuid.uuid4().hex[:6]}",
        type="standard",
        auth_mode="password",
        description="d",
        created_by=owner.id,
    )
    db.add(p)
    db.flush()
    return p


def _client(db: Any, user: User) -> TestClient:
    app = FastAPI()
    app.include_router(poradca_routes.router, prefix="/api/v1/poradca")

    def _db():
        yield db

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


@pytest.fixture(autouse=True)
def _fake_run(monkeypatch):
    """Otázka sa uloží ako pri skutočnom behu, ale odpoveď hneď skončí — bez kontajnera."""
    calls: list[str] = []

    def _ask(db, conversation, question, user):
        running = (
            db.query(PoradcaMessage)
            .filter(PoradcaMessage.conversation_id == conversation.id, PoradcaMessage.status == "running")
            .first()
        )
        if running is not None:
            raise runner.PoradcaBusy("V tomto rozhovore už Poradca odpovedá.")
        calls.append(question)
        human = PoradcaMessage(conversation_id=conversation.id, author="human", content=question, status="done")
        answer = PoradcaMessage(
            conversation_id=conversation.id,
            author="poradca",
            content="Odpoveď.",
            status="done",
            usage={"input_tokens": 1000, "output_tokens": 100, "model": "claude-opus-test"},
        )
        db.add_all([human, answer])
        db.commit()
        return human, answer

    monkeypatch.setattr(runner, "ask", _ask)
    monkeypatch.setattr(
        poradca_routes.readiness,
        "status",
        lambda db: PoradcaStatus(ready=True, problems=[], running=0, max_concurrent=3),
    )
    return calls


def test_owner_starts_a_conversation_and_reads_it(db_session):
    owner = _user(db_session)
    project = _project(db_session, owner)
    c = _client(db_session, owner)
    r = c.post(f"/api/v1/poradca/projects/{project.slug}/conversations", json={"question": "Prečo agent stojí?"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["title"] == "Prečo agent stojí?" and body["version_id"] is None
    assert [m["author"] for m in body["messages"]] == ["human", "poradca"]
    answer = body["messages"][1]
    assert answer["model"] == "claude-opus-test" and answer["input_tokens"] == 1000
    listed = c.get(f"/api/v1/poradca/projects/{project.slug}/conversations").json()
    assert [x["id"] for x in listed] == [body["id"]]
    assert c.get(f"/api/v1/poradca/conversations/{body['id']}").status_code == 200


def test_someone_elses_project_is_refused(db_session):
    owner = _user(db_session)
    other = _user(db_session)
    project = _project(db_session, owner)
    r = _client(db_session, other).post(
        f"/api/v1/poradca/projects/{project.slug}/conversations", json={"question": "x"}
    )
    assert r.status_code == 403


def test_someone_elses_conversation_does_not_exist_for_you_but_admin_sees_it(db_session):
    owner = _user(db_session)
    project = _project(db_session, owner)
    cid = (
        _client(db_session, owner)
        .post(f"/api/v1/poradca/projects/{project.slug}/conversations", json={"question": "x"})
        .json()["id"]
    )
    stranger = _user(db_session)
    assert _client(db_session, stranger).get(f"/api/v1/poradca/conversations/{cid}").status_code == 404
    admin = db_session.query(User).filter(User.username == "admin").first() or _user(db_session, "admin")
    assert _client(db_session, admin).get(f"/api/v1/poradca/conversations/{cid}").status_code == 200
    listed = _client(db_session, admin).get(f"/api/v1/poradca/projects/{project.slug}/conversations").json()
    assert cid in [x["id"] for x in listed]


def test_scope_can_switch_between_a_version_and_the_whole_project(db_session):
    owner = _user(db_session)
    project = _project(db_session, owner)
    version = Version(project_id=project.id, version_number="1.2.0")
    db_session.add(version)
    db_session.flush()
    c = _client(db_session, owner)
    cid = c.post(
        f"/api/v1/poradca/projects/{project.slug}/conversations",
        json={"question": "x", "version_id": str(version.id)},
    ).json()["id"]
    r = c.patch(f"/api/v1/poradca/conversations/{cid}", json={"version_id": None})
    assert r.status_code == 200 and r.json()["version_id"] is None
    r = c.patch(f"/api/v1/poradca/conversations/{cid}", json={"version_id": str(version.id)})
    assert r.json()["version_number"] == "1.2.0"


def test_a_version_of_another_project_is_refused(db_session):
    owner = _user(db_session)
    project = _project(db_session, owner)
    foreign = Version(project_id=_project(db_session, owner).id, version_number="9.9.9")
    db_session.add(foreign)
    db_session.flush()
    r = _client(db_session, owner).post(
        f"/api/v1/poradca/projects/{project.slug}/conversations",
        json={"question": "x", "version_id": str(foreign.id)},
    )
    assert r.status_code == 422


def test_question_while_one_is_running_is_409(db_session):
    owner = _user(db_session)
    project = _project(db_session, owner)
    c = _client(db_session, owner)
    cid = c.post(f"/api/v1/poradca/projects/{project.slug}/conversations", json={"question": "x"}).json()["id"]
    db_session.add(PoradcaMessage(conversation_id=uuid.UUID(cid), author="poradca", content="", status="running"))
    db_session.commit()
    assert c.post(f"/api/v1/poradca/conversations/{cid}/messages", json={"question": "y"}).status_code == 409
    listed = c.get(f"/api/v1/poradca/projects/{project.slug}/conversations").json()
    assert listed[0]["running"] is True


def test_not_ready_says_why_instead_of_running(db_session, monkeypatch, _fake_run):
    owner = _user(db_session)
    project = _project(db_session, owner)
    monkeypatch.setattr(
        poradca_routes.readiness,
        "status",
        lambda db: PoradcaStatus(ready=False, problems=["chýba obraz"], running=0, max_concurrent=3),
    )
    r = _client(db_session, owner).post(
        f"/api/v1/poradca/projects/{project.slug}/conversations", json={"question": "x"}
    )
    assert r.status_code == 503 and "chýba obraz" in r.json()["detail"]


def test_stopping_an_answer_that_is_not_running_is_409(db_session):
    owner = _user(db_session)
    project = _project(db_session, owner)
    c = _client(db_session, owner)
    body = c.post(f"/api/v1/poradca/projects/{project.slug}/conversations", json={"question": "x"}).json()
    answer_id = body["messages"][1]["id"]
    assert c.post(f"/api/v1/poradca/messages/{answer_id}/stop").status_code == 409
    assert _client(db_session, _user(db_session)).post(f"/api/v1/poradca/messages/{answer_id}/stop").status_code == 404


def test_empty_or_huge_question_is_refused(db_session):
    owner = _user(db_session)
    project = _project(db_session, owner)
    c = _client(db_session, owner)
    url = f"/api/v1/poradca/projects/{project.slug}/conversations"
    assert c.post(url, json={"question": ""}).status_code == 422
    assert c.post(url, json={"question": "x" * 20_001}).status_code == 422


# ── „Založiť novú verziu z tejto požiadavky" ────────────────────────────────────


def _answer(db, conversation_id, content, status="done"):
    msg = PoradcaMessage(conversation_id=conversation_id, author="poradca", content=content, status=status)
    db.add(msg)
    db.commit()
    return msg


def test_new_version_from_the_request_block_is_a_draft_and_only_once(db_session):
    owner = _user(db_session)
    project = _project(db_session, owner)
    c = _client(db_session, owner)
    cid = c.post(f"/api/v1/poradca/projects/{project.slug}/conversations", json={"question": "x"}).json()["id"]
    msg = _answer(
        db_session,
        uuid.UUID(cid),
        "Odporúčam novú verziu.\n<poziadavka-na-novu-verziu>\nPridať export faktúr do CSV.\n"
        "</poziadavka-na-novu-verziu>\n<pokyn-pre-agenta>Oprav test_login.</pokyn-pre-agenta>",
    )
    detail = c.get(f"/api/v1/poradca/conversations/{cid}").json()
    last = detail["messages"][-1]
    assert last["new_version_request"] == "Pridať export faktúr do CSV."
    assert last["instruction"] == "Oprav test_login."

    first = c.post(f"/api/v1/poradca/messages/{msg.id}/new-version")
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["created"] is True and body["project_slug"] == project.slug
    version = db_session.get(Version, uuid.UUID(body["version_id"]))
    assert version.project_id == project.id and version.status == "planned"
    again = c.post(f"/api/v1/poradca/messages/{msg.id}/new-version").json()
    assert again["created"] is False and again["version_id"] == body["version_id"]


def test_no_request_block_no_version(db_session):
    owner = _user(db_session)
    project = _project(db_session, owner)
    c = _client(db_session, owner)
    cid = c.post(f"/api/v1/poradca/projects/{project.slug}/conversations", json={"question": "x"}).json()["id"]
    plain = _answer(db_session, uuid.UUID(cid), "Len vysvetlenie, nič na zmenu.")
    assert c.post(f"/api/v1/poradca/messages/{plain.id}/new-version").status_code == 422
    running = _answer(
        db_session,
        uuid.UUID(cid),
        "<poziadavka-na-novu-verziu>Ešte nedopísané, ale dlhé dosť.</poziadavka-na-novu-verziu>",
        status="running",
    )
    assert c.post(f"/api/v1/poradca/messages/{running.id}/new-version").status_code == 422
    stranger = _client(db_session, _user(db_session))
    assert stranger.post(f"/api/v1/poradca/messages/{plain.id}/new-version").status_code == 404
