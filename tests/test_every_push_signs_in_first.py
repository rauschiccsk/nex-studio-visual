"""DEV-161 — every push of a project to GitHub signs in first.

The backend container's git signs in through gh's credential helper, which every recreate of the container (every
cockpit deploy) throws away. The switch to Web pushed without wiring it: Career Asistent's switch stayed on ANDROS
(21.10.2026: „could not read Username for 'https://github.com'“). Its tests pushed to a local repository that needs
no sign-in, so they could not see it. Director: „Áno treba opraviť a nasaď.“ Here every way the cockpit pushes a
project is asked whether gh signed in before the push reached git, and the backend is searched for a push that goes
around :mod:`backend.services.git_push`.
"""

from __future__ import annotations

import ast
import os
import stat
import subprocess
from pathlib import Path

import pytest

from backend.services import create_project_postscaffold as postscaffold
from backend.services import orchestrator, template_bootstrap

BACKEND = Path(__file__).resolve().parents[1] / "backend"
#: The one module that pushes, and the Knowledge Base, which signs every one of its calls in itself
#: (``kb_git.network_env``) and pushes the cockpit's KB, not a project.
MAY_PUSH = {"services/git_push.py", "services/kb_git.py"}


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True).stdout.strip()


def _executable(path: Path, body: str) -> None:
    path.write_text("#!/usr/bin/env bash\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


@pytest.fixture
def signed(tmp_path, monkeypatch) -> tuple[Path, Path]:
    """A project with an origin, a ``gh`` that writes down that it signed git in, and a ``pre-push`` hook that writes
    down the push — both into one log, in the order they happened."""
    log = tmp_path / "log"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _executable(bin_dir / "gh", f'echo "gh $*" >> {log}\n')
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    ws = tmp_path / "ws"
    ws.mkdir()
    _git(ws, "init", "-q", "-b", "main")
    _git(ws, "config", "user.email", "t@example.com")
    _git(ws, "config", "user.name", "t")
    (ws / "README.md").write_text("# project\n")
    _git(ws, "add", "-A")
    _git(ws, "commit", "-q", "-m", "founding")
    _git(ws, "remote", "add", "origin", str(origin))
    _executable(ws / ".git" / "hooks" / "pre-push", f'echo "push" >> {log}\n')
    (ws / "web").mkdir()
    (ws / "web" / "package.json").write_text("{}")
    return ws, log


def _switch(ws: Path) -> None:
    assert postscaffold.commit_and_push(ws, ["web"], "feat(web): x", "kariera", scaffold=True) is None


def _founding_leftovers(ws: Path) -> None:
    postscaffold._commit_and_push_scaffold_finalisation(ws, "kariera")


def _build(ws: Path) -> None:
    _git(ws, "add", "web")
    _git(ws, "commit", "-q", "-m", "feat: task")
    assert orchestrator._push_branch(ws) == (True, "main", None)


def _founding(ws: Path) -> None:
    _git(ws, "add", "web")
    _git(ws, "commit", "-q", "-m", "feat: founding")
    origin = _git(ws, "remote", "get-url", "origin")
    template_bootstrap.push_and_verify(target=str(ws), repo_full_name="rauschiccsk/kariera", remote_url=origin)


@pytest.mark.parametrize("pushes", [_switch, _founding_leftovers, _build, _founding], ids=lambda f: f.__name__)
def test_every_push_signs_in_to_github_before_it_pushes(signed, pushes):
    ws, log = signed

    pushes(ws)

    assert log.read_text().splitlines() == ["gh auth setup-git", "push"]


def _pushes_in(path: Path) -> list[int]:
    """Lines of *path* that build a git command with ``push`` in it — a list or tuple holding the word."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, (ast.List, ast.Tuple))
        and any(isinstance(item, ast.Constant) and item.value == "push" for item in node.elts)
    ]


def test_no_push_goes_around_the_sign_in():
    found = {
        str(path.relative_to(BACKEND)): _pushes_in(path)
        for path in BACKEND.rglob("*.py")
        if "tests" not in path.relative_to(BACKEND).parts and _pushes_in(path)
    }

    assert set(found) == MAY_PUSH, f"a git push outside backend/services/git_push.py: {found}"
