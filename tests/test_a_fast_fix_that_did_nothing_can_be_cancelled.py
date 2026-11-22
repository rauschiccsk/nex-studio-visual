"""DEV-491 — a fast fix that did nothing yet is cancelled with one button; it goes away whole and frees its number.

22.11.2026, Orthodox Hermenia: the Director pressed „Rýchla oprava“ on the project page to found a version for the
ticket DEV-489; version 1.1.1 started at once with the brief „Oprava chyby v dodanom kóde“, the Staviteľ stopped on
„Akú chybu mám opraviť?“ and the cockpit had no way to take the version back. Director: „Nevidím nikde možnosť ako
zrušiť verziu 1.1.1“ — and to the proposal „Rýchlu opravu, ktorá ešte nič neurobila, pôjde zrušiť tlačidlom. Ďalšia
oprava potom dostane znova číslo 1.1.1.“: „Súhlasím.“
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select

from backend.db.models.pipeline import PipelineMessage, PipelineState
from backend.db.models.tasks import Epic, Feat, Task
from backend.db.models.versions import Version
from backend.services import fast_fix, pipeline_runner, plane_tickets
from backend.tests.fake_plane import FakePlane, gateway_for
from tests.test_a_ticket_goes_into_a_version_or_starts_a_fast_fix import TICKET_HTML
from tests.test_dedo_proposal_before_the_work_starts import _bearer, _make_project, _make_user


@pytest.fixture
def plane(monkeypatch):
    fake = FakePlane()
    monkeypatch.setattr(plane_tickets, "gateway", lambda: gateway_for(fake))
    return fake


@pytest.fixture(autouse=True)
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
    plane.modules += [{"id": "m1", "name": project.name}]
    plane.issues["x11"] = {
        "id": "x11",
        "sequence_id": 11,
        "name": "Tiket x11",
        "description_html": TICKET_HTML,
        "state": "s-todo",
        "labels": ["l-bug", "l-fix"],
        "assignees": [],
        "updated_at": "2026-10-15T09:00:00Z",
        "deleted_at": None,
        "archived_at": None,
    }
    plane.module_issues.setdefault("m1", []).append("x11")
    db_session.commit()
    return user, project


def _fast_fix(client, user, project, **over) -> uuid.UUID:
    body = {"project_id": str(project.id), "directive": "Oprava chyby v dodanom kóde", "work_kind": "fix", **over}
    r = client.post("/api/v1/pipeline/fast-fix", headers=_bearer(user), json=body)
    assert r.status_code == 201, r.text
    return uuid.UUID(r.json()["version_id"])


def _settle(db_session, version_id, **fields) -> None:
    """The fix as the Staviteľ left it — by default stopped on his question in Príprava, as Hermenia 1.1.1."""
    state = db_session.execute(select(PipelineState).where(PipelineState.version_id == version_id)).scalar_one()
    values = {"status": "blocked", "block_reason": "agent_question", "dispatch_in_flight": False, **fields}
    for name, value in values.items():
        setattr(state, name, value)
    db_session.commit()


def _cancel(client, user, version_id):
    return client.delete(f"/api/v1/pipeline/{version_id}/fast-fix", headers=_bearer(user))


def _rows(db_session, version_id) -> dict[str, int]:
    db_session.expire_all()
    count = {
        "versions": select(func.count()).select_from(Version).where(Version.id == version_id),
        "pipeline_state": select(func.count()).select_from(PipelineState).where(PipelineState.version_id == version_id),
        "pipeline_message": select(func.count())
        .select_from(PipelineMessage)
        .where(PipelineMessage.version_id == version_id),
        "epics": select(func.count()).select_from(Epic).where(Epic.version_id == version_id),
    }
    return {table: db_session.execute(query).scalar_one() for table, query in count.items()}


def _comments(plane: FakePlane, issue_id: str) -> list[str]:
    return [c["comment_html"] for c in plane.comments.get(issue_id, [])]


def test_a_fix_from_a_ticket_goes_away_whole_and_its_ticket_goes_back_to_todo(client, setup, plane, db_session):
    user, project = setup
    version_id = _fast_fix(client, user, project, plane_issue_id="x11")
    _settle(db_session, version_id)
    assert plane.issues["x11"]["state"] == "s-ip"

    r = _cancel(client, user, version_id)

    assert r.status_code == 200, r.text
    assert r.json() == {"version_number": "1.5.6", "plane_warning": None}
    assert _rows(db_session, version_id) == {"versions": 0, "pipeline_state": 0, "pipeline_message": 0, "epics": 0}
    assert plane.issues["x11"]["state"] == "s-todo"
    assert _comments(plane, "x11") == [
        "<p>Rýchla oprava v1.5.6 je spustená z tohto tiketu. Spustil: Zoltán Rausch.</p>",
        "<p>Rýchla oprava v1.5.6 je zrušená skôr, než niečo urobila — tiket čaká na novú opravu. "
        "Zrušil: Zoltán Rausch.</p>",
    ]


def test_the_next_fix_gets_the_number_again_and_the_ticket_can_start_it(client, setup, plane, db_session):
    user, project = setup
    first = _fast_fix(client, user, project)
    _settle(db_session, first)
    assert _cancel(client, user, first).status_code == 200

    again = _fast_fix(client, user, project, plane_issue_id="x11")

    assert db_session.get(Version, again).version_number == "1.5.6"
    assert db_session.get(Version, again).plane_identifier == "DEV-11"


def test_a_fix_without_a_ticket_never_asks_plane(client, setup, plane, db_session):
    user, project = setup
    version_id = _fast_fix(client, user, project)
    _settle(db_session, version_id)
    asked = len(plane.requests)

    r = _cancel(client, user, version_id)

    assert r.status_code == 200, r.text
    assert len(plane.requests) == asked


def test_the_tasks_of_a_fix_go_with_it(client, setup, plane, db_session):
    user, project = setup
    version_id = _fast_fix(client, user, project)
    task = fast_fix.ensure_build_task(db_session, version_id)
    feat_id, task_id = task.feat_id, task.id
    _settle(db_session, version_id)

    assert _cancel(client, user, version_id).status_code == 200

    assert _rows(db_session, version_id)["epics"] == 0
    assert db_session.get(Feat, feat_id) is None and db_session.get(Task, task_id) is None


def test_when_plane_does_not_follow_the_fix_is_cancelled_and_a_sentence_says_so(client, setup, plane, db_session):
    user, project = setup
    version_id = _fast_fix(client, user, project, plane_issue_id="x11")
    _settle(db_session, version_id)
    plane.ignore_patch = True

    r = _cancel(client, user, version_id)

    assert r.status_code == 200, r.text
    assert r.json()["plane_warning"].startswith(
        "Rýchla oprava je zrušená, ale tiket DEV-11 sa v Plane nevrátil do Todo: "
    )
    assert _rows(db_session, version_id)["versions"] == 0


REFUSED = [
    pytest.param({"status": "agent_working", "block_reason": None}, fast_fix.CANCEL_AGENT_WORKING, id="agent-working"),
    pytest.param({"dispatch_in_flight": True}, fast_fix.CANCEL_AGENT_WORKING, id="dispatch-in-flight"),
    pytest.param(
        {"current_stage": "programovanie", "status": "awaiting_manazer", "block_reason": None},
        fast_fix.CANCEL_PAST_PRIPRAVA,
        id="past-priprava",
    ),
    pytest.param({"flow_type": "new_version"}, fast_fix.CANCEL_NOT_A_FIX, id="regular-version"),
]


@pytest.mark.parametrize(("fields", "sentence"), REFUSED)
def test_a_fix_that_works_or_did_something_stays_and_the_refusal_is_said_in_words(
    client, setup, db_session, fields, sentence
):
    user, project = setup
    version_id = _fast_fix(client, user, project)
    _settle(db_session, version_id, **fields)

    r = _cancel(client, user, version_id)

    assert (r.status_code, r.json()["detail"]) == (409, sentence)
    assert _rows(db_session, version_id)["versions"] == 1


def test_a_version_that_never_started_is_no_fast_fix_to_cancel(client, setup, db_session):
    user, project = setup
    version = Version(project_id=project.id, version_number="2.0.0")
    db_session.add(version)
    db_session.commit()

    r = _cancel(client, user, version.id)

    assert (r.status_code, r.json()["detail"]) == (409, fast_fix.CANCEL_NOT_A_FIX)


def test_a_turn_still_finishing_in_the_runner_holds_the_fix(client, setup, db_session, monkeypatch):
    user, project = setup
    version_id = _fast_fix(client, user, project)
    _settle(db_session, version_id)

    async def busy(_version_id, _timeout):
        return False

    monkeypatch.setattr(pipeline_runner, "idle_within", busy)
    r = _cancel(client, user, version_id)

    assert (r.status_code, r.json()["detail"]) == (409, fast_fix.CANCEL_AGENT_WORKING)
    assert _rows(db_session, version_id)["versions"] == 1


def test_someone_without_the_project_cannot_cancel_its_fix(client, setup, db_session):
    user, project = setup
    version_id = _fast_fix(client, user, project)
    _settle(db_session, version_id)
    stranger = _make_user(db_session, role="shu")
    db_session.commit()

    r = _cancel(client, stranger, version_id)

    assert r.status_code in (403, 404)
    assert _rows(db_session, version_id)["versions"] == 1


CASES = [pytest.param({}, id="did-nothing"), *REFUSED]


@pytest.mark.parametrize("fields", [case.values[0] for case in CASES], ids=[case.id for case in CASES])
def test_the_board_offers_the_cancel_exactly_when_the_route_takes_it(client, setup, db_session, fields):
    user, project = setup
    version_id = _fast_fix(client, user, project)
    _settle(db_session, version_id, **fields)
    board = client.get(f"/api/v1/pipeline/{version_id}", headers=_bearer(user)).json()["state"]

    r = _cancel(client, user, version_id)

    assert board["cancellable"] is (r.status_code == 200)
    assert board["cancel_refusal"] == (None if r.status_code == 200 else r.json()["detail"])
