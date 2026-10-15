"""DEV-120 — the „Plane tikety" tab shows the tickets of the pinned project's module in the DEV register.

Director 14.10.2026: „Chcel by som pridať novú záložku "Plane tikety" tu by som videl tikety pre konkrétny projekt.“
Identifier, title, column, type, work kind, who works on it, last change; open or finished; a link to Plane; a ticket
the Zásobník founded names its REQ-N and the version. Reading founds nothing in Plane.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.routes.plane_tickets import router as plane_tickets_router
from backend.core.security import get_current_user
from backend.db.session import get_db
from backend.schemas.backlog import BacklogItemCreate
from backend.services import backlog as backlog_service
from backend.services import backlog_tickets, plane_tickets
from backend.tests.fake_plane import FakePlane, gateway_for
from tests.test_backlog import _make_project, _make_user, _make_version


@pytest.fixture
def plane(monkeypatch):
    fake = FakePlane()
    monkeypatch.setattr(plane_tickets, "gateway", lambda: gateway_for(fake))
    return fake


def _client(db_session, user) -> TestClient:
    app = FastAPI()
    app.include_router(plane_tickets_router, prefix="/api/v1")

    def _db():
        yield db_session

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


def _issue(plane: FakePlane, issue_id: str, *, state: str, labels=(), doer="Andros Dedo", minute=0, **over):
    plane.issues[issue_id] = {
        "id": issue_id,
        "sequence_id": int(issue_id.removeprefix("x")),
        "name": f"Tiket {issue_id}",
        "description_html": f"<p>Robí: {doer}</p><p>Popis.</p>",
        "state": state,
        "labels": list(labels),
        "assignees": [],
        "updated_at": f"2026-10-15T09:{minute:02d}:00Z",
        "deleted_at": None,
        "archived_at": None,
        **over,
    }
    plane.module_issues.setdefault("m1", []).append(issue_id)


@pytest.fixture
def setup(db_session, plane):
    owner = _make_user(db_session, "ha")
    owner.first_name = "Zoltán"
    project = _make_project(db_session, owner)
    project.plane_module_id = "m1"
    plane.modules.append({"id": "m1", "name": project.name})
    _issue(plane, "x11", state="s-todo", labels=("l-bug", "l-fix"), minute=5)
    _issue(plane, "x12", state="s-ip", labels=("l-task", "l-change"), doer="Tibor", minute=20)
    _issue(plane, "x13", state="s-done", labels=("l-task",), minute=1)
    _issue(plane, "x14", state="s-cancelled", minute=2)
    _issue(plane, "x15", state="s-todo", deleted_at="2026-10-15T09:30:00Z", minute=30)
    _issue(plane, "x16", state="s-todo", archived_at="2026-10-15T09:31:00Z", minute=31)
    db_session.flush()
    return owner, project, _client(db_session, owner)


def test_the_open_tickets_of_the_projects_module_newest_change_first(setup, plane):
    _, project, client = setup
    r = client.get(f"/api/v1/projects/{project.slug}/plane-tickets")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["module"] == project.name
    assert [(t["identifier"], t["state"], t["ticket_type"], t["work_kind"], t["doer"]) for t in body["tickets"]] == [
        ("DEV-12", "In Progress", "Task", "change", "Tibor"),
        ("DEV-11", "Todo", "Bug", "fix", "Andros Dedo"),
    ]
    assert body["tickets"][1]["url"] == "http://plane.test/icc/projects/p1/issues/x11"
    assert [t["work_kind_label"] for t in body["tickets"]] == ["Požiadavka (CHR)", "Oprava (BUG)"]
    assert {t["closed"] for t in body["tickets"]} == {False}


def test_the_finished_tickets_are_the_done_and_the_cancelled_ones(setup):
    _, project, client = setup
    body = client.get(f"/api/v1/projects/{project.slug}/plane-tickets", params={"closed": True}).json()
    assert [(t["identifier"], t["state"], t["closed"]) for t in body["tickets"]] == [
        ("DEV-14", "Cancelled", True),
        ("DEV-13", "Done", True),
    ]


def test_a_ticket_of_the_zasobnik_names_its_requirement_and_version(setup, plane, db_session, monkeypatch):
    owner, project, client = setup
    monkeypatch.setattr(backlog_tickets, "gateway", lambda: gateway_for(plane))
    item = backlog_service.create(db_session, BacklogItemCreate(project_id=project.id, title="Import ISDOC"))
    version = _make_version(db_session, project)
    backlog_service.assign_to_version(db_session, item.id, version.id)
    backlog_tickets.found(
        db_session,
        gateway_for(plane),
        item.id,
        owner,
        title="Import ISDOC",
        description="",
        work_kind="change",
        ticket_type="Task",
        responsible="Zoltán",
    )
    plane.issues[item.plane_issue_id]["state"] = "s-ip"
    tickets = client.get(f"/api/v1/projects/{project.slug}/plane-tickets").json()["tickets"]
    linked = [t for t in tickets if t["identifier"] == item.plane_identifier]
    assert [(t["requirement"], t["requirement_version"]) for t in linked] == [
        (f"REQ-{item.number}", f"v{version.version_number}")
    ]
    assert all(t["requirement"] is None for t in tickets if t["identifier"] != item.plane_identifier)


def test_a_ticket_with_two_work_kinds_says_so_instead_of_guessing(setup, plane):
    _, project, client = setup
    plane.issues["x11"]["labels"] = ["l-bug", "l-fix", "l-gift"]
    ticket = next(
        t
        for t in client.get(f"/api/v1/projects/{project.slug}/plane-tickets").json()["tickets"]
        if t["identifier"] == "DEV-11"
    )
    assert ticket["work_kind"] is None
    assert "dva druhy práce" in ticket["warning"]


def test_a_project_without_a_module_has_no_tickets_and_reading_founds_none(db_session, plane):
    owner = _make_user(db_session, "ha")
    project = _make_project(db_session, owner)
    r = _client(db_session, owner).get(f"/api/v1/projects/{project.slug}/plane-tickets")
    assert r.status_code == 200 and r.json() == {"module": None, "tickets": []}
    assert {req.method for req in plane.requests} == {"GET"}  # only read — no module founded
    assert project.plane_module_id is None


def test_the_module_is_found_by_the_projects_name_when_none_is_remembered(setup, plane, db_session):
    _, project, client = setup
    project.plane_module_id = None
    db_session.flush()
    body = client.get(f"/api/v1/projects/{project.slug}/plane-tickets").json()
    assert body["module"] == project.name and len(body["tickets"]) == 2


def test_someone_elses_project_shows_nothing(setup, db_session):
    _, project, _ = setup
    stranger = _make_user(db_session, "shu")
    r = _client(db_session, stranger).get(f"/api/v1/projects/{project.slug}/plane-tickets")
    assert r.status_code in (403, 404)


def test_when_plane_does_not_answer_the_sentence_says_so(setup, plane):
    _, project, client = setup
    plane.status = 503
    r = client.get(f"/api/v1/projects/{project.slug}/plane-tickets")
    assert r.status_code == 502
    assert r.json()["detail"].startswith("Plane požiadavku odmietol: 503")


def test_a_renamed_project_keeps_its_module_by_the_remembered_id(setup, db_session):
    _, project, client = setup
    project.name = "Nové meno projektu"
    db_session.flush()
    body = client.get(f"/api/v1/projects/{project.slug}/plane-tickets").json()
    assert len(body["tickets"]) == 2 and body["module"] != project.name
