"""DEV-318 — a new idea ends in a project, by a person's click: a project founded from its brief, or the brief added to
an existing project's Zásobník.

Director 06.11.2026: „návrh nemusí vždy znamenať nový projekt, ak napríklad počas návrhu zistíme, že danú úlohu bude
lepšie pridať do existujúceho projektu, tak aby tam bola aj taká možnosť.“ — and on the plan „Súhlasím“, with „Pridať
do projektu“ saving the brief as a requirement of the Zásobník. A founded project gets the brief in its 0.1.0 as a
Dedo's brief does and its ticket in the DEV register; the talk moves to the project; a second click repeats nothing.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select

from backend.db.models.backlog import BacklogItem
from backend.db.models.poradca import PoradcaConversation, PoradcaMessage
from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.schemas.version import VersionCreate
from backend.services import plane_tickets
from backend.services import version as version_service
from backend.services.poradca import ideas
from backend.tests.fake_plane import FakePlane, gateway_for
from tests.test_ideas_talk_without_a_project import ADDITION, BRIEF, _client, _user, answers  # noqa: F401

PAID_BRIEF = BRIEF.replace("ai: nie\n", "ai: nie\ndruh: poziadavka\n")


@pytest.fixture()
def world(db_session, tmp_path, monkeypatch, answers):  # noqa: F811
    from backend.services import claude_agent

    monkeypatch.setattr(claude_agent, "PROJECTS_ROOT", tmp_path)
    monkeypatch.setattr(version_service, "_PROJECTS_ROOT", tmp_path)
    fake = FakePlane()
    monkeypatch.setattr(plane_tickets, "gateway", lambda: gateway_for(fake))
    me = _user(db_session)
    me.first_name = "Zoltán"
    db_session.flush()
    return {"db": db_session, "me": me, "plane": fake, "answers": answers, "root": tmp_path}


def _new_project(db, owner, slug="orthodox-register") -> Project:
    """What the form Nový projekt leaves: a project and its unstarted 0.1.0."""
    project = Project(
        name="Orthodox Register", slug=slug, type="standard", auth_mode="password", description="d", created_by=owner.id
    )
    db.add(project)
    db.flush()
    version_service.create(db, project.id, VersionCreate(version_number="0.1.0", name="Initial prototype"), owner.id)
    db.flush()
    return project


def _idea_with(world, *answers_text: str) -> tuple[dict, list[str]]:
    world["answers"].extend(answers_text)
    c = _client(world["db"], world["me"])
    idea = c.post("/api/v1/poradca/ideas", json={"question": "Mám nápad na matriku."}).json()
    for _ in answers_text[1:]:
        c.post(f"/api/v1/poradca/conversations/{idea['conversation_id']}/messages", json={"question": "A ďalej?"})
    messages = c.get(f"/api/v1/poradca/conversations/{idea['conversation_id']}").json()["messages"]
    return idea, [m["id"] for m in messages if m["author"] == "poradca"]


def test_a_project_founded_from_the_brief_gets_it_in_0_1_0_and_its_ticket(world):
    db, plane = world["db"], world["plane"]
    idea, (answer,) = _idea_with(world, PAID_BRIEF)
    project = _new_project(db, world["me"])
    c = _client(db, world["me"])

    first = c.post(
        f"/api/v1/poradca/ideas/{idea['id']}/founded", json={"message_id": answer, "project_id": str(project.id)}
    )
    second = c.post(
        f"/api/v1/poradca/ideas/{idea['id']}/founded", json={"message_id": answer, "project_id": str(project.id)}
    )

    assert first.status_code == 200, first.text
    body = first.json()
    assert (body["state"], body["outcome_project_slug"], body["ticket_error"]) == ("founded", project.slug, None)
    assert second.json()["plane_identifier"] == body["plane_identifier"] is not None
    brief = (world["root"] / project.slug / "docs/specs/versions/v0.1.0/customer-requirements.md").read_text("utf-8")
    assert brief.startswith("## Prečo\nFarnosť vedie matriku")
    version = db.execute(select(Version).where(Version.project_id == project.id)).scalar_one()
    assert version.description == brief
    assert db.get(PoradcaConversation, uuid.UUID(idea["conversation_id"])).project_id == project.id
    (issue,) = plane.issues.values()
    assert issue["name"] == "Orthodox Register (orthodox-register): zadanie prvej verzie 0.1.0"
    labels = {label["name"] for label in plane.labels if label["id"] in issue["labels"]}
    assert labels == {"Typ: Task", "Zodpovedný: Zoltán", "Druh práce: Požiadavka (CHR)"}
    assert [m["name"] for m in plane.modules] == ["Orthodox Register"]
    assert "(z Nápadov)" in issue["description_html"]


def test_a_brief_without_who_pays_founds_nothing(world):
    idea, (answer,) = _idea_with(world, BRIEF)
    project = _new_project(world["db"], world["me"])
    r = _client(world["db"], world["me"]).post(
        f"/api/v1/poradca/ideas/{idea['id']}/founded", json={"message_id": answer, "project_id": str(project.id)}
    )
    assert r.status_code == 409 and r.json()["detail"] == ideas.NO_WORK_KIND
    assert world["db"].get(PoradcaConversation, uuid.UUID(idea["conversation_id"])).project_id is None


def test_an_older_brief_is_not_taken(world):
    idea, (older, latest) = _idea_with(world, PAID_BRIEF, PAID_BRIEF.replace("Farnosť", "Eparchia"))
    project = _new_project(world["db"], world["me"])
    c = _client(world["db"], world["me"])
    r = c.post(f"/api/v1/poradca/ideas/{idea['id']}/founded", json={"message_id": older, "project_id": str(project.id)})
    assert r.status_code == 409 and r.json()["detail"] == ideas.REWRITTEN


def test_a_project_that_is_not_new_gets_no_brief(world):
    idea, (answer,) = _idea_with(world, PAID_BRIEF)
    project = _new_project(world["db"], world["me"])
    db = world["db"]
    db.execute(select(Version).where(Version.project_id == project.id)).scalar_one().status = "active"
    db.flush()
    r = _client(db, world["me"]).post(
        f"/api/v1/poradca/ideas/{idea['id']}/founded", json={"message_id": answer, "project_id": str(project.id)}
    )
    assert r.status_code == 409 and r.json()["detail"] == ideas.NOT_NEW


def test_a_ticket_plane_refuses_leaves_the_project_founded_and_a_second_click_files_it(world):
    db, plane = world["db"], world["plane"]
    world["me"].first_name = "Eva"
    db.flush()
    idea, (answer,) = _idea_with(world, PAID_BRIEF)
    project = _new_project(db, world["me"])
    c = _client(db, world["me"])
    payload = {"message_id": answer, "project_id": str(project.id)}

    first = c.post(f"/api/v1/poradca/ideas/{idea['id']}/founded", json=payload).json()
    assert first["state"] == "founded" and first["plane_identifier"] is None
    assert "v skupine „Zodpovedný“ nemá" in first["ticket_error"]

    plane.labels.append({"id": "l-eva", "name": "Zodpovedný: Eva"})
    second = c.post(f"/api/v1/poradca/ideas/{idea['id']}/founded", json=payload).json()
    assert second["ticket_error"] is None and second["plane_identifier"] is not None
    assert len(plane.issues) == 1


def test_the_brief_added_to_an_existing_project_is_a_requirement_of_its_zasobnik(world):
    db = world["db"]
    existing = _new_project(db, world["me"], slug="nex-manager")
    idea, (answer,) = _idea_with(world, ADDITION)
    c = _client(db, world["me"])

    first = c.post(f"/api/v1/poradca/ideas/{idea['id']}/added", json={"message_id": answer})
    second = c.post(f"/api/v1/poradca/ideas/{idea['id']}/added", json={"message_id": answer})

    assert first.status_code == 200, first.text
    assert (first.json()["state"], first.json()["outcome_project_slug"]) == ("added", "nex-manager")
    assert first.json()["outcome_backlog_number"] == second.json()["outcome_backlog_number"] is not None
    (item,) = db.execute(select(BacklogItem).where(BacklogItem.project_id == existing.id)).scalars()
    assert item.title == "Skupiny podľa farností" and item.description.startswith("Skupiny podľa farností\nSprávca")
    assert db.get(PoradcaConversation, uuid.UUID(idea["conversation_id"])).project_id == existing.id


def test_a_brief_is_added_to_a_project_another_manager_leads_and_the_talk_stays_with_its_author(world, db_session):
    """DEV-350: the Projektant reads every project (DEV-345), so an idea may end in any project's Zásobník — which
    version takes the requirement, its manager decides. Until 10.11.2026 only the project's own manager could add,
    and the Director, working as zoltan, could not add the change NEX Legal Watch needs to NEX Knowledge (rausch's).
    The talk then stays in Nápady with its author: in a project he may not open he would lose his own talk."""
    other = _user(db_session)
    theirs = _new_project(db_session, other, slug="nex-manager")
    idea, (answer,) = _idea_with(world, ADDITION)
    c = _client(db_session, world["me"])

    r = c.post(f"/api/v1/poradca/ideas/{idea['id']}/added", json={"message_id": answer})

    assert r.status_code == 200, r.text
    assert (r.json()["state"], r.json()["outcome_project_slug"], r.json()["talk_in_project"]) == (
        "added",
        "nex-manager",
        False,
    )
    (item,) = db_session.execute(select(BacklogItem).where(BacklogItem.project_id == theirs.id)).scalars()
    assert item.title == "Skupiny podľa farností"
    assert db_session.get(PoradcaConversation, uuid.UUID(idea["conversation_id"])).project_id is None
    assert c.get(f"/api/v1/poradca/conversations/{idea['conversation_id']}").status_code == 200, "his own talk"


def test_a_brief_added_to_a_project_of_the_author_moves_its_talk_there(world):
    db = world["db"]
    _new_project(db, world["me"], slug="nex-manager")
    idea, (answer,) = _idea_with(world, ADDITION)

    r = _client(db, world["me"]).post(f"/api/v1/poradca/ideas/{idea['id']}/added", json={"message_id": answer})

    assert r.status_code == 200 and r.json()["talk_in_project"] is True


def test_a_brief_for_a_project_that_is_not_in_the_cockpit_is_refused(world, db_session):
    idea, (answer,) = _idea_with(world, ADDITION)
    r = _client(db_session, world["me"]).post(f"/api/v1/poradca/ideas/{idea['id']}/added", json={"message_id": answer})
    assert r.status_code == 404
    assert db_session.execute(select(func.count()).select_from(BacklogItem)).scalar_one() == 0


def test_each_brief_offers_only_its_own_outcome(world):
    db = world["db"]
    _new_project(db, world["me"], slug="nex-manager")
    idea, (answer,) = _idea_with(world, PAID_BRIEF)
    c = _client(db, world["me"])
    r = c.post(f"/api/v1/poradca/ideas/{idea['id']}/added", json={"message_id": answer})
    assert r.status_code == 409 and r.json()["detail"] == ideas.NOT_AN_ADDITION
    message = c.get(f"/api/v1/poradca/conversations/{idea['conversation_id']}").json()["messages"][-1]
    assert message["idea_project"]["slug"] == "orthodox-register" and message["idea_project"]["work_kind"] == "change"
    assert message["idea_addition"] is None
    assert db.get(PoradcaMessage, uuid.UUID(answer)) is not None


def test_both_checks_of_an_addition_stand_on_their_own(world):
    """The route asks for the target first and the outcome checks again — each one refuses a new project's brief."""
    db = world["db"]
    existing = _new_project(db, world["me"], slug="nex-manager")
    idea, (answer,) = _idea_with(world, PAID_BRIEF)
    row, conversation = ideas.get_for(db, world["me"], uuid.UUID(idea["id"]))
    with pytest.raises(ideas.OutcomeRefused, match="na nový projekt"):
        ideas.addition_target(db, conversation, uuid.UUID(answer))
    with pytest.raises(ideas.OutcomeRefused, match="na nový projekt"):
        ideas.add(db, row, conversation, message_id=uuid.UUID(answer), project=existing)
