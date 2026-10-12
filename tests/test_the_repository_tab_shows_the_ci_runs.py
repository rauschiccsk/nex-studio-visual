"""DEV-84 — the repository tab: the selected project's CI runs as GitHub lists them, and what only the cockpit knows.

12.10.2026 the Director keeps GitHub open on another monitor to follow CI and wants a tab in the cockpit that shows at
least what GitHub's Actions page does.

GitHub is played by ``httpx.MockTransport``: the requests the cockpit sends are the real ones, the answers are the
shapes GitHub sent for NEX Inbox on 12.10.2026.
"""

from __future__ import annotations

import asyncio
import subprocess
import uuid
from pathlib import Path

import httpx
import pytest

from backend.config.settings import settings
from backend.core.security import get_current_user
from backend.db.models.customers import Customer
from backend.db.models.deploy import DeployEvent
from backend.db.models.foundation import User
from backend.db.models.projects import Project
from backend.db.models.tasks import Epic, Feat, Task
from backend.db.models.versions import Version
from backend.main import app
from backend.services import ci_runs, ci_status, claude_agent

REPO = "rauschiccsk/nex-inbox"


def _run(run_id, *, conclusion="success", status="completed", event="push", branch="main", sha="a" * 40, **extra):
    return {
        "id": run_id,
        "run_number": run_id % 1000,
        "run_attempt": 1,
        "name": "CI",
        "display_title": extra.pop("display_title", "CI"),
        "event": event,
        "status": status,
        "conclusion": conclusion,
        "head_branch": branch,
        "head_sha": sha,
        "head_commit": {"message": extra.pop("message", "feat: export (TASK #20.1.1)\n\nbody")},
        "actor": {"login": "rauschiccsk"},
        "triggering_actor": {"login": extra.pop("by", "rauschiccsk")},
        "created_at": "2026-10-12T20:42:08Z",
        "run_started_at": "2026-10-12T20:42:08Z",
        "updated_at": "2026-10-12T20:54:04Z",
        "html_url": f"https://github.com/{REPO}/actions/runs/{run_id}",
        **extra,
    }


JOBS_RED = {
    "jobs": [
        {"name": "smoke", "conclusion": "success", "steps": [{"name": "Run", "conclusion": "success"}]},
        {
            "name": "gate",
            "conclusion": "failure",
            "steps": [
                {"name": "Set up job", "conclusion": "success"},
                {"name": "Brána vydania (release_smoke_test.sh)", "conclusion": "failure"},
                {"name": "Post", "conclusion": "skipped"},
            ],
        },
    ]
}


class FakeGitHub:
    """Answers like GitHub and remembers every question."""

    def __init__(self, runs, *, jobs=None, status=200, total=193):
        self.runs, self.jobs, self.status, self.total = runs, jobs or JOBS_RED, status, total
        self.asked: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.asked.append(request.url.path)
        assert request.headers["Authorization"] == "Bearer test-token"
        if self.status != 200:
            return httpx.Response(self.status, json={"message": "no"})
        if request.url.path.endswith("/jobs"):
            return httpx.Response(200, json=self.jobs)
        assert request.url.params["per_page"] == str(settings.ci_runs_listed)
        return httpx.Response(200, json={"total_count": self.total, "workflow_runs": self.runs})


@pytest.fixture
def github(monkeypatch):
    """Install a fake GitHub; returns a function that swaps its answers."""
    monkeypatch.setattr(settings, "github_token", "test-token")
    ci_runs.forget_all()
    current: dict[str, FakeGitHub] = {}
    real = httpx.AsyncClient

    async def _answer(request):
        await asyncio.sleep(0.01)  # a real answer takes a while — long enough for another window to ask meanwhile
        return current["gh"](request)

    def _client(**kwargs):
        return real(transport=httpx.MockTransport(_answer), **kwargs)

    monkeypatch.setattr(ci_runs.httpx, "AsyncClient", _client)

    def _use(fake: FakeGitHub) -> FakeGitHub:
        current["gh"] = fake
        return fake

    yield _use
    ci_runs.forget_all()


# ── what a run is ──────────────────────────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("conclusion", sorted(ci_status.CI_CONCLUSIONS_RED))
def test_the_tab_says_failed_exactly_where_the_gate_floors_a_version(conclusion):
    assert ci_runs.state_of("completed", conclusion) == (ci_runs.FAILURE, "Zlyhalo")


@pytest.mark.parametrize("conclusion", sorted(ci_status.CI_CONCLUSIONS_OK))
def test_what_the_gate_lets_through_is_never_red(conclusion):
    assert ci_runs.state_of("completed", conclusion)[0] == ci_runs.SUCCESS


@pytest.mark.parametrize(
    ("status", "conclusion", "expected"),
    [
        ("completed", "success", ("success", "Prešlo")),
        ("completed", "skipped", ("success", "Preskočené")),
        ("completed", "cancelled", ("cancelled", "Zrušené")),
        ("completed", "stale", ("other", "Bez výsledku")),
        ("in_progress", None, ("running", "Beží")),
        ("queued", None, ("waiting", "Čaká")),
        ("waiting", None, ("waiting", "Čaká")),
    ],
)
def test_every_state_reads_in_words(status, conclusion, expected):
    assert ci_runs.state_of(status, conclusion) == expected


def test_a_run_started_by_hand_shows_its_commit_not_the_workflow_name():
    """The rows „CI“ without a description the Director asked about were runs started by hand: GitHub titles them with
    the workflow's name. The commit's subject says what was checked."""
    run = ci_runs.run_from_github(
        _run(7, event="workflow_dispatch", display_title="CI", message="proof(red): nginx without the .mjs type\n\nx")
    )

    assert (run.subject, run.event_label) == ("proof(red): nginx without the .mjs type", "spustené ručne")


def test_the_person_is_who_started_the_run():
    run = ci_runs.run_from_github(_run(7, by="tibor"))

    assert run.actor == "tibor"


def test_the_failed_step_is_the_first_failed_step_of_the_first_failed_job():
    assert ci_runs.failed_step_from_jobs(JOBS_RED["jobs"]) == "gate › Brána vydania (release_smoke_test.sh)"


def test_a_job_that_failed_without_a_failed_step_is_named_alone():
    assert ci_runs.failed_step_from_jobs([{"name": "test", "conclusion": "failure", "steps": []}]) == "test"


def test_a_run_still_going_counts_its_time_up_to_now():
    run = ci_runs.run_from_github(_run(7, status="in_progress", conclusion=None))
    later = run.started_at.replace(minute=run.started_at.minute + 5)

    assert run.duration_seconds(later) == 300


def test_a_finished_run_counts_up_to_its_end():
    run = ci_runs.run_from_github(_run(7))

    assert run.duration_seconds(run.started_at.replace(hour=23)) == 716


# ── asking GitHub, once for everybody ──────────────────────────────────────────────────────────────────────────────


async def test_many_windows_at_once_cost_one_question(github):
    gh = github(FakeGitHub([_run(1)]))

    answers = await asyncio.gather(*(ci_runs.listing(REPO, timeout=5) for _ in range(5)))

    assert [len(a.runs) for a in answers] == [1] * 5
    assert gh.asked == [f"/repos/{REPO}/actions/runs"]


async def test_the_answer_is_asked_again_once_it_is_older_than_the_setting(github, monkeypatch):
    gh = github(FakeGitHub([_run(1)]))
    clock = [1000.0]
    monkeypatch.setattr(ci_runs, "_clock", lambda: clock[0])

    await ci_runs.listing(REPO, timeout=5)
    clock[0] += settings.ci_runs_cache_seconds - 1
    await ci_runs.listing(REPO, timeout=5)
    assert len(gh.asked) == 1, "asked before the shared answer was old"
    clock[0] += 2
    await ci_runs.listing(REPO, timeout=5)

    assert len(gh.asked) == 2, "the answer stayed forever — a fixed build would keep showing red"


async def test_the_failed_step_of_a_run_is_asked_once(github, monkeypatch):
    gh = github(FakeGitHub([_run(1, conclusion="failure"), _run(2)]))
    clock = [1000.0]
    monkeypatch.setattr(ci_runs, "_clock", lambda: clock[0])

    first = await ci_runs.listing(REPO, timeout=5)
    clock[0] += settings.ci_runs_cache_seconds + 1
    second = await ci_runs.listing(REPO, timeout=5)

    assert [r.failed_step for r in first.runs] == ["gate › Brána vydania (release_smoke_test.sh)", None]
    assert [r.failed_step for r in second.runs] == [r.failed_step for r in first.runs]
    assert gh.asked.count(f"/repos/{REPO}/actions/runs/1/attempts/1/jobs") == 1


async def test_when_github_stops_answering_the_last_answer_stays_marked_as_old(github, monkeypatch):
    github(FakeGitHub([_run(1)]))
    clock = [1000.0]
    monkeypatch.setattr(ci_runs, "_clock", lambda: clock[0])
    fresh = await ci_runs.listing(REPO, timeout=5)

    github(FakeGitHub([], status=503))
    clock[0] += settings.ci_runs_cache_seconds + 1
    old = await ci_runs.listing(REPO, timeout=5)

    assert (old.stale, old.error, [r.id for r in old.runs]) == (True, "GitHub odpovedal kódom 503", [1])
    assert old.fetched_at == fresh.fetched_at, "old runs must say when they are from"


async def test_without_any_answer_there_are_no_runs_only_the_reason(github):
    github(FakeGitHub([], status=404))

    found = await ci_runs.listing(REPO, timeout=5)

    assert (found.runs, found.stale) == ([], False)
    assert found.error == "repozitár na GitHube nie je, alebo k nemu kokpit nemá prístup"


async def test_a_network_that_fails_is_a_reason_not_a_crash(monkeypatch):
    monkeypatch.setattr(settings, "github_token", "test-token")
    ci_runs.forget_all()
    real = httpx.AsyncClient

    def _down(request):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(ci_runs.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(_down), **kw))

    found = await ci_runs.listing(REPO, timeout=5)

    assert found.error == ci_runs.NOT_REACHED
    ci_runs.forget_all()


async def test_no_repository_and_no_token_say_so(monkeypatch):
    ci_runs.forget_all()
    assert (await ci_runs.listing(None, timeout=5)).error == ci_runs.NO_REPOSITORY
    monkeypatch.setattr(settings, "github_token", "")
    assert (await ci_runs.listing(REPO, timeout=5)).error == ci_runs.NO_TOKEN


# ── the route: what only the cockpit knows ─────────────────────────────────────────────────────────────────────────


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def nex_inbox(db_session, monkeypatch, tmp_path):
    """A project with task 20.1.1 in version 1.7.0, a customer running 1.7.0 on PROD and its tag in the checkout."""
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
    )
    db.add(project)
    db.flush()
    version = Version(project_id=project.id, version_number="1.7.0")
    db.add(version)
    db.flush()
    epic = Epic(project_id=project.id, version_id=version.id, number=20, title="Export", status="planned")
    db.add(epic)
    db.flush()
    feat = Feat(epic_id=epic.id, number=1, title="ISDOC", status="todo")
    db.add(feat)
    db.flush()
    task = Task(feat_id=feat.id, number=1, title="Kód položky dodávateľa", task_type="backend", status="done")
    customer = Customer(project_id=project.id, name="MÁGERSTAV", slug="magerstav")
    db.add_all([task, customer])
    db.flush()
    db.add(
        DeployEvent(
            customer_id=customer.id,
            project_id=project.id,
            version_number="1.7.0",
            environment="prod",
            event_type="deploy",
            status="ok",
        )
    )
    db.flush()

    projects = tmp_path / "projects"
    work = projects / project.slug
    work.mkdir(parents=True)
    _git(work, "init", "-q", "-b", "main")
    _git(work, "config", "user.email", "studio@isnex.eu")
    _git(work, "config", "user.name", "NEX Studio")
    (work / "a.txt").write_text("a\n", encoding="utf-8")
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", "feat: export (TASK #20.1.1)")
    _git(work, "tag", "-a", "v1.7.0", "-m", "NEX Studio: v1.7.0 verified")
    tagged = _git(work, "rev-parse", "HEAD")
    # The checkout moved on after the release: what runs at the customer is the tagged commit, not HEAD.
    (work / "b.txt").write_text("b\n", encoding="utf-8")
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", "feat: next (TASK #21.1.1)")
    monkeypatch.setattr(claude_agent, "PROJECTS_ROOT", projects)
    return db, owner, stranger, project, task, version, tagged


def _same_label_elsewhere(db, owner):
    """Another project of the same person with its own task 20.1.1 — numbers run per project, not globally."""
    other = Project(
        name="NEX Manager",
        slug=f"nex-manager-{uuid.uuid4().hex[:6]}",
        type="standard",
        auth_mode="password",
        description="",
        created_by=owner.id,
    )
    db.add(other)
    db.flush()
    version = Version(project_id=other.id, version_number="9.9.9")
    db.add(version)
    db.flush()
    epic = Epic(project_id=other.id, version_id=version.id, number=20, title="Iné", status="planned")
    db.add(epic)
    db.flush()
    feat = Feat(epic_id=epic.id, number=1, title="Iné", status="todo")
    db.add(feat)
    db.flush()
    db.add(Task(feat_id=feat.id, number=1, title="Cudzia úloha", task_type="backend", status="done"))
    db.flush()


def _as(db, user):
    user_id = user.id
    app.dependency_overrides[get_current_user] = lambda: db.get(User, user_id)


def test_a_run_links_its_task_and_says_where_its_commit_runs(client, nex_inbox, github):
    db, owner, _stranger, project, task, version, deployed_sha = nex_inbox
    _same_label_elsewhere(db, owner)
    github(
        FakeGitHub(
            [
                _run(1, sha=deployed_sha, branch="v1.7.0"),
                _run(2, conclusion="failure", message="fix: something else", sha="b" * 40),
            ]
        )
    )
    _as(db, owner)

    answer = client.get(f"/api/v1/projects/{project.slug}/ci-runs")

    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert (body["repo"], body["total_count"], body["refresh_seconds"]) == (REPO, 193, settings.ci_runs_cache_seconds)
    assert body["actions_url"] == f"https://github.com/{REPO}/actions"
    first, second = body["runs"]
    assert first["task"] == {
        "label": "20.1.1",
        "task_id": str(task.id),
        "title": "Kód položky dodávateľa",
        "version_id": str(version.id),
        "version_number": "1.7.0",
    }
    assert first["deployed"] == [{"environment": "prod", "customer": "MÁGERSTAV", "version": "1.7.0"}]
    assert (second["task"], second["deployed"]) == (None, [])
    assert (second["state_label"], second["failed_step"]) == ("Zlyhalo", "gate › Brána vydania (release_smoke_test.sh)")


def test_another_persons_project_shows_nothing(client, nex_inbox, github):
    db, _owner, stranger, project, *_ = nex_inbox
    gh = github(FakeGitHub([_run(1)]))
    _as(db, stranger)

    answer = client.get(f"/api/v1/projects/{project.slug}/ci-runs")

    assert answer.status_code in (403, 404)
    assert gh.asked == [], "GitHub was asked for a project the person may not see"


def test_a_project_without_a_repository_says_so(client, nex_inbox, github):
    db, owner, _stranger, project, *_ = nex_inbox
    project.repo_url = None
    db.flush()
    gh = github(FakeGitHub([_run(1)]))
    _as(db, owner)

    body = client.get(f"/api/v1/projects/{project.slug}/ci-runs").json()

    assert (body["repo"], body["runs"], body["error"]) == (None, [], ci_runs.NO_REPOSITORY)
    assert gh.asked == []


@pytest.mark.parametrize(
    ("stored", "repo"),
    [
        ("rauschiccsk/nex-inbox", "rauschiccsk/nex-inbox"),
        ("https://github.com/rauschiccsk/nex-inbox.git", "rauschiccsk/nex-inbox"),
        ("git@github.com:rauschiccsk/nex-inbox.git", "rauschiccsk/nex-inbox"),
        ("nex-inbox", None),
        ("", None),
    ],
)
def test_the_repository_is_read_from_what_the_project_keeps(stored, repo):
    from backend.api.routes.ci_runs import _repo

    assert _repo(stored) == repo
