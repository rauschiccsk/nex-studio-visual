"""Rozhranie Poradcu — prístup a tvar odpovedí (ICCINT-167).

Prístup ako všade v kokpite: k projektu vlastník alebo účet admin; k rozhovoru jeho autor alebo admin.
Cudzí rozhovor je 404 — o jeho existencii sa iný človek nedozvie. Beh otázky tu nahrádza atrapa;
skutočný beh skúša ``test_poradca_runner``.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from backend.api.routes import poradca as poradca_routes
from backend.core.security import get_current_user
from backend.db.models.backlog import BacklogItem
from backend.db.models.foundation import User
from backend.db.models.poradca import PoradcaConversation, PoradcaMessage
from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.db.session import get_db
from backend.schemas.poradca import PoradcaStatus
from backend.services import metrics
from backend.services.poradca import runner, sandbox


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


# ── „Uložiť do Zásobníka" (DEV-29) ───────────────────────────────────────────────
#
# Director 08.10.2026: „O verziách rozhodujem ja. Treba, aby zapísal len do zásobníku. To je všetko." The button
# used to mint a whole new version (the next number, with the request as its brief) — on NEX Inbox 1.7.0 it would
# have taken 1.8.0, which he had set aside for something else.


def _answer(db, conversation_id, content, status="done"):
    msg = PoradcaMessage(conversation_id=conversation_id, author="poradca", content=content, status=status)
    db.add(msg)
    db.commit()
    return msg


def _versions(db, project) -> int:
    return db.execute(select(func.count()).select_from(Version).where(Version.project_id == project.id)).scalar_one()


def test_the_request_goes_to_the_backlog_only_never_a_version_and_only_once(db_session):
    owner = _user(db_session)
    project = _project(db_session, owner)
    c = _client(db_session, owner)
    cid = c.post(f"/api/v1/poradca/projects/{project.slug}/conversations", json={"question": "x"}).json()["id"]
    msg = _answer(
        db_session,
        uuid.UUID(cid),
        "Do tejto stavby to nepatrí.\n<poziadavka-do-zasobnika>\nPridať export faktúr do CSV.\n\nKvôli účtovníčke.\n"
        "</poziadavka-do-zasobnika>\n<pokyn-pre-agenta>Oprav test_login.</pokyn-pre-agenta>",
    )
    last = c.get(f"/api/v1/poradca/conversations/{cid}").json()["messages"][-1]
    assert last["backlog_request"] == "Pridať export faktúr do CSV.\n\nKvôli účtovníčke."
    assert last["instruction"] == "Oprav test_login."
    assert last["captured_backlog_number"] is None
    versions_before = _versions(db_session, project)

    first = c.post(f"/api/v1/poradca/messages/{msg.id}/backlog")
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["created"] is True and body["project_slug"] == project.slug
    item = db_session.get(BacklogItem, uuid.UUID(body["backlog_item_id"]))
    assert item.project_id == project.id and item.number == body["number"]
    assert item.title == "Pridať export faktúr do CSV."
    assert item.description == "Pridať export faktúr do CSV.\n\nKvôli účtovníčke."
    assert item.version_id is None  # assigned to no version — the Director decides that
    assert _versions(db_session, project) == versions_before  # and no version came of it

    again = c.post(f"/api/v1/poradca/messages/{msg.id}/backlog").json()
    assert again["created"] is False and again["backlog_item_id"] == body["backlog_item_id"]
    assert (
        db_session.execute(
            select(func.count()).select_from(BacklogItem).where(BacklogItem.project_id == project.id)
        ).scalar_one()
        == 1
    )
    shown = c.get(f"/api/v1/poradca/conversations/{cid}").json()["messages"][-1]
    assert shown["captured_backlog_number"] == body["number"]


def test_an_answer_written_before_the_change_is_saved_to_the_backlog_too(db_session):
    """Answers from before DEV-29 carry the old block — e.g. the request about credit notes on 1.7.0."""
    owner = _user(db_session)
    project = _project(db_session, owner)
    c = _client(db_session, owner)
    cid = c.post(f"/api/v1/poradca/projects/{project.slug}/conversations", json={"question": "x"}).json()["id"]
    msg = _answer(
        db_session,
        uuid.UUID(cid),
        "<poziadavka-na-novu-verziu>Riadne spracovanie dobropisov.</poziadavka-na-novu-verziu>",
    )
    versions_before = _versions(db_session, project)
    body = c.post(f"/api/v1/poradca/messages/{msg.id}/backlog").json()
    assert db_session.get(BacklogItem, uuid.UUID(body["backlog_item_id"])).title == "Riadne spracovanie dobropisov."
    assert _versions(db_session, project) == versions_before


def test_no_request_block_nothing_is_saved(db_session):
    owner = _user(db_session)
    project = _project(db_session, owner)
    c = _client(db_session, owner)
    cid = c.post(f"/api/v1/poradca/projects/{project.slug}/conversations", json={"question": "x"}).json()["id"]
    plain = _answer(db_session, uuid.UUID(cid), "Len vysvetlenie, nič na zmenu.")
    assert c.post(f"/api/v1/poradca/messages/{plain.id}/backlog").status_code == 422
    running = _answer(
        db_session,
        uuid.UUID(cid),
        "<poziadavka-do-zasobnika>Ešte nedopísané, ale dlhé dosť.</poziadavka-do-zasobnika>",
        status="running",
    )
    assert c.post(f"/api/v1/poradca/messages/{running.id}/backlog").status_code == 422
    stranger = _client(db_session, _user(db_session))
    assert stranger.post(f"/api/v1/poradca/messages/{plain.id}/backlog").status_code == 404


def test_there_is_no_way_left_to_mint_a_version_from_poradca(db_session):
    owner = _user(db_session)
    project = _project(db_session, owner)
    c = _client(db_session, owner)
    cid = c.post(f"/api/v1/poradca/projects/{project.slug}/conversations", json={"question": "x"}).json()["id"]
    msg = _answer(
        db_session, uuid.UUID(cid), "<poziadavka-do-zasobnika>Pridať export do CSV.</poziadavka-do-zasobnika>"
    )
    assert c.post(f"/api/v1/poradca/messages/{msg.id}/new-version").status_code in (404, 405)


def test_context_lists_versions_with_build_state_and_where_an_instruction_can_go(db_session):
    from backend.db.models.pipeline import PipelineState

    owner = _user(db_session)
    project = _project(db_session, owner)
    states = {
        "1.0.0": None,
        "1.1.0": ("done", "done", None),
        "1.2.0": ("programovanie", "blocked", "framework_issue"),
        "1.3.0": ("programovanie", "agent_working", None),
    }
    for number, st in states.items():
        v = Version(project_id=project.id, version_number=number)
        db_session.add(v)
        db_session.flush()
        if st:
            db_session.add(
                PipelineState(
                    version_id=v.id,
                    flow_type="new_version",
                    current_stage=st[0],
                    current_actor="ai_agent",
                    status=st[1],
                    block_reason=st[2],
                )
            )
    db_session.flush()
    body = _client(db_session, owner).get(f"/api/v1/poradca/projects/{project.slug}/context").json()
    by = {v["version_number"]: v for v in body["versions"]}
    assert by["1.3.0"]["instruction_open"] is True and by["1.3.0"]["stage"] == "programovanie"
    assert by["1.0.0"]["instruction_open"] is False and "nezačala" in by["1.0.0"]["instruction_closed_reason"]
    assert "novej verzie" in by["1.1.0"]["instruction_closed_reason"]
    assert "opravu kokpitu" in by["1.2.0"]["instruction_closed_reason"]
    assert (
        _client(db_session, _user(db_session)).get(f"/api/v1/poradca/projects/{project.slug}/context").status_code
        == 403
    )


# ── premenovanie a vymazanie (Director 05.10.2026: „chýba mi premenovanie a vymazanie rozhovoru") ──

_LONG_AGO = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _start(c: TestClient, project: Project, question: str = "Prečo agent stojí?") -> str:
    r = c.post(f"/api/v1/poradca/projects/{project.slug}/conversations", json={"question": question})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _age(db: Any, cid: str) -> None:
    """Posledná otázka „dávno" — v jednej transakcii skúšky dá ``now()`` stále ten istý čas, takže bez toho
    by posun ``updated_at`` úpravou nebolo vidieť."""
    db.query(PoradcaConversation).filter(PoradcaConversation.id == uuid.UUID(cid)).update({"updated_at": _LONG_AGO})
    db.commit()


def _updated_at(db: Any, cid: str) -> datetime:
    db.expire_all()
    return db.get(PoradcaConversation, uuid.UUID(cid)).updated_at


def test_rename_changes_the_title_and_the_conversation_keeps_its_place(db_session):
    owner = _user(db_session)
    project = _project(db_session, owner)
    c = _client(db_session, owner)
    older, newer = _start(c, project, "Prvá"), _start(c, project, "Druhá")
    _age(db_session, older)

    r = c.put(f"/api/v1/poradca/conversations/{older}/title", json={"title": "  Prečo   padá\n zostavenie  "})
    assert r.status_code == 200, r.text
    assert r.json()["title"] == "Prečo padá zostavenie"
    # Poradie určuje posledná otázka, nie úprava názvu.
    assert _updated_at(db_session, older) == _LONG_AGO
    listed = c.get(f"/api/v1/poradca/projects/{project.slug}/conversations").json()
    assert [x["id"] for x in listed] == [newer, older]
    assert listed[1]["title"] == "Prečo padá zostavenie"


def test_scope_change_does_not_move_the_conversation_either(db_session):
    owner = _user(db_session)
    project = _project(db_session, owner)
    c = _client(db_session, owner)
    cid = _start(c, project)
    _age(db_session, cid)
    assert c.patch(f"/api/v1/poradca/conversations/{cid}", json={"version_id": None}).status_code == 200
    assert _updated_at(db_session, cid) == _LONG_AGO


@pytest.mark.parametrize("title", ["", "   \n  ", "x" * 201])
def test_empty_or_too_long_title_is_refused(db_session, title):
    owner = _user(db_session)
    project = _project(db_session, owner)
    c = _client(db_session, owner)
    cid = _start(c, project)
    assert c.put(f"/api/v1/poradca/conversations/{cid}/title", json={"title": title}).status_code == 422


def test_only_the_author_or_admin_renames_or_deletes(db_session):
    owner = _user(db_session)
    project = _project(db_session, owner)
    cid = _start(_client(db_session, owner), project)
    stranger = _client(db_session, _user(db_session))
    assert stranger.put(f"/api/v1/poradca/conversations/{cid}/title", json={"title": "cudzí"}).status_code == 404
    assert stranger.delete(f"/api/v1/poradca/conversations/{cid}").status_code == 404
    admin = db_session.query(User).filter(User.username == "admin").first() or _user(db_session, "admin")
    r = _client(db_session, admin).put(f"/api/v1/poradca/conversations/{cid}/title", json={"title": "Admin"})
    assert r.status_code == 200 and r.json()["title"] == "Admin"


def test_delete_wipes_what_was_said_and_its_disk_record_but_keeps_the_cost(db_session, tmp_path, monkeypatch):
    monkeypatch.setattr(sandbox.settings, "poradca_data_dir", str(tmp_path))
    owner = _user(db_session)
    project = _project(db_session, owner)
    c = _client(db_session, owner)
    cid = _start(c, project, "Aké heslo má databáza?")
    kept = _start(c, project, "Iný rozhovor")
    record = sandbox.session_dir(uuid.UUID(cid))
    (record / "subagents").mkdir(parents=True)
    (record / "subagents" / "x.jsonl").write_text('{"tool_result": "obsah súboru"}')
    answer = (
        db_session.query(PoradcaMessage)
        .filter(PoradcaMessage.conversation_id == uuid.UUID(cid), PoradcaMessage.author == "poradca")
        .one()
    )
    answer.steps = [{"tool": "Read", "target": "backend/x.py"}]
    answer.error = "chyba s textom"
    db_session.commit()
    before = metrics.compute_project_metrics(db_session, project)

    assert c.delete(f"/api/v1/poradca/conversations/{cid}").status_code == 204

    # Pre rozhranie rozhovor neexistuje — nikde.
    assert c.get(f"/api/v1/poradca/conversations/{cid}").status_code == 404
    assert [x["id"] for x in c.get(f"/api/v1/poradca/projects/{project.slug}/conversations").json()] == [kept]
    assert c.post(f"/api/v1/poradca/conversations/{cid}/messages", json={"question": "y"}).status_code == 404
    assert c.put(f"/api/v1/poradca/conversations/{cid}/title", json={"title": "z"}).status_code == 404
    assert c.patch(f"/api/v1/poradca/conversations/{cid}", json={"version_id": None}).status_code == 404
    assert c.delete(f"/api/v1/poradca/conversations/{cid}").status_code == 404
    assert c.post(f"/api/v1/poradca/messages/{answer.id}/backlog").status_code == 404
    # Text, kroky, chyba, názov aj záznam na disku sú preč (aj z koša)…
    assert not record.exists()
    assert not any(sandbox.trash_dir().iterdir())
    db_session.expire_all()
    conversation = db_session.get(PoradcaConversation, uuid.UUID(cid))
    assert conversation.deleted_at is not None and conversation.title == runner.DELETED_TITLE
    messages = db_session.query(PoradcaMessage).filter(PoradcaMessage.conversation_id == uuid.UUID(cid)).all()
    assert len(messages) == 2
    assert all(m.content == "" and m.steps == [] and m.error is None for m in messages)
    # …cena ostala: Náklady projektu ukazujú to isté ako pred vymazaním.
    after = metrics.compute_project_metrics(db_session, project)
    row = lambda result: next(r for r in result.rows if r.kind == "poradca")  # noqa: E731
    assert (row(after).input_tokens, row(after).output_tokens, row(after).turns) == (
        row(before).input_tokens,
        row(before).output_tokens,
        row(before).turns,
    )
    assert row(after).input_tokens == 2000  # obe odpovede atrapy po 1000


def test_delete_while_the_answer_runs_is_409_and_changes_nothing(db_session, tmp_path, monkeypatch):
    monkeypatch.setattr(sandbox.settings, "poradca_data_dir", str(tmp_path))
    owner = _user(db_session)
    project = _project(db_session, owner)
    c = _client(db_session, owner)
    cid = _start(c, project)
    record = sandbox.session_dir(uuid.UUID(cid))
    record.mkdir(parents=True)
    db_session.add(PoradcaMessage(conversation_id=uuid.UUID(cid), author="poradca", content="", status="running"))
    db_session.commit()

    r = c.delete(f"/api/v1/poradca/conversations/{cid}")
    assert r.status_code == 409 and "zastav" in r.json()["detail"]
    assert record.exists()
    body = c.get(f"/api/v1/poradca/conversations/{cid}").json()
    assert body["messages"][0]["content"] == "Prečo agent stojí?"


def test_disk_record_that_cannot_be_moved_leaves_the_conversation_whole(db_session, tmp_path, monkeypatch):
    monkeypatch.setattr(sandbox.settings, "poradca_data_dir", str(tmp_path))
    owner = _user(db_session)
    project = _project(db_session, owner)
    c = _client(db_session, owner)
    cid = _start(c, project)
    sandbox.session_dir(uuid.UUID(cid)).mkdir(parents=True)

    def _denied(conversation_id: uuid.UUID) -> Path:
        raise PermissionError(13, "Permission denied", str(conversation_id))

    monkeypatch.setattr(sandbox, "move_to_trash", _denied)
    r = c.delete(f"/api/v1/poradca/conversations/{cid}")
    assert r.status_code == 500 and "ostal celý" in r.json()["detail"]
    body = c.get(f"/api/v1/poradca/conversations/{cid}").json()
    assert body["title"] == "Prečo agent stojí?" and body["messages"][1]["content"] == "Odpoveď."
    assert sandbox.session_dir(uuid.UUID(cid)).is_dir()


def test_rename_or_scope_that_loses_the_race_with_a_delete_is_404_and_writes_nothing(db_session, monkeypatch):
    owner = _user(db_session)
    project = _project(db_session, owner)
    c = _client(db_session, owner)
    cid = _start(c, project)

    def _gone(*a, **k):
        raise runner.ConversationGone("Rozhovor bol vymazaný.")

    monkeypatch.setattr(runner, "edit_conversation", _gone)
    assert c.put(f"/api/v1/poradca/conversations/{cid}/title", json={"title": "x"}).status_code == 404
    assert c.patch(f"/api/v1/poradca/conversations/{cid}", json={"version_id": None}).status_code == 404


def test_question_that_loses_the_race_with_a_delete_is_404(db_session, monkeypatch):
    owner = _user(db_session)
    project = _project(db_session, owner)
    c = _client(db_session, owner)
    cid = _start(c, project)

    def _gone(*a, **k):
        raise runner.ConversationGone("Rozhovor bol vymazaný.")

    monkeypatch.setattr(runner, "ask", _gone)
    assert c.post(f"/api/v1/poradca/conversations/{cid}/messages", json={"question": "y"}).status_code == 404


def test_nul_in_a_title_or_question_is_a_sentence_not_a_server_error(db_session):
    owner = _user(db_session)
    project = _project(db_session, owner)
    c = _client(db_session, owner)
    cid = _start(c, project)
    assert c.put(f"/api/v1/poradca/conversations/{cid}/title", json={"title": "a\x00b"}).status_code == 422
    assert c.post(f"/api/v1/poradca/conversations/{cid}/messages", json={"question": "a\x00b"}).status_code == 422
    r = c.post(f"/api/v1/poradca/projects/{project.slug}/conversations", json={"question": "a\x00b"})
    assert r.status_code == 422


def test_the_charter_asks_for_the_block_the_cockpit_saves_to_the_backlog():
    """DEV-29: Poradca writes the block the button reads — and is no longer told a version will come of it."""
    from backend.services.poradca import handoff

    charter = (Path(__file__).resolve().parents[1] / "templates" / "poradca-charter.md").read_text(encoding="utf-8")
    assert f"<{handoff.BLOCK_BACKLOG}>" in charter
    assert handoff.BLOCK_BACKLOG_LEGACY not in charter
    assert '„Uložiť do Zásobníka"' in charter
    assert "Založiť novú verziu" not in charter
