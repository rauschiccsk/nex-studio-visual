"""DEV-174 — what the cockpit itself writes into a project is not the Manažér's unsaved work.

The cockpit refreshes the agents' rules and skills before every new session without a commit (DEV-47 — HEAD must not
move) and writes each version's task plan from the database. Measured 21.10.2026: they were most of the unsaved
changes of every live project (Career Asistent 10 of 15, NEX Inbox 9 of 10, NEX Manager 9 of 9, NEX Websites 12 of
15), and the New-Version page asked the Manažér to save or throw away files that were never his. Director: „Súhlasím“,
„Poradie nechám na teba“. The agent's own permissions forbid editing ``.claude/agents`` and ``.claude/skills`` — no
work of the agent can be there.
"""

from __future__ import annotations

import subprocess
import uuid
from pathlib import Path

import pytest

from backend.core.security import get_current_user
from backend.db.models.foundation import User
from backend.db.models.projects import Project
from backend.main import app
from backend.services import create_project_postscaffold as postscaffold
from backend.services import git_state, workspace_owner


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True).stdout


@pytest.fixture
def workspace(tmp_path) -> Path:
    """A project's checkout as the cockpit leaves it: rules, an old loose skill and a frontend file, committed."""
    ws = tmp_path / "projekt"
    (ws / ".claude" / "agents" / "ai-agent").mkdir(parents=True)
    (ws / ".claude" / "agents" / "ai-agent" / "CLAUDE.md").write_text("# old rules\n")
    (ws / ".claude" / "skills").mkdir()
    (ws / ".claude" / "skills" / "tdd.md").write_text("# loose skill\n")
    (ws / "frontend").mkdir()
    (ws / "frontend" / "a.ts").write_text("export const a = 1;\n")
    _git(ws.parent, "init", "-q", "-b", "main", str(ws))
    _git(ws, "config", "user.email", "t@example.com")
    _git(ws, "config", "user.name", "t")
    _git(ws, "add", "-A")
    _git(ws, "commit", "-q", "-m", "founding")
    return ws


def _cockpit_writes_its_files(ws: Path) -> None:
    (ws / ".claude" / "agents" / "ai-agent" / "CLAUDE.md").write_text("# refreshed rules\n")
    (ws / ".claude" / "skills" / "tdd.md").unlink()
    (ws / ".claude" / "skills" / "tdd").mkdir()
    (ws / ".claude" / "skills" / "tdd" / "SKILL.md").write_text("# skill\n")
    (ws / "docs" / "specs" / "versions" / "v1.1.0" / "spec").mkdir(parents=True)
    (ws / "docs" / "specs" / "versions" / "v1.1.0" / "spec" / "task-plan.md").write_text("# plan\n")


def _status(client, db_session, ws: Path) -> dict:
    name = f"u-{uuid.uuid4().hex[:8]}"
    owner = User(username=name, email=f"{name}@example.com", password_hash="x", role="ri", is_active=True)
    db_session.add(owner)
    db_session.flush()
    project = Project(
        name="P",
        slug=f"p-{uuid.uuid4().hex[:6]}",
        type="standard",
        auth_mode="password",
        description="",
        created_by=owner.id,
        source_path=str(ws),
    )
    db_session.add(project)
    db_session.flush()
    app.dependency_overrides[get_current_user] = lambda: db_session.get(User, owner.id)
    answer = client.get(f"/api/v1/projects/{project.id}/git-status")
    assert answer.status_code == 200, answer.text
    return answer.json()


def test_the_cockpits_own_files_are_not_shown_as_unsaved_work(client, db_session, workspace):
    _cockpit_writes_its_files(workspace)

    status = _status(client, db_session, workspace)

    assert (status["clean"], status["dirty_count"], status["files"]) == (True, 0, [])


def test_real_unsaved_work_is_still_shown_and_only_it(client, db_session, workspace):
    _cockpit_writes_its_files(workspace)
    (workspace / "frontend" / "a.ts").write_text("export const a = 2;\n")

    status = _status(client, db_session, workspace)

    assert (status["clean"], status["dirty_count"], status["files"]) == (
        False,
        1,
        [{"code": "M", "path": "frontend/a.ts"}],
    )


def test_what_the_rule_refresh_writes_is_all_counted_as_the_cockpits(workspace):
    """The list of the cockpit's paths is checked against what the refresh really writes, not against itself."""
    (workspace / ".claude" / "agents" / "auditor").mkdir()
    (workspace / ".claude" / "agents" / "auditor" / "CLAUDE.md").write_text("# old\n")
    _git(workspace, "add", "-A")
    _git(workspace, "commit", "-q", "-m", "auditor")

    changed = postscaffold.refresh_v2_agent_charters(workspace, "projekt")
    everything = _git(workspace, "status", "--porcelain").splitlines()
    left = git_state.working_tree_status(str(workspace), exclude=workspace_owner.COCKPIT_WRITES)

    assert changed and len(everything) >= 3, "the refresh wrote nothing — the check would prove nothing"
    assert (left["dirty_count"], left["files"]) == (0, [])
