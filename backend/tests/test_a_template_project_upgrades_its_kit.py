"""DEV-253 — a project on an app template is offered the newest nex-app-kit when a new version is founded.

As for nex-shared: the Manažér sees which release the project builds with, the newest one and what it brings, and
decides per version. The kit lives on two sides — the frame (frontend/package.json and its lockfile, which ``npm ci``
obeys) and the server (backend/pyproject.toml); the status names both and an upgrade moves both in one commit, or
neither: a frame and a server on two releases would speak two versions of the standard interface.
"""

from __future__ import annotations

import json
import subprocess
import uuid
from pathlib import Path

from backend.db.models.foundation import User
from backend.db.models.projects import Project
from backend.services import app_kit, app_template, nexshared

PYPROJECT = """[project]
name = "backend"
dependencies = [
    "{kit}",
    "fastapi>=0.115,<1",
]
"""
CHANGELOG = "## v0.5.0\n- `[nové]` päť\n\n## v0.4.0\n- `[oprava]` štyri\n\n## v0.3.0\n- tri\n"


def _lock(version: str) -> str:
    return json.dumps({"packages": {"node_modules/nex-app-kit": {"version": version}}}, indent=2)


def seed(root: Path, frame: str = "0.3.0", server: str | None = "0.3.0") -> Path:
    """A project on the template as the founding writes it — the kit pinned on both sides."""
    (root / "frontend").mkdir(parents=True, exist_ok=True)
    (root / "frontend" / "package.json").write_text(
        json.dumps(
            {
                "dependencies": {
                    "nex-app-kit": nexshared.git_dependency("nex-app-kit", frame),
                    "nex-shared": nexshared.git_dependency("nex-shared", "0.28.0"),
                }
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (root / "frontend" / "package-lock.json").write_text(_lock(frame), encoding="utf-8")
    (root / "backend").mkdir(exist_ok=True)
    kit = app_template.kit_server_dependency(server) if server else "requests>=2"
    (root / "backend" / "pyproject.toml").write_text(PYPROJECT.format(kit=kit), encoding="utf-8")
    return root


def npm_resolves_to(monkeypatch, version: str) -> list[str]:
    asked: list[str] = []

    def resolve(frontend_dir: Path, spec: str, **_kwargs) -> bool:
        asked.append(spec)
        (frontend_dir / "package-lock.json").write_text(_lock(version), encoding="utf-8")
        return True

    monkeypatch.setattr(nexshared, "resolve_lockfile", resolve)
    return asked


def files(root: Path) -> dict[str, str]:
    return {
        rel: (root / rel).read_text(encoding="utf-8")
        for rel in ("frontend/package.json", "frontend/package-lock.json", "backend/pyproject.toml")
    }


# ── the status ──────────────────────────────────────────────────────────────────────────────────────────────────


def test_the_status_names_both_sides_the_newest_release_and_what_it_brings(tmp_path):
    status = app_kit.status_for_source(
        str(seed(tmp_path)), tags=["0.2.0", "0.3.0", "0.4.0", "0.5.0"], changelog_text=CHANGELOG
    )

    assert status == {
        "current": "0.3.0",
        "latest": "0.5.0",
        "behind": 2,
        "up_to_date": False,
        "changelog": [
            {"version": "0.5.0", "body": "- `[nové]` päť"},
            {"version": "0.4.0", "body": "- `[oprava]` štyri"},
        ],
        "parts": [
            {"name": "frontend", "label": "Rám (frontend)", "current": "0.3.0"},
            {"name": "server", "label": "Server", "current": "0.3.0"},
        ],
    }


def test_the_older_side_decides_how_far_behind_the_project_is(tmp_path):
    status = app_kit.status_for_source(str(seed(tmp_path, server="0.2.0")), tags=["0.2.0", "0.3.0"], changelog_text="")

    assert (status["current"], status["behind"]) == ("0.2.0", 1)


def test_a_side_that_cannot_be_read_offers_nothing(tmp_path):
    status = app_kit.status_for_source(str(seed(tmp_path, server=None)), tags=["0.3.0", "0.4.0"], changelog_text="")

    assert (status["current"], status["behind"], status["up_to_date"]) == (None, 0, False)


def test_an_up_to_date_project_is_said_so(tmp_path):
    status = app_kit.status_for_source(str(seed(tmp_path)), tags=["0.2.0", "0.3.0"], changelog_text=CHANGELOG)

    assert (status["behind"], status["up_to_date"], status["changelog"]) == (0, True, [])


# ── the upgrade ─────────────────────────────────────────────────────────────────────────────────────────────────


def test_an_upgrade_moves_the_frame_and_the_server_together(tmp_path, monkeypatch):
    root = seed(tmp_path)
    asked = npm_resolves_to(monkeypatch, "0.5.0")

    assert app_kit.upgrade_source(str(root), "0.5.0") is True

    assert asked == ["github:rauschiccsk/nex-app-kit#v0.5.0"]
    package = json.loads((root / "frontend" / "package.json").read_text(encoding="utf-8"))
    assert package["dependencies"] == {
        "nex-app-kit": "github:rauschiccsk/nex-app-kit#v0.5.0",
        "nex-shared": "github:rauschiccsk/nex-shared#v0.28.0",
    }
    assert (root / "backend" / "pyproject.toml").read_text(encoding="utf-8") == PYPROJECT.format(
        kit=app_template.kit_server_dependency("0.5.0")
    )
    assert app_kit.status_for_source(str(root), tags=["0.5.0"], changelog_text="")["current"] == "0.5.0"


def test_a_lockfile_that_does_not_follow_puts_every_file_back(tmp_path, monkeypatch):
    root = seed(tmp_path)
    before = files(root)
    npm_resolves_to(monkeypatch, "0.3.0")

    assert app_kit.upgrade_source(str(root), "0.5.0") is False
    assert files(root) == before


def test_npm_failing_puts_every_file_back(tmp_path, monkeypatch):
    root = seed(tmp_path)
    before = files(root)
    monkeypatch.setattr(nexshared, "resolve_lockfile", lambda *_a, **_k: False)

    assert app_kit.upgrade_source(str(root), "0.5.0") is False
    assert files(root) == before


def test_a_lockfile_npm_wrote_where_there_was_none_is_removed_again(tmp_path, monkeypatch):
    root = seed(tmp_path)
    (root / "frontend" / "package-lock.json").unlink()
    npm_resolves_to(monkeypatch, "0.3.0")

    assert app_kit.upgrade_source(str(root), "0.5.0") is False
    assert not (root / "frontend" / "package-lock.json").exists()


def test_nothing_is_touched_without_a_pin_on_both_sides_or_a_real_version(tmp_path, monkeypatch):
    asked = npm_resolves_to(monkeypatch, "0.5.0")
    no_server = seed(tmp_path / "a", server=None)
    before = files(no_server)

    assert app_kit.upgrade_source(str(no_server), "0.5.0") is False
    assert files(no_server) == before
    assert app_kit.upgrade_source(str(seed(tmp_path / "b")), "najnovšia") is False
    assert asked == []


def test_one_commit_carries_both_sides(tmp_path, monkeypatch):
    root = seed(tmp_path)
    git = ["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-qm", "init"], check=True)
    monkeypatch.setenv("GIT_AUTHOR_NAME", "t")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "t@t")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "t")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "t@t")
    npm_resolves_to(monkeypatch, "0.5.0")
    assert app_kit.upgrade_source(str(root), "0.5.0")

    assert app_kit.commit_upgrade(str(root), "0.5.0") is True

    show = subprocess.run(
        ["git", "-C", str(root), "show", "--name-only", "--format=%s", "HEAD"], capture_output=True, text=True
    ).stdout.split()
    assert " ".join(show[:5]) == "chore(deps): bump nex-app-kit → v0.5.0"
    assert sorted(show[5:]) == ["backend/pyproject.toml", "frontend/package-lock.json", "frontend/package.json"]


# ── the routes ──────────────────────────────────────────────────────────────────────────────────────────────────


def _project(db, source_path: Path, template: str | None) -> Project:
    s = uuid.uuid4().hex[:8]
    user = User(username=f"kit_{s}", email=f"kit_{s}@t.local", password_hash="x", role="ri")
    db.add(user)
    db.flush()
    project = Project(
        name=f"K{s}",
        slug=f"k-{s}",
        type="standard",
        auth_mode="password",
        description="nex-app-kit upgrade",
        created_by=user.id,
        source_path=str(source_path),
        template=template,
    )
    db.add(project)
    db.flush()
    return project


def test_a_template_project_is_offered_the_newest_kit(client, db_session, tmp_path, monkeypatch):
    monkeypatch.setattr(nexshared, "list_remote_tags", lambda *a, **k: ["0.3.0", "0.4.0"])
    monkeypatch.setattr(nexshared, "fetch_changelog", lambda *a, **k: CHANGELOG)
    project = _project(db_session, seed(tmp_path), "nsv-standard")

    body = client.get(f"/api/v1/projects/{project.id}/app-kit-status").json()

    assert (body["current"], body["latest"], body["behind"]) == ("0.3.0", "0.4.0", 1)
    assert [c["version"] for c in body["changelog"]] == ["0.4.0"]


def test_a_project_without_a_template_is_offered_nothing(client, db_session, tmp_path, monkeypatch):
    monkeypatch.setattr(nexshared, "list_remote_tags", lambda *a, **k: ["0.3.0", "0.4.0"])
    project = _project(db_session, seed(tmp_path), None)

    body = client.get(f"/api/v1/projects/{project.id}/app-kit-status").json()

    assert body == {"current": None, "latest": None, "behind": 0, "up_to_date": False, "changelog": [], "parts": []}
    refused = client.post(f"/api/v1/projects/{project.id}/app-kit-upgrade", json={"target_version": "0.4.0"})
    assert refused.status_code == 400
    assert refused.json()["detail"] == (
        "Projekt nie je založený na šablóne aplikácie — nex-app-kit v ňom nie je čo povýšiť."
    )


def test_the_upgrade_route_moves_the_kit_or_says_why_not(client, db_session, tmp_path, monkeypatch):
    project = _project(db_session, seed(tmp_path), "nsv-standard")
    npm_resolves_to(monkeypatch, "0.3.0")

    failed = client.post(f"/api/v1/projects/{project.id}/app-kit-upgrade", json={"target_version": "0.4.0"})
    assert failed.status_code == 400
    assert "Rám ani server sa nezmenili." in failed.json()["detail"]

    npm_resolves_to(monkeypatch, "0.4.0")
    done = client.post(f"/api/v1/projects/{project.id}/app-kit-upgrade", json={"target_version": "0.4.0"})
    assert done.status_code == 200
    assert done.json() == {"upgraded": True, "target_version": "0.4.0", "committed": False}  # no git → not committed
    assert app_kit.server_version(str(tmp_path)) == "0.4.0"
