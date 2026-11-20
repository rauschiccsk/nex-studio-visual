"""DEV-445 — the live preview bundles what is INSTALLED, and is started only on what the lock file prescribes.

20.11.2026, Orthodox Hermenia 0.2.0: the version upgraded the template (nex-app-kit v0.17.0, nex-shared v0.40.0) — the
lock file was new, ``node_modules`` was not. The preview started at 16:12 and Vite bundled the old libraries; the agent
installed the new ones at 16:13; the lock file did not move, so a fingerprint of the lock file kept the stale preview
„current“ and the Manažér checked the old screens: „mám taký dojem že to povýšenie vôbec nebolo urobené“. Pinned: the
fingerprint is what npm records as installed, and before a preview starts ``node_modules`` is brought to the lock file.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

from backend.services import vizual_sandbox

KIT_OLD = {"version": "0.15.2", "resolved": "git+ssh://git@github.com/rauschiccsk/nex-app-kit.git#old"}
KIT_NEW = {"version": "0.17.0", "resolved": "git+ssh://git@github.com/rauschiccsk/nex-app-kit.git#new"}
REACT = {"version": "19.0.0", "resolved": "https://registry.npmjs.org/react/-/react-19.0.0.tgz"}
ROLLUP_LINUX = {"version": "4.68.5", "resolved": "https://x/rollup-linux.tgz", "optional": True}


def _frontend(tmp_path: Path, *, locked: dict, installed: dict | None) -> Path:
    lock = {"name": "app", "lockfileVersion": 3, "packages": {"": {"name": "app"}, **locked}}
    (tmp_path / "package.json").write_text('{"name": "app"}', encoding="utf-8")
    (tmp_path / "package-lock.json").write_text(json.dumps(lock), encoding="utf-8")
    if installed is not None:
        (tmp_path / "node_modules").mkdir()
        hidden = {"name": "app", "lockfileVersion": 3, "packages": installed}
        (tmp_path / vizual_sandbox.INSTALLED_LOCK).write_text(json.dumps(hidden), encoding="utf-8")
    return tmp_path


def test_the_fingerprint_follows_an_install_even_when_the_lock_file_stays(tmp_path) -> None:
    front = _frontend(
        tmp_path, locked={"node_modules/nex-app-kit": KIT_NEW}, installed={"node_modules/nex-app-kit": KIT_OLD}
    )
    before = vizual_sandbox.dependencies_fingerprint(front)

    (front / vizual_sandbox.INSTALLED_LOCK).write_text(
        json.dumps({"packages": {"node_modules/nex-app-kit": KIT_NEW}}), encoding="utf-8"
    )

    assert vizual_sandbox.dependencies_fingerprint(front) != before
    assert (
        vizual_sandbox.dependencies_fingerprint(front)
        == hashlib.sha256((front / vizual_sandbox.INSTALLED_LOCK).read_bytes()).hexdigest()[:16]
    )


def test_installed_older_than_the_lock_file_does_not_match(tmp_path) -> None:
    front = _frontend(
        tmp_path,
        locked={"node_modules/nex-app-kit": KIT_NEW, "node_modules/react": REACT},
        installed={"node_modules/nex-app-kit": KIT_OLD, "node_modules/react": REACT},
    )

    assert vizual_sandbox.installed_matches_lock(front) is False


def test_a_locked_package_not_installed_does_not_match_unless_it_is_optional(tmp_path) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    missing = _frontend(
        tmp_path / "a",
        locked={"node_modules/nex-app-kit": KIT_NEW, "node_modules/react": REACT},
        installed={"node_modules/nex-app-kit": KIT_NEW},
    )
    optional_only = _frontend(
        tmp_path / "b",
        locked={"node_modules/react": REACT, "node_modules/@rollup/rollup-linux": ROLLUP_LINUX},
        installed={"node_modules/react": REACT},
    )

    assert vizual_sandbox.installed_matches_lock(missing) is False
    assert vizual_sandbox.installed_matches_lock(optional_only) is True


def test_what_the_lock_file_prescribes_matches(tmp_path) -> None:
    front = _frontend(
        tmp_path,
        locked={"node_modules/nex-app-kit": KIT_NEW, "node_modules/react": REACT},
        installed={"node_modules/nex-app-kit": KIT_NEW, "node_modules/react": REACT},
    )

    assert vizual_sandbox.installed_matches_lock(front) is True


def test_node_modules_without_a_record_of_what_it_holds_does_not_match(tmp_path) -> None:
    front = _frontend(tmp_path, locked={"node_modules/react": REACT}, installed=None)
    (front / "node_modules").mkdir()

    assert vizual_sandbox.installed_matches_lock(front) is False


def _installs(monkeypatch) -> list[list[str]]:
    ran: list[list[str]] = []

    def fake_run(argv, **_kw):
        ran.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(vizual_sandbox.subprocess, "run", fake_run)
    return ran


def test_before_the_preview_starts_stale_node_modules_are_brought_to_the_lock_file(tmp_path, monkeypatch) -> None:
    ran = _installs(monkeypatch)
    front = _frontend(
        tmp_path, locked={"node_modules/nex-app-kit": KIT_NEW}, installed={"node_modules/nex-app-kit": KIT_OLD}
    )

    vizual_sandbox._ensure_node_modules(front, slug="orthodox-hermenia")

    assert len(ran) == 1 and ran[0][-2:] == ["npm", "ci"]
    assert f"{front}:/app" in ran[0], "only the frontend is mounted, as for the dev server"


def test_node_modules_that_match_are_left_alone(tmp_path, monkeypatch) -> None:
    ran = _installs(monkeypatch)
    front = _frontend(
        tmp_path, locked={"node_modules/nex-app-kit": KIT_NEW}, installed={"node_modules/nex-app-kit": KIT_NEW}
    )

    vizual_sandbox._ensure_node_modules(front, slug="orthodox-hermenia")

    assert ran == []
