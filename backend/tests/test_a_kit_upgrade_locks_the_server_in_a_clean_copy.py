"""DEV-414 — the server's poetry.lock is re-locked in a clean copy of the project, and a failed upgrade says why.

16.11.2026 the upgrade of NEX Manager's template to nex-app-kit 0.15.2 failed twice with „over dostupnosť GitHubu“. The
lock pointed at the right tag and commit, but recorded the version 0.8.0: poetry, run in the project, reused the old
checkout in its ``backend/.venv/src/nex-app-kit`` (3.11.2026) and read the version from its ignored
``nex_app_kit.egg-info``. On a clean clone the same upgrade went through. Director: „Áno založ a urob.“
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from backend.config.settings import settings
from backend.services import app_kit
from backend.tests.test_a_poetry_project_upgrades_its_kit import _poetry_lock, poetry_locks_to, seed_poetry
from backend.tests.test_a_template_project_upgrades_its_kit import files, npm_resolves_to, release

REAL_RUN = subprocess.run
STALE = "Name: nex-app-kit\nVersion: 0.8.0\n"


def _git(root: Path, *args: str) -> None:
    REAL_RUN(["git", "-C", str(root), *args], check=True, capture_output=True)


def _project_with_a_stale_venv(root: Path) -> Path:
    """A poetry project committed in git, with what NEX Manager had beside it: an in-project virtualenv holding an old
    checkout of the kit, and a file nobody tracks."""
    seed_poetry(root)
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "T")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "founding")
    stale = root / "backend" / ".venv" / "src" / "nex-app-kit" / "server" / "src" / "nex_app_kit.egg-info"
    stale.mkdir(parents=True)
    (stale / "PKG-INFO").write_text(STALE, encoding="utf-8")
    (root / "backend" / "notes.txt").write_text("untracked\n", encoding="utf-8")
    return root


def _poetry_runs(monkeypatch) -> list[dict]:
    """Every ``poetry lock`` the cockpit starts: where it ran and what it saw there. It writes a lock with 0.5.0."""
    seen: list[dict] = []

    def run(argv, *args, cwd=None, **kwargs):
        if argv and argv[0] == settings.poetry_bin:
            here = Path(cwd)
            seen.append(
                {
                    "cwd": here,
                    "venv": (here / ".venv").exists(),
                    "untracked": (here / "notes.txt").exists(),
                    "pyproject": (here / "pyproject.toml").read_text(encoding="utf-8"),
                }
            )
            (here / "poetry.lock").write_text(_poetry_lock("0.5.0"), encoding="utf-8")
            return subprocess.CompletedProcess(argv, 0, "", "")
        return REAL_RUN(argv, *args, cwd=cwd, **kwargs)

    monkeypatch.setattr(app_kit.subprocess, "run", run)
    return seen


def test_poetry_locks_in_a_clean_copy_and_only_the_lock_comes_back(tmp_path, monkeypatch):
    root = _project_with_a_stale_venv(tmp_path / "nex-manager")
    pyproject = root / "backend" / "pyproject.toml"
    pyproject.write_text(pyproject.read_text(encoding="utf-8").replace("v0.3.0", "v0.5.0"), encoding="utf-8")
    seen = _poetry_runs(monkeypatch)

    assert app_kit.lock_poetry(root / "backend") is True

    [run] = seen
    assert run["cwd"] != root / "backend" and root not in run["cwd"].parents, "poetry ran in the project itself"
    assert (run["venv"], run["untracked"]) == (False, False), "poetry saw what git does not track"
    assert 'tag = "v0.5.0"' in run["pyproject"], "the copy carries the pin as the upgrade just wrote it"
    assert app_kit._poetry_locked_version(root / "backend" / "poetry.lock") == "0.5.0"
    assert not run["cwd"].exists(), "the copy is thrown away"
    assert (root / "backend" / ".venv" / "src").exists() and (root / "backend" / "notes.txt").exists()


def test_without_git_only_the_files_poetry_reads_are_copied(tmp_path, monkeypatch):
    root = seed_poetry(tmp_path / "project")
    (root / "backend" / "app.py").write_text("print('x')\n", encoding="utf-8")
    copied: list[list[str]] = []

    def run(argv, *args, cwd=None, **kwargs):
        if argv[0] == settings.poetry_bin:
            copied.append(sorted(p.name for p in Path(cwd).iterdir()))
            return subprocess.CompletedProcess(argv, 0, "", "")
        return subprocess.CompletedProcess(argv, 128, b"", b"fatal: not a git repository")

    monkeypatch.setattr(app_kit.subprocess, "run", run)

    assert app_kit.lock_poetry(root / "backend") is True
    assert copied == [["poetry.lock", "pyproject.toml"]]


def test_poetry_failing_leaves_the_projects_lock_as_it_was(tmp_path, monkeypatch):
    root = _project_with_a_stale_venv(tmp_path / "nex-manager")
    before = (root / "backend" / "poetry.lock").read_text(encoding="utf-8")

    def run(argv, *args, cwd=None, **kwargs):
        if argv[0] == settings.poetry_bin:
            (Path(cwd) / "poetry.lock").write_text("half written\n", encoding="utf-8")
            return subprocess.CompletedProcess(argv, 1, "", "resolver failed")
        return REAL_RUN(argv, *args, cwd=cwd, **kwargs)

    monkeypatch.setattr(app_kit.subprocess, "run", run)

    assert app_kit.lock_poetry(root / "backend") is False
    assert (root / "backend" / "poetry.lock").read_text(encoding="utf-8") == before


# --- a failed upgrade says which step failed --------------------------------------------------------------------


def _upgrade(root: Path, scaffold=None) -> str | None:
    return app_kit.upgrade_problem(str(root), "0.5.0", "nsv-standard", scaffold_package=scaffold or release("0.5.0"))


def test_a_server_lock_on_another_release_is_named_and_everything_goes_back(tmp_path, monkeypatch):
    seed_poetry(tmp_path)
    before = files(tmp_path)
    npm_resolves_to(monkeypatch, "0.5.0")
    poetry_locks_to(monkeypatch, "0.8.0")

    assert _upgrade(tmp_path) == "zámok servera zapísal nex-app-kit 0.8.0 namiesto 0.5.0"
    assert files(tmp_path) == before


def test_poetry_failing_is_named(tmp_path, monkeypatch):
    seed_poetry(tmp_path)
    npm_resolves_to(monkeypatch, "0.5.0")
    poetry_locks_to(monkeypatch, None)

    assert _upgrade(tmp_path) == "zámok servera (poetry) sa nepodarilo prepočítať"


def test_a_release_github_does_not_give_is_named(tmp_path, monkeypatch):
    seed_poetry(tmp_path)

    assert _upgrade(tmp_path, scaffold=lambda template, version: None) == (
        "verziu šablóny 0.5.0 sa nepodarilo stiahnuť z GitHubu — skús to o chvíľu znova"
    )


def test_a_frontend_lock_on_another_release_is_named(tmp_path, monkeypatch):
    seed_poetry(tmp_path)
    npm_resolves_to(monkeypatch, "0.4.0")
    poetry_locks_to(monkeypatch, "0.5.0")

    assert _upgrade(tmp_path) == "zámok frontendu ukazuje nex-app-kit 0.4.0 namiesto 0.5.0"


def test_the_settings_say_the_step_that_failed(client, db_session, tmp_path, monkeypatch):
    from backend.tests.test_an_upgrade_is_saved_in_the_projects_git import _db_project

    seed_poetry(tmp_path)
    project = _db_project(db_session, tmp_path)
    monkeypatch.setattr(app_kit, "fetch_scaffold_package", release("0.5.0"))
    npm_resolves_to(monkeypatch, "0.5.0")
    poetry_locks_to(monkeypatch, "0.8.0")

    refused = client.post(f"/api/v1/projects/{project.id}/app-kit-upgrade", json={"target_version": "0.5.0"})

    assert refused.status_code == 400
    assert refused.json()["detail"] == (
        "Povýšenie šablóny sa nepodarilo dokončiť — zámok servera zapísal nex-app-kit 0.8.0 namiesto 0.5.0. Rám ani "
        "server sa nezmenili."
    )
