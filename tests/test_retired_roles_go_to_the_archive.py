"""DEV-217 — the refresh of the agents' rules moves the role dirs the cockpit no longer has into an archive, never
deletes them.

24.10.2026 NEX Manager carried .claude/agents/{designer,implementer} and NEX Inbox also {coordinator,customer} — the
rules of the cockpit's first version with three agents, 483–712 lines written for each project. Nothing reads them
(the cockpit reads only ai-agent and auditor; no launcher of the old roles is left on ANDROS) and git ignores them, so
they lived only on disk. The audit of 28.07.2026 had forbidden deleting them in an adopted project („would destroy
live configuration on the strength of a name“). Director: „Áno, súhlasím, presuň ich do archívu a implementuj“.

Pinned: the refresh moves every role dir but ai-agent and auditor to .claude/agents-v1-archiv/ byte for byte, with a
note saying why; git is made to ignore the archive locally before anything moves (no tracked file changes); a project
whose own rules would put the archive into git keeps its dirs where they are; a second refresh changes nothing.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from backend.services import create_project_postscaffold as cps

ARCHIVE = ".claude/agents-v1-archiv"


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=30).stdout


def _project(tmp_path: Path, *, roles=("designer", "implementer", "coordinator", "customer"), gitignore=None) -> Path:
    root = tmp_path / "proj"
    agents = root / ".claude" / "agents"
    agents.mkdir(parents=True)
    for role in roles:
        (agents / role).mkdir()
        (agents / role / "CLAUDE.md").write_text(f"# {role} — pravidlá prvej verzie\n", encoding="utf-8")
        (agents / role / "settings.json").write_text('{"role": "%s"}\n' % role, encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(root)], check=True, timeout=30)
    (root / ".gitignore").write_text(gitignore if gitignore is not None else "/.claude/agents/\n", encoding="utf-8")
    _git(root, "add", ".gitignore")
    _git(root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init")
    return root


def _refresh(root: Path) -> None:
    cps.refresh_v2_agent_charters(root, "demo")


def test_every_role_the_cockpit_no_longer_has_moves_to_the_archive_byte_for_byte(tmp_path):
    root = _project(tmp_path)

    _refresh(root)

    agents = root / ".claude" / "agents"
    assert sorted(p.name for p in agents.iterdir()) == ["ai-agent", "auditor"]
    for role in ("designer", "implementer", "coordinator", "customer"):
        assert (root / ARCHIVE / role / "CLAUDE.md").read_text(
            encoding="utf-8"
        ) == f"# {role} — pravidlá prvej verzie\n"
        assert (root / ARCHIVE / role / "settings.json").read_text(encoding="utf-8") == '{"role": "%s"}\n' % role


def test_the_archive_says_why_it_is_there(tmp_path):
    root = _project(tmp_path, roles=("designer", "implementer"))

    _refresh(root)

    note = (root / ARCHIVE / "README.md").read_text(encoding="utf-8")
    assert note.startswith("# Pravidlá rolí z prvej verzie kokpitu\n")
    assert "Nič sa nezmazalo." in note
    assert "presunuté: designer, implementer" in note


def test_git_never_sees_the_archive_and_no_tracked_file_changes(tmp_path):
    root = _project(tmp_path)

    _refresh(root)

    assert _git(root, "status", "--porcelain", "--untracked-files=all", "--", ARCHIVE, ".gitignore") == ""
    assert (root / ".gitignore").read_text(encoding="utf-8") == "/.claude/agents/\n"
    assert f"/{ARCHIVE}/" in (root / ".git" / "info" / "exclude").read_text(encoding="utf-8").splitlines()


def test_a_project_whose_rules_would_put_the_archive_into_git_keeps_its_dirs(tmp_path):
    root = _project(tmp_path, roles=("designer",), gitignore=f"/.claude/agents/\n!/{ARCHIVE}/\n")

    _refresh(root)

    assert (root / ".claude" / "agents" / "designer" / "CLAUDE.md").is_file(), "nothing moves into a place git sees"
    assert not (root / ARCHIVE / "designer").exists()


def test_a_second_refresh_changes_nothing(tmp_path):
    root = _project(tmp_path, roles=("designer",))
    _refresh(root)
    note = (root / ARCHIVE / "README.md").read_text(encoding="utf-8")

    _refresh(root)

    assert (root / ARCHIVE / "README.md").read_text(encoding="utf-8") == note
    assert sorted(p.name for p in (root / ARCHIVE).iterdir()) == ["README.md", "designer"]


def test_a_role_already_in_the_archive_is_never_overwritten(tmp_path):
    root = _project(tmp_path, roles=("designer",))
    (root / ARCHIVE / "designer").mkdir(parents=True)
    (root / ARCHIVE / "designer" / "CLAUDE.md").write_text("staršia kópia\n", encoding="utf-8")

    _refresh(root)

    assert (root / ARCHIVE / "designer" / "CLAUDE.md").read_text(encoding="utf-8") == "staršia kópia\n"
    assert (root / ARCHIVE / "designer-2" / "CLAUDE.md").read_text(encoding="utf-8") == (
        "# designer — pravidlá prvej verzie\n"
    )


def test_a_project_with_only_the_current_roles_gets_no_archive(tmp_path):
    root = _project(tmp_path, roles=())

    _refresh(root)

    assert not (root / ARCHIVE).exists()
    assert sorted(p.name for p in (root / ".claude" / "agents").iterdir()) == ["ai-agent", "auditor"]


@pytest.mark.parametrize("name", ["ai-agent", "auditor"])
def test_the_current_roles_stay_where_the_engine_reads_them(tmp_path, name):
    root = _project(tmp_path)

    _refresh(root)

    assert (root / ".claude" / "agents" / name / "CLAUDE.md").is_file()
    assert not (root / ARCHIVE / name).exists()
