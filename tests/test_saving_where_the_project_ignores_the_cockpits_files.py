"""DEV-303 — „Uložiť ich“ saves the card's work in a project whose ``.gitignore`` ignores the cockpit's own files.

04.11.2026, NEX Knowledge: the card showed one change (``MEMORY.md``, the project agent's notes) and „Uložiť ich“
answered „Uloženie zmien zlyhalo — git add zlyhal“. The saving kept the cockpit's files out by naming their folders as
exceptions (DEV-203: ``git add -A -- . ':(glob,exclude).claude/agents/**' …``); the project ignores those folders in its
``.gitignore``, and git refuses a pathspec that names an ignored path — „The following paths are ignored by one of your
.gitignore files“, exit 1. The card blocks founding a version or a fast fix until the work is saved or discarded, so the
project stood still. Saving now names, one by one, exactly the files the card shows — the ignored folders never reach
git. Director 04.11.2026: „Súhlasím“.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.test_a_version_is_founded_only_over_saved_work import _project_in
from tests.test_dedo_proposal_before_the_work_starts import _bearer, _make_user

#: The lines NEX Knowledge's ``.gitignore`` carries for the cockpit's own folders.
IGNORES_THE_COCKPITS_FILES = "/.claude/agents/\n/.claude/skills/\n.env\n"
RULES = ".claude/agents/ai-agent/CLAUDE.md"
SKILL = ".claude/skills/fresh/SKILL.md"
TASK_PLAN = "docs/specs/versions/v1.0.0/spec/task-plan.md"

STAGED = "MEMORY.md"
CHANGED = "frontend/a.ts"
DELETED = "docs/old-notes.md"
DELETED_STAGED = "docs/retired.md"
RENAMED_FROM, RENAMED_TO = "docs/draft.md", "docs/final.md"
ADDED = "backend/new_module/handler.py"
ADDED_THEN_DROPPED = "scratch.md"
#: Names git would read as a pattern or as pathspec magic — a route of the frontend, a colon up front.
ODD_NAMES = ("frontend/app/[id]/page.tsx", ":memo.md")


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True).stdout


def _write(root: Path, path: str, text: str) -> None:
    (root / path).parent.mkdir(parents=True, exist_ok=True)
    (root / path).write_text(text)


@pytest.fixture
def workspace(tmp_path) -> Path:
    """A project that ignores the cockpit's folders; the Manažér's work is in every state git knows — staged, unstaged,
    deleted, renamed, new files in new folders, odd names —, and the cockpit refreshed its rules, a skill and the task
    plan."""
    ws = tmp_path / "projekt"
    _write(ws, ".gitignore", IGNORES_THE_COCKPITS_FILES)
    for path in (STAGED, CHANGED, DELETED, DELETED_STAGED, RENAMED_FROM, TASK_PLAN):
        _write(ws, path, f"{path} v1\n")
    _git(ws.parent, "init", "-q", "-b", "main", str(ws))
    _git(ws, "config", "user.email", "t@example.com")
    _git(ws, "config", "user.name", "t")
    _git(ws, "add", "-A")
    _git(ws, "commit", "-q", "-m", "founding")

    _write(ws, STAGED, "notes of the agent\n")
    _git(ws, "add", STAGED)
    _write(ws, CHANGED, "export const a = 2;\n")
    (ws / DELETED).unlink()
    _git(ws, "rm", "-q", DELETED_STAGED)
    _git(ws, "mv", RENAMED_FROM, RENAMED_TO)
    _write(ws, ADDED, "def handle(): ...\n")
    for path in ODD_NAMES:
        _write(ws, path, "x\n")
    _write(ws, ADDED_THEN_DROPPED, "x\n")
    _git(ws, "add", ADDED_THEN_DROPPED)
    (ws / ADDED_THEN_DROPPED).unlink()
    for path in (RULES, SKILL, TASK_PLAN):
        _write(ws, path, f"{path} as the cockpit wrote it\n")
    return ws


def _card(client, user, project) -> set[str]:
    answer = client.get(f"/api/v1/projects/{project.id}/git-status", headers=_bearer(user))
    assert answer.status_code == 200, answer.text
    return {f["path"] for f in answer.json()["files"]}


def _changed(ws: Path) -> set[str]:
    return {line[3:] for line in _git(ws, "status", "--porcelain", "--untracked-files=all").splitlines()}


def test_saving_commits_exactly_the_card_where_the_cockpits_files_are_ignored(client, db_session, workspace):
    user = _make_user(db_session)
    project = _project_in(db_session, user, workspace)
    assert _card(client, user, project), "the card shows nothing — the case would prove nothing"
    head = _git(workspace, "rev-parse", "HEAD")

    answer = client.post(f"/api/v1/projects/{project.id}/git-commit", headers=_bearer(user), json={})

    assert answer.status_code == 200, answer.text
    assert _git(workspace, "rev-parse", "HEAD~1").strip() == head.strip(), "one commit, on top of the founding"
    committed = _git(workspace, "show", "--name-status", "--format=", "-M", "HEAD").splitlines()
    assert sorted(committed) == sorted(
        [
            f"M\t{STAGED}",
            f"M\t{CHANGED}",
            f"D\t{DELETED}",
            f"D\t{DELETED_STAGED}",
            f"R100\t{RENAMED_FROM}\t{RENAMED_TO}",
            f"A\t{ADDED}",
            *(f"A\t{path}" for path in ODD_NAMES),
        ]
    )
    assert _changed(workspace) == {TASK_PLAN}, "the cockpit's task plan went into the project's git, or work stayed"
    assert _git(workspace, "diff", "--cached", "--name-only") == "", "something stays staged for the agent's commit"
    assert _card(client, user, project) == set()


def test_discarding_works_where_the_cockpits_files_are_ignored(client, db_session, workspace):
    user = _make_user(db_session)
    project = _project_in(db_session, user, workspace)
    head = _git(workspace, "rev-parse", "HEAD")

    answer = client.post(f"/api/v1/projects/{project.id}/git-discard", headers=_bearer(user))

    assert answer.status_code == 200, answer.text
    assert _git(workspace, "rev-parse", "HEAD") == head
    assert (workspace / STAGED).read_text() == f"{STAGED} v1\n"
    assert not (workspace / ADDED).exists()
    assert (workspace / RULES).read_text() == f"{RULES} as the cockpit wrote it\n"
    assert _card(client, user, project) == set()
