"""DEV-400 — an upgrade of a library is saved in the project's git even where the project's own pre-commit check cannot
run, and an upgrade left unsaved is seen and saved with one click.

16.11.2026 the Manažér raised Orthodox Hermenia's app template from nex-app-kit 0.13.0 to 0.15.2 in its settings. The
files changed, but the commit was refused by the project's pre-commit check (it type-checks the frontend, and nothing
had been built in the project yet — „tsc: not found“), and the settings did not say so. Director, on the fix:
„súhlasím“.
"""

from __future__ import annotations

import subprocess
import uuid
from pathlib import Path

from backend.db.models.foundation import User
from backend.db.models.pipeline import PipelineState
from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.services import app_kit, nexshared
from backend.tests.test_a_template_project_upgrades_its_kit import seed

REFUSING_HOOK = '#!/bin/sh\necho "tsc: not found" >&2\nexit 1\n'


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True).stdout


def _project_repo(root: Path, version: str = "0.13.0") -> Path:
    """A project on the template at *version*, committed, with a pre-commit check that refuses every commit — as a
    project nothing has been built in yet."""
    seed(root, frame=version, server=version)
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    (root / ".githooks").mkdir()
    hook = root / ".githooks" / "pre-commit"
    hook.write_text(REFUSING_HOOK, encoding="utf-8")
    hook.chmod(0o755)
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "--no-verify", "-m", "founding")
    _git(root, "config", "core.hooksPath", ".githooks")
    return root


def _raise_files(root: Path, version: str) -> None:
    """What the upgrade wrote: both sides of the kit on *version*, in the files only."""
    seed(root, frame=version, server=version)


def test_the_upgrade_is_committed_although_the_projects_check_refuses(tmp_path):
    root = _project_repo(tmp_path)
    (root / "MEMORY.md").write_text("the agent's notes\n", encoding="utf-8")
    _git(root, "add", "MEMORY.md")  # something else staged in the project
    _raise_files(root, "0.15.2")

    assert app_kit.commit_upgrade(str(root), "0.15.2") is True

    assert _git(root, "log", "-1", "--format=%s").strip() == "chore(deps): bump nex-app-kit → v0.15.2"
    committed = set(_git(root, "show", "--name-only", "--format=", "HEAD").split())
    assert committed == {"frontend/package.json", "frontend/package-lock.json", "backend/pyproject.toml"}
    assert _git(root, "diff", "--cached", "--name-only").split() == ["MEMORY.md"]  # stays staged, not committed


def test_a_nex_shared_upgrade_is_committed_the_same_way(tmp_path):
    root = _project_repo(tmp_path)
    lock = root / "frontend" / "package-lock.json"
    lock.write_text(lock.read_text(encoding="utf-8").replace('"0.28.0"', '"0.29.0"'), encoding="utf-8")

    assert nexshared.commit_pin_upgrade(str(root), "0.29.0") is True
    assert _git(root, "log", "-1", "--format=%s").strip() == "chore(deps): bump nex-shared → v0.29.0"


def test_an_upgrade_left_in_the_files_is_seen_until_it_is_saved(tmp_path):
    root = _project_repo(tmp_path)
    assert app_kit.uncommitted_version(str(root)) is None  # the files say what git says

    _raise_files(root, "0.15.2")
    assert app_kit.uncommitted_version(str(root)) == "0.15.2"
    assert app_kit.status_for_source(str(root), tags=["0.13.0", "0.15.2"], changelog_text="")["uncommitted"] == "0.15.2"

    assert app_kit.commit_upgrade(str(root), "0.15.2") is True
    assert app_kit.uncommitted_version(str(root)) is None


def test_two_sides_on_different_releases_are_no_upgrade_to_save(tmp_path):
    root = _project_repo(tmp_path)
    seed(root, frame="0.15.2", server="0.13.0")

    assert app_kit.uncommitted_version(str(root)) is None
    assert app_kit.uncommitted_version(str(tmp_path / "nowhere")) is None


def _db_project(db, source: Path) -> Project:
    s = uuid.uuid4().hex[:8]
    user = User(username=f"kit_{s}", email=f"kit_{s}@t.local", password_hash="x", role="ri")
    db.add(user)
    db.flush()
    project = Project(
        name=f"K{s}",
        slug=f"k-{s}",
        type="standard",
        auth_mode="password",
        description="",
        created_by=user.id,
        source_path=str(source),
        template="nsv-standard",
    )
    db.add(project)
    db.flush()
    return project


def test_the_settings_save_an_unsaved_upgrade_with_one_click(client, db_session, tmp_path):
    root = _project_repo(tmp_path)
    _raise_files(root, "0.15.2")
    project = _db_project(db_session, root)

    saved = client.post(f"/api/v1/projects/{project.id}/app-kit-commit")

    assert saved.status_code == 200, saved.text
    assert saved.json() == {"upgraded": True, "target_version": "0.15.2", "committed": True}
    assert _git(root, "log", "-1", "--format=%s").strip() == "chore(deps): bump nex-app-kit → v0.15.2"
    again = client.post(f"/api/v1/projects/{project.id}/app-kit-commit")
    assert again.status_code == 400
    assert again.json()["detail"] == (
        "Šablóna aplikácie je v gite projektu uložená tak, ako ju majú jeho súbory — nie je čo uložiť."
    )


def test_nothing_is_saved_while_an_agent_works_in_the_checkout(client, db_session, tmp_path):
    root = _project_repo(tmp_path)
    _raise_files(root, "0.15.2")
    project = _db_project(db_session, root)
    version = Version(project_id=project.id, version_number="0.1.0", status="active")
    db_session.add(version)
    db_session.flush()
    db_session.add(
        PipelineState(
            version_id=version.id,
            flow_type="new_version",
            current_stage="priprava",
            current_actor="ai_agent",
            status="agent_working",
        )
    )
    db_session.flush()

    refused = client.post(f"/api/v1/projects/{project.id}/app-kit-commit")

    assert refused.status_code == 409
    assert app_kit.uncommitted_version(str(root)) == "0.15.2"  # untouched
