"""DEV-196 — the agent's rules and skills, which the cockpit writes into a project, are not part of the project's git.

Measured 23.10.2026: NEX Inbox, NEX Manager, NEX Websites and Career Asistent track 7–17 files under ``.claude`` and
show 9–12 unsaved changes there — the cockpit rewrites the role charters and its skills from its templates before
every agent session (DEV-47), so the working tree was never clean, and the agent's instructions (in Slovak) would go
to the customer with the source code. Director 23.10.2026: „Áno, schvaľujem všetkých 14 bodov“ (point 6).

The cockpit's paths are ignored and stop being tracked in one commit of the cockpit's after a finished task — the
same cleanup as the build's leftovers (DEV-176). A file of the project's own under ``.claude`` stays as it was. With
the rules out of git, a project cloned again from GitHub has none: the refresh before a session writes them whole.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from backend.config.settings import settings
from backend.services import build_leftovers, create_project_postscaffold, project_surfaces

MARKER = create_project_postscaffold.COCKPIT_SKILL_MARKER


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True).stdout.strip()


def _write(root: Path, rel: str, text: str) -> None:
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    (root / rel).write_text(text, encoding="utf-8")


@pytest.fixture
def project(tmp_path) -> Path:
    """A project as NEX Inbox is today: the cockpit's rules tracked, beside a file of the project's own."""
    ws = tmp_path / "ws"
    ws.mkdir()
    _git(ws, "init", "-q", "-b", "main")
    _git(ws, "config", "user.email", "t@example.com")
    _git(ws, "config", "user.name", "t")
    _write(ws, ".gitignore", f"node_modules\n{build_leftovers.PATTERN}\n")
    _write(ws, ".claude/settings.json", '{"hooks": {}}\n')  # from the project template — the project's own
    _write(ws, ".claude/agents/ai-agent/CLAUDE.md", "# Pravidlá agenta\n")
    _write(ws, ".claude/agents/ai-agent/settings.json", "{}\n")
    _write(ws, ".claude/agents/coordinator/CLAUDE.md", "# v1\n")
    _write(ws, ".claude/skills/tdd/SKILL.md", f"---\nname: tdd\n---\n{MARKER}\nbody\n")
    _write(ws, ".claude/skills/tdd.md", "loose copy of the old template\n")
    _write(ws, ".claude/skills/own/SKILL.md", "---\nname: own\n---\nthe project's own skill\n")
    _write(ws, ".claude/wip-actions.log", "Read x\n")
    _write(ws, "backend/app.py", "print('app')\n")
    _git(ws, "add", "-A")
    _git(ws, "commit", "-q", "-m", "founding")
    return ws


def test_the_cockpits_rules_stop_being_tracked_in_one_commit_and_stay_on_disk(project):
    assert build_leftovers.keep_out_of_git(project) is None

    assert _git(project, "ls-files", ".claude").splitlines() == [".claude/settings.json", ".claude/skills/own/SKILL.md"]
    assert _git(project, "log", "-1", "--format=%s") == build_leftovers.AGENT_RULES.commit_message
    assert (project / ".claude/agents/ai-agent/CLAUDE.md").is_file(), "the agent reads its rules from the disk"
    for line in build_leftovers.AGENT_RULES.ignore:
        assert line in (project / ".gitignore").read_text().splitlines()


def test_rules_rewritten_before_a_session_leave_the_working_tree_clean(project):
    build_leftovers.keep_out_of_git(project)

    _write(project, ".claude/agents/ai-agent/CLAUDE.md", "# Pravidlá agenta — nové\n")
    _write(project, ".claude/skills/verification-fix/SKILL.md", f"---\nname: verification-fix\n---\n{MARKER}\n")
    _write(project, ".claude/wip-actions.log", "Read x\nEdit y\n")

    assert _git(project, "status", "--porcelain") == ""


def test_both_cleanups_go_in_one_commit_that_names_both(project):
    _write(project, ".gitignore", "node_modules\n")
    _write(project, "frontend/tsconfig.app.tsbuildinfo", "{}\n")
    _git(project, "add", "-A")
    _git(project, "commit", "-q", "-m", "the cache got in")

    assert build_leftovers.keep_out_of_git(project) is None

    assert _git(project, "log", "-1", "--format=%s") == (
        "chore: build leftovers (*.tsbuildinfo) and the agent's rules (.claude) stay out of git"
    )
    assert [p for p in _git(project, "ls-files").splitlines() if "tsbuildinfo" in p or "agents" in p] == []


def test_a_staged_change_of_the_projects_own_file_there_is_someone_elses(project):
    _write(project, ".claude/skills/own/SKILL.md", "---\nname: own\n---\nchanged by a person\n")
    _git(project, "add", ".claude/skills/own/SKILL.md")
    head = _git(project, "rev-parse", "HEAD")

    assert build_leftovers.keep_out_of_git(project) == build_leftovers.FOREIGN_CHANGES

    assert _git(project, "rev-parse", "HEAD") == head
    assert ".claude/agents/ai-agent/CLAUDE.md" in _git(project, "ls-files").splitlines()


def test_a_project_cloned_again_gets_its_rules_whole_before_the_session(tmp_path):
    """Out of git, the rules do not come with a clone — the refresh writes the roles' charters and settings anew."""
    root = tmp_path / "projekt"
    (root / ".claude").mkdir(parents=True)

    create_project_postscaffold.refresh_v2_agent_charters(root, "projekt", project_type=project_surfaces.STANDARD)

    for role in create_project_postscaffold._V2_AGENTS:
        assert (root / ".claude" / "agents" / role / "CLAUDE.md").read_text(encoding="utf-8").startswith("# Pravidlá")
        profile = json.loads((root / ".claude" / "agents" / role / "settings.json").read_text(encoding="utf-8"))
        assert "<PROJECT_ROOT>" not in json.dumps(profile)
    assert (root / ".claude" / "skills" / "tdd" / create_project_postscaffold.SKILL_FILE).is_file()


def test_the_refresh_writes_the_same_role_settings_as_the_founding(tmp_path):
    founded, refreshed = tmp_path / "a" / "projekt", tmp_path / "b" / "projekt"
    (founded / ".claude").mkdir(parents=True)
    (refreshed / ".claude").mkdir(parents=True)
    create_project_postscaffold.provision_v2_agent_charters(founded, "projekt", "Projekt", adopted=False)
    create_project_postscaffold.refresh_v2_agent_charters(refreshed, "projekt")

    for role in create_project_postscaffold._V2_AGENTS:
        written = [(root / ".claude" / "agents" / role / "settings.json").read_text() for root in (founded, refreshed)]
        assert written[1] == written[0].replace(str(founded).lstrip("/"), str(refreshed).lstrip("/"))
        assert f"(//{str(refreshed).lstrip('/')}/" in written[1], "an absolute path rule is spelled with a double slash"


def test_a_new_project_ignores_the_rules_where_the_cockpit_looks_for_them():
    template = Path(settings.knowledge_base_path) / "templates" / "claude-project" / ".gitignore.tmpl"
    if not template.is_file():
        pytest.skip("the Knowledge Base template is not on this machine")

    lines = template.read_text().splitlines()
    assert [line for line in build_leftovers.AGENT_RULES.ignore if line not in lines] == []
