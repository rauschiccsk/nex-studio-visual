"""DEV-176 — what a build leaves behind (``*.tsbuildinfo``, TypeScript's incremental cache) stays out of git.

The project template's ``.gitignore`` did not name it: the switch of Career Asistent to Web committed the web's two
files with it, the agent its frontend's, and NEX Websites carries two more — after every type check they show as
changes nobody made. Director 21.10.2026: „Súhlasím“ (the batch), „Poradie nechám na teba“. The template now ignores
them; a project that still tracks them loses them in one commit of the cockpit's after a finished task.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from backend.config.settings import settings
from backend.services import build_leftovers, ci_runs, git_push, orchestrator


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def project(tmp_path, monkeypatch) -> tuple[Path, Path]:
    """A project that tracks two leftovers, with an origin."""
    monkeypatch.setattr(git_push, "wire_credentials", lambda *, timeout: None)
    monkeypatch.setattr(orchestrator, "_start_ci", lambda root, workflow, branch: None)
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-q", "-b", "main", str(origin)], check=True)
    ws = tmp_path / "ws"
    ws.mkdir()
    _git(ws, "init", "-q", "-b", "main")
    _git(ws, "config", "user.email", "t@example.com")
    _git(ws, "config", "user.name", "t")
    (ws / ".gitignore").write_text("node_modules\n")
    for rel in ("frontend/tsconfig.app.tsbuildinfo", "web/tsconfig.node.tsbuildinfo", "frontend/src/a.ts"):
        (ws / rel).parent.mkdir(parents=True, exist_ok=True)
        (ws / rel).write_text("{}\n")
    _git(ws, "add", "-A")
    _git(ws, "commit", "-q", "-m", "founding")
    _git(ws, "remote", "add", "origin", str(origin))
    _git(ws, "push", "-q", "origin", "main")
    return ws, origin


def _tracked_leftovers(ws: Path) -> list[str]:
    return [path for path in _git(ws, "ls-files").splitlines() if path.endswith(".tsbuildinfo")]


def test_a_project_that_tracks_leftovers_stops_tracking_them_in_one_commit(project):
    ws, _origin = project

    assert build_leftovers.keep_out_of_git(ws) is None

    assert _tracked_leftovers(ws) == []
    assert (ws / "frontend" / "tsconfig.app.tsbuildinfo").is_file(), "the build's cache must stay on disk"
    assert build_leftovers.PATTERN in (ws / ".gitignore").read_text().splitlines()
    assert _git(ws, "log", "-1", "--format=%s") == build_leftovers.COMMIT_MESSAGE
    assert sorted(_git(ws, "show", "--name-only", "--format=", "HEAD").split()) == [
        ".gitignore",
        "frontend/tsconfig.app.tsbuildinfo",
        "web/tsconfig.node.tsbuildinfo",
    ]
    (ws / "frontend" / "tsconfig.app.tsbuildinfo").write_text('{"changed": true}\n')
    assert _git(ws, "status", "--porcelain") == "", "a leftover changed by the next type check is not a change"


def test_an_ignore_file_without_a_final_newline_keeps_its_last_line(project):
    ws, _origin = project
    (ws / ".gitignore").write_text("node_modules")
    _git(ws, "commit", "-q", "-am", "an ignore file without a final newline")

    assert build_leftovers.keep_out_of_git(ws) is None

    lines = (ws / ".gitignore").read_text().splitlines()
    assert (lines[0], lines[-1]) == ("node_modules", build_leftovers.PATTERN)


def test_a_clean_project_gets_no_commit(project):
    ws, _origin = project
    build_leftovers.keep_out_of_git(ws)
    head = _git(ws, "rev-parse", "HEAD")

    assert build_leftovers.keep_out_of_git(ws) is None

    assert _git(ws, "rev-parse", "HEAD") == head


def test_someone_elses_staged_change_is_neither_committed_nor_touched(project):
    ws, _origin = project
    (ws / "frontend" / "src" / "a.ts").write_text("export const a = 2;\n")
    _git(ws, "add", "frontend/src/a.ts")
    head = _git(ws, "rev-parse", "HEAD")

    why = build_leftovers.keep_out_of_git(ws)

    assert why == "pozostatky stavby ostali v gite — v projekte sú cudzie zmeny, ktoré by sa primiešali do commitu"
    assert _git(ws, "rev-parse", "HEAD") == head
    assert _git(ws, "status", "--porcelain").splitlines() == ["M  frontend/src/a.ts"]
    assert len(_tracked_leftovers(ws)) == 2


def test_leftovers_waiting_in_the_index_are_the_cleanups_own_not_someone_elses(project):
    """Career Asistent 22.10.2026: the Vizuál commit killed on 21.10. (DEV-180) left the frontend's two leftovers
    staged, and the cleanup took them for someone else's change — it never ran, and they showed as unsaved work
    (DEV-175)."""
    ws, _origin = project
    (ws / "frontend" / "tsconfig.node.tsbuildinfo").write_text("{}\n")
    _git(ws, "add", "frontend/tsconfig.node.tsbuildinfo")

    assert build_leftovers.keep_out_of_git(ws) is None

    assert _tracked_leftovers(ws) == []
    assert sorted(_git(ws, "show", "--name-only", "--format=", "HEAD").split()) == [
        ".gitignore",
        "frontend/tsconfig.app.tsbuildinfo",
        "web/tsconfig.node.tsbuildinfo",
    ]
    assert (ws / "frontend" / "tsconfig.node.tsbuildinfo").is_file(), "the build's cache must stay on disk"
    assert _git(ws, "status", "--porcelain") == ""


def test_a_leftover_staged_in_an_ignoring_project_leaves_the_index_without_an_empty_commit(project):
    ws, _origin = project
    build_leftovers.keep_out_of_git(ws)
    head = _git(ws, "rev-parse", "HEAD")
    (ws / "web" / "tsconfig.app.tsbuildinfo").write_text("{}\n")
    _git(ws, "add", "-f", "web/tsconfig.app.tsbuildinfo")

    assert build_leftovers.keep_out_of_git(ws) is None

    assert _git(ws, "rev-parse", "HEAD") == head
    assert (_tracked_leftovers(ws), _git(ws, "status", "--porcelain")) == ([], "")


def test_a_leftover_staged_next_to_someone_elses_change_touches_neither(project):
    ws, _origin = project
    (ws / "frontend" / "tsconfig.node.tsbuildinfo").write_text("{}\n")
    (ws / "frontend" / "src" / "a.ts").write_text("export const a = 2;\n")
    _git(ws, "add", "frontend/tsconfig.node.tsbuildinfo", "frontend/src/a.ts")
    head = _git(ws, "rev-parse", "HEAD")

    assert build_leftovers.keep_out_of_git(ws) == build_leftovers.FOREIGN_CHANGES

    assert _git(ws, "rev-parse", "HEAD") == head
    assert _git(ws, "status", "--porcelain").splitlines() == [
        "M  frontend/src/a.ts",
        "A  frontend/tsconfig.node.tsbuildinfo",
    ]


def test_someone_elses_unsaved_edit_of_the_ignore_file_is_neither_committed_nor_touched(project):
    ws, _origin = project
    (ws / ".gitignore").write_text("node_modules\ncoverage\n")
    head = _git(ws, "rev-parse", "HEAD")

    assert build_leftovers.keep_out_of_git(ws) == build_leftovers.FOREIGN_CHANGES

    assert _git(ws, "rev-parse", "HEAD") == head
    assert (ws / ".gitignore").read_text() == "node_modules\ncoverage\n"
    assert (_git(ws, "diff", "--name-only"), _git(ws, "diff", "--cached", "--name-only")) == (".gitignore", "")


def test_a_red_proof_the_agent_stayed_on_stays_a_proof(project):
    ws, _origin = project
    _git(ws, "checkout", "-q", "-b", "ci/gate-red")
    (ws / "frontend" / "src" / "a.ts").write_text("export const a = 2;\n")
    _git(ws, "commit", "-q", "-am", f"{ci_runs.PROOF_MARK} the gate without its check (do not merge)")
    head = _git(ws, "rev-parse", "HEAD")

    assert build_leftovers.keep_out_of_git(ws) is None

    assert _git(ws, "rev-parse", "HEAD") == head
    assert len(_tracked_leftovers(ws)) == 2


def test_the_projects_pre_commit_check_does_not_stop_the_cleanup(project):
    """The project's pre-commit hook type-checks the frontend; for two cache files it has nothing to say."""
    ws, _origin = project
    hook = ws / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)

    assert build_leftovers.keep_out_of_git(ws) is None

    assert _git(ws, "log", "-1", "--format=%s") == build_leftovers.COMMIT_MESSAGE


def test_a_cleanup_that_cannot_be_done_says_why_and_does_not_raise(project):
    ws, _origin = project
    _git(ws, "rm", "-q", ".gitignore")
    (ws / ".gitignore").mkdir()
    (ws / ".gitignore" / "kept").write_text("a directory where the ignore file should be\n")
    _git(ws, "add", "-A")
    _git(ws, "commit", "-q", "-m", "an ignore file that cannot be written")

    assert build_leftovers.keep_out_of_git(ws) == "pozostatky stavby sa z gitu nepodarilo odobrať"

    assert len(_tracked_leftovers(ws)) == 2


async def test_after_a_finished_task_the_cleanup_goes_to_github_with_the_work(project):
    ws, origin = project

    assert await orchestrator._push_after_task(ws) is None

    assert _git(origin, "log", "-1", "--format=%s", "main") == build_leftovers.COMMIT_MESSAGE
    assert [p for p in _git(origin, "ls-tree", "-r", "--name-only", "main").splitlines() if "tsbuildinfo" in p] == []


def test_a_new_project_ignores_the_leftovers_where_the_cockpit_looks_for_it():
    """``init.sh`` makes the project's root ``.gitignore`` of this file — the one :func:`keep_out_of_git` reads, so a
    new project needs no cleanup commit; a pattern without a slash covers ``frontend/`` and ``web/`` alike."""
    template = Path(settings.knowledge_base_path) / "templates" / "claude-project" / ".gitignore.tmpl"
    if not template.is_file():
        pytest.skip("the Knowledge Base template is not on this machine")

    assert build_leftovers.PATTERN in template.read_text().splitlines()
