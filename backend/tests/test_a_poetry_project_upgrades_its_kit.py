"""DEV-383 — the cockpit reads and upgrades the template's server pinned as a poetry git dependency (NEX Manager).

14.11.2026, measured on the live cockpit: the template status of NEX Manager said its server was „None“ and „behind 0“
although backend/pyproject.toml pinned nex-app-kit v0.13.0 — the cockpit read only the spelling the founding writes
(„nex-app-kit @ …/archive/refs/tags/vX.tar.gz#subdirectory=server“), and NEX Manager, converted by hand, has a poetry
git dependency. So no screen offered it the upgrade; on 13.11. the Staviteľ caught it in Príprava. Director, on the
ticket and on doing it: „Áno“, „Súhlasím“.

A poetry project builds from poetry.lock (NEX Manager's Dockerfile: ``poetry export``), so the upgrade re-locks it and
must see the lock follow — the same rule as the frontend's lockfile; otherwise every file goes back.
"""

from __future__ import annotations

from pathlib import Path

from backend.services import app_kit, nexshared
from backend.tests.test_a_template_project_upgrades_its_kit import (
    _lock,
    files,
    npm_resolves_to,
    seed,
    upgrade,
)

NEX_MANAGER_PYPROJECT = Path("/opt/projects/nex-manager/backend/pyproject.toml")

POETRY_PYPROJECT = """[tool.poetry]
name = "backend"

[tool.poetry.dependencies]
python = "^3.12"
fastapi = "^0.115"
# The ICC app template's server (nex-app-kit) — pinned to its tag.
nex-app-kit = {{git = "https://github.com/rauschiccsk/nex-app-kit.git", tag = "v{version}", subdirectory = "server"}}
"""


def _poetry_lock(version: str) -> str:
    return (
        '[[package]]\nname = "fastapi"\nversion = "0.115.0"\n\n'
        f'[[package]]\nname = "nex-app-kit"\nversion = "{version}"\n\n'
        '[metadata]\ncontent-hash = "x"\n'
    )


def seed_poetry(root: Path, frame: str = "0.3.0", server: str = "0.3.0") -> Path:
    seed(root, frame=frame, server=None)
    (root / "backend" / "pyproject.toml").write_text(POETRY_PYPROJECT.format(version=server), encoding="utf-8")
    (root / "backend" / "poetry.lock").write_text(_poetry_lock(server), encoding="utf-8")
    return root


def poetry_locks_to(monkeypatch, version: str | None) -> list[Path]:
    asked: list[Path] = []

    def lock(backend_dir: Path, **_kwargs) -> bool:
        asked.append(backend_dir)
        if version is None:
            return False
        (backend_dir / "poetry.lock").write_text(_poetry_lock(version), encoding="utf-8")
        return True

    monkeypatch.setattr(app_kit, "lock_poetry", lock)
    return asked


def test_the_server_pinned_as_a_poetry_git_dependency_is_read(tmp_path):
    seed_poetry(tmp_path, frame="0.3.0", server="0.3.0")

    status = app_kit.status_for_source(str(tmp_path), tags=["0.3.0", "0.5.0"], changelog_text="")

    assert [(p["name"], p["current"]) for p in status["parts"]] == [("frontend", "0.3.0"), ("server", "0.3.0")]
    assert (status["current"], status["behind"]) == ("0.3.0", 1)


def test_nex_managers_own_pyproject_is_read():
    """The real file the live cockpit could not read — a rule, not a hand-made copy of it."""
    if not NEX_MANAGER_PYPROJECT.is_file():
        import pytest

        pytest.skip("NEX Manager is not on this machine")
    tag = NEX_MANAGER_PYPROJECT.read_text(encoding="utf-8").split('tag = "v', 1)[1].split('"', 1)[0]

    assert app_kit.server_version(str(NEX_MANAGER_PYPROJECT.parent.parent)) == tag


def test_a_poetry_project_upgrades_both_sides_and_re_locks_the_server(tmp_path, monkeypatch):
    seed_poetry(tmp_path)
    npm_resolves_to(monkeypatch, "0.5.0")
    locked = poetry_locks_to(monkeypatch, "0.5.0")

    assert upgrade(tmp_path)

    pyproject = (tmp_path / "backend" / "pyproject.toml").read_text(encoding="utf-8")
    assert 'tag = "v0.5.0", subdirectory = "server"}' in pyproject and 'tag = "v0.3.0"' not in pyproject
    assert locked == [tmp_path / "backend"]
    assert app_kit.server_version(str(tmp_path)) == "0.5.0"
    assert nexshared.effective_version(str(tmp_path), "frontend", "nex-app-kit") == "0.5.0"


def test_a_poetry_lock_that_does_not_follow_puts_every_file_back(tmp_path, monkeypatch):
    seed_poetry(tmp_path)
    before = files(tmp_path) | {"lock": (tmp_path / "backend" / "poetry.lock").read_text(encoding="utf-8")}
    npm_resolves_to(monkeypatch, "0.5.0")
    poetry_locks_to(monkeypatch, "0.3.0")  # poetry kept the old release

    assert not upgrade(tmp_path)
    assert files(tmp_path) | {"lock": (tmp_path / "backend" / "poetry.lock").read_text(encoding="utf-8")} == before


def test_poetry_failing_puts_every_file_back(tmp_path, monkeypatch):
    seed_poetry(tmp_path)
    before = files(tmp_path)
    npm_resolves_to(monkeypatch, "0.5.0")
    poetry_locks_to(monkeypatch, None)

    assert not upgrade(tmp_path)
    assert files(tmp_path) == before


def test_the_founding_spelling_is_never_re_locked(tmp_path, monkeypatch):
    seed(tmp_path)
    npm_resolves_to(monkeypatch, "0.5.0")
    locked = poetry_locks_to(monkeypatch, "0.5.0")

    assert upgrade(tmp_path)
    assert locked == []


def test_the_upgrade_commit_carries_the_poetry_lock(tmp_path, monkeypatch):
    seed_poetry(tmp_path)
    staged = []
    monkeypatch.setattr(nexshared, "commit_pin_upgrade", lambda source, target, **kw: staged.append(kw["also"]) or True)

    assert app_kit.commit_upgrade(str(tmp_path), "0.5.0")
    assert staged == [("backend/pyproject.toml", "backend/poetry.lock")]
    assert _lock  # the frontend helpers are shared with the founding spelling's tests


def test_the_image_brings_the_poetry_the_settings_name():
    """The re-lock runs settings.poetry_bin; the image must install poetry exactly there — one path, two readers."""
    from backend.config.settings import settings

    dockerfile = (Path(__file__).resolve().parents[2] / "Dockerfile").read_text(encoding="utf-8")
    venv = str(Path(settings.poetry_bin).parent.parent)
    assert f"python -m venv {venv} && {venv}/bin/pip install --no-cache-dir poetry==1.8" in dockerfile
