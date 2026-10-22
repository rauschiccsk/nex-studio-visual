"""DEV-96 — the cockpit pushes the project's branch after every finished task, and the build line says when it did not.

12.10.2026, NEX Inbox 1.8.0: the Director saw „CI v poriadku“ in the cockpit and something else on GitHub. Nobody had
pushed ``main`` for a week — 31 commits lay only on ANDROS. The agent tested copies on side branches and wrote that
everything passed; six commits with code never ran the full CI. The cockpit pushed ``main`` only when verification
passed, so the first CI on the delivered code ran at the very end.

These run the real build round against REAL git repositories — a bare repository plays GitHub — so every assertion
is about a ref that moved, not about a call that was made.
"""

from __future__ import annotations

import asyncio
import subprocess
import time
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select

from backend.core.offload import BlockingWorkTimedOut
from backend.db.models.foundation import User
from backend.db.models.pipeline import PipelineMessage, PipelineState
from backend.db.models.projects import Project
from backend.db.models.tasks import Epic, Feat, Task
from backend.db.models.versions import Version
from backend.services import build_leftovers, ci_status, claude_agent, orchestrator
from backend.services.pipeline_status import PipelineStatusBlock


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True).stdout.strip()


def _commit(work: Path, name: str, subject: str) -> str:
    (work / name).write_text(f"{name}\n", encoding="utf-8")
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", subject)
    return _git(work, "rev-parse", "HEAD")


def _repo(work: Path, origin: Path | None) -> None:
    """A checkout on ``main`` with one commit; with ``origin``, that commit is already on it — as after the
    project's creation, whose ``.gitignore`` names the build's leftovers (DEV-176), so the cockpit adds no commit."""
    work.mkdir(parents=True)
    _git(work, "init", "-q", "-b", "main")
    _git(work, "config", "user.email", "studio@isnex.eu")
    _git(work, "config", "user.name", "NEX Studio")
    (work / ".gitignore").write_text(f"{build_leftovers.PATTERN}\n", encoding="utf-8")
    _commit(work, "README.md", "chore: start")
    if origin is not None:
        subprocess.run(["git", "init", "--bare", "-q", "-b", "main", str(origin)], check=True)
        _git(work, "remote", "add", "origin", str(origin))
        _git(work, "push", "-q", "origin", "main")


def _on_origin(origin: Path) -> str:
    return _git(origin, "rev-parse", "refs/heads/main")


# ── the build round ─────────────────────────────────────────────────────────────────────────────────────────────


def _build(db, projects_root: Path, titles: list[str]):
    """A version in Programovanie with one task per title, its project checked out under ``projects_root``."""
    user = User(
        username=f"u_{uuid.uuid4().hex[:8]}", email=f"{uuid.uuid4().hex[:8]}@example.com", password_hash="x", role="ri"
    )
    db.add(user)
    db.flush()
    project = Project(
        name=f"P {uuid.uuid4().hex[:8]}",
        slug=f"p-{uuid.uuid4().hex[:8]}",
        type="standard",
        auth_mode="password",
        description="d",
        created_by=user.id,
        miera_autonomie="po_kazdej_faze",
    )
    db.add(project)
    db.flush()
    version = Version(project_id=project.id, version_number="1.8.0")
    db.add(version)
    db.flush()
    db.add(
        PipelineState(
            version_id=version.id,
            flow_type="new_version",
            current_stage="programovanie",
            current_actor="ai_agent",
            status="agent_working",
            next_action="working",
        )
    )
    epic = Epic(project_id=project.id, version_id=version.id, number=1, title="Foundation", status="planned")
    db.add(epic)
    db.flush()
    feat = Feat(epic_id=epic.id, number=1, title="Schema", status="todo")
    db.add(feat)
    db.flush()
    for i, title in enumerate(titles, start=1):
        db.add(Task(feat_id=feat.id, number=i, title=title, task_type="backend", status="todo"))
    db.flush()
    return version, projects_root / project.slug


def _agent_that_commits(monkeypatch, work: Path, origin: Path | None, seen_on_origin: list[str]):
    """Every turn commits one file and reports it — and first notes what GitHub holds at that moment."""
    commits: list[str] = []

    async def _turn(db, *, version_id, role, stage, prompt, **_kw):
        if origin is not None:
            seen_on_origin.append(_on_origin(origin))
        sha = _commit(
            work, f"task{len(commits) + 1}.py", f"feat: task {len(commits) + 1} (TASK #1.1.{len(commits) + 1})"
        )
        commits.append(sha)
        return PipelineStatusBlock(
            stage="programovanie", kind="gate_report", summary="hotovo", awaiting="manazer", commits=[sha]
        )

    monkeypatch.setattr(orchestrator, "invoke_agent_with_parse_retry", _turn)
    monkeypatch.setattr(orchestrator, "verify_mechanical", lambda slug, block, baseline_sha=None: None)
    return commits


def _messages(db, version_id) -> list[PipelineMessage]:
    return list(
        db.execute(
            select(PipelineMessage).where(PipelineMessage.version_id == version_id).order_by(PipelineMessage.seq)
        ).scalars()
    )


async def test_every_finished_task_is_on_github_before_the_next_one_starts(db_session, monkeypatch, tmp_path):
    """Not once at the end: the second task starts with the first one's commit already on GitHub, and after the
    last task GitHub holds exactly the version's head."""
    projects = tmp_path / "projects"
    monkeypatch.setattr(claude_agent, "PROJECTS_ROOT", projects)
    origin = tmp_path / "origin.git"
    version, work = _build(db_session, projects, ["T1", "T2"])
    _repo(work, origin)
    seen: list[str] = []
    commits = _agent_that_commits(monkeypatch, work, origin, seen)

    await orchestrator.run_dispatch(db_session, version.id)

    assert len(commits) == 2
    assert seen[1] == commits[0], "the second task started while the first one's code was only on this machine"
    assert _on_origin(origin) == commits[1] == _git(work, "rev-parse", "HEAD")
    assert ci_status.unpushed_commits(work, "HEAD") == ("main", 0)
    assert not [m for m in _messages(db_session, version.id) if (m.payload or {}).get("push_failed")]


async def test_a_push_that_fails_is_told_to_the_manager_and_the_task_stays_done(db_session, monkeypatch, tmp_path):
    """A network hiccup must not undo finished work — but it must not be silent either."""
    projects = tmp_path / "projects"
    monkeypatch.setattr(claude_agent, "PROJECTS_ROOT", projects)
    version, work = _build(db_session, projects, ["T1"])
    _repo(work, None)
    _git(work, "remote", "add", "origin", str(tmp_path / "nowhere.git"))
    _agent_that_commits(monkeypatch, work, None, [])

    await orchestrator.run_dispatch(db_session, version.id)

    task = db_session.execute(
        select(Task)
        .join(Feat, Feat.id == Task.feat_id)
        .join(Epic, Epic.id == Feat.epic_id)
        .where(Epic.version_id == version.id)
    ).scalar_one()
    assert task.status == "done"
    told = [m for m in _messages(db_session, version.id) if (m.payload or {}).get("push_failed")]
    assert len(told) == 1, [m.content for m in _messages(db_session, version.id)]
    assert told[0].recipient == "manazer" and told[0].payload["task_id"] == str(task.id)
    assert told[0].content.startswith("Kód úlohy #1.1.1 sa nepodarilo odoslať na GitHub ("), told[0].content
    assert told[0].content.endswith(
        "). Kontroly na GitHube preň zatiaľ nebežia; kokpit ho odošle spolu s ďalšou úlohou."
    ), told[0].content
    assert "nowhere.git" in told[0].content, "the reason git gave is not in the sentence"


async def test_a_project_without_github_is_not_a_failure(db_session, monkeypatch, tmp_path):
    projects = tmp_path / "projects"
    monkeypatch.setattr(claude_agent, "PROJECTS_ROOT", projects)
    version, work = _build(db_session, projects, ["T1"])
    _repo(work, None)
    _agent_that_commits(monkeypatch, work, None, [])

    await orchestrator.run_dispatch(db_session, version.id)

    assert not [m for m in _messages(db_session, version.id) if (m.payload or {}).get("push_failed")]


async def test_a_push_that_never_ends_gives_the_build_back_with_a_reason(monkeypatch, tmp_path):
    async def _hangs(fn, *args, cap):
        raise BlockingWorkTimedOut(f"{fn.__name__} > {cap} s")

    monkeypatch.setattr(orchestrator, "run_blocking", _hangs)

    assert await orchestrator._push_after_task(tmp_path) == f"odoslanie neskončilo do {orchestrator.GIT_PUSH_CAP} s"


async def test_a_slow_push_never_holds_the_main_loop(monkeypatch, tmp_path):
    """A push waits for GitHub — on the main loop it would freeze the whole cockpit while it does (ICCINT-74). The
    scan that guards this does not look at calls inside the orchestrator, so this measures it: the loop keeps
    turning while the push waits."""

    def _slow(_root):
        time.sleep(0.5)
        return True, "main", None

    monkeypatch.setattr(orchestrator, "_push_branch", _slow)
    ticks = 0

    async def _tick():
        nonlocal ticks
        while True:
            await asyncio.sleep(0.01)
            ticks += 1

    ticker = asyncio.create_task(_tick())
    try:
        assert await orchestrator._push_after_task(tmp_path) is None
    finally:
        ticker.cancel()

    assert ticks >= 10, f"the main loop turned {ticks}× during a push of half a second"


def test_a_detached_head_is_a_reason_not_a_push(tmp_path):
    origin = tmp_path / "origin.git"
    work = tmp_path / "work"
    _repo(work, origin)
    sha = _commit(work, "a.py", "feat: a")
    _git(work, "checkout", "-q", "--detach", sha)

    assert orchestrator._push_branch(work) == (False, None, orchestrator.BRANCH_NOT_NAMED)
    assert _on_origin(origin) != sha


@pytest.mark.parametrize("flow", ["new_version", "fast_fix"])
def test_every_task_brief_says_the_cockpit_pushes(flow):
    task = Task(number=1, title="Detail faktúry", description="Oprav detail.", task_type="backend")

    brief = orchestrator._directive_for_build_task(task, None, [], flow, task_label="1.1.1", version_number="1.8.0")

    assert orchestrator.PUSH_RULE in brief


def test_the_charter_says_the_cockpit_pushes():
    charter = (Path(__file__).resolve().parents[1] / "templates" / "ai-agent-charter.md").read_text(encoding="utf-8")

    # DEV-173 (Director 21.10.2026: „Súhlasím“): the agent sends nothing — neither the main branch nor a red proof.
    assert "Na GitHub odosiela kokpit (DEV-96, DEV-173)." in charter
    assert "Na GitHub nič neodosielaj" in charter


# ── the build line ──────────────────────────────────────────────────────────────────────────────────────────────


def _no_runs(rows=None):
    async def _ask(_root, _repo, _sha):
        return (rows or []), None

    return _ask


def _github_remote(work: Path, origin: Path) -> None:
    """The line asks GitHub only for a GitHub remote; pushes still go to the bare repository."""
    _git(work, "remote", "set-url", "origin", "https://github.com/rauschiccsk/nex-inbox.git")
    _git(work, "remote", "set-url", "--push", "origin", str(origin))


async def test_the_build_line_says_the_code_is_not_on_github_yet(monkeypatch, tmp_path):
    origin = tmp_path / "origin.git"
    work = tmp_path / "work"
    _repo(work, origin)
    _github_remote(work, origin)
    _commit(work, "a.py", "feat: a")
    _commit(work, "b.py", "feat: b")
    monkeypatch.setattr(ci_status, "_behy_z_githubu", _no_runs())
    ci_status._zabudni_vsetko()

    line = await ci_status.snapshot(work)

    assert line.stav == "unknown"
    assert (
        line.detail
        == "kód ešte nie je na GitHube vo vetve main — 2 commity čakajú na odoslanie, kontroly preň nebežali"
    )


async def test_code_on_a_side_branch_only_is_still_not_on_the_main_branch(monkeypatch, tmp_path):
    """NEX Inbox 1.8.0 exactly: the agent pushed its work to ``ci/…`` branches — 32 commits behind ``main`` on GitHub,
    only 3 of them on no GitHub branch at all. The line speaks of the branch the version is delivered from."""
    origin = tmp_path / "origin.git"
    work = tmp_path / "work"
    _repo(work, origin)
    _github_remote(work, origin)
    _commit(work, "a.py", "feat: a")
    _git(work, "push", "-q", "origin", "HEAD:refs/heads/ci/v180-gate")
    _commit(work, "b.py", "feat: b")
    monkeypatch.setattr(ci_status, "_behy_z_githubu", _no_runs())
    ci_status._zabudni_vsetko()

    line = await ci_status.snapshot(work)

    assert (
        line.detail
        == "kód ešte nie je na GitHube vo vetve main — 2 commity čakajú na odoslanie, kontroly preň nebežali"
    )


async def test_once_pushed_the_build_line_asks_github_again(monkeypatch, tmp_path):
    origin = tmp_path / "origin.git"
    work = tmp_path / "work"
    _repo(work, origin)
    _github_remote(work, origin)
    _commit(work, "a.py", "feat: a")
    assert orchestrator._push_branch(work)[0] is True
    monkeypatch.setattr(ci_status, "_behy_z_githubu", _no_runs())
    ci_status._zabudni_vsetko()

    line = await ci_status.snapshot(work)

    assert line.detail == "pre tento commit sa zatiaľ neobjavil žiadny beh CI", line.detail


async def test_a_run_github_has_is_never_hidden_behind_the_reason(monkeypatch, tmp_path):
    """A red run for the commit is evidence, whichever branch carried the commit to GitHub — an unpushed ``main``
    must not turn it into „we do not know“."""
    origin = tmp_path / "origin.git"
    work = tmp_path / "work"
    _repo(work, origin)
    _github_remote(work, origin)
    _commit(work, "a.py", "feat: a")
    red = {"workflowName": "CI", "status": "completed", "conclusion": "failure", "databaseId": 7}
    monkeypatch.setattr(ci_status, "_behy_z_githubu", _no_runs([red]))
    ci_status._zabudni_vsetko()

    line = await ci_status.snapshot(work)

    assert line.stav == "red", line.detail


@pytest.mark.parametrize(
    ("n", "words"),
    [(1, "1 commit čaká"), (2, "2 commity čakajú"), (4, "4 commity čakajú"), (5, "5 commitov čaká")],
)
def test_the_count_reads_as_slovak(n, words):
    assert ci_status._commits_waiting(n) == words


def test_a_branch_github_has_never_seen_counts_what_is_on_no_branch_there(tmp_path):
    origin = tmp_path / "origin.git"
    work = tmp_path / "work"
    _repo(work, origin)
    _git(work, "checkout", "-q", "-b", "v2")
    _commit(work, "a.py", "feat: a")

    assert ci_status.unpushed_commits(work, "HEAD") == ("v2", 1)
