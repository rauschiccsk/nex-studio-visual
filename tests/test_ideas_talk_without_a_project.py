"""DEV-318 — Nápady: a new idea is a Poradca conversation that belongs to no project yet.

Director 06.11.2026: „Poradca nie je dostupný pokiaľ nemám pripojený konkrétny projekt. Robiť nový projekt pod iným
cudzím projektom, je nelogické.“ An idea is started without any project, only its author (and the admin account)
sees it, its conversation goes on through Poradca's own routes, the list tells its state, and what needs a project
(the Zásobník, a project ticket) says why it is not there yet. The question run is replaced by a dummy; the real run
of an idea is in ``test_an_idea_question_runs_in_its_own_folder``.
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
from backend.db.models.idea import Idea
from backend.db.models.poradca import PoradcaConversation, PoradcaMessage
from backend.db.session import get_db
from backend.schemas.poradca import PoradcaStatus
from backend.services.poradca import handoff, runner, tickets

BRIEF = """Rozumiem, tu je zadanie.

<zadanie-projektu>
meno: Orthodox Register
slug: orthodox-register
opis: Pravoslávna matrika — evidencia krstov, sobášov a pohrebov
typ: web
prihlasenie: password
sablona: ziadna
ai: nie
---
## Prečo
Farnosť vedie matriku na papieri a chce ju mať v aplikácii.
</zadanie-projektu>
"""

ADDITION = """Patrí to do NEX Managera.

<doplnenie-projektu projekt="nex-manager">
Skupiny podľa farností
Správca vytvorí skupinu pre každú farnosť a pridelí jej ľudí.
</doplnenie-projektu>
"""


def _user(db: Any, username: str | None = None, role: str = "ri") -> User:
    u = User(
        username=username or f"u_{uuid.uuid4().hex[:8]}",
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        password_hash="x",
        role=role,
    )
    db.add(u)
    db.flush()
    return u


def _client(db: Any, user: User) -> TestClient:
    app = FastAPI()
    app.include_router(poradca_routes.router, prefix="/api/v1/poradca")

    def _db():
        yield db

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


@pytest.fixture()
def answers(monkeypatch):
    """What the next answers say — the question is saved as in a real run, the answer finishes at once."""
    queue: list[str] = []

    def _ask(db, conversation, question, user, images=()):
        human = PoradcaMessage(conversation_id=conversation.id, author="human", content=question, status="done")
        answer = PoradcaMessage(
            conversation_id=conversation.id,
            author="poradca",
            content=queue.pop(0) if queue else "Aký je rozsah prvej verzie?",
            status="done",
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
    return queue


def test_an_idea_starts_without_any_project_and_its_talk_goes_on(db_session, answers):
    me = _user(db_session)
    c = _client(db_session, me)

    r = c.post("/api/v1/poradca/ideas", json={"question": "Mám nápad na matriku pre farnosť."})

    assert r.status_code == 201, r.text
    body = r.json()
    assert body["state"] == "open"
    assert body["title"] == "Mám nápad na matriku pre farnosť."
    conversation = db_session.get(PoradcaConversation, uuid.UUID(body["conversation_id"]))
    assert conversation.project_id is None and conversation.idea_id == uuid.UUID(body["id"])
    assert [m["author"] for m in body["conversation"]["messages"]] == ["human", "poradca"]

    # The talk goes on through Poradca's own routes, as any conversation.
    answers.append(BRIEF)
    r = c.post(f"/api/v1/poradca/conversations/{conversation.id}/messages", json={"question": "Prvá verzia: krsty."})
    assert r.status_code == 200, r.text
    assert c.get(f"/api/v1/poradca/conversations/{conversation.id}").json()["idea_id"] == body["id"]
    assert c.get(f"/api/v1/poradca/ideas/{body['id']}").json()["state"] == "brief_ready"


def test_only_the_author_and_the_admin_see_an_idea(db_session, answers):
    author, other, admin = _user(db_session), _user(db_session), _user(db_session, username="admin")
    idea = _client(db_session, author).post("/api/v1/poradca/ideas", json={"question": "Nápad"}).json()

    assert [i["id"] for i in _client(db_session, author).get("/api/v1/poradca/ideas").json()] == [idea["id"]]
    assert _client(db_session, other).get("/api/v1/poradca/ideas").json() == []
    assert _client(db_session, other).get(f"/api/v1/poradca/ideas/{idea['id']}").status_code == 404
    assert _client(db_session, other).get(f"/api/v1/poradca/conversations/{idea['conversation_id']}").status_code == 404
    assert idea["id"] in [i["id"] for i in _client(db_session, admin).get("/api/v1/poradca/ideas").json()]


def test_an_idea_is_started_by_whoever_may_found_a_project(db_session, answers):
    assert (
        _client(db_session, _user(db_session, role="shu"))
        .post("/api/v1/poradca/ideas", json={"question": "Nápad"})
        .status_code
        == 201
    )


def test_an_answer_that_asks_is_no_brief_yet(db_session, answers):
    answers.append(BRIEF.replace("</zadanie-projektu>\n", "</zadanie-projektu>\n\nChceš ešte niečo doplniť?"))
    idea = _client(db_session, _user(db_session)).post("/api/v1/poradca/ideas", json={"question": "Nápad"}).json()
    assert idea["state"] == "open"


def test_a_thrown_away_idea_is_gone_and_a_concluded_one_leaves_the_list_its_talk_stays(db_session, answers):
    """DEV-331 (Director 09.11.2026 „Súhlasím“): an idea that ended in a project leaves the list of Nápady and its talk
    stays in the project's Poradca — the history of how the project came about. Until then the cockpit refused."""
    me = _user(db_session)
    c = _client(db_session, me)
    first = c.post("/api/v1/poradca/ideas", json={"question": "Prvý"}).json()
    second = c.post("/api/v1/poradca/ideas", json={"question": "Druhý"}).json()

    assert c.delete(f"/api/v1/poradca/ideas/{first['id']}").status_code == 204
    assert [i["id"] for i in c.get("/api/v1/poradca/ideas").json()] == [second["id"]]
    assert c.get(f"/api/v1/poradca/ideas/{first['id']}").status_code == 404
    gone = db_session.get(PoradcaConversation, uuid.UUID(first["conversation_id"]))
    assert gone.deleted_at is not None, "an idea that ended nowhere is thrown away whole, its talk too"

    db_session.get(Idea, uuid.UUID(second["id"])).outcome = "added"
    db_session.flush()
    assert c.delete(f"/api/v1/poradca/ideas/{second['id']}").status_code == 204
    assert c.get("/api/v1/poradca/ideas").json() == []
    kept = db_session.get(PoradcaConversation, uuid.UUID(second["conversation_id"]))
    db_session.refresh(kept)
    assert kept.deleted_at is None, "the talk of an idea that ended in a project stays with the project"


def test_an_idea_is_renamed_through_its_conversation(db_session, answers):
    """DEV-331 — the idea's title is its conversation's: renamed the way a Poradca conversation is."""
    c = _client(db_session, _user(db_session))
    idea = c.post("/api/v1/poradca/ideas", json={"question": "Matrika"}).json()

    r = c.put(f"/api/v1/poradca/conversations/{idea['conversation_id']}/title", json={"title": "Pravoslávna matrika"})

    assert r.status_code == 200
    assert [i["title"] for i in c.get("/api/v1/poradca/ideas").json()] == ["Pravoslávna matrika"]


def test_the_briefs_are_read_from_the_answer():
    brief = handoff.project_brief(BRIEF)
    assert (brief.name, brief.slug, brief.type, brief.auth_mode, brief.template, brief.ai) == (
        "Orthodox Register",
        "orthodox-register",
        "web",
        "password",
        "",
        False,
    )
    assert brief.description == "Pravoslávna matrika — evidencia krstov, sobášov a pohrebov"
    assert brief.brief.startswith("## Prečo\nFarnosť vedie matriku")
    addition = handoff.project_addition(ADDITION)
    assert (addition.project_slug, addition.title) == ("nex-manager", "Skupiny podľa farností")
    assert addition.brief.startswith("Skupiny podľa farností\nSprávca vytvorí")
    # A head the cockpit cannot read is left out, never guessed; a brief without the line is no brief.
    odd = handoff.project_brief(BRIEF.replace("typ: web", "typ: desktop").replace("prihlasenie: password", ""))
    assert (odd.type, odd.auth_mode) == (None, None)
    assert handoff.project_brief("<zadanie-projektu>\nmeno: X\n</zadanie-projektu>") is None
    assert (
        handoff.project_addition("<doplnenie-projektu>\nBez projektu a s dosť dlhým textom\n</doplnenie-projektu>")
        is None
    )


def test_what_needs_a_project_says_the_idea_has_none_yet(db_session, answers):
    me = _user(db_session)
    answers.append(
        "Do Zásobníka:\n<poziadavka-do-zasobnika>\nNiečo dlhšie než desať znakov\n</poziadavka-do-zasobnika>"
    )
    idea = _client(db_session, me).post("/api/v1/poradca/ideas", json={"question": "Nápad"}).json()
    answer = idea["conversation"]["messages"][-1]["id"]

    r = _client(db_session, me).post(f"/api/v1/poradca/messages/{answer}/backlog")

    assert r.status_code == 422 and r.json()["detail"] == handoff.IDEA_NOT_A_PROJECT_YET


def test_a_cockpit_ticket_can_come_from_an_idea_a_project_ticket_cannot(db_session, answers):
    me = _user(db_session)
    answers.append(
        "Kokpit to nevie.\n<tiket-nsv>\nWeb projekt na šablóne\nNápad potrebuje web aj šablónu.\n</tiket-nsv>"
    )
    answers.append(
        'Oprava.\n<tiket-projektu druh="oprava" cesta="verzia">\nNiečo\nOpraviť niečo v projekte.\n</tiket-projektu>'
    )
    c = _client(db_session, me)
    idea = c.post("/api/v1/poradca/ideas", json={"question": "Nápad"}).json()
    c.post(f"/api/v1/poradca/conversations/{idea['conversation_id']}/messages", json={"question": "A projekt?"})
    nsv_answer, project_answer = [
        db_session.get(PoradcaMessage, uuid.UUID(m["id"]))
        for m in c.get(f"/api/v1/poradca/conversations/{idea['conversation_id']}").json()["messages"]
        if m["author"] == "poradca"
    ]
    conversation = db_session.get(PoradcaConversation, uuid.UUID(idea["conversation_id"]))

    found_conversation, project = tickets._conversation_and_project(db_session, nsv_answer, project_needed=False)
    assert (found_conversation.id, project) == (conversation.id, None)
    assert tickets.context_lines(db_session, nsv_answer, conversation, None)[0] == (
        "Nápad: „Nápad“ (projekt zatiaľ neexistuje)"
    )
    with pytest.raises(handoff.HandoffRefused, match="Nápad ešte nemá projekt"):
        tickets._conversation_and_project(db_session, project_answer, project_needed=True)
