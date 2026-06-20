"""Spec-compliance tests for the release-notes changelog (the *Aktualizácie* feature).

Covers the design contract for ``GET /api/v1/release-notes``:

  * file presence drives WHICH versions appear (a version dir without a
    ``RELEASE_NOTES.md`` is skipped; a non-version dir is ignored);
  * the ``versions`` DB drives the release date, joined by ``version_number``;
  * a missing / NULL ``release_date`` falls back to the file mtime (never the
    Markdown heading);
  * newest-first ordering is NUMERIC (``v0.10.0`` > ``v0.2.0``, not the
    lexicographic order a string sort would give);
  * the endpoint is public (no auth) and returns a JSON array.
"""

from __future__ import annotations

import uuid
from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient

from backend.db.models.foundation import User
from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.db.session import get_db
from backend.main import app
from backend.services import release_notes as release_notes_service


def _write_notes(root: Path, version: str, body: str) -> None:
    version_dir = root / version
    version_dir.mkdir(parents=True, exist_ok=True)
    (version_dir / "RELEASE_NOTES.md").write_text(body, encoding="utf-8")


def _seed_released_version(db, version_number: str, release_date: date) -> None:
    creator = User(
        username=f"rn_{uuid.uuid4().hex[:8]}",
        email=f"rn_{uuid.uuid4().hex[:8]}@test.local",
        password_hash="x",
        role="ri",
    )
    db.add(creator)
    db.flush()
    project = Project(
        name=f"RN Fixture {uuid.uuid4().hex[:6]}",
        slug=f"rn-{uuid.uuid4().hex[:8]}",
        category="multimodule",
        description="release-notes fixture",
        created_by=creator.id,
    )
    db.add(project)
    db.flush()
    db.add(
        Version(
            project_id=project.id,
            version_number=version_number,
            status="released",
            release_date=release_date,
        )
    )
    db.flush()


def test_list_release_notes_orders_numeric_and_joins_db_date(db_session, tmp_path, monkeypatch):
    monkeypatch.setattr(release_notes_service, "DOCS_VERSIONS_ROOT", tmp_path)
    _write_notes(tmp_path, "v0.1.0", "## v0.1.0\nfirst")
    _write_notes(tmp_path, "v0.2.0", "## v0.2.0\nsecond")
    _write_notes(tmp_path, "v0.10.0", "## v0.10.0\ntenth")
    # Version dir WITHOUT a RELEASE_NOTES.md → skipped (file presence drives appearance).
    (tmp_path / "v0.3.0").mkdir()
    (tmp_path / "v0.3.0" / "CHANGES.md").write_text("internal", encoding="utf-8")
    # Non-version dir → ignored even if it ships a RELEASE_NOTES.md.
    (tmp_path / "scratch").mkdir()
    (tmp_path / "scratch" / "RELEASE_NOTES.md").write_text("nope", encoding="utf-8")

    _seed_released_version(db_session, "v0.2.0", date(2026, 5, 26))

    out = release_notes_service.list_release_notes(db_session)

    # Numeric newest-first: v0.10.0 sorts ABOVE v0.2.0 (lexicographic would invert this).
    assert [n["version"] for n in out] == ["v0.10.0", "v0.2.0", "v0.1.0"]

    by_version = {n["version"]: n for n in out}
    # DB-sourced date for the version that has a released row.
    assert by_version["v0.2.0"]["released_at"] == "2026-05-26"
    # mtime fallback for versions with no DB row — a real date string, never None.
    assert by_version["v0.10.0"]["released_at"] is not None
    assert by_version["v0.1.0"]["markdown"] == "## v0.1.0\nfirst"
    # The skipped + ignored dirs never surface.
    assert "v0.3.0" not in by_version
    assert "scratch" not in by_version


def test_release_notes_endpoint_public_returns_array(db_session, tmp_path, monkeypatch):
    monkeypatch.setattr(release_notes_service, "DOCS_VERSIONS_ROOT", tmp_path)
    _write_notes(tmp_path, "v0.9.0", "## v0.9.0\nhello")

    def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    try:
        # No `with` block → lifespan/migrations don't run. No auth override is
        # supplied either — a 200 proves the no-auth (public) contract.
        client = TestClient(app)
        resp = client.get("/api/v1/release-notes")
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["version"] == "v0.9.0"
    assert data[0]["markdown"] == "## v0.9.0\nhello"
    assert data[0]["released_at"] is not None
