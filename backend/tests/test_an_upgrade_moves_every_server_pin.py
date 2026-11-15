"""DEV-353 — an upgrade of the app template moves the server's pin in every file the server is built from.

10.11.2026, NEX Knowledge 1.1.0: the Manažér accepted „K dispozícii je novšia šablóna aplikácie“ when founding the
version; the cockpit moved the frame (frontend/package.json and its lockfile) and backend/pyproject.toml to v0.11.2 —
but the server's image is built from backend/requirements.txt, which stayed on v0.9.0. The server would have been
built on the old template, the frame on the new one: exactly what the upgrade is there to prevent. Director, on the
ticket: „Súhlasím“; on doing it now: „súhlasím“.

Every file the server is built from moves, or nothing does: a requirements.txt that names the kit in the founding's
spelling is rewritten with pyproject.toml and goes into the same commit; one that names it in a spelling the cockpit
cannot rewrite refuses the upgrade before any file is touched.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from backend.services import app_kit, app_template
from backend.tests.test_a_template_project_upgrades_its_kit import files, npm_resolves_to, seed, upgrade

REQUIREMENTS = "backend/requirements.txt"


def _requirements(root: Path, kit_line: str) -> Path:
    path = root / REQUIREMENTS
    path.write_text(f"fastapi>=0.110\n{kit_line}\nuvicorn[standard]>=0.29\n", encoding="utf-8")
    return path


def _all(root: Path) -> dict[str, str]:
    return {**files(root), REQUIREMENTS: (root / REQUIREMENTS).read_text(encoding="utf-8")}


def test_the_server_moves_in_requirements_too(tmp_path, monkeypatch):
    root = seed(tmp_path)
    requirements = _requirements(root, app_template.kit_server_dependency("0.3.0"))
    npm_resolves_to(monkeypatch, "0.5.0")

    assert upgrade(root) is True

    assert requirements.read_text(encoding="utf-8") == (
        f"fastapi>=0.110\n{app_template.kit_server_dependency('0.5.0')}\nuvicorn[standard]>=0.29\n"
    )
    assert app_template.kit_server_dependency("0.5.0") in (root / "backend" / "pyproject.toml").read_text("utf-8")
    assert app_kit.server_version(str(root)) == "0.5.0"


def test_a_pin_the_cockpit_cannot_rewrite_refuses_the_upgrade_and_touches_nothing(tmp_path, monkeypatch):
    root = seed(tmp_path)
    _requirements(root, "nex-app-kit==0.3.0")
    before = _all(root)
    asked = npm_resolves_to(monkeypatch, "0.5.0")

    assert upgrade(root) is False

    assert _all(root) == before
    assert asked == [], "refused before anything ran"


def test_requirements_without_the_kit_stay_as_they_are(tmp_path, monkeypatch):
    root = seed(tmp_path)
    requirements = _requirements(root, "requests>=2")
    npm_resolves_to(monkeypatch, "0.5.0")

    assert upgrade(root) is True

    assert requirements.read_text(encoding="utf-8") == "fastapi>=0.110\nrequests>=2\nuvicorn[standard]>=0.29\n"


def test_two_files_on_two_releases_read_as_no_one_version(tmp_path):
    """The state of 10.11.2026 — the status does not claim the server is on the release requirements.txt does not
    build."""
    root = seed(tmp_path, server="0.11.2")
    _requirements(root, app_template.kit_server_dependency("0.9.0"))

    assert app_kit.server_version(str(root)) is None


def test_the_commit_carries_requirements(tmp_path, monkeypatch):
    root = seed(tmp_path)
    _requirements(root, app_template.kit_server_dependency("0.3.0"))
    git = ["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-qm", "init"], check=True)
    for key in ("GIT_AUTHOR_NAME", "GIT_COMMITTER_NAME"):
        monkeypatch.setenv(key, "t")
    for key in ("GIT_AUTHOR_EMAIL", "GIT_COMMITTER_EMAIL"):
        monkeypatch.setenv(key, "t@t")
    npm_resolves_to(monkeypatch, "0.5.0")
    assert upgrade(root)

    assert app_kit.commit_upgrade(str(root), "0.5.0") is True

    show = subprocess.run(
        ["git", "-C", str(root), "show", "--name-only", "--format=%s", "HEAD"], capture_output=True, text=True
    ).stdout.split()
    assert sorted(show[5:]) == [
        "backend/pyproject.toml",
        REQUIREMENTS,
        "frontend/package-lock.json",
        "frontend/package.json",
    ]
