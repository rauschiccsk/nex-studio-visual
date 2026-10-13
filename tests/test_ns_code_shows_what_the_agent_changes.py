"""DEV-87 — NS Code shows what the agent changes: M and U marks as in VS Code, a stamp that moves when a file changes
(the screen refreshes on it during a build), the file as it was at the last commit or where the version began, and
the newest commit of a file with its task.

A real git repository with dated commits: the version's build began between the two, so its start is the first one —
the same start the delivery statement counts from.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

from backend.config.settings import settings
from backend.core.security import get_current_user
from backend.db.models.foundation import User
from backend.db.models.pipeline import PipelineMessage
from backend.db.models.projects import Project
from backend.db.models.tasks import Epic, Feat, Task
from backend.db.models.versions import Version
from backend.main import app
from backend.services import claude_agent, ns_code


def _git(root: Path, *args: str, when: str | None = None) -> str:
    env = dict(os.environ)
    if when:
        env.update(GIT_AUTHOR_DATE=when, GIT_COMMITTER_DATE=when)
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True, env=env).stdout


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def repo(tmp_path) -> Path:
    root = tmp_path / "projects" / "nex-inbox"
    root.mkdir(parents=True)
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "studio@isnex.eu")
    _git(root, "config", "user.name", "NEX Studio")
    _write(root, "a.py", "a = 1\n")
    _write(root, "b.py", "b = 1\n")
    _write(root, "c.py", "c = 1\n")
    # A file whose name, read as a ``git status`` entry, would point at a.py — the trap of an unskipped rename source.
    _write(root, "zz_a.py", "z = 1\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "start of 1.0", when="2026-10-01T10:00:00+00:00")
    _write(root, "a.py", "a = 2\n")
    _git(root, "commit", "-q", "-am", "feat: a is two (TASK #20.1.1)", when="2026-10-08T10:00:00+00:00")
    # What the agent has on its desk now: a changed file, a new one, a renamed one, a secret.
    _write(root, "b.py", "b = 2\n")
    _write(root, "new.py", "new = 1\n")
    _git(root, "mv", "c.py", "d.py")
    _git(root, "mv", "zz_a.py", "e.py")
    _write(root, ".env", "SECRET=1\n")
    return root


def test_the_marks_are_what_git_status_says_for_the_files_ns_code_shows(repo):
    found = ns_code.listing(repo)

    assert found.changes == {"b.py": "M", "d.py": "U", "e.py": "U", "new.py": "U"}
    assert found.files == ["a.py", "b.py", "d.py", "e.py", "new.py"]
    assert set(found.stamps) == set(found.files)


def test_a_stamp_moves_when_the_file_changes_and_only_then(repo):
    before = ns_code.listing(repo).stamps
    # The agent changes a file without changing its size — only the time of the write tells.
    st = os.stat(repo / "b.py")
    _write(repo, "b.py", "b = 9\n")
    os.utime(repo / "b.py", ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))

    after = ns_code.listing(repo).stamps

    assert after["b.py"] != before["b.py"]
    assert {p: s for p, s in after.items() if p != "b.py"} == {p: s for p, s in before.items() if p != "b.py"}


def test_the_file_as_it_was_at_the_last_commit(repo):
    head = _git(repo, "rev-parse", "HEAD").strip()

    assert ns_code.original(repo, "b.py", head).text == "b = 1\n"
    assert ns_code.original(repo, "new.py", head) is None, "a new file had no past"


def test_a_secret_has_no_past_either(repo):
    _git(repo, "add", "-f", ".env")
    _git(repo, "commit", "-q", "-m", "oops")

    with pytest.raises(ns_code.NotServed):
        ns_code.original(repo, ".env", "HEAD")


def test_the_last_commit_of_a_file(repo):
    last = ns_code.last_commit(repo, "a.py")

    assert (last.subject, last.at) == ("feat: a is two (TASK #20.1.1)", "2026-10-08T10:00:00+00:00")
    assert ns_code.last_commit(repo, "new.py") is None


# ── the routes ─────────────────────────────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def served(db_session, repo, monkeypatch):
    db = db_session
    owner = User(
        username=f"o_{uuid.uuid4().hex[:6]}", email=f"{uuid.uuid4().hex[:6]}@t.sk", password_hash="x", role="ri"
    )
    db.add(owner)
    db.flush()
    project = Project(
        name="NEX Inbox", slug="nex-inbox", type="standard", auth_mode="password", description="", created_by=owner.id
    )
    other = Project(name="Iný", slug="iny", type="standard", auth_mode="password", description="", created_by=owner.id)
    db.add_all([project, other])
    db.flush()
    version = Version(project_id=project.id, version_number="1.1.0")
    foreign = Version(project_id=other.id, version_number="9.0.0")
    db.add_all([version, foreign])
    db.flush()
    # The version's build began on 05.10. — between the two commits.
    db.add(
        PipelineMessage(
            version_id=version.id,
            stage="priprava",
            author="manazer",
            recipient="ai_agent",
            kind="kickoff",
            content="Začni.",
            created_at=datetime(2026, 10, 5, tzinfo=timezone.utc),
        )
    )
    epic = Epic(project_id=project.id, version_id=version.id, number=20, title="A", status="planned")
    db.add(epic)
    db.flush()
    feat = Feat(epic_id=epic.id, number=1, title="A", status="todo")
    db.add(feat)
    db.flush()
    task = Task(feat_id=feat.id, number=1, title="A je dva", task_type="backend", status="done")
    db.add(task)
    db.flush()
    monkeypatch.setattr(claude_agent, "PROJECTS_ROOT", repo.parent)
    user_id = owner.id
    app.dependency_overrides[get_current_user] = lambda: db.get(User, user_id)
    return db, version, foreign, task


def test_the_list_carries_the_marks_the_stamps_and_the_schedule(client, served):
    body = client.get("/api/v1/projects/nex-inbox/code/files").json()

    assert body["changes"] == {"b.py": "M", "d.py": "U", "e.py": "U", "new.py": "U"}
    assert set(body["stamps"]) == set(body["files"])
    assert body["refresh_seconds"] == settings.ns_code_refresh_seconds


def test_a_file_says_its_last_commit_and_task(client, served):
    _db, version, _foreign, task = served

    body = client.get("/api/v1/projects/nex-inbox/code/file", params={"path": "a.py"}).json()

    assert body["last_commit"]["subject"] == "feat: a is two (TASK #20.1.1)"
    assert body["last_commit"]["task"] == {
        "label": "20.1.1",
        "task_id": str(task.id),
        "title": "A je dva",
        "version_id": str(version.id),
        "version_number": "1.1.0",
    }
    assert body["stamp"]


def test_the_file_compared_with_the_last_commit_and_with_the_start_of_the_version(client, served, repo):
    _db, version, _foreign, _task = served
    first = _git(repo, "rev-list", "--max-parents=0", "HEAD").strip()

    head = client.get("/api/v1/projects/nex-inbox/code/original", params={"path": "a.py"}).json()
    base = client.get(
        "/api/v1/projects/nex-inbox/code/original",
        params={"path": "a.py", "against": "base", "version_id": str(version.id)},
    ).json()

    assert (head["against"], head["existed"], head["text"]) == ("head", True, "a = 2\n")
    assert (base["against"], base["rev"], base["text"]) == ("base", first, "a = 1\n")


def test_a_new_file_had_nothing_to_compare_with(client, served):
    body = client.get("/api/v1/projects/nex-inbox/code/original", params={"path": "new.py"}).json()

    assert (body["existed"], body["text"]) == (False, None)


def test_the_start_of_another_projects_version_is_refused(client, served):
    _db, _version, foreign, _task = served

    answer = client.get(
        "/api/v1/projects/nex-inbox/code/original",
        params={"path": "a.py", "against": "base", "version_id": str(foreign.id)},
    )

    assert answer.status_code == 404


def test_a_version_whose_start_git_cannot_tell_says_so(client, served):
    db, version, _foreign, _task = served
    for m in db.query(PipelineMessage).filter(PipelineMessage.version_id == version.id):
        db.delete(m)
    db.flush()

    answer = client.get(
        "/api/v1/projects/nex-inbox/code/original",
        params={"path": "a.py", "against": "base", "version_id": str(version.id)},
    )

    assert (answer.status_code, answer.json()["detail"]) == (
        409,
        "Začiatok tejto verzie sa v gite nedá určiť — porovnaj s posledným commitom.",
    )


def test_a_secret_is_not_compared(client, served, repo):
    answer = client.get("/api/v1/projects/nex-inbox/code/original", params={"path": ".env"})

    assert answer.status_code == 404
