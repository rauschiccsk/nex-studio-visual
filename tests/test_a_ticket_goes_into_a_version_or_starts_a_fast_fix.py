"""DEV-122, DEV-455 — from a ticket of the „Plane tikety" tab into a version or to a fast fix; the ticket follows.

Director 14.10.2026 approved: at an open ticket „Spustiť rýchlu opravu"
(the ticket's text is the directive, its work kind preselected, the version is created by the Manažér's click as
today). The fast fix and its ticket stay linked: started → In Progress, deployed to UAT → a comment, deployed to PROD
→ Done with a comment (version, date, customer). Nothing starts without the Manažér's click (DEV-29).
DEV-455 (Director 21.11.2026: „Zásobník by sme mali teraz už úplne zrušiť a ponechať len jediný zdroj - PLANE tikety“):
the ticket goes straight into a version — In Progress with a comment; the version's PROD deploy closes it, once.
One ticket goes one way — a version or a fast fix, never both (Director 15.10.2026: „Súhlasím“ to „Súhlasíš
s pravidlom ‚jeden tiket ide jednou cestou‘?“): otherwise the requirement and the fix would both move the ticket.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from backend.db.models.version_tickets import VersionTicket
from backend.db.models.versions import Version
from backend.services import deploy, pipeline_runner, plane_tickets
from backend.tests.fake_plane import FakePlane, gateway_for
from tests.test_dedo_proposal_before_the_work_starts import _bearer, _make_project, _make_user

TICKET_HTML = (
    "<p>Robí: Andros Dedo</p>"
    "<p>Faktúra FLORASERVIS sa z ISDOC nespracuje.</p>"
    "<p>Číslo faktúry je v ISDOC len vo variabilnom symbole &amp; nikde inde.<br>Over aj dobropis.</p>"
)


@pytest.fixture
def plane(monkeypatch):
    fake = FakePlane()
    monkeypatch.setattr(plane_tickets, "gateway", lambda: gateway_for(fake))
    return fake


def _issue(plane: FakePlane, issue_id: str, *, module: str = "m1", state: str = "s-todo", labels=("l-bug", "l-fix")):
    plane.issues[issue_id] = {
        "id": issue_id,
        "sequence_id": int(issue_id.removeprefix("x")),
        "name": f"Tiket {issue_id}",
        "description_html": TICKET_HTML,
        "state": state,
        "labels": list(labels),
        "assignees": [],
        "updated_at": "2026-10-15T09:00:00Z",
        "deleted_at": None,
        "archived_at": None,
    }
    plane.module_issues.setdefault(module, []).append(issue_id)


@pytest.fixture
def dispatched(monkeypatch) -> list:
    """The agent's first turns a started fast fix schedules — recorded, never run."""
    scheduled: list = []
    monkeypatch.setattr(pipeline_runner, "schedule_dispatch", lambda v, d=None: scheduled.append(v))
    return scheduled


@pytest.fixture
def setup(db_session, plane):
    user = _make_user(db_session)
    user.first_name, user.last_name = "Zoltán", "Rausch"
    project = _make_project(db_session, user)
    project.plane_module_id = "m1"
    plane.modules += [{"id": "m1", "name": project.name}, {"id": "m2", "name": "Iný projekt"}]
    _issue(plane, "x11")
    _issue(plane, "x21", module="m2")
    db_session.commit()
    return user, project


def _comments(plane: FakePlane, issue_id: str) -> list[str]:
    return [c["comment_html"] for c in plane.comments.get(issue_id, [])]


def _tickets(client, user, project) -> dict[str, dict]:
    r = client.get(f"/api/v1/projects/{project.slug}/plane-tickets", headers=_bearer(user))
    assert r.status_code == 200, r.text
    return {t["identifier"]: t for t in r.json()["tickets"]}


# ── the ticket's text ────────────────────────────────────────────────────────────────────────────────────────────


def test_the_tab_gives_the_tickets_text_without_who_works_on_it(client, setup):
    user, project = setup
    ticket = _tickets(client, user, project)["DEV-11"]
    assert ticket["issue_id"] == "x11"
    assert ticket["text"] == (
        "Faktúra FLORASERVIS sa z ISDOC nespracuje.\n\n"
        "Číslo faktúry je v ISDOC len vo variabilnom symbole & nikde inde.\nOver aj dobropis."
    )
    assert ticket["fix_version"] is None


# ── „Zaradiť do verzie" ──────────────────────────────────────────────────────────────────────────────────────────────


def _new_version(db_session, project, number="2.0.0") -> Version:
    version = Version(project_id=project.id, version_number=number)
    db_session.add(version)
    db_session.commit()
    return version


def _into(client, user, project, version, issue_id="x11"):
    return client.post(
        f"/api/v1/projects/{project.slug}/plane-tickets/{issue_id}/version",
        headers=_bearer(user),
        json={"version_id": str(version.id)},
    )


def test_a_ticket_goes_into_a_version_once_moves_to_in_progress_and_says_so(client, setup, plane, db_session):
    user, project = setup
    version = _new_version(db_session, project)
    first = _into(client, user, project, version)
    second = _into(client, user, project, version)
    assert first.status_code == second.status_code == 200, first.text
    assert first.json() == {
        "issue_id": "x11",
        "identifier": "DEV-11",
        "version_number": "2.0.0",
        "plane_warning": None,
    }
    link = db_session.execute(select(VersionTicket).where(VersionTicket.plane_issue_id == "x11")).scalar_one()
    assert (link.version_id, link.title, link.plane_identifier) == (version.id, "Tiket x11", "DEV-11")
    assert link.text == _tickets(client, user, project)["DEV-11"]["text"]
    assert plane.issues["x11"]["state"] == "s-ip"
    assert _comments(plane, "x11") == [
        f"<p>Tiket je zaradený do verzie v2.0.0 projektu {project.name}. Zaradil: Zoltán Rausch.</p>"
    ]
    assert _tickets(client, user, project)["DEV-11"]["in_version"] == "v2.0.0"


def test_a_ticket_taken_out_goes_back_to_todo(client, setup, plane, db_session):
    user, project = setup
    version = _new_version(db_session, project)
    _into(client, user, project, version)
    r = client.delete(f"/api/v1/projects/{project.slug}/plane-tickets/x11/version/{version.id}", headers=_bearer(user))
    assert r.status_code == 200, r.text
    assert db_session.execute(select(VersionTicket).where(VersionTicket.plane_issue_id == "x11")).first() is None
    assert plane.issues["x11"]["state"] == "s-todo"
    assert _tickets(client, user, project)["DEV-11"]["in_version"] is None


def test_a_ticket_one_version_solves_goes_into_no_other(client, setup, plane, db_session):
    user, project = setup
    _into(client, user, project, _new_version(db_session, project, "2.0.0"))
    r = _into(client, user, project, _new_version(db_session, project, "2.1.0"))
    assert (r.status_code, r.json()["detail"]) == (409, "Tiket DEV-11 už rieši verzia v2.0.0.")


def test_a_ticket_of_another_projects_module_goes_into_no_version(client, setup, plane, db_session):
    user, project = setup
    r = _into(client, user, project, _new_version(db_session, project), "x21")
    assert r.status_code == 400
    assert "nie je" in r.json()["detail"]
    assert db_session.execute(select(VersionTicket).where(VersionTicket.plane_issue_id == "x21")).first() is None
    assert _comments(plane, "x21") == []


def test_a_ticket_with_a_fast_fix_goes_into_no_version(client, setup, plane, db_session, dispatched):
    """One ticket goes one way — otherwise the version and the fix would both move it."""
    user, project = setup
    started = _fast_fix(client, user, project)
    assert started.status_code == 201, started.text
    number = db_session.get(Version, uuid.UUID(started.json()["version_id"])).version_number
    r = _into(client, user, project, _new_version(db_session, project, "3.0.0"))
    assert (r.status_code, r.json()["detail"]) == (409, f"Z tohto tiketu už je spustená rýchla oprava v{number}.")


def test_when_the_ticket_does_not_take_the_comment_the_link_stays(client, setup, plane, db_session):
    user, project = setup
    plane.drop_comments = True
    r = _into(client, user, project, _new_version(db_session, project))
    assert r.status_code == 200, r.text
    assert r.json()["plane_warning"].startswith("Zmena je uložená, ale tiket DEV-11 v Plane sa neposunul")
    assert db_session.execute(select(VersionTicket).where(VersionTicket.plane_issue_id == "x11")).scalar_one()


# ── „Spustiť rýchlu opravu" ─────────────────────────────────────────────────────────────────────────────────────


def _fast_fix(client, user, project, issue_id="x11", **over):
    body = {
        "project_id": str(project.id),
        "directive": "Faktúra FLORASERVIS sa z ISDOC nespracuje.",
        "work_kind": "fix",
        "plane_issue_id": issue_id,
        **over,
    }
    return client.post("/api/v1/pipeline/fast-fix", headers=_bearer(user), json=body)


def _versions(db_session, project) -> list[str]:
    return sorted(
        db_session.execute(select(Version.version_number).where(Version.project_id == project.id)).scalars().all()
    )


def test_a_fast_fix_from_a_ticket_remembers_it_and_moves_it_to_in_progress(
    client, setup, plane, db_session, dispatched
):
    user, project = setup
    r = _fast_fix(client, user, project)
    assert r.status_code == 201, r.text
    assert r.json()["plane_warning"] is None
    version = db_session.get(Version, uuid.UUID(r.json()["version_id"]))
    assert (version.plane_issue_id, version.plane_identifier, version.work_kind) == ("x11", "DEV-11", "fix")
    assert version.plane_url == "http://plane.test/icc/projects/p1/issues/x11"
    assert plane.issues["x11"]["state"] == "s-ip"
    assert _comments(plane, "x11") == [
        f"<p>Rýchla oprava v{version.version_number} je spustená z tohto tiketu. Spustil: Zoltán Rausch.</p>"
    ]
    assert _tickets(client, user, project)["DEV-11"]["fix_version"] == f"v{version.version_number}"


def test_one_ticket_starts_one_fast_fix(client, setup, plane, db_session, dispatched):
    user, project = setup
    first = _fast_fix(client, user, project)
    assert first.status_code == 201, first.text
    before = _versions(db_session, project)
    again = _fast_fix(client, user, project)
    number = db_session.get(Version, uuid.UUID(first.json()["version_id"])).version_number
    assert (again.status_code, again.json()["detail"]) == (
        409,
        f"Z tohto tiketu už je spustená rýchla oprava v{number}.",
    )
    assert _versions(db_session, project) == before and len(dispatched) == 1


def test_a_ticket_in_a_version_starts_no_fast_fix(client, setup, plane, db_session, dispatched):
    user, project = setup
    _into(client, user, project, _new_version(db_session, project))
    before = _versions(db_session, project)
    r = _fast_fix(client, user, project)
    assert r.status_code == 409
    assert r.json()["detail"] == "Tiket už rieši verzia v2.0.0 — pôjde s ňou."
    assert _versions(db_session, project) == before and dispatched == []


def test_a_ticket_of_another_project_starts_no_fast_fix(client, setup, plane, db_session, dispatched):
    user, project = setup
    before = _versions(db_session, project)
    r = _fast_fix(client, user, project, "x21")
    assert r.status_code == 400 and "nie je" in r.json()["detail"]
    assert _versions(db_session, project) == before and dispatched == []
    assert plane.issues["x21"]["state"] == "s-todo"


def test_when_plane_does_not_follow_the_fast_fix_runs_and_a_sentence_says_so(
    client, setup, plane, db_session, dispatched
):
    user, project = setup
    plane.ignore_patch = True
    r = _fast_fix(client, user, project)
    assert r.status_code == 201, r.text
    assert r.json()["plane_warning"].startswith("Rýchla oprava je spustená, ale tiket DEV-11 sa v Plane neposunul: ")
    assert len(dispatched) == 1


def test_a_fast_fix_without_a_ticket_never_asks_plane(client, setup, plane, db_session, dispatched):
    user, project = setup
    plane.requests.clear()
    r = _fast_fix(client, user, project, None)
    assert r.status_code == 201, r.text
    assert plane.requests == []
    assert db_session.get(Version, uuid.UUID(r.json()["version_id"])).plane_issue_id is None


# ── deployed ─────────────────────────────────────────────────────────────────────────────────────────────────────


def _fix_version(client, user, project, db_session) -> Version:
    r = _fast_fix(client, user, project)
    assert r.status_code == 201, r.text
    return db_session.get(Version, uuid.UUID(r.json()["version_id"]))


class _Clock(datetime):
    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 10, 15, 22, 30, tzinfo=timezone.utc)


@pytest.fixture
def today(monkeypatch) -> str:
    """22:30 UTC on 15 October is already the 16th in Bratislava — the date a person there reads."""
    monkeypatch.setattr(deploy, "datetime", _Clock)
    return "16.10.2026"


def test_uat_says_where_the_fix_runs_and_prod_closes_the_ticket(client, setup, plane, db_session, dispatched, today):
    user, project = setup
    version = _fix_version(client, user, project, db_session)
    customer = type("C", (), {"name": "FLORASERVIS"})()
    v = f"v{version.version_number}"

    assert asyncio.run(deploy._follow_fix_ticket(version, version.version_number, customer, "uat")) is None
    assert plane.issues["x11"]["state"] == "s-ip"
    assert asyncio.run(deploy._follow_fix_ticket(version, version.version_number, customer, "prod")) is None
    assert plane.issues["x11"]["state"] == "s-done"
    assert _comments(plane, "x11")[1:] == [
        f"<p>Rýchla oprava {v} je nasadená na UAT u zákazníka FLORASERVIS ({today}).</p>",
        f"<p>Rýchla oprava {v} je nasadená na PROD u zákazníka FLORASERVIS ({today}).</p>",
    ]


def test_a_ticket_that_does_not_close_is_a_warning_not_a_failed_deploy(client, setup, plane, db_session, dispatched):
    user, project = setup
    version = _fix_version(client, user, project, db_session)
    plane.status = 503
    customer = type("C", (), {"name": "FLORASERVIS"})()
    warning = asyncio.run(deploy._follow_fix_ticket(version, version.version_number, customer, "prod"))
    assert warning.startswith("Tiket DEV-11 rýchlej opravy sa po nasadení na PROD neuzavrel: Plane")


@pytest.mark.asyncio
async def test_only_a_successful_deploy_of_a_fix_from_a_ticket_tells_the_ticket(db_session, monkeypatch):
    """The hook sits in the deploy itself: a failed deploy and a version without a ticket leave Plane alone."""
    from tests.test_deploy_service import _deploy, _FakeRunner, _make_customer
    from tests.test_deploy_service import _make_project as _deploy_project
    from tests.test_deploy_service import _make_user as _deploy_user
    from tests.test_deploy_service import _make_version as _deploy_version

    calls = []

    async def spy(version, deployed_version, customer, environment):
        calls.append((deployed_version, customer.name, environment))
        return "tiket sa neposunul"

    monkeypatch.setattr(deploy, "_follow_fix_ticket", spy)
    user = _deploy_user(db_session)
    project = _deploy_project(db_session, user=user)
    plain = _deploy_version(db_session, project, "0.1.0")
    customer = _make_customer(db_session, project)

    await _deploy(db_session, customer, version_number="0.1.0", environment="uat", actor=user, runner=_FakeRunner())
    assert calls == [] and plain.plane_issue_id is None

    fixed = _deploy_version(db_session, project, "0.1.1")
    fixed.plane_issue_id, fixed.plane_identifier = "x11", "DEV-11"
    db_session.flush()
    await _deploy(
        db_session, customer, version_number="0.1.1", environment="uat", actor=user, runner=_FakeRunner(ok=False)
    )
    assert calls == []
    outcome = await _deploy(
        db_session, customer, version_number="0.1.1", environment="uat", actor=user, runner=_FakeRunner()
    )
    assert calls == [("0.1.1", "ANDROS", "uat")]
    assert "tiket sa neposunul" in outcome.warnings
    deploy.accept(db_session, customer.id, "0.1.1", user.id)
    await _deploy(db_session, customer, version_number="0.1.1", environment="prod", actor=user, runner=_FakeRunner())
    assert calls[-1][1:] == ("ANDROS", "prod")


def test_the_versions_prod_deploy_closes_its_tickets_once(client, setup, plane, db_session):
    """DEV-455 — the version solves the ticket: its PROD deploy moves it to Done with a comment; a second PROD deploy
    (another customer) closes nothing again; a ticket Plane did not take stays open for the next deploy."""
    user, project = setup
    version = _new_version(db_session, project)
    _into(client, user, project, version)
    customer = type("C", (), {"name": "FLORASERVIS"})()

    assert asyncio.run(deploy._close_version_tickets(db_session, version, "2.0.0", customer)) == []
    assert plane.issues["x11"]["state"] == "s-done"
    assert _comments(plane, "x11")[-1] == (
        "<p>Verzia v2.0.0, ktorá tiket rieši, je nasadená na PROD u zákazníka FLORASERVIS.</p>"
    )
    link = db_session.execute(select(VersionTicket).where(VersionTicket.plane_issue_id == "x11")).scalar_one()
    assert link.closed_at is not None
    comments = len(_comments(plane, "x11"))

    assert asyncio.run(deploy._close_version_tickets(db_session, version, "2.0.0", customer)) == []
    assert len(_comments(plane, "x11")) == comments, "closed once"


def test_a_ticket_plane_did_not_close_stays_open_for_the_next_deploy(client, setup, plane, db_session):
    user, project = setup
    version = _new_version(db_session, project)
    _into(client, user, project, version)
    customer = type("C", (), {"name": "FLORASERVIS"})()
    plane.status = 503

    warnings = asyncio.run(deploy._close_version_tickets(db_session, version, "2.0.0", customer))

    assert warnings and warnings[0].startswith("Tiket DEV-11 sa po nasadení na PROD neuzavrel")
    link = db_session.execute(select(VersionTicket).where(VersionTicket.plane_issue_id == "x11")).scalar_one()
    assert link.closed_at is None
