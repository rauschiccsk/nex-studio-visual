"""DEV-360 — an account names its responsible person in Plane; a founded idea without its ticket offers a second try.

Director 11.11.2026 (NEX Archive founded under the account alex, „Tiket v DEV sa nezaložil“): „Máme ten istý problém
ako minule.“ The cockpit looked for the responsible person of the project's ticket by the account's names — full name,
first name, username — among the people of the „Zodpovedný“ group of the DEV register. The accounts alex („Alexander
Honest“) and rausch („Develop Rausch“) are the Director's, and no such person is in Plane, so the ticket was never
founded and the sentence said Andros Dedo would found it; no button tried again. On the proposal — each account gets
its responsible person, alex and rausch the Director — „Súhlasím, založ tiket a sprav to.“
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.routes import users as users_routes
from backend.core.security import get_current_user
from backend.db.session import get_db
from backend.services import backlog_tickets, plane_tickets
from backend.services.poradca import ideas
from backend.tests.fake_plane import FakePlane, gateway_for
from tests.test_ideas_end_in_a_project import PAID_BRIEF, _idea_with, _new_project, world  # noqa: F401
from tests.test_ideas_talk_without_a_project import ADDITION, _client, _user, answers  # noqa: F401

PEOPLE = ["Andros Dedo", "Zoltán"]


def _alex(world):  # noqa: F811
    me = world["me"]
    me.username, me.first_name, me.last_name = "alex", "Alexander", "Honest"
    world["db"].flush()
    return me


def _found(world, idea, answer, project):  # noqa: F811
    return (
        _client(world["db"], world["me"])
        .post(f"/api/v1/poradca/ideas/{idea['id']}/founded", json={"message_id": answer, "project_id": str(project.id)})
        .json()
    )


def _labels(plane, issue) -> set[str]:
    return {label["name"] for label in plane.labels if label["id"] in issue["labels"]}


# ── who is responsible ──────────────────────────────────────────────────────────────────────────────────────────────


def test_the_person_set_at_the_account_wins_over_its_names(world):  # noqa: F811
    me = _alex(world)
    assert backlog_tickets.responsible_of(me, PEOPLE) is None  # the names alone find nobody — the founding of 11.11.

    me.plane_responsible = "Zoltán"
    assert backlog_tickets.responsible_of(me, PEOPLE) == "Zoltán"

    me.first_name = "Andros"
    me.last_name = "Dedo"
    assert backlog_tickets.responsible_of(me, PEOPLE) == "Zoltán"  # a name that matches someone else does not decide


def test_an_account_without_a_person_is_matched_by_its_names_as_before(world):  # noqa: F811
    me = world["me"]  # first name Zoltán
    assert me.plane_responsible is None
    assert backlog_tickets.responsible_of(me, PEOPLE) == "Zoltán"


def test_a_person_who_left_the_group_is_nobody_not_a_guess_by_name(world):  # noqa: F811
    me = world["me"]  # first name Zoltán, so the names would find Zoltán
    me.plane_responsible = "Eva"
    assert backlog_tickets.responsible_of(me, PEOPLE) is None


# ── the ticket of a founded project ─────────────────────────────────────────────────────────────────────────────────


def test_a_project_founded_under_alex_gets_its_ticket_with_the_person_set_at_the_account(world):  # noqa: F811
    db, plane = world["db"], world["plane"]
    _alex(world).plane_responsible = "Zoltán"
    db.flush()
    idea, (answer,) = _idea_with(world, PAID_BRIEF)
    project = _new_project(db, world["me"], slug="nex-archive")

    body = _found(world, idea, answer, project)

    assert body["ticket_error"] is None and body["plane_identifier"] is not None
    (issue,) = plane.issues.values()
    assert "Zodpovedný: Zoltán" in _labels(plane, issue)


def test_without_a_person_the_sentence_says_what_to_do_in_the_cockpit(world):  # noqa: F811
    _alex(world)
    idea, (answer,) = _idea_with(world, PAID_BRIEF)
    project = _new_project(world["db"], world["me"], slug="nex-archive")

    body = _found(world, idea, answer, project)

    assert body["plane_identifier"] is None
    assert body["ticket_error"] == (
        "Konto alex nemá zodpovedného v Plane. Manažér ho nastaví v Nastaveniach, karta Používatelia, časť "
        "„Zodpovedný v Plane“; potom pri nápade stlač „Založiť tiket projektu v DEV“."
    )


def test_the_button_of_a_founded_idea_files_its_ticket_once(world):  # noqa: F811
    db, plane = world["db"], world["plane"]
    me = _alex(world)
    idea, (answer,) = _idea_with(world, PAID_BRIEF)
    project = _new_project(db, me, slug="nex-archive")
    assert _found(world, idea, answer, project)["plane_identifier"] is None
    c = _client(db, me)

    me.plane_responsible = "Zoltán"
    db.flush()
    first = c.post(f"/api/v1/poradca/ideas/{idea['id']}/ticket")
    second = c.post(f"/api/v1/poradca/ideas/{idea['id']}/ticket")

    assert first.status_code == 200, first.text
    assert first.json()["ticket_error"] is None
    assert second.json()["plane_identifier"] == first.json()["plane_identifier"] is not None
    (issue,) = plane.issues.values()
    assert issue["name"] == "Orthodox Register (nex-archive): zadanie prvej verzie 0.1.0"
    assert _labels(plane, issue) == {"Typ: Task", "Zodpovedný: Zoltán", "Druh práce: Požiadavka (CHR)"}
    detail = c.get(f"/api/v1/poradca/ideas/{idea['id']}").json()
    assert (detail["state"], detail["plane_identifier"]) == ("founded", first.json()["plane_identifier"])


def test_the_button_of_an_idea_that_founded_nothing_is_refused(world):  # noqa: F811
    idea, _ = _idea_with(world, PAID_BRIEF)

    r = _client(world["db"], world["me"]).post(f"/api/v1/poradca/ideas/{idea['id']}/ticket")

    assert r.status_code == 409 and r.json()["detail"] == ideas.NOT_FOUNDED
    assert world["plane"].issues == {}


def test_the_button_of_an_idea_added_to_a_zasobnik_is_refused(world):  # noqa: F811
    """A requirement of the Zásobník gets its own ticket there — the idea's project ticket is only of a new project."""
    db = world["db"]
    _new_project(db, world["me"], slug="nex-manager")
    idea, (answer,) = _idea_with(world, ADDITION)
    c = _client(db, world["me"])
    assert c.post(f"/api/v1/poradca/ideas/{idea['id']}/added", json={"message_id": answer}).json()["state"] == "added"

    r = c.post(f"/api/v1/poradca/ideas/{idea['id']}/ticket")

    assert r.status_code == 409 and r.json()["detail"] == ideas.NOT_FOUNDED
    assert world["plane"].issues == {}


# ── the Manažér sets it ─────────────────────────────────────────────────────────────────────────────────────────────


def _users_client(db, user) -> TestClient:
    app = FastAPI()
    app.include_router(users_routes.router, prefix="/api/v1/users")

    def _db():
        yield db

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


@pytest.fixture()
def plane(monkeypatch):
    fake = FakePlane()
    monkeypatch.setattr(plane_tickets, "gateway", lambda: gateway_for(fake))
    return fake


def test_the_manazer_sees_each_account_with_its_person_and_who_its_names_would_find(db_session, plane):
    manazer = _user(db_session)
    zoltan = _user(db_session, username="zoltan")
    zoltan.first_name, zoltan.last_name = "Zoltán", "Rausch"
    alex = _user(db_session, username="alex")
    alex.first_name, alex.last_name = "Alexander", "Honest"
    db_session.flush()

    body = _users_client(db_session, manazer).get("/api/v1/users/plane-responsible").json()

    assert body["people"] == PEOPLE
    rows = {row["username"]: row for row in body["accounts"]}
    assert (rows["zoltan"]["chosen"], rows["zoltan"]["by_name"]) == (None, "Zoltán")
    assert (rows["alex"]["chosen"], rows["alex"]["by_name"]) == (None, None)


def test_the_manazer_sets_and_clears_the_person_of_an_account(db_session, plane):
    manazer = _user(db_session)
    alex = _user(db_session, username="alex")
    c = _users_client(db_session, manazer)

    set_ = c.put(f"/api/v1/users/{alex.id}/plane-responsible", json={"name": "zoltán"})
    assert set_.status_code == 200, set_.text
    assert set_.json()["chosen"] == "Zoltán" and alex.plane_responsible == "Zoltán"  # the group's spelling is kept

    cleared = c.put(f"/api/v1/users/{alex.id}/plane-responsible", json={"name": None})
    assert cleared.json()["chosen"] is None and alex.plane_responsible is None


def test_a_person_who_is_not_in_the_group_is_refused(db_session, plane):
    manazer = _user(db_session)
    alex = _user(db_session, username="alex")

    r = _users_client(db_session, manazer).put(f"/api/v1/users/{alex.id}/plane-responsible", json={"name": "Eva"})

    assert r.status_code == 422
    assert r.json()["detail"] == "„Eva“ v skupine „Zodpovedný“ evidencie DEV nie je."
    assert alex.plane_responsible is None


def test_only_a_manazer_sets_it(db_session, plane):
    junior = _user(db_session, role="shu")
    c = _users_client(db_session, junior)

    assert c.get("/api/v1/users/plane-responsible").status_code == 403
    assert c.put(f"/api/v1/users/{junior.id}/plane-responsible", json={"name": "Zoltán"}).status_code == 403
    assert junior.plane_responsible is None
