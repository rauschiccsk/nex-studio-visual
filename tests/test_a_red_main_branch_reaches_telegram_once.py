"""DEV-85 — a failed CI run on a project's main branch reaches the project's addressee on Telegram once, the return to
green once more, and the repository tab is marked red while the main branch is.

GitHub is played by ``httpx.MockTransport`` and Telegram by a recorder; the project's checkout is a real git repository,
so the branch the watch follows is the one git reports.
"""

from __future__ import annotations

import subprocess
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from backend.config.settings import settings
from backend.core.security import get_current_user
from backend.db.models.foundation import User
from backend.db.models.project_ci_watch import ProjectCiWatch
from backend.db.models.projects import Project
from backend.main import app
from backend.services import ci_runs, ci_watch, claude_agent, notify

REPO = "rauschiccsk/nex-inbox"
CHAT = "7204918893"


def _run(run_id, *, conclusion="success", status="completed", branch="main", workflow="CI", attempt=1, subject=None):
    return {
        "id": run_id,
        "run_number": run_id,
        "run_attempt": attempt,
        "name": workflow,
        "display_title": workflow,
        "event": "push",
        "status": status,
        "conclusion": conclusion,
        "head_branch": branch,
        "head_sha": "a" * 40,
        "head_commit": {"message": subject or f"feat: change {run_id} (TASK #20.1.1)"},
        "actor": {"login": "rauschiccsk"},
        "triggering_actor": {"login": "rauschiccsk"},
        "created_at": "2026-10-12T20:42:08Z",
        "run_started_at": "2026-10-12T20:42:08Z",
        "updated_at": "2026-10-12T20:54:04Z",
        "html_url": f"https://github.com/{REPO}/actions/runs/{run_id}",
    }


JOBS = {"jobs": [{"name": "lint", "conclusion": "failure", "steps": [{"name": "ruff check", "conclusion": "failure"}]}]}


class World:
    """GitHub answering the runs it is given, Telegram recording (or refusing) what it is sent."""

    def __init__(self, monkeypatch):
        self.runs: list[dict] = []
        self.github_down = False
        self.telegram_up = True
        self.sent: list[tuple[str, str]] = []
        monkeypatch.setattr(settings, "github_token", "test-token")
        monkeypatch.setattr(settings, "app_public_url", "https://studio.isnex.eu")
        real = httpx.AsyncClient

        def _answer(request: httpx.Request) -> httpx.Response:
            if self.github_down:
                return httpx.Response(503)
            if request.url.path.endswith("/jobs"):
                return httpx.Response(200, json=JOBS)
            return httpx.Response(200, json={"total_count": len(self.runs), "workflow_runs": self.runs})

        monkeypatch.setattr(
            ci_runs.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(_answer), **kw)
        )

        async def _send(message: str, chat_id: str) -> bool:
            if not self.telegram_up:
                return False
            self.sent.append((chat_id, message))
            return True

        monkeypatch.setattr(notify, "send_telegram", _send)

    def show(self, *runs):
        """GitHub now lists these runs (newest first); the shared answer is forgotten, as after its 30 s."""
        self.runs = list(runs)
        ci_runs.forget_all()


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def world(db_session, monkeypatch, tmp_path):
    db = db_session
    # The project's owner (who may open it) and its addressee (who gets its Telegram messages) are two fields; here two
    # people, so a message sent to the wrong one shows.
    creator = User(
        username=f"c_{uuid.uuid4().hex[:6]}",
        email=f"{uuid.uuid4().hex[:6]}@t.sk",
        password_hash="x",
        role="ri",
        telegram_chat_id="111",
    )
    owner = User(
        username=f"o_{uuid.uuid4().hex[:6]}",
        email=f"{uuid.uuid4().hex[:6]}@t.sk",
        password_hash="x",
        role="ri",
        telegram_chat_id=f" {CHAT} ",
    )
    db.add_all([creator, owner])
    db.flush()
    project = Project(
        name="NEX Inbox",
        slug=f"nex-inbox-{uuid.uuid4().hex[:6]}",
        type="standard",
        auth_mode="password",
        description="",
        created_by=creator.id,
        owner_id=owner.id,
        repo_url=REPO,
    )
    db.add(project)
    db.flush()
    projects = tmp_path / "projects"
    work = projects / project.slug
    work.mkdir(parents=True)
    _git(work, "init", "-q", "-b", "main")
    _git(work, "config", "user.email", "studio@isnex.eu")
    _git(work, "config", "user.name", "NEX Studio")
    _git(work, "commit", "-q", "--allow-empty", "-m", "chore: start")
    monkeypatch.setattr(claude_agent, "PROJECTS_ROOT", projects)
    ci_runs.forget_all()
    w = World(monkeypatch)
    yield db, project, owner, work, w
    ci_runs.forget_all()


def _creator(db, project):
    return db.get(User, project.created_by)


async def _look(db, project):
    due = await ci_watch.watch_project(db, project, timeout=5)
    db.flush()
    return due


async def test_the_first_look_tells_nothing_even_when_red(world):
    """The watch tells what happens from now on — the red runs of yesterday are not news."""
    db, project, _owner, _work, w = world
    w.show(_run(10, conclusion="failure"))

    await _look(db, project)
    w.show(_run(10, conclusion="failure"))
    await _look(db, project)

    assert w.sent == [], "yesterday's red run was told as news"
    row = db.get(ProjectCiWatch, project.id)
    assert (row.branch, row.runs["CI"]["run_id"], row.runs["CI"]["state"]) == ("main", 10, "failure")


async def test_a_failed_run_on_the_main_branch_is_told_once(world):
    db, project, _owner, _work, w = world
    w.show(_run(10))
    await _look(db, project)

    w.show(_run(11, conclusion="failure", subject="fix: delivery to Genesis (TASK #33.1.2)"), _run(10))
    await _look(db, project)
    w.show(_run(11, conclusion="failure", subject="fix: delivery to Genesis (TASK #33.1.2)"), _run(10))
    await _look(db, project)

    assert w.sent == [
        (
            CHAT,
            "❌ NEX Inbox: kontroly na vetve main zlyhali — CI #11, commit „fix: delivery to Genesis (TASK #33.1.2)“."
            f"\nZlyhal krok: lint › ruff check\nhttps://studio.isnex.eu/repozitar?projekt={project.slug}",
        )
    ]


async def test_green_after_red_is_told_once_and_green_after_green_not_at_all(world):
    db, project, _owner, _work, w = world
    w.show(_run(10, conclusion="failure"))
    await _look(db, project)

    w.show(_run(12), _run(10, conclusion="failure"))
    await _look(db, project)
    w.show(_run(13), _run(12), _run(10, conclusion="failure"))
    await _look(db, project)

    assert [m for _c, m in w.sent] == [
        "✅ NEX Inbox: kontroly na vetve main sú znovu zelené — CI #12, commit „feat: change 12 (TASK #20.1.1)“."
        f"\nhttps://studio.isnex.eu/repozitar?projekt={project.slug}"
    ]


async def test_a_side_branch_and_a_run_still_going_tell_nothing(world):
    db, project, _owner, _work, w = world
    w.show(_run(10))
    await _look(db, project)

    w.show(
        _run(14, status="in_progress", conclusion=None),
        _run(13, conclusion="failure", branch="ci/v180-gate-red-mjs"),
        _run(10),
    )
    await _look(db, project)

    assert w.sent == []


async def test_a_run_that_was_still_going_is_told_when_it_fails(world):
    """A run seen while it runs is not finished — it must not count as seen, or its failure would never be told."""
    db, project, _owner, _work, w = world
    w.show(_run(10))
    await _look(db, project)
    w.show(_run(14, status="in_progress", conclusion=None), _run(10))
    await _look(db, project)

    w.show(_run(14, conclusion="failure"), _run(10))
    await _look(db, project)

    assert [m.split(" — ")[1].split(",")[0] for _c, m in w.sent] == ["CI #14"]


async def test_each_workflow_is_followed_on_its_own_and_the_red_one_marks_the_tab(world):
    db, project, _owner, _work, w = world
    w.show(_run(10), _run(9, workflow="Release smoke gate"))
    await _look(db, project)

    w.show(_run(21, workflow="Release smoke gate"), _run(20, conclusion="failure"), _run(10))
    await _look(db, project)

    assert [m.split(" — ")[1].split(",")[0] for _c, m in w.sent] == ["CI #20"]
    assert [f["workflow"] for f in ci_watch.failing(db.get(ProjectCiWatch, project.id))] == ["CI"]


async def test_a_rerun_of_a_failed_run_that_passes_is_told_as_green(world):
    db, project, _owner, _work, w = world
    w.show(_run(10))
    await _look(db, project)
    w.show(_run(20, conclusion="failure"), _run(10))
    await _look(db, project)

    w.show(_run(20, attempt=2), _run(10))
    await _look(db, project)

    assert [m[:2] for _c, m in w.sent] == ["❌ ", "✅ "]
    assert ci_watch.failing(db.get(ProjectCiWatch, project.id)) == []


async def test_what_was_told_survives_a_restart(world):
    """The state is in the database — a new session (a restarted or redeployed cockpit) repeats nothing."""
    db, project, _owner, _work, w = world
    w.show(_run(10))
    await _look(db, project)
    w.show(_run(11, conclusion="failure"), _run(10))
    await _look(db, project)

    db.expire_all()
    w.show(_run(11, conclusion="failure"), _run(10))
    await _look(db, project)

    assert len(w.sent) == 1


async def test_a_message_telegram_refused_is_sent_later_and_one_too_old_is_dropped(world):
    db, project, _owner, _work, w = world
    w.show(_run(10))
    await _look(db, project)
    w.telegram_up = False
    w.show(_run(11, conclusion="failure"), _run(10))
    await _look(db, project)
    assert w.sent == [] and len(db.get(ProjectCiWatch, project.id).undelivered) == 1

    w.telegram_up = True
    await _look(db, project)

    assert len(w.sent) == 1 and w.sent[0][1].startswith("❌ NEX Inbox")
    assert db.get(ProjectCiWatch, project.id).undelivered == []

    w.telegram_up = False
    w.show(_run(12), _run(11, conclusion="failure"), _run(10))
    await _look(db, project)
    row = db.get(ProjectCiWatch, project.id)
    old = datetime.now(timezone.utc) - timedelta(hours=settings.ci_watch_retry_hours, minutes=1)
    row.undelivered = [{**row.undelivered[0], "at": old.isoformat()}]
    db.flush()
    w.telegram_up = True
    await _look(db, project)

    assert len(w.sent) == 1, "a message older than the retry window still went out"
    assert db.get(ProjectCiWatch, project.id).undelivered == []


async def test_without_a_telegram_chat_nothing_is_kept_to_retry(world):
    db, project, owner, _work, w = world
    owner.telegram_chat_id = None
    db.flush()
    w.show(_run(10))
    await _look(db, project)
    w.show(_run(11, conclusion="failure"), _run(10))

    due = await _look(db, project)

    assert len(due) == 1 and w.sent == []
    assert db.get(ProjectCiWatch, project.id).undelivered == []


async def test_a_checkout_moved_to_another_branch_starts_over_quietly(world):
    db, project, _owner, work, w = world
    w.show(_run(10, conclusion="failure"))
    await _look(db, project)

    _git(work, "checkout", "-q", "-b", "release")
    w.show(_run(30, conclusion="failure", branch="release"), _run(10, conclusion="failure"))
    await _look(db, project)

    assert w.sent == []
    assert db.get(ProjectCiWatch, project.id).branch == "release"


async def test_github_not_answering_changes_nothing(world):
    db, project, _owner, _work, w = world
    w.show(_run(10))
    await _look(db, project)
    w.github_down = True
    w.show(_run(11, conclusion="failure"), _run(10))

    await _look(db, project)

    assert w.sent == []
    assert db.get(ProjectCiWatch, project.id).runs["CI"]["run_id"] == 10


async def test_without_a_public_address_the_message_says_why_there_is_no_link(world, monkeypatch):
    db, project, _owner, _work, w = world
    monkeypatch.setattr(settings, "app_public_url", "")
    w.show(_run(10))
    await _look(db, project)
    w.show(_run(11, conclusion="failure"), _run(10))
    await _look(db, project)

    assert w.sent[0][1].endswith("\n(odkaz do kokpitu chýba — na serveri nie je nastavené APP_PUBLIC_URL)")


async def test_one_projects_trouble_does_not_silence_the_others(world, monkeypatch):
    db, project, _owner, _work, _w = world
    seen: list[str] = []

    async def _watch(db, p, *, timeout):
        seen.append(p.slug)
        if p.slug == project.slug:
            raise RuntimeError("boom")
        return []

    other = Project(
        name="Ďalší",
        slug=f"zz-{uuid.uuid4().hex[:6]}",
        type="standard",
        auth_mode="password",
        description="",
        created_by=project.created_by,
        repo_url="rauschiccsk/nex-manager",
    )
    db.add(other)
    db.flush()
    monkeypatch.setattr(ci_watch, "watch_project", _watch)
    monkeypatch.setattr(db, "commit", db.flush)  # the test owns the transaction
    monkeypatch.setattr(db, "rollback", lambda: None)

    await ci_watch.watch_once(db)

    assert project.slug in seen and other.slug in seen


def _as(db, user):
    user_id = user.id
    app.dependency_overrides[get_current_user] = lambda: db.get(User, user_id)


async def test_the_tab_mark_says_which_workflow_is_red_and_only_to_the_owner(client, world):
    db, project, owner, _work, w = world
    w.show(_run(10))
    await _look(db, project)
    w.show(_run(11, conclusion="failure"), _run(10))
    await _look(db, project)
    _as(db, _creator(db, project))

    body = client.get(f"/api/v1/projects/{project.slug}/ci-main").json()

    assert body == {
        "branch": "main",
        "failing": [
            {
                "workflow": "CI",
                "number": 11,
                "subject": "feat: change 11 (TASK #20.1.1)",
                "failed_step": "lint › ruff check",
                "url": f"https://github.com/{REPO}/actions/runs/11",
            }
        ],
        "refresh_seconds": settings.ci_watch_interval_seconds,
    }
    stranger = User(username=f"s_{uuid.uuid4().hex[:6]}", email=f"{uuid.uuid4().hex[:6]}@t.sk", password_hash="x")
    stranger.role = "ri"
    db.add(stranger)
    db.flush()
    _as(db, stranger)
    assert client.get(f"/api/v1/projects/{project.slug}/ci-main").status_code in (403, 404)


def test_a_project_the_watch_has_not_seen_yet_has_no_mark(client, world):
    db, project, _owner, _work, _w = world
    _as(db, _creator(db, project))

    body = client.get(f"/api/v1/projects/{project.slug}/ci-main").json()

    assert (body["branch"], body["failing"]) == (None, [])
