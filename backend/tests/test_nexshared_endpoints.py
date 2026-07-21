"""nex-shared auto-notify endpoints (#3): status + opt-in upgrade, project-scoped."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from backend.db.models.foundation import User
from backend.db.models.projects import Project
from backend.services import nexshared


def _make_project(db, source_path) -> Project:
    s = uuid.uuid4().hex[:8]
    user = User(username=f"cc_{s}", email=f"cc_{s}@t.local", password_hash="x", role="ri")
    db.add(user)
    db.flush()
    project = Project(
        name=f"P{s}",
        slug=f"p-{s}",
        type="standard",
        auth_mode="password",
        description="nexshared endpoint test",
        created_by=user.id,
        source_path=str(source_path),
    )
    db.add(project)
    db.flush()
    return project


def _seed_frontend(root: Path, pin: str = "0.11.0") -> None:
    fe = root / "frontend"
    fe.mkdir(parents=True, exist_ok=True)
    (fe / "package.json").write_text(
        json.dumps({"dependencies": {"nex-shared": f"github:rauschiccsk/nex-shared#v{pin}"}}, indent=2),
        encoding="utf-8",
    )


def test_get_nexshared_status_reports_the_gap(client, db_session, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(nexshared, "list_remote_tags", lambda *a, **k: ["0.11.0", "0.14.0", "0.15.0"])
    monkeypatch.setattr(nexshared, "fetch_changelog", lambda *a, **k: "## v0.15.0\n- `[vzhľad]` x\n")
    _seed_frontend(tmp_path)
    project = _make_project(db_session, tmp_path)
    resp = client.get(f"/api/v1/projects/{project.id}/nexshared-status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["current"] == "0.11.0"
    assert body["latest"] == "0.15.0"
    assert body["behind"] == 2
    assert [c["version"] for c in body["changelog"]] == ["0.15.0"]


def test_upgrade_nexshared_rewrites_the_pin(client, db_session, tmp_path) -> None:
    _seed_frontend(tmp_path)
    project = _make_project(db_session, tmp_path)
    resp = client.post(f"/api/v1/projects/{project.id}/nexshared-upgrade", json={"target_version": "0.15.0"})
    assert resp.status_code == 200
    assert resp.json()["upgraded"] is True
    # The pin on disk is now the target (no git repo → committed False, but the write happened).
    pkg = (tmp_path / "frontend" / "package.json").read_text(encoding="utf-8")
    assert nexshared.parse_pin(pkg) == "0.15.0"


def test_upgrade_bad_version_is_rejected(client, db_session, tmp_path) -> None:
    _seed_frontend(tmp_path)
    project = _make_project(db_session, tmp_path)
    resp = client.post(f"/api/v1/projects/{project.id}/nexshared-upgrade", json={"target_version": "not-a-version"})
    assert resp.status_code == 400
