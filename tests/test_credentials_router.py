"""Tests for the Credentials REST router.

Verifies CRUD + file IO for the credentials registry. Auth is enforced
by ``require_ri_role`` on every endpoint; tests override the dependency
to simulate an ri user (auth-enforcement itself is covered by the
shared security tests). What we cover here:

* Happy CRUD for both metadata and content endpoints.
* Filesystem-store invariants: file is written on create, removed on
  delete, content round-trips through PUT.
* §13 invariants enforced by the service: no slash in filename, no
  symlink-escape, store dir auto-created with mode 0700, file mode
  0600 on every write.
* 404 / 409 / 422 mappings.
"""

from __future__ import annotations

import os
import uuid

import bcrypt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.routes.credentials import router as credentials_router
from backend.core.security import get_current_user, require_ri_role
from backend.db.models.foundation import User
from backend.db.session import get_db


@pytest.fixture()
def credentials_root(tmp_path, monkeypatch):
    """Point settings at a tmp credentials store; return the path."""
    from backend.config.settings import settings

    root = tmp_path / "credentials"
    monkeypatch.setattr(settings, "credentials_storage_path", str(root))
    return root


@pytest.fixture()
def router_client(db_session, credentials_root):
    ri_user = User(
        username=f"ri_{uuid.uuid4().hex[:8]}",
        email=f"ri_{uuid.uuid4().hex[:8]}@test.local",
        password_hash=bcrypt.hashpw(b"test", bcrypt.gensalt(rounds=4)).decode(),
        role="ri",
        is_active=True,
    )
    db_session.add(ri_user)
    db_session.flush()

    app = FastAPI()
    app.include_router(credentials_router, prefix="/api/v1/credentials")

    def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_current_user] = lambda: ri_user
    app.dependency_overrides[require_ri_role] = lambda: ri_user
    # M2.D.2 RBAC overrides for inline TestClient.
    import uuid as _uuid_inline

    import bcrypt as _bcrypt_inline

    from backend.core.security import (
        get_current_user as _gcu_inline,
    )
    from backend.core.security import (
        require_ha_or_above as _rha_inline,
    )
    from backend.core.security import (
        require_ri_role as _rri_inline,
    )
    from backend.core.security import (
        require_shu_or_above as _rshu_inline,
    )
    from backend.db.models.foundation import User as _UserInline

    _suffix_inline = _uuid_inline.uuid4().hex[:8]
    _ri_inline = _UserInline(
        username=f"ri_inline_{_suffix_inline}",
        email=f"ri_inline_{_suffix_inline}@test.local",
        password_hash=_bcrypt_inline.hashpw(b"test", _bcrypt_inline.gensalt(rounds=4)).decode(),
        role="ri",
        is_active=True,
    )
    db_session.add(_ri_inline)
    db_session.flush()

    def _override_user_inline() -> _UserInline:
        return _ri_inline

    app.dependency_overrides[_gcu_inline] = _override_user_inline
    app.dependency_overrides[_rri_inline] = _override_user_inline
    app.dependency_overrides[_rha_inline] = _override_user_inline
    app.dependency_overrides[_rshu_inline] = _override_user_inline

    with TestClient(app) as client:
        yield client

    app.dependency_overrides.clear()


class TestCredentialsCRUD:
    def test_list_initially_empty(self, router_client):
        resp = router_client.get("/api/v1/credentials")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_create_writes_file_and_row(self, router_client, credentials_root):
        resp = router_client.post(
            "/api/v1/credentials",
            json={"title": "AWS keys", "filename": "AWS.md", "content": "# AWS\nkey=xyz"},
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["title"] == "AWS keys"
        assert body["file_path"].endswith("/AWS.md")

        # File on disk has 0600 perms.
        target = credentials_root / "AWS.md"
        assert target.is_file()
        assert (target.stat().st_mode & 0o777) == 0o600
        assert target.read_text(encoding="utf-8") == "# AWS\nkey=xyz"

    def test_get_content_returns_disk_content(self, router_client):
        created = router_client.post(
            "/api/v1/credentials",
            json={"title": "T2", "filename": "T2.md", "content": "hello"},
        ).json()
        resp = router_client.get(f"/api/v1/credentials/{created['id']}/content")
        assert resp.status_code == 200
        body = resp.json()
        assert body["content"] == "hello"
        assert body["size_bytes"] == 5

    def test_put_content_overwrites_file(self, router_client, credentials_root):
        created = router_client.post(
            "/api/v1/credentials",
            json={"title": "T3", "filename": "T3.md", "content": "old"},
        ).json()
        resp = router_client.put(
            f"/api/v1/credentials/{created['id']}/content",
            json={"content": "new content"},
        )
        assert resp.status_code == 200
        assert resp.json()["content"] == "new content"

        # Disk reflects new content + 0600.
        target = credentials_root / "T3.md"
        assert target.read_text(encoding="utf-8") == "new content"
        assert (target.stat().st_mode & 0o777) == 0o600

    def test_delete_removes_row_and_file(self, router_client, credentials_root):
        created = router_client.post(
            "/api/v1/credentials",
            json={"title": "T5", "filename": "T5.md", "content": "x"},
        ).json()
        target = credentials_root / "T5.md"
        assert target.is_file()

        resp = router_client.delete(f"/api/v1/credentials/{created['id']}")
        assert resp.status_code == 204
        assert not target.exists()
        assert created["id"] not in [c["id"] for c in router_client.get("/api/v1/credentials").json()]


class TestCredentialsValidation:
    def test_create_409_for_existing_filename(self, router_client):
        router_client.post(
            "/api/v1/credentials",
            json={"title": "A", "filename": "DUP.md", "content": "x"},
        )
        resp = router_client.post(
            "/api/v1/credentials",
            json={"title": "B", "filename": "DUP.md", "content": "y"},
        )
        # Service raises "already exists" → mapped to 409.
        assert resp.status_code == 409

    def test_create_422_for_filename_with_slash(self, router_client):
        resp = router_client.post(
            "/api/v1/credentials",
            json={"title": "Bad", "filename": "../etc/passwd", "content": "x"},
        )
        assert resp.status_code == 422

    def test_get_content_404_when_file_missing_on_disk(self, router_client, credentials_root):
        created = router_client.post(
            "/api/v1/credentials",
            json={"title": "T6", "filename": "T6.md", "content": "x"},
        ).json()
        # Manually delete the file on disk leaving the DB row.
        os.unlink(credentials_root / "T6.md")
        resp = router_client.get(f"/api/v1/credentials/{created['id']}/content")
        assert resp.status_code == 404

    def test_put_content_422_for_oversize(self, router_client, monkeypatch):
        from backend.config.settings import settings

        monkeypatch.setattr(settings, "credentials_content_max_bytes", 50)
        created = router_client.post(
            "/api/v1/credentials",
            json={"title": "T7", "filename": "T7.md", "content": "ok"},
        ).json()
        resp = router_client.put(
            f"/api/v1/credentials/{created['id']}/content",
            json={"content": "x" * 100},
        )
        assert resp.status_code == 422

    def test_get_content_422_for_symlink_escape(self, router_client, db_session, credentials_root, tmp_path):
        """A symlink inside the store pointing outside must trip the path check."""
        # Create the row + initial file via the API to ensure the dir exists.
        created = router_client.post(
            "/api/v1/credentials",
            json={"title": "T8", "filename": "T8.md", "content": "x"},
        ).json()
        # Replace the file on disk with a symlink pointing outside the store.
        target_path = credentials_root / "T8.md"
        target_path.unlink()
        outside = tmp_path / "outside.md"
        outside.write_text("escape", encoding="utf-8")
        target_path.symlink_to(outside)

        resp = router_client.get(f"/api/v1/credentials/{created['id']}/content")
        assert resp.status_code == 422


class TestCredentialsStorageDir:
    def test_store_dir_auto_created_with_0700(self, router_client, credentials_root):
        # Pre-existing dir from fixture? Remove it to assert auto-create.
        if credentials_root.exists():
            for f in credentials_root.iterdir():
                f.unlink()
            credentials_root.rmdir()
        assert not credentials_root.exists()

        router_client.post(
            "/api/v1/credentials",
            json={"title": "X", "filename": "X.md", "content": "x"},
        )
        assert credentials_root.is_dir()
        assert (credentials_root.stat().st_mode & 0o777) == 0o700
