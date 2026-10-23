"""DEV-203 — „Uložiť ich“ and „Zahodiť“ on the card of unsaved changes touch exactly what the card shows.

The card leaves out the files the cockpit writes itself — the agents' rules and skills and the task plan (DEV-174)
—, but „Uložiť ich“ ran ``git add -A`` over the whole workspace and „Zahodiť“ ``git reset --hard`` + ``git clean -fd``.
On NEX Inbox 23.10.2026 the card showed one change; saving it would have committed two agent charters, two deleted
skills and five new skill folders as well — the agents' rules back in the app's git, which DEV-196 takes out of it.
The card, the saving and the discarding now take the set from one place. Director 23.10.2026: „Súhlasím, založ
tiket“, „Poďme dokončiť tikety NSV“.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from backend.services import workspace_owner
from tests.test_a_version_is_founded_only_over_saved_work import _project_in
from tests.test_dedo_proposal_before_the_work_starts import _bearer, _make_user

RULES = ".claude/agents/ai-agent/CLAUDE.md"
NEW_SKILL = ".claude/skills/fresh/SKILL.md"
TASK_PLAN = "docs/specs/versions/v1.0.0/spec/task-plan.md"
CHANGED = "frontend/a.ts"
ADDED = "frontend/b.ts"

#: The cockpit's own files with what the cockpit last wrote into them — none of it may be saved or thrown away.
COCKPITS_WRITING = {RULES: "# refreshed rules\n", NEW_SKILL: "# a new skill\n", TASK_PLAN: "# plan v2\n"}


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True).stdout


def _write(root: Path, path: str, text: str) -> None:
    (root / path).parent.mkdir(parents=True, exist_ok=True)
    (root / path).write_text(text)


@pytest.fixture
def workspace(tmp_path) -> Path:
    """A project's checkout: the Manažér changed one file and added another; the cockpit refreshed the agents' rules,
    wrote a new skill and the task plan."""
    ws = tmp_path / "projekt"
    _write(ws, CHANGED, "export const a = 1;\n")
    _write(ws, RULES, "# rules\n")
    _write(ws, TASK_PLAN, "# plan v1\n")
    _git(ws.parent, "init", "-q", "-b", "main", str(ws))
    _git(ws, "config", "user.email", "t@example.com")
    _git(ws, "config", "user.name", "t")
    _git(ws, "add", "-A")
    _git(ws, "commit", "-q", "-m", "founding")
    _write(ws, CHANGED, "export const a = 2;\n")
    _write(ws, ADDED, "export const b = 1;\n")
    for path, text in COCKPITS_WRITING.items():
        _write(ws, path, text)
    return ws


def _card(client, user, project) -> set[str]:
    answer = client.get(f"/api/v1/projects/{project.id}/git-status", headers=_bearer(user))
    assert answer.status_code == 200, answer.text
    return {f["path"] for f in answer.json()["files"]}


def _changed(ws: Path) -> set[str]:
    return {line[3:] for line in _git(ws, "status", "--porcelain", "--untracked-files=all").splitlines()}


def _assert_the_cockpits_files_kept(ws: Path) -> None:
    assert {path: (ws / path).read_text() for path in COCKPITS_WRITING} == COCKPITS_WRITING


def test_saving_commits_only_the_managers_work(client, db_session, workspace):
    user = _make_user(db_session)
    project = _project_in(db_session, user, workspace)
    assert _card(client, user, project) == {CHANGED, ADDED}

    answer = client.post(f"/api/v1/projects/{project.id}/git-commit", headers=_bearer(user), json={})

    assert answer.status_code == 200, answer.text
    assert set(_git(workspace, "show", "--name-only", "--format=", "HEAD").split()) == {CHANGED, ADDED}
    assert _changed(workspace) == set(COCKPITS_WRITING), "the cockpit's own files went into the project's git"
    assert _git(workspace, "diff", "--cached", "--name-only") == "", "the next commit of the agent would take them"
    _assert_the_cockpits_files_kept(workspace)
    assert _card(client, user, project) == set()


def test_discarding_throws_away_only_the_managers_work(client, db_session, workspace):
    user = _make_user(db_session)
    project = _project_in(db_session, user, workspace)
    head = _git(workspace, "rev-parse", "HEAD")

    answer = client.post(f"/api/v1/projects/{project.id}/git-discard", headers=_bearer(user))

    assert answer.status_code == 200, answer.text
    assert (workspace / CHANGED).read_text() == "export const a = 1;\n"
    assert not (workspace / ADDED).exists()
    _assert_the_cockpits_files_kept(workspace)
    assert _git(workspace, "rev-parse", "HEAD") == head
    assert _card(client, user, project) == set()


def test_a_cockpit_file_staged_before_does_not_ride_along(client, db_session, workspace):
    """Something staged the refreshed rules earlier — saving still commits only what the card shows."""
    _git(workspace, "add", RULES)
    user = _make_user(db_session)
    project = _project_in(db_session, user, workspace)

    assert client.post(f"/api/v1/projects/{project.id}/git-commit", headers=_bearer(user), json={}).status_code == 200

    assert set(_git(workspace, "show", "--name-only", "--format=", "HEAD").split()) == {CHANGED, ADDED}
    _assert_the_cockpits_files_kept(workspace)


@pytest.mark.parametrize("button", ["git-commit", "git-discard"])
@pytest.mark.parametrize(
    "cockpits_files",
    [workspace_owner.COCKPIT_WRITES, (".claude/agents/**",)],
    ids=["as today", "a narrower set"],
)
def test_the_card_saving_and_discarding_take_one_set(
    client, db_session, workspace, monkeypatch, cockpits_files, button
):
    """Whatever the cockpit's set is, Uložiť commits exactly the card's files and Zahodiť throws away exactly them."""
    monkeypatch.setattr(workspace_owner, "COCKPIT_WRITES", cockpits_files)
    user = _make_user(db_session)
    project = _project_in(db_session, user, workspace)
    everything = _changed(workspace)
    shown = {path for path in everything if any(path == f or path.startswith(f) for f in _card(client, user, project))}
    assert shown and shown != everything, "the card shows nothing or everything — the case would prove nothing"
    head = _git(workspace, "rev-parse", "HEAD")

    answer = client.post(f"/api/v1/projects/{project.id}/{button}", headers=_bearer(user), json={})

    assert answer.status_code == 200, answer.text
    assert _changed(workspace) == everything - shown
    committed = set(_git(workspace, "show", "--name-only", "--format=", "HEAD").split())
    assert committed == (shown if button == "git-commit" else committed)
    assert (_git(workspace, "rev-parse", "HEAD") == head) is (button == "git-discard")
