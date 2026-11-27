"""DEV-491, DEV-492 — a version that did nothing yet is cancelled on its page; it goes away whole and frees its number.

22.11.2026 a fast fix of Orthodox Hermenia was founded by mistake and could not be taken back (DEV-491 put a button
into the Riadiace centrum). DEV-492 made one way for every version — founded first, tickets put in, started then — and
the Director: „Zrušenie verzie nemá byť v Riadiacom centre - nepatrí tam. Má byť vo Verzie - Otvoriť.“ A version that
never started — regular or fast fix — or a fast fix still in Príprava with no agent turn running is cancelled; the
tickets put into it go back to Todo with a comment.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select

from backend.db.models.pipeline import PipelineMessage, PipelineState
from backend.db.models.tasks import Epic, Feat, Task
from backend.db.models.version_tickets import VersionTicket
from backend.db.models.versions import Version
from backend.services import fast_fix, pipeline_runner, plane_tickets, version_cancel
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
    """The agent's first turns a started fix schedules — recorded, never run."""
    scheduled: list = []
    monkeypatch.setattr(pipeline_runner, "schedule_dispatch", lambda v, d=None: scheduled.append(v))
    return scheduled


def _issue(plane: FakePlane, issue_id: str) -> None:
    plane.issues[issue_id] = {
        "id": issue_id,
        "sequence_id": int(issue_id.removeprefix("x")),
        "name": f"Tiket {issue_id}",
        "description_html": TICKET_HTML,
        "state": "s-todo",
        "labels": ["l-bug", "l-fix"],
        "assignees": [],
        "updated_at": "2026-10-15T09:00:00Z",
        "deleted_at": None,
        "archived_at": None,
    }
    plane.module_issues.setdefault("m1", []).append(issue_id)


@pytest.fixture
def setup(db_session, plane):
    user = _make_user(db_session)
    user.first_name, user.last_name = "Zoltán", "Rausch"
    project = _make_project(db_session, user)
    project.plane_module_id = "m1"
    plane.modules += [{"id": "m1", "name": project.name}]
    _issue(plane, "x11")
    db_session.commit()
    return user, project


def _found_fix(client, user, project) -> Version:
    r = client.post(f"/api/v1/projects/{project.id}/fast-fixes", headers=_bearer(user), json={"work_kind": "fix"})
    assert r.status_code == 201, r.text
    return uuid.UUID(r.json()["id"])


def _regular(db_session, project, number="2.0.0") -> uuid.UUID:
    version = Version(project_id=project.id, version_number=number)
    db_session.add(version)
    db_session.commit()
    return version.id


def _into(client, user, project, version_id, issue_id="x11"):
    r = client.post(
        f"/api/v1/projects/{project.slug}/plane-tickets/{issue_id}/version",
        headers=_bearer(user),
        json={"version_id": str(version_id)},
    )
    assert r.status_code == 200, r.text


def _start(client, user, version_id) -> None:
    r = client.post(f"/api/v1/pipeline/{version_id}/action", headers=_bearer(user), json={"action": "start"})
    assert r.status_code == 200, r.text


def _settle(db_session, version_id, **fields) -> None:
    """The build as the Staviteľ left it — by default stopped on his question in Príprava."""
    state = db_session.execute(select(PipelineState).where(PipelineState.version_id == version_id)).scalar_one()
    values = {"status": "blocked", "block_reason": "agent_question", "dispatch_in_flight": False, **fields}
    for name, value in values.items():
        setattr(state, name, value)
    db_session.commit()


def _cancel(client, user, version_id):
    return client.delete(f"/api/v1/versions/{version_id}", headers=_bearer(user))


def _page(client, user, version_id) -> dict:
    r = client.get(f"/api/v1/versions/{version_id}", headers=_bearer(user))
    assert r.status_code == 200, r.text
    return r.json()


def _rows(db_session, version_id) -> dict[str, int]:
    db_session.expire_all()
    count = {
        "versions": select(func.count()).select_from(Version).where(Version.id == version_id),
        "pipeline_state": select(func.count()).select_from(PipelineState).where(PipelineState.version_id == version_id),
        "pipeline_message": select(func.count())
        .select_from(PipelineMessage)
        .where(PipelineMessage.version_id == version_id),
        "version_tickets": select(func.count())
        .select_from(VersionTicket)
        .where(VersionTicket.version_id == version_id),
        "epics": select(func.count()).select_from(Epic).where(Epic.version_id == version_id),
    }
    return {table: db_session.execute(query).scalar_one() for table, query in count.items()}


GONE = {"versions": 0, "pipeline_state": 0, "pipeline_message": 0, "version_tickets": 0, "epics": 0}


def _comments(plane: FakePlane, issue_id: str) -> list[str]:
    return [c["comment_html"] for c in plane.comments.get(issue_id, [])]


def _back_to_todo(project, number: str) -> str:
    return (
        f"<p>Verzia v{number} projektu {project.name}, do ktorej bol tiket zaradený, je zrušená skôr, než niečo urobila"
        " — tiket čaká na ďalšiu verziu. Zrušil: Zoltán Rausch.</p>"
    )


def test_a_waiting_fast_fix_goes_away_whole_its_ticket_back_to_todo_and_its_number_free(
    client, setup, plane, db_session
):
    user, project = setup
    fix = _found_fix(client, user, project)
    _into(client, user, project, fix)
    assert plane.issues["x11"]["state"] == "s-ip"

    r = _cancel(client, user, fix)

    assert r.status_code == 200, r.text
    assert r.json() == {"version_number": "1.5.6", "plane_warning": None}
    assert _rows(db_session, fix) == GONE
    assert plane.issues["x11"]["state"] == "s-todo"
    assert _comments(plane, "x11")[-1] == _back_to_todo(project, "1.5.6")
    again = _found_fix(client, user, project)
    assert db_session.get(Version, again).version_number == "1.5.6"


def test_a_regular_version_that_never_started_is_cancelled_too(client, setup, plane, db_session):
    user, project = setup
    version = _regular(db_session, project)
    _into(client, user, project, version)

    r = _cancel(client, user, version)

    assert r.status_code == 200, r.text
    assert _rows(db_session, version) == GONE
    assert plane.issues["x11"]["state"] == "s-todo"
    assert _comments(plane, "x11")[-1] == _back_to_todo(project, "2.0.0")


def test_a_cancelled_version_takes_the_screenshots_of_its_conversation(
    client, setup, plane, db_session, monkeypatch, tmp_path
):
    """DEV-534 — what the Manažér pasted into its conversation goes with it; another version's stays."""
    from backend.config.settings import settings

    monkeypatch.setattr(settings, "conversation_images_dir", str(tmp_path / "shots"))
    user, project = setup
    version = _regular(db_session, project)
    (tmp_path / "shots" / project.slug / str(version)).mkdir(parents=True)
    (tmp_path / "shots" / project.slug / "ina-verzia").mkdir()

    assert _cancel(client, user, version).status_code == 200

    assert sorted(p.name for p in (tmp_path / "shots" / project.slug).iterdir()) == ["ina-verzia"]


def test_a_started_fast_fix_still_in_priprava_goes_away_with_its_tasks(client, setup, plane, db_session):
    user, project = setup
    fix = _found_fix(client, user, project)
    _into(client, user, project, fix)
    _start(client, user, fix)
    task = fast_fix.ensure_build_task(db_session, fix)
    feat_id, task_id = task.feat_id, task.id
    _settle(db_session, fix)

    r = _cancel(client, user, fix)

    assert r.status_code == 200, r.text
    assert _rows(db_session, fix) == GONE
    assert db_session.get(Feat, feat_id) is None and db_session.get(Task, task_id) is None
    assert plane.issues["x11"]["state"] == "s-todo"


def test_a_fix_started_from_its_ticket_before_dev_492_gives_the_ticket_back(client, setup, plane, db_session):
    user, project = setup
    fix = Version(
        project_id=project.id,
        version_number="1.5.6",
        fast_fix=True,
        work_kind="fix",
        plane_issue_id="x11",
        plane_identifier="DEV-11",
        plane_url="http://plane.test/issues/x11",
    )
    db_session.add(fix)
    db_session.flush()
    db_session.add(
        PipelineState(
            version_id=fix.id,
            flow_type="fast_fix",
            current_stage="priprava",
            current_actor="ai_agent",
            status="blocked",
            block_reason="agent_question",
            next_action="…",
        )
    )
    db_session.commit()
    plane.issues["x11"]["state"] = "s-ip"

    r = _cancel(client, user, fix.id)

    assert r.status_code == 200, r.text
    assert plane.issues["x11"]["state"] == "s-todo"
    assert _comments(plane, "x11") == [
        "<p>Rýchla oprava v1.5.6 je zrušená skôr, než niečo urobila — tiket čaká na novú opravu. "
        "Zrušil: Zoltán Rausch.</p>"
    ]


def test_when_plane_does_not_follow_the_version_is_cancelled_and_a_sentence_says_so(client, setup, plane, db_session):
    user, project = setup
    fix = _found_fix(client, user, project)
    _into(client, user, project, fix)
    plane.ignore_patch = True

    r = _cancel(client, user, fix)

    assert r.status_code == 200, r.text
    assert r.json()["plane_warning"].startswith("Verzia je zrušená, ale tiket DEV-11 sa v Plane nevrátil do Todo: ")
    assert _rows(db_session, fix)["versions"] == 0


def test_a_version_without_tickets_never_asks_plane(client, setup, plane, db_session):
    user, project = setup
    fix = _found_fix(client, user, project)
    asked = len(plane.requests)

    assert _cancel(client, user, fix).status_code == 200
    assert len(plane.requests) == asked


def _started_fix(client, user, project, db_session, **fields) -> uuid.UUID:
    fix = _found_fix(client, user, project)
    _into(client, user, project, fix)
    _start(client, user, fix)
    _settle(db_session, fix, **fields)
    return fix


def _started_regular(client, user, project, db_session, **fields) -> uuid.UUID:
    version = _regular(db_session, project)
    _start(client, user, version)
    _settle(db_session, version, **fields)
    return version


def _finished(client, user, project, db_session) -> uuid.UUID:
    version = _regular(db_session, project, "1.6.0")
    db_session.get(Version, version).status = "done"
    db_session.commit()
    return version


REFUSED = {
    "fix-agent-working": (
        lambda c, u, p, db: _started_fix(c, u, p, db, status="agent_working", block_reason=None),
        version_cancel.CANCEL_AGENT_WORKING.format(number="1.5.6"),
    ),
    "fix-dispatch-in-flight": (
        lambda c, u, p, db: _started_fix(c, u, p, db, dispatch_in_flight=True),
        version_cancel.CANCEL_AGENT_WORKING.format(number="1.5.6"),
    ),
    "fix-past-priprava": (
        lambda c, u, p, db: _started_fix(
            c, u, p, db, current_stage="programovanie", status="awaiting_manazer", block_reason=None
        ),
        version_cancel.CANCEL_PAST_PRIPRAVA.format(number="1.5.6"),
    ),
    "regular-started": (
        lambda c, u, p, db: _started_regular(c, u, p, db),
        version_cancel.CANCEL_STARTED.format(number="2.0.0"),
    ),
    "finished": (_finished, version_cancel.CANCEL_FINISHED.format(number="1.6.0")),
}


@pytest.mark.parametrize("case", list(REFUSED))
def test_a_version_that_works_or_did_something_stays_and_the_refusal_is_said_in_words(
    client, setup, plane, db_session, case
):
    user, project = setup
    make, sentence = REFUSED[case]
    version = make(client, user, project, db_session)

    r = _cancel(client, user, version)

    assert (r.status_code, r.json()["detail"]) == (409, sentence)
    assert _rows(db_session, version)["versions"] == 1


def test_a_turn_still_finishing_in_the_runner_holds_the_version(client, setup, plane, db_session, monkeypatch):
    user, project = setup
    fix = _found_fix(client, user, project)

    async def busy(_version_id, _timeout):
        return False

    monkeypatch.setattr(pipeline_runner, "idle_within", busy)
    r = _cancel(client, user, fix)

    assert (r.status_code, r.json()["detail"]) == (409, version_cancel.CANCEL_AGENT_WORKING.format(number="1.5.6"))
    assert _rows(db_session, fix)["versions"] == 1


def test_someone_without_the_project_cannot_cancel_its_version(client, setup, plane, db_session):
    user, project = setup
    fix = _found_fix(client, user, project)
    stranger = _make_user(db_session, role="shu")
    db_session.commit()

    r = _cancel(client, stranger, fix)

    assert r.status_code in (403, 404)
    assert _rows(db_session, fix)["versions"] == 1


CASES = {
    "fix-waiting": lambda c, u, p, db: _found_fix(c, u, p),
    "regular-waiting": lambda c, u, p, db: _regular(db, p),
    "fix-in-priprava": lambda c, u, p, db: _started_fix(c, u, p, db),
    **{name: make for name, (make, _sentence) in REFUSED.items()},
}


@pytest.mark.parametrize("case", list(CASES))
def test_the_page_offers_the_cancel_exactly_when_the_route_takes_it(client, setup, plane, db_session, case):
    user, project = setup
    version = CASES[case](client, user, project, db_session)
    page = _page(client, user, version)

    r = _cancel(client, user, version)

    assert page["cancellable"] is (r.status_code == 200), r.text
    assert page["cancel_refusal"] == (None if r.status_code == 200 else r.json()["detail"])


def test_the_page_lists_the_tickets_and_knows_a_fast_fix(client, setup, plane, db_session):
    user, project = setup
    fix = _found_fix(client, user, project)
    _into(client, user, project, fix)

    tickets = client.get(f"/api/v1/versions/{fix}/tickets", headers=_bearer(user))

    assert tickets.status_code == 200, tickets.text
    assert tickets.json() == [
        {"identifier": "DEV-11", "title": "Tiket x11", "url": "http://plane.test/icc/projects/p1/issues/x11"}
    ]
    assert _page(client, user, fix)["fast_fix"] is True
    assert _page(client, user, _regular(db_session, project))["fast_fix"] is False
