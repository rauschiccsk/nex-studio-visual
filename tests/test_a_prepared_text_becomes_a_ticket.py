"""DEV-492 — a text the cockpit prepared (a broken UAT test, old dead code) becomes a ticket of the project, not a fix.

Both used to open the „Rýchla oprava“ dialog and start a fix from the text. With one way for every version (Director
22.11.2026: „Treba mať jednotný postup a to najprv verzia a potom priradzovanie tiketov.“) the text is filed as a ticket
of the project's module; the Manažér puts it into a version or a fast fix on the „Plane tikety“ tab. Nothing starts.
"""

from __future__ import annotations

import pytest

from backend.db.models.versions import Version
from backend.services import plane_tickets
from backend.services.user_notes import NO_RESPONSIBLE
from backend.tests.fake_plane import FakePlane, gateway_for
from tests.test_dedo_proposal_before_the_work_starts import _bearer, _make_project, _make_user


@pytest.fixture
def plane(monkeypatch):
    fake = FakePlane()
    monkeypatch.setattr(plane_tickets, "gateway", lambda: gateway_for(fake))
    return fake


@pytest.fixture
def setup(db_session, plane):
    user = _make_user(db_session)
    user.first_name = "Zoltán"  # the account's names find its responsible person in Plane
    project = _make_project(db_session, user)
    project.plane_module_id = "m1"
    plane.modules += [{"id": "m1", "name": project.name}]
    db_session.commit()
    return user, project


def _file(client, user, project, **over):
    body = {
        "title": "UAT: Overiť spojenie neukáže výsledok",
        "description": "Kroky: Nastavenia → Overiť spojenie.\nOčakávané: Funkčný.\nPoznámka: ostane „Overuje sa…“.",
        "work_kind": "fix",
        "source": "uat",
        **over,
    }
    return client.post(f"/api/v1/projects/{project.slug}/plane-tickets", headers=_bearer(user), json=body)


def _labels(plane: FakePlane, issue: dict) -> set[str]:
    return {label["name"] for label in plane.labels if label["id"] in issue["labels"]}


def test_a_broken_uat_test_becomes_a_bug_of_the_project_and_starts_nothing(client, setup, plane, db_session):
    user, project = setup
    versions_before = db_session.query(Version).filter(Version.project_id == project.id).count()

    r = _file(client, user, project)

    assert r.status_code == 201, r.text
    (issue,) = plane.issues.values()
    assert r.json() == {"identifier": f"DEV-{issue['sequence_id']}", "url": plane_tickets.gateway().ticket_url(issue)}
    assert issue["name"] == "UAT: Overiť spojenie neukáže výsledok"
    assert "Očakávané: Funkčný." in issue["description_html"]
    assert "zo skúšky na UAT" in issue["description_html"]
    assert {"Typ: Bug", "Zodpovedný: Zoltán", "Druh práce: Oprava (BUG)"} <= _labels(plane, issue)
    assert issue["id"] in plane.module_issues["m1"]
    assert db_session.query(Version).filter(Version.project_id == project.id).count() == versions_before


def test_the_cleanup_of_dead_code_is_a_task_with_its_work_kind(client, setup, plane):
    user, project = setup

    r = _file(client, user, project, title="Upratať nepoužívaný kód", source="dead_code", work_kind="change")

    assert r.status_code == 201, r.text
    (issue,) = plane.issues.values()
    assert "z kontroly nepoužívaného kódu" in issue["description_html"]
    assert {"Typ: Task", "Druh práce: Požiadavka (CHR)"} <= _labels(plane, issue)


def test_an_account_without_its_person_in_plane_files_nothing_and_says_where_to_set_it(client, setup, plane):
    user, project = setup
    user.first_name, user.username = "Nikto", "nikto"

    r = _file(client, user, project)

    assert (r.status_code, r.json()["detail"]) == (502, NO_RESPONSIBLE.format(username="nikto"))
    assert plane.issues == {}


def test_a_project_the_person_may_not_touch_gets_no_ticket(client, setup, plane, db_session):
    _user, project = setup
    stranger = _make_user(db_session, role="shu")
    db_session.commit()

    r = _file(client, stranger, project)

    assert r.status_code in (403, 404)
    assert plane.issues == {}
