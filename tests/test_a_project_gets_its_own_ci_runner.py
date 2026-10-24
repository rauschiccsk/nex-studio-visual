"""DEV-158 — a project the cockpit took over gets its own CI runner on ANDROS like a founded one, and the repository
tab shows the jobs that run elsewhere.

20.10.2026 GitHub warned that the account had used 90 % of its Actions minutes: NEX Inbox, taken over rather than
founded, had run its CI on GitHub's machines since 14.06.2026 — against ICC decision D-009 — and nothing in the cockpit
said so. Founding is the only place that started a runner.

Docker and GitHub are played here: ``ci_runner.state`` answers what the server would, ``github_validation`` what GitHub
would, and the start founding does is recorded, never run.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from backend.core.security import get_current_user
from backend.db.models.foundation import User
from backend.db.models.projects import Project
from backend.main import app
from backend.services import ci_runner, claude_agent, create_project_postscaffold, github_validation
from backend.services import project_ci_runner as pcr


@pytest.fixture(autouse=True)
def _one_runner(monkeypatch):
    """These tests pin the mechanics of ONE runner pair; the several runners of a project (DEV-215) are pinned in
    tests/services/test_a_project_gets_several_ci_runners.py."""
    from backend.config.settings import settings

    monkeypatch.setattr(settings, "ci_runners_per_project", 1)


REPO = "rauschiccsk/nex-inbox"

# NEX Inbox's two workflows as they stood on 20.10.2026, cut down to what decides where a job runs.
NIB_CI = """\
name: CI
on:
  push:
    branches: [main]
jobs:
  lint:
    runs-on: ubuntu-latest
    steps: [{run: "true"}]
  test:
    runs-on: ubuntu-latest
    steps: [{run: "true"}]
"""
NIB_SMOKE = """\
name: Release smoke gate
on: {push: {tags: ["v*"]}}
jobs:
  gate:
    runs-on: ubuntu-latest
    steps: [{run: "true"}]
"""


def _workflows(checkout: Path, **files: str) -> None:
    folder = checkout / ".github" / "workflows"
    folder.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (folder / name).write_text(text, encoding="utf-8")


# --- which jobs run elsewhere -----------------------------------------------------------------------------------------


def test_every_job_not_on_the_projects_runner_is_named_with_what_it_runs_on(tmp_path):
    label = ci_runner.runner_label("nex-demo")
    _workflows(
        tmp_path,
        **{
            "ci.yml": f"""\
on: push
jobs:
  github: {{runs-on: ubuntu-latest, steps: [{{run: "true"}}]}}
  own: {{runs-on: {label}, steps: [{{run: "true"}}]}}
  own_in_a_list: {{runs-on: [self-hosted, {label}], steps: [{{run: "true"}}]}}
  another_projects: {{runs-on: andros-ubuntu-nex-other, steps: [{{run: "true"}}]}}
  matrix: {{runs-on: "${{{{ matrix.os }}}}", steps: [{{run: "true"}}]}}
  reusable: {{uses: ./.github/workflows/release.yaml}}
""",
            "release.yaml": f"""\
on: workflow_call
jobs:
  grouped: {{runs-on: {{group: big}}, steps: [{{run: "true"}}]}}
  grouped_own: {{runs-on: {{group: ours, labels: [{label}]}}, steps: [{{run: "true"}}]}}
""",
        },
    )

    found, unreadable = pcr.jobs_elsewhere(tmp_path, "nex-demo")

    assert found == [
        pcr.JobElsewhere(workflow="ci.yml", job="github", runs_on="ubuntu-latest"),
        pcr.JobElsewhere(workflow="ci.yml", job="another_projects", runs_on="andros-ubuntu-nex-other"),
        pcr.JobElsewhere(workflow="ci.yml", job="matrix", runs_on="${{ matrix.os }}"),
        pcr.JobElsewhere(workflow="release.yaml", job="grouped", runs_on="group: big"),
    ]
    assert unreadable == []


def test_a_workflow_that_cannot_be_read_is_named_not_skipped(tmp_path):
    _workflows(tmp_path, **{"broken.yml": "jobs: [unclosed", "nojobs.yml": "name: x\n"})

    assert pcr.jobs_elsewhere(tmp_path, "nex-demo") == ([], ["broken.yml", "nojobs.yml"])


def test_a_checkout_without_workflows_has_nothing_elsewhere(tmp_path):
    assert pcr.jobs_elsewhere(tmp_path, "nex-demo") == ([], [])


# --- what the tab offers ----------------------------------------------------------------------------------------------


def _decide(**overrides):
    given = dict(
        label="andros-ubuntu-nex-demo",
        repo=REPO,
        archived=False,
        state=ci_runner.CURRENT,
        github_status="online",
        github_error=False,
        starting=False,
    )
    given.update(overrides)
    return pcr.decide(**given)


@pytest.mark.parametrize(
    ("overrides", "action"),
    [
        ({"state": ci_runner.NONE, "github_status": None}, pcr.START),
        ({"state": ci_runner.NONE, "github_status": None, "github_error": True}, pcr.START),
        ({"github_status": "offline"}, pcr.START),
        ({"github_status": None}, pcr.START),
        ({"state": ci_runner.OUTDATED}, pcr.RENEW),
        ({"state": ci_runner.LEGACY}, pcr.RENEW),
        ({}, None),
        ({"github_status": None, "github_error": True}, None),
        ({"state": None}, None),
        ({"state": ci_runner.NONE, "starting": True}, None),
        ({"state": ci_runner.NONE, "archived": True}, None),
        ({"state": ci_runner.NONE, "repo": None}, None),
    ],
)
def test_the_button_is_offered_exactly_where_the_runner_is_not_working(overrides, action):
    assert _decide(**overrides)[1] == action


def test_each_refusal_says_why_in_words():
    assert _decide(state=None) == (pcr.DOCKER_UNKNOWN, None)
    assert _decide(starting=True, state=ci_runner.NONE) == (pcr.STARTING, None)
    assert _decide(archived=True) == (pcr.IS_ARCHIVED, None)
    assert _decide(repo=None) == (pcr.NO_REPOSITORY, None)
    assert _decide() == (
        "Kontroly bežia na vlastnom vykonávači andros-ubuntu-nex-demo na ANDROSe, prihlásenom na GitHube.",
        None,
    )


# --- the route --------------------------------------------------------------------------------------------------------


class Server:
    """Docker and GitHub as the cockpit would find them, and the starts it asked for."""

    def __init__(self, monkeypatch, *, state=ci_runner.NONE, status=None, busy=False, start_note=None):
        self.state, self.status, self.busy, self.start_note = state, status, busy, start_note
        self.asked: list[str] = []
        self.started: list[tuple] = []
        monkeypatch.setattr(ci_runner, "state", self._state)
        monkeypatch.setattr(github_validation, "runner_status", self._status)
        monkeypatch.setattr(github_validation, "runner_busy", self._busy)
        monkeypatch.setattr(create_project_postscaffold, "start_ci_runner", self._start)

    def _state(self, slug):
        self.asked.append(f"docker {slug}")
        if isinstance(self.state, Exception):
            raise self.state
        return self.state

    def _status(self, repo, label, *, timeout):
        self.asked.append(f"github {repo} {label}")
        if isinstance(self.status, Exception):
            raise self.status
        return self.status

    def _busy(self, repo, label, *, timeout):
        self.asked.append(f"busy {repo} {label}")
        return self.busy

    def _start(self, slug, repo_url, *, github_org=None):
        self.started.append((slug, repo_url, github_org))
        # What founding does when it succeeds: the pair runs and GitHub has it online.
        if self.start_note is None:
            self.state, self.status = ci_runner.CURRENT, "online"
        return self.start_note


@pytest.fixture
def nex_inbox(db_session, monkeypatch, tmp_path):
    """NEX Inbox as on 20.10.2026: taken over, a repository on GitHub, both workflows on GitHub's machines."""
    pcr.forget_all()
    db = db_session
    owner = User(
        username=f"o_{uuid.uuid4().hex[:6]}", email=f"{uuid.uuid4().hex[:6]}@t.sk", password_hash="x", role="ri"
    )
    stranger = User(
        username=f"s_{uuid.uuid4().hex[:6]}", email=f"{uuid.uuid4().hex[:6]}@t.sk", password_hash="x", role="ri"
    )
    db.add_all([owner, stranger])
    db.flush()
    project = Project(
        name="NEX Inbox",
        slug=f"nex-inbox-{uuid.uuid4().hex[:6]}",
        type="standard",
        auth_mode="password",
        description="",
        created_by=owner.id,
        repo_url=REPO,
        adopted=True,
    )
    db.add(project)
    db.flush()
    projects = tmp_path / "projects"
    _workflows(projects / project.slug, **{"ci.yml": NIB_CI, "release-smoke.yml": NIB_SMOKE})
    monkeypatch.setattr(claude_agent, "PROJECTS_ROOT", projects)
    yield db, owner, stranger, project
    pcr.forget_all()


def _as(db, user):
    user_id = user.id
    app.dependency_overrides[get_current_user] = lambda: db.get(User, user_id)


def test_a_taken_over_project_without_a_runner_is_offered_one_and_shows_its_jobs_elsewhere(
    client, nex_inbox, monkeypatch
):
    db, owner, _stranger, project = nex_inbox
    Server(monkeypatch)
    _as(db, owner)

    answer = client.get(f"/api/v1/projects/{project.slug}/ci-runner")

    assert answer.status_code == 200, answer.text
    body = answer.json()
    label = f"andros-ubuntu-{project.slug}"
    assert body == {
        "label": label,
        "repo": REPO,
        "state": "none",
        "github_status": None,
        "starting": False,
        "healthy": False,
        "summary": "Projekt nemá vlastný vykonávač kontrol na ANDROSe.",
        "action": pcr.START,
        "note": None,
        "jobs_elsewhere": [
            {"workflow": "ci.yml", "job": "lint", "runs_on": "ubuntu-latest"},
            {"workflow": "ci.yml", "job": "test", "runs_on": "ubuntu-latest"},
            {"workflow": "release-smoke.yml", "job": "gate", "runs_on": "ubuntu-latest"},
        ],
        "unreadable_workflows": [],
    }


def test_one_click_starts_the_runner_as_founding_does_and_the_tab_then_shows_it_working(client, nex_inbox, monkeypatch):
    db, owner, _stranger, project = nex_inbox
    server = Server(monkeypatch)
    _as(db, owner)

    answer = client.post(f"/api/v1/projects/{project.slug}/ci-runner/start")

    assert answer.status_code == 202, answer.text
    assert (answer.json()["starting"], answer.json()["summary"], answer.json()["action"]) == (True, pcr.STARTING, None)
    assert server.started == [(project.slug, REPO, "rauschiccsk")]

    after = client.get(f"/api/v1/projects/{project.slug}/ci-runner").json()

    assert (after["starting"], after["healthy"], after["action"], after["note"]) == (False, True, None, None)
    assert after["jobs_elsewhere"], "the runner does not change where the jobs run — that is the project's change"


def test_a_start_that_did_not_end_online_says_why_and_offers_the_button_again(client, nex_inbox, monkeypatch):
    db, owner, _stranger, project = nex_inbox
    Server(monkeypatch, start_note=create_project_postscaffold.CI_RUNNER_NOT_STARTED)
    _as(db, owner)

    client.post(f"/api/v1/projects/{project.slug}/ci-runner/start")
    after = client.get(f"/api/v1/projects/{project.slug}/ci-runner").json()

    assert (after["note"], after["action"]) == (create_project_postscaffold.CI_RUNNER_NOT_STARTED, pcr.START)


def test_a_working_runner_is_not_started_again(client, nex_inbox, monkeypatch):
    db, owner, _stranger, project = nex_inbox
    server = Server(monkeypatch, state=ci_runner.CURRENT, status="online")
    _as(db, owner)

    tab = client.get(f"/api/v1/projects/{project.slug}/ci-runner").json()
    answer = client.post(f"/api/v1/projects/{project.slug}/ci-runner/start")

    assert (tab["healthy"], tab["action"]) == (True, None)
    assert answer.status_code == 409
    assert answer.json()["detail"] == tab["summary"]
    assert server.started == []


def test_a_runner_doing_a_check_is_not_renewed_under_it(client, nex_inbox, monkeypatch):
    db, owner, _stranger, project = nex_inbox
    server = Server(monkeypatch, state=ci_runner.OUTDATED, status="online", busy=True)
    _as(db, owner)

    answer = client.post(f"/api/v1/projects/{project.slug}/ci-runner/start")

    assert (answer.status_code, answer.json()["detail"]) == (409, pcr.BUSY)
    assert server.started == []


def test_an_idle_outdated_runner_is_renewed(client, nex_inbox, monkeypatch):
    db, owner, _stranger, project = nex_inbox
    server = Server(monkeypatch, state=ci_runner.OUTDATED, status="online", busy=False)
    _as(db, owner)

    answer = client.post(f"/api/v1/projects/{project.slug}/ci-runner/start")

    assert answer.status_code == 202, answer.text
    assert server.started == [(project.slug, REPO, "rauschiccsk")]


def test_a_second_click_while_starting_does_not_start_twice(client, nex_inbox, monkeypatch):
    db, owner, _stranger, project = nex_inbox
    server = Server(monkeypatch)
    _as(db, owner)
    assert pcr.claim(project.slug)  # the first click's start still running

    tab = client.get(f"/api/v1/projects/{project.slug}/ci-runner").json()
    answer = client.post(f"/api/v1/projects/{project.slug}/ci-runner/start")

    assert (tab["starting"], tab["action"]) == (True, None)
    assert answer.status_code == 409
    assert server.started == []


def test_a_docker_that_cannot_be_asked_is_said_and_offers_nothing(client, nex_inbox, monkeypatch):
    db, owner, _stranger, project = nex_inbox
    server = Server(monkeypatch, state=RuntimeError("docker ps failed: Cannot connect"))
    _as(db, owner)

    tab = client.get(f"/api/v1/projects/{project.slug}/ci-runner").json()
    answer = client.post(f"/api/v1/projects/{project.slug}/ci-runner/start")

    assert (tab["summary"], tab["action"]) == (pcr.DOCKER_UNKNOWN, None)
    assert answer.status_code == 409
    assert server.started == []


def test_a_github_that_cannot_be_asked_is_never_read_as_offline(client, nex_inbox, monkeypatch):
    db, owner, _stranger, project = nex_inbox
    Server(monkeypatch, state=ci_runner.CURRENT, status=RuntimeError("GitHub API returned unexpected status 502"))
    _as(db, owner)

    tab = client.get(f"/api/v1/projects/{project.slug}/ci-runner").json()

    assert (tab["healthy"], tab["action"]) == (False, None)
    assert "nedá zistiť" in tab["summary"]


def test_another_persons_project_shows_nothing_and_asks_nobody(client, nex_inbox, monkeypatch):
    db, _owner, stranger, project = nex_inbox
    server = Server(monkeypatch)
    _as(db, stranger)

    seen = client.get(f"/api/v1/projects/{project.slug}/ci-runner")
    started = client.post(f"/api/v1/projects/{project.slug}/ci-runner/start")

    assert (seen.status_code in (403, 404), started.status_code in (403, 404)) == (True, True)
    assert (server.asked, server.started) == ([], [])


def test_two_clicks_at_once_start_the_runner_once(client, nex_inbox, monkeypatch):
    """Both clicks looked at the tab before either claimed the start — the claim, not the look, decides."""
    db, owner, _stranger, project = nex_inbox
    server = Server(monkeypatch)
    _as(db, owner)
    looked = pcr.overview

    def look_then_the_other_click_claims(**given):
        found = looked(**given)
        pcr.claim(project.slug)
        return found

    monkeypatch.setattr(pcr, "overview", look_then_the_other_click_claims)

    answer = client.post(f"/api/v1/projects/{project.slug}/ci-runner/start")

    assert (answer.status_code, answer.json()["detail"]) == (409, pcr.ALREADY_STARTING)
    assert server.started == []


def test_a_runner_that_came_online_after_the_wait_drops_the_old_warning(client, nex_inbox, monkeypatch):
    """GitHub keeps a replaced runner's session up to ~3 minutes — the start may give up waiting before it is online."""
    db, owner, _stranger, project = nex_inbox
    never_online = create_project_postscaffold.CI_RUNNER_NEVER_ONLINE.format(
        wait=create_project_postscaffold.CI_RUNNER_ONLINE_WAIT
    )
    server = Server(monkeypatch, start_note=never_online)
    _as(db, owner)
    client.post(f"/api/v1/projects/{project.slug}/ci-runner/start")
    server.state = ci_runner.CURRENT
    assert client.get(f"/api/v1/projects/{project.slug}/ci-runner").json()["note"] == never_online

    server.status = "online"
    after = client.get(f"/api/v1/projects/{project.slug}/ci-runner").json()

    assert (after["healthy"], after["note"]) == (True, None)
