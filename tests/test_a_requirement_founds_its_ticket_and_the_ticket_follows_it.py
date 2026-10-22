"""DEV-121 — a requirement of the Zásobník founds its ticket in Plane once, and the ticket follows the requirement.

Director 14.10.2026: from a requirement a person founds a ticket in the project's module of the DEV register; the
requirement and the ticket stay linked; included in a version → In Progress, deployed to PROD → Done, rejected →
Cancelled with the reason. The requirement's own change never waits for Plane — when Plane does not follow, the
change is saved and a sentence says so.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from sqlalchemy import text

from backend.db.models.backlog import BacklogItem
from backend.schemas.backlog import BacklogItemCreate
from backend.services import backlog as backlog_service
from backend.services import backlog_tickets, deploy
from backend.services.plane_gateway import PlaneError
from backend.tests.fake_plane import FakePlane, gateway_for
from tests.test_backlog import _client, _make_project, _make_user, _make_version

DRAFT = {
    "title": "Import faktúr z ISDOC",
    "description": "Zákazník chce načítať faktúry aj z ISDOC.",
    "work_kind": "change",
    "ticket_type": "Task",
    "responsible": "Zoltán",
}


@pytest.fixture
def plane(monkeypatch):
    fake = FakePlane()
    monkeypatch.setattr(backlog_tickets, "gateway", lambda: gateway_for(fake))
    return fake


@pytest.fixture
def setup(db_session):
    user = _make_user(db_session, "ha")
    user.first_name, user.last_name = "Zoltán", "Rausch"
    project = _make_project(db_session, user)
    item = backlog_service.create(db_session, BacklogItemCreate(project_id=project.id, title="Import ISDOC"))
    db_session.flush()
    return user, project, item, _client(db_session, user)


def _issue_posts(plane: FakePlane) -> int:
    return sum(1 for r in plane.requests if r.method == "POST" and r.url.path.endswith("/issues/"))


def _state_and_comments(plane: FakePlane, issue_id: str) -> tuple[str, list[str]]:
    comments = [c["comment_html"] for c in plane.comments.get(issue_id, [])]
    return plane.issues[issue_id]["state"], comments


# ── founding ────────────────────────────────────────────────────────────────────────────────────────────────────


def test_the_draft_offers_the_register_people_and_picks_the_person_who_clicked(setup, plane):
    _, _, item, client = setup
    r = client.get(f"/api/v1/backlog/{item.id}/plane-ticket")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["title"] == "Import ISDOC"
    assert body["responsible_options"] == ["Andros Dedo", "Zoltán"]
    assert body["responsible"] == "Zoltán"
    assert body["plane_identifier"] is None


def test_a_ticket_is_founded_once_names_its_requirement_and_carries_the_chosen_labels(setup, plane):
    _, _, item, client = setup
    first = client.post(f"/api/v1/backlog/{item.id}/plane-ticket", json=DRAFT)
    second = client.post(f"/api/v1/backlog/{item.id}/plane-ticket", json=DRAFT)
    assert first.status_code == 200, first.text
    assert first.json()["plane_identifier"] == second.json()["plane_identifier"] == "DEV-1"
    assert _issue_posts(plane) == 1
    issue = plane.issues["i1"]
    assert f"Požiadavka REQ-{item.number} zo Zásobníka projektu" in issue["description_html"]
    assert sorted(issue["labels"]) == ["l-change", "l-task", "l-zoltan"]
    assert issue["state"] == "s-todo"


def test_a_second_click_that_waited_finds_the_ticket_the_first_one_founded(setup, plane, db_session):
    """The route reads the row before the lock; without a fresh read the waiting click would found another ticket."""
    user, _, item, _ = setup
    db_session.refresh(item)  # fully loaded in this session, as the route has it after checking access
    assert item.plane_issue_id is None
    db_session.execute(
        text("UPDATE backlog_items SET plane_issue_id = 'i9', plane_identifier = 'DEV-9' WHERE id = :id"),
        {"id": item.id},
    )
    found, founded_now = backlog_tickets.found(db_session, gateway_for(plane), item.id, user, **DRAFT)
    assert (found.plane_identifier, founded_now) == ("DEV-9", False)
    assert _issue_posts(plane) == 0


def test_a_requirement_already_in_a_version_founds_its_ticket_in_progress(setup, plane, db_session):
    _, project, item, client = setup
    version = _make_version(db_session, project)
    backlog_service.assign_to_version(db_session, item.id, version.id)
    r = client.post(f"/api/v1/backlog/{item.id}/plane-ticket", json=DRAFT)
    assert r.status_code == 200 and r.json()["plane_warning"] is None
    state, comments = _state_and_comments(plane, "i1")
    assert state == "s-ip"
    assert f"zaradená do verzie v{version.version_number}" in comments[0]


def test_a_rejected_requirement_founds_no_ticket(setup, plane):
    _, _, item, client = setup
    client.patch(f"/api/v1/backlog/{item.id}", json={"status": "rejected", "rejection_reason": "duplicita"})
    r = client.post(f"/api/v1/backlog/{item.id}/plane-ticket", json=DRAFT)
    assert r.status_code == 422 and "zamietnutá" in r.json()["detail"]
    assert _issue_posts(plane) == 0


def test_a_person_outside_the_register_group_is_refused_before_anything_is_written(setup, plane):
    _, _, item, client = setup
    r = client.post(f"/api/v1/backlog/{item.id}/plane-ticket", json={**DRAFT, "responsible": "Niekto"})
    assert r.status_code == 422 and "Zodpovedný" in r.json()["detail"]
    assert _issue_posts(plane) == 0


def test_the_draft_of_a_ticketed_requirement_returns_the_ticket_without_asking_plane(setup, plane):
    _, _, item, client = setup
    client.post(f"/api/v1/backlog/{item.id}/plane-ticket", json=DRAFT)
    asked = len(plane.requests)
    body = client.get(f"/api/v1/backlog/{item.id}/plane-ticket").json()
    assert (body["plane_identifier"], body["plane_url"]) == ("DEV-1", "http://plane.test/icc/projects/p1/issues/i1")
    assert len(plane.requests) == asked


# ── the ticket follows the requirement ──────────────────────────────────────────────────────────────────────────


def _ticketed(setup, plane):
    _, _, item, client = setup
    assert client.post(f"/api/v1/backlog/{item.id}/plane-ticket", json=DRAFT).status_code == 200
    return item, client


def test_included_in_a_version_moves_the_ticket_to_in_progress_with_a_comment(setup, plane, db_session):
    item, client = _ticketed(setup, plane)
    version = _make_version(db_session, setup[1])
    r = client.patch(f"/api/v1/backlog/{item.id}", json={"version_id": str(version.id)})
    assert r.status_code == 200 and r.json()["plane_warning"] is None
    state, comments = _state_and_comments(plane, "i1")
    assert state == "s-ip"
    assert comments == [
        f"<p>Požiadavka REQ-{item.number} je zaradená do verzie v{version.version_number}. Zaradil: Zoltán Rausch.</p>"
    ]


def test_a_rejection_needs_its_reason_and_cancels_the_ticket_with_it(setup, plane):
    item, client = _ticketed(setup, plane)
    without = client.patch(f"/api/v1/backlog/{item.id}", json={"status": "rejected"})
    assert without.status_code == 422 and "dôvod" in without.json()["detail"]
    r = client.patch(f"/api/v1/backlog/{item.id}", json={"status": "rejected", "rejection_reason": "Robí to už ERP."})
    assert r.status_code == 200 and r.json()["rejection_reason"] == "Robí to už ERP."
    state, comments = _state_and_comments(plane, "i1")
    assert state == "s-cancelled"
    assert comments == [
        f"<p>Požiadavka REQ-{item.number} je zamietnutá, dôvod: Robí to už ERP. Zamietol: Zoltán Rausch.</p>"
    ]


def test_returned_to_the_backlog_the_ticket_is_todo_again_and_the_reason_is_gone(setup, plane):
    item, client = _ticketed(setup, plane)
    client.patch(f"/api/v1/backlog/{item.id}", json={"status": "rejected", "rejection_reason": "neskôr"})
    r = client.patch(f"/api/v1/backlog/{item.id}", json={"status": "open"})
    assert r.status_code == 200 and r.json()["rejection_reason"] is None
    state, comments = _state_and_comments(plane, "i1")
    assert state == "s-todo"
    assert comments[-1] == f"<p>Požiadavka REQ-{item.number} je vrátená do Zásobníka. Vrátil: Zoltán Rausch.</p>"


def test_when_plane_does_not_follow_the_change_is_saved_and_a_sentence_says_so(setup, plane, db_session):
    item, client = _ticketed(setup, plane)
    version = _make_version(db_session, setup[1])
    plane.status = 500
    r = client.patch(f"/api/v1/backlog/{item.id}", json={"version_id": str(version.id)})
    assert r.status_code == 200
    assert r.json()["status"] == "included"
    assert r.json()["plane_warning"].startswith("Požiadavka je uložená, ale tiket DEV-1 v Plane sa neposunul:")


def test_a_requirement_without_a_ticket_never_asks_plane(setup, plane, db_session):
    _, project, item, client = setup
    version = _make_version(db_session, project)
    client.patch(f"/api/v1/backlog/{item.id}", json={"version_id": str(version.id)})
    client.patch(f"/api/v1/backlog/{item.id}", json={"status": "rejected", "rejection_reason": "nie"})
    assert plane.requests == []


def test_an_edit_that_does_not_move_the_requirement_does_not_touch_its_ticket(setup, plane):
    item, client = _ticketed(setup, plane)
    asked = len(plane.requests)
    client.patch(f"/api/v1/backlog/{item.id}", json={"title": "Import ISDOC a PDF"})
    assert len(plane.requests) == asked


# ── deployed to PROD ────────────────────────────────────────────────────────────────────────────────────────────


def _two_requirements_in_a_version(setup, plane, db_session):
    _, project, item, client = setup
    other = backlog_service.create(db_session, BacklogItemCreate(project_id=project.id, title="Bez tiketu"))
    version = _make_version(db_session, project)
    client.post(f"/api/v1/backlog/{item.id}/plane-ticket", json=DRAFT)
    for it in (item, other):
        client.patch(f"/api/v1/backlog/{it.id}", json={"version_id": str(version.id)})
    return item, version


def test_a_prod_deploy_closes_the_tickets_of_the_versions_requirements(setup, plane, db_session):
    item, version = _two_requirements_in_a_version(setup, plane, db_session)
    customer = type("C", (), {"name": "FLORASERVIS"})()
    warnings = asyncio.run(deploy._close_requirement_tickets(db_session, version, version.version_number, customer))
    assert warnings == []
    state, comments = _state_and_comments(plane, "i1")
    assert state == "s-done"
    assert comments[-1] == (
        f"<p>Verzia v{version.version_number} s požiadavkou REQ-{item.number} je nasadená na PROD"
        " u zákazníka FLORASERVIS.</p>"
    )


def test_a_ticket_that_does_not_close_is_a_warning_not_a_failed_deploy(setup, plane, db_session):
    _, version = _two_requirements_in_a_version(setup, plane, db_session)
    plane.status = 503
    customer = type("C", (), {"name": "FLORASERVIS"})()
    warnings = asyncio.run(deploy._close_requirement_tickets(db_session, version, version.version_number, customer))
    assert len(warnings) == 1 and warnings[0].startswith("Tiket DEV-1 (REQ-")


def test_a_version_without_ticketed_requirements_never_asks_plane(setup, db_session, monkeypatch):
    _, project, _, _ = setup
    version = _make_version(db_session, project)

    def no_plane():
        raise AssertionError("Plane sa nemá pýtať")

    monkeypatch.setattr(backlog_tickets, "gateway", no_plane)
    customer = type("C", (), {"name": "X"})()
    assert asyncio.run(deploy._close_requirement_tickets(db_session, version, version.version_number, customer)) == []


def test_plane_is_asked_off_the_event_loop(setup, plane, db_session, monkeypatch):
    """The deploy is a coroutine on the event loop; Plane's synchronous calls go through run_blocking."""
    _, version = _two_requirements_in_a_version(setup, plane, db_session)
    seen = []
    from backend.core import offload

    real = offload.run_blocking

    async def spy(fn, *args, cap, **kwargs):
        seen.append(fn.__name__)
        return await real(fn, *args, cap=cap, **kwargs)

    monkeypatch.setattr(offload, "run_blocking", spy)
    customer = type("C", (), {"name": "X"})()
    asyncio.run(deploy._close_requirement_tickets(db_session, version, version.version_number, customer))
    assert seen == ["close_deployed"]


def test_the_ticket_move_reads_back_what_it_wrote(plane):
    """Comment and state go through the gateway, which reads both back — a dropped comment is not reported done."""
    plane.drop_comments = True
    item = BacklogItem(number=3, plane_issue_id="i1", plane_identifier="DEV-1")
    plane.issues["i1"] = {"id": "i1", "state": "s-todo", "labels": [], "assignees": []}
    with pytest.raises(PlaneError):
        backlog_tickets.move(gateway_for(plane), "i1", "reopened", req="REQ-3", who="Z")
    warning = backlog_tickets.follow(lambda: gateway_for(plane), item, "reopened", who="Z")
    assert warning and "DEV-1" in warning
    assert json.dumps(plane.issues["i1"]["state"]) == '"s-todo"'


@pytest.mark.asyncio
async def test_only_a_successful_prod_deploy_closes_the_tickets(db_session, monkeypatch):
    """The hook sits in the deploy itself: UAT and a failed PROD deploy leave the tickets as they are."""
    from tests.test_deploy_service import _deploy, _FakeRunner, _make_customer
    from tests.test_deploy_service import _make_project as _deploy_project
    from tests.test_deploy_service import _make_user as _deploy_user
    from tests.test_deploy_service import _make_version as _deploy_version

    calls = []

    async def spy(db, version, deployed_version, customer):
        calls.append((deployed_version, customer.name))
        return ["tiket sa neuzavrel"]

    monkeypatch.setattr(deploy, "_close_requirement_tickets", spy)
    user = _deploy_user(db_session)
    project = _deploy_project(db_session, user=user)
    _deploy_version(db_session, project, "0.1.0")
    customer = _make_customer(db_session, project)

    await _deploy(db_session, customer, version_number="0.1.0", environment="uat", actor=user, runner=_FakeRunner())
    assert calls == []
    deploy.accept(db_session, customer.id, "0.1.0", user.id)
    await _deploy(
        db_session, customer, version_number="0.1.0", environment="prod", actor=user, runner=_FakeRunner(ok=False)
    )
    assert calls == []
    outcome = await _deploy(
        db_session, customer, version_number="0.1.0", environment="prod", actor=user, runner=_FakeRunner()
    )
    assert len(calls) == 1 and calls[0][1] == "ANDROS"
    assert "tiket sa neuzavrel" in outcome.warnings
