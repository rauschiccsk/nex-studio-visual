"""DEV-307 — a first PROD deploy that graduates the version to 1.0.0 leaves its release notes consistent.

04.11.2026, NEX Knowledge: the graduation commit 95ef27e („docs(release-notes): graduate notes to v1.0.0“) moved
``docs/specs/versions/v0.2.1`` to ``v1.0.0`` and nothing else. The note's heading still said „## v0.2.1 — Rýchla
oprava“ and ``release-dates.json`` (DEV-258, nex-app-kit's Aktualizácie read it) had no v1.0.0 — the app's own guard
(„the file has no heading of its own version“) turned the main branch red and Aktualizácie would show 1.0.0 without a
day. The graduation now rewrites the heading's version and moves the day to the new number, in the one commit.
Director 05.11.2026: „Súhlasím, môžeš začať.“
"""

from __future__ import annotations

import json
import subprocess
from datetime import date
from pathlib import Path

import pytest

from backend.services import claude_agent
from backend.services import deploy as deploy_service
from tests.test_deploy_service import _deploy, _FakeRunner, _make_customer, _make_project, _make_user, _make_version

NOTE = "## v0.2.1 — Rýchla oprava\n\n- Aplikácia ukladá súbory tak, aby ich nočná záloha vždy zálohovala.\n"
DATES = {"v0.1.0": "2026-10-30", "v0.2.0": "2026-10-30", "v0.2.1": "2026-11-04"}


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True).stdout.strip()


def _checkout(projects_root: Path, slug: str, origin: Path) -> Path:
    work = projects_root / slug
    versions = work / "docs" / "specs" / "versions"
    for number, text in (("v0.2.0", "## v0.2.0\n\n- Druhá verzia.\n"), ("v0.2.1", NOTE)):
        (versions / number).mkdir(parents=True)
        (versions / number / "RELEASE_NOTES.md").write_text(text, encoding="utf-8")
    (versions / "release-dates.json").write_text(json.dumps(DATES, indent=2) + "\n", encoding="utf-8")
    _git(work, "init", "-q", "-b", "main")
    _git(work, "config", "user.email", "studio@isnex.eu")
    _git(work, "config", "user.name", "NEX Studio")
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", "docs(release-notes): v0.2.1")
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    _git(work, "remote", "add", "origin", str(origin))
    _git(work, "push", "-q", "origin", "main")
    return work


@pytest.fixture
async def graduated(db_session, monkeypatch, tmp_path) -> Path:
    projects_root = tmp_path / "projects"
    monkeypatch.setattr(claude_agent, "PROJECTS_ROOT", projects_root)
    user = _make_user(db_session)
    project = _make_project(db_session, user=user)
    work = _checkout(projects_root, project.slug, tmp_path / "origin.git")
    _make_version(db_session, project, "0.2.1")
    customer = _make_customer(db_session, project)
    runner = _FakeRunner()
    await _deploy(db_session, customer, version_number="0.2.1", environment="uat", actor=user, runner=runner)
    deploy_service.accept(db_session, customer.id, "0.2.1", user.id)
    _event, _url, bumped = await _deploy(
        db_session, customer, version_number="0.2.1", environment="prod", actor=user, runner=runner
    )
    assert bumped == "1.0.0"
    return work


@pytest.mark.asyncio
async def test_the_moved_note_is_headed_by_its_new_number(graduated):
    note = (graduated / "docs" / "specs" / "versions" / "v1.0.0" / "RELEASE_NOTES.md").read_text(encoding="utf-8")

    assert note == NOTE.replace("## v0.2.1 —", "## v1.0.0 —", 1)
    assert not (graduated / "docs" / "specs" / "versions" / "v0.2.1" / "RELEASE_NOTES.md").exists()


@pytest.mark.asyncio
async def test_the_day_of_the_release_moves_to_the_new_number(graduated):
    dates = json.loads((graduated / "docs" / "specs" / "versions" / "release-dates.json").read_text(encoding="utf-8"))

    assert dates == {"v0.1.0": "2026-10-30", "v0.2.0": "2026-10-30", "v1.0.0": date.today().isoformat()}


@pytest.mark.asyncio
async def test_it_is_one_commit_and_nothing_is_left_behind(graduated):
    assert _git(graduated, "log", "-1", "--format=%s") == "docs(release-notes): graduate notes to v1.0.0"
    assert sorted(_git(graduated, "show", "--no-renames", "--name-only", "--format=", "HEAD").split()) == [
        "docs/specs/versions/release-dates.json",
        "docs/specs/versions/v0.2.1/RELEASE_NOTES.md",
        "docs/specs/versions/v1.0.0/RELEASE_NOTES.md",
    ]
    assert _git(graduated, "status", "--porcelain") == ""


def test_only_the_version_of_the_heading_changes():
    """The heading of another note, the version mentioned in the body and a heading without a name stay as they are."""
    text = "# Aktualizácie\n\n## v0.2.1\n\n- Oprava z v0.2.1 na v0.2.0 sa nevracia.\n"

    assert deploy_service.note_headed_by(text, "0.2.1", "1.0.0") == (
        "# Aktualizácie\n\n## v1.0.0\n\n- Oprava z v0.2.1 na v0.2.0 sa nevracia.\n"
    )
    assert deploy_service.note_headed_by("## v0.2.10\n", "0.2.1", "1.0.0") == "## v0.2.10\n"
