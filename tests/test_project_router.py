"""Tests for the Project REST router.

Verifies the CRUD surface exposed by :mod:`backend.api.routes.projects`
against the SAVEPOINT-isolated test database. The router is mounted at
``/api/v1/projects`` — the same prefix it will have in production via
``backend/main.py`` — but since this router is not yet wired into
``main.py`` we mount it on a dedicated ``TestClient`` app here (same
pattern as :mod:`tests.test_user_router` and
:mod:`tests.test_guardian_precedent_router`).

Covers:

* Create / get / list / patch / delete happy paths.
* ``PaginatedResponse`` envelope (items / total / skip / limit).
* Pagination via ``skip`` and ``limit``.
* Filter by ``status``, ``type`` and ``created_by``.
* 404 on missing id (get, patch, delete).
* 409 on duplicate ``name`` / ``slug``.
* 422 on schema validation failure (e.g. invalid type or status,
  limit > 100).
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.dependencies import get_knowledge_base_writer, get_rag_indexer
from backend.api.routes.projects import router as projects_router
from backend.db.models.foundation import User
from backend.db.session import get_db
from backend.services.knowledge_base_writer import KnowledgeBaseWriter

# KB-ghost follow-up (docs/specs/kb-ghost-followup.md Fix A): this module lives
# OUTSIDE tests/integration/, so it can't see that dir's autouse isolation.
# Opt in to the shared root-conftest fixture so every create here also runs
# against a tmp KB (settings + init.sh dry-run) with the real-KB sentinel — the
# module's own ``router_client`` already redirects the writer DI on its private
# app; this neutralises the remaining settings + init.sh vectors.
pytestmark = pytest.mark.usefixtures("_isolate_create_project_kb")


@pytest.fixture()
def router_client(db_session, tmp_path, monkeypatch):
    """Mount the projects router on a fresh app with DB + KB + GitHub overrides.

    * DB is the SAVEPOINT-isolated session from the root conftest.
    * KB writes go to a ``tmp_path``-rooted writer — no test touches
      the real ``/home/icc/knowledge`` tree.
    * ``create_github_repo`` is monkey-patched to a no-op returning
      ``True`` so every successful POST does not hit the live GitHub
      API.
    """
    monkeypatch.setattr(
        "backend.services.github_validation.create_github_repo",
        lambda repo, **kwargs: True,
    )

    app = FastAPI()
    app.include_router(projects_router, prefix="/api/v1/projects")

    def _override_get_db():
        yield db_session

    def _override_kb_writer() -> KnowledgeBaseWriter:
        return KnowledgeBaseWriter(tmp_path)

    app.dependency_overrides[get_db] = _override_get_db
    # Auto-added by M2.D RBAC roll-out — override role gates so existing
    # tests (which never sent JWTs) keep working. Tests that exercise
    # role denial should re-override these to a lower-role user locally.
    import uuid as _uuid_m2

    import bcrypt as _bcrypt

    from backend.core.security import (
        get_current_user as _gcu_m2,
    )
    from backend.core.security import (
        require_ha_or_above as _rha_m2,
    )
    from backend.core.security import (
        require_ri_role as _rri_m2,
    )
    from backend.core.security import (
        require_shu_or_above as _rshu_m2,
    )
    from backend.db.models.foundation import User as _UserM2

    _suffix_m2 = _uuid_m2.uuid4().hex[:8]
    _ri_m2 = _UserM2(
        username=f"ri_m2_{_suffix_m2}",
        email=f"ri_m2_{_suffix_m2}@test.local",
        password_hash=_bcrypt.hashpw(b"test", _bcrypt.gensalt(rounds=4)).decode(),
        role="ri",
        is_active=True,
    )
    db_session.add(_ri_m2)
    db_session.flush()

    def _override_user_m2() -> _UserM2:
        return _ri_m2

    app.dependency_overrides[_gcu_m2] = _override_user_m2
    app.dependency_overrides[_rri_m2] = _override_user_m2
    app.dependency_overrides[_rha_m2] = _override_user_m2
    app.dependency_overrides[_rshu_m2] = _override_user_m2

    app.dependency_overrides[get_knowledge_base_writer] = _override_kb_writer
    # Live-doc writes reindex into RAG; tests must not hit the real Qdrant/Ollama
    # (reachable in this env) — disable indexing by returning no indexer.
    app.dependency_overrides[get_rag_indexer] = lambda: None

    with TestClient(app) as client:
        yield client

    app.dependency_overrides.clear()


@pytest.fixture()
def creator(db_session) -> User:
    """Persist a user that may own the projects created in a test."""
    user = User(
        username=f"owner_{uuid.uuid4().hex[:8]}",
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        password_hash="hashed_password_placeholder",
        role="ri",
    )
    db_session.add(user)
    db_session.flush()
    return user


def _payload(creator_id, **overrides) -> dict:
    """Return a project-create payload with deterministic-ish defaults."""
    suffix = uuid.uuid4().hex[:8]
    body = {
        "name": f"Project {suffix}",
        "slug": f"project-{suffix}",
        "type": "standard",
        "auth_mode": "password",
        "description": "Test project description",
        "created_by": str(creator_id),
    }
    body.update(overrides)
    return body


class TestProjectRouter:
    """End-to-end HTTP coverage for the router."""

    def test_create_project(self, router_client, creator):
        payload = _payload(
            creator.id,
            name="Alpha",
            slug="alpha",
            type="web",
            auth_mode="token",
        )
        resp = router_client.post("/api/v1/projects", json=payload)
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["name"] == "Alpha"
        assert body["slug"] == "alpha"
        assert body["type"] == "web"
        assert body["auth_mode"] == "token"
        assert body["status"] == "active"
        assert body["guardian_enabled"] is False
        assert body["created_by"] == str(creator.id)
        assert body["id"]
        assert body["created_at"]
        assert body["updated_at"]

    def test_create_project_sets_derivable_uat_slug(self, router_client, creator, db_session):
        """CR-R2-1 (#1a): Create-Project sets uat_slug at creation (nex- prefix stripped) so a deployable
        app carries its UAT target from the start."""
        from sqlalchemy import select

        from backend.db.models.projects import Project

        resp = router_client.post("/api/v1/projects", json=_payload(creator.id, name="Nex Foo", slug="nex-foo"))
        assert resp.status_code == 201, resp.text
        project = db_session.execute(select(Project).where(Project.slug == "nex-foo")).scalar_one()
        assert project.uat_slug == "foo"

    def test_create_project_underivable_uat_slug_does_not_500(self, router_client, creator, monkeypatch):
        """CR-R2-1 (#1a): an underivable uat_slug must NOT 500 the create — the ValueError is logged +
        swallowed (the Phase-3 lazy derive stays the safety net)."""

        def _raise(*_a, **_k):
            raise ValueError("underivable slug")

        monkeypatch.setattr("backend.services.project.set_uat_slug", _raise)
        resp = router_client.post("/api/v1/projects", json=_payload(creator.id, name="Beta", slug="beta-proj"))
        assert resp.status_code == 201, resp.text

    def test_create_duplicate_name_returns_409(self, router_client, creator):
        base = _payload(creator.id, name="DupName")
        assert router_client.post("/api/v1/projects", json=base).status_code == 201
        # Same name, different slug.
        dup = _payload(creator.id, name="DupName")
        resp = router_client.post("/api/v1/projects", json=dup)
        assert resp.status_code == 409

    def test_create_duplicate_slug_returns_409(self, router_client, creator):
        base = _payload(creator.id, slug="dup-slug")
        assert router_client.post("/api/v1/projects", json=base).status_code == 201
        # Same slug, different name.
        dup = _payload(creator.id, slug="dup-slug")
        resp = router_client.post("/api/v1/projects", json=dup)
        assert resp.status_code == 409

    def test_create_invalid_type_returns_422(self, router_client, creator):
        payload = _payload(creator.id, type="bogus")
        resp = router_client.post("/api/v1/projects", json=payload)
        assert resp.status_code == 422

    def test_create_invalid_auth_mode_returns_422(self, router_client, creator):
        payload = _payload(creator.id, auth_mode="bogus")
        resp = router_client.post("/api/v1/projects", json=payload)
        assert resp.status_code == 422

    def test_create_invalid_status_returns_422(self, router_client, creator):
        payload = _payload(creator.id, status="bogus")
        resp = router_client.post("/api/v1/projects", json=payload)
        assert resp.status_code == 422

    def test_get_by_id(self, router_client, creator):
        created = router_client.post(
            "/api/v1/projects",
            json=_payload(creator.id),
        ).json()
        resp = router_client.get(f"/api/v1/projects/{created['id']}")
        assert resp.status_code == 200
        assert resp.json()["id"] == created["id"]

    def test_get_missing_returns_404(self, router_client):
        resp = router_client.get(f"/api/v1/projects/{uuid.uuid4()}")
        assert resp.status_code == 404

    def test_list_envelope_and_pagination(self, router_client, creator):
        for _ in range(3):
            router_client.post(
                "/api/v1/projects",
                json=_payload(creator.id),
            ).raise_for_status()

        resp = router_client.get("/api/v1/projects", params={"skip": 0, "limit": 2})
        assert resp.status_code == 200
        body = resp.json()
        assert set(body.keys()) >= {"items", "total", "skip", "limit"}
        assert body["skip"] == 0
        assert body["limit"] == 2
        assert body["total"] >= 3
        assert len(body["items"]) == 2

        page2 = router_client.get(
            "/api/v1/projects",
            params={"skip": 2, "limit": 2},
        ).json()
        page1_ids = {row["id"] for row in body["items"]}
        page2_ids = {row["id"] for row in page2["items"]}
        assert page1_ids.isdisjoint(page2_ids)

    def test_list_filter_by_status(self, router_client, creator):
        router_client.post(
            "/api/v1/projects",
            json=_payload(creator.id, status="active"),
        ).raise_for_status()
        router_client.post(
            "/api/v1/projects",
            json=_payload(creator.id, status="archived"),
        ).raise_for_status()

        resp = router_client.get("/api/v1/projects", params={"status": "archived"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] >= 1
        assert all(item["status"] == "archived" for item in body["items"])

    def test_list_filter_by_type(self, router_client, creator):
        router_client.post(
            "/api/v1/projects",
            json=_payload(creator.id, type="standard"),
        ).raise_for_status()
        router_client.post(
            "/api/v1/projects",
            json=_payload(creator.id, type="web"),
        ).raise_for_status()

        resp = router_client.get(
            "/api/v1/projects",
            params={"type": "web"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] >= 1
        assert all(item["type"] == "web" for item in body["items"])

    def test_list_filter_by_created_by(self, router_client, creator, db_session):
        other = User(
            username=f"other_{uuid.uuid4().hex[:8]}",
            email=f"{uuid.uuid4().hex[:8]}@example.com",
            password_hash="hashed_password_placeholder",
            role="ri",
        )
        db_session.add(other)
        db_session.flush()

        router_client.post(
            "/api/v1/projects",
            json=_payload(creator.id),
        ).raise_for_status()
        router_client.post(
            "/api/v1/projects",
            json=_payload(other.id),
        ).raise_for_status()

        resp = router_client.get(
            "/api/v1/projects",
            params={"created_by": str(other.id)},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] >= 1
        assert all(item["created_by"] == str(other.id) for item in body["items"])

    def test_list_limit_over_100_returns_422(self, router_client):
        resp = router_client.get("/api/v1/projects", params={"limit": 101})
        assert resp.status_code == 422

    def test_patch_partial_update(self, router_client, creator):
        created = router_client.post(
            "/api/v1/projects",
            json=_payload(
                creator.id,
                status="active",
                guardian_enabled=False,
            ),
        ).json()

        resp = router_client.patch(
            f"/api/v1/projects/{created['id']}",
            json={"status": "paused", "description": "Updated description"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "paused"
        assert body["description"] == "Updated description"
        # Fields omitted from the PATCH payload are untouched.
        assert body["name"] == created["name"]
        assert body["slug"] == created["slug"]
        assert body["type"] == created["type"]
        assert body["auth_mode"] == created["auth_mode"]
        assert body["guardian_enabled"] is False
        # Immutable fields unchanged.
        assert body["id"] == created["id"]
        assert body["created_at"] == created["created_at"]
        assert body["created_by"] == created["created_by"]

    def test_patch_duplicate_name_returns_409(self, router_client, creator):
        first = router_client.post(
            "/api/v1/projects",
            json=_payload(creator.id, name="First Proj"),
        ).json()
        router_client.post(
            "/api/v1/projects",
            json=_payload(creator.id, name="Second Proj"),
        ).raise_for_status()

        resp = router_client.patch(
            f"/api/v1/projects/{first['id']}",
            json={"name": "Second Proj"},
        )
        assert resp.status_code == 409

    def test_patch_missing_returns_404(self, router_client):
        resp = router_client.patch(
            f"/api/v1/projects/{uuid.uuid4()}",
            json={"status": "archived"},
        )
        assert resp.status_code == 404

    def test_delete_returns_204(self, router_client, creator):
        created = router_client.post(
            "/api/v1/projects",
            json=_payload(creator.id),
        ).json()
        resp = router_client.delete(f"/api/v1/projects/{created['id']}")
        assert resp.status_code == 204
        # Second read confirms removal.
        assert router_client.get(f"/api/v1/projects/{created['id']}").status_code == 404

    def test_delete_missing_returns_404(self, router_client):
        resp = router_client.delete(f"/api/v1/projects/{uuid.uuid4()}")
        assert resp.status_code == 404

    def test_delete_tears_down_uat_when_uat_slug_set(self, router_client, creator, db_session, monkeypatch):
        """v0.9.0 Phase 3 CR-2: deleting a project with a UAT tears it down (orphan prevention)."""
        from backend.db.models.projects import Project as _Project

        created = router_client.post("/api/v1/projects", json=_payload(creator.id)).json()
        # uat_slug has no create/update API surface — set it directly on the row.
        proj = db_session.get(_Project, uuid.UUID(created["id"]))
        proj.uat_slug = "tear-slug"
        db_session.flush()

        calls = []
        monkeypatch.setattr(
            "backend.services.uat_provisioner.teardown_uat",
            lambda slug, **kw: (calls.append(slug), (True, "OK"))[1],
        )

        resp = router_client.delete(f"/api/v1/projects/{created['id']}")
        assert resp.status_code == 204
        assert calls == ["tear-slug"]  # teardown invoked with the project's uat_slug

    def test_delete_without_uat_slug_skips_teardown(self, router_client, creator, db_session, monkeypatch):
        """No uat_slug → no UAT to tear down (no-op, never calls teardown).

        CR-R2-1 (#1a) sets a uat_slug at creation, so the None branch is exercised by explicitly clearing it
        on the row (mirrors the sibling's direct-row set — uat_slug has no create/update API surface)."""
        from backend.db.models.projects import Project

        created = router_client.post("/api/v1/projects", json=_payload(creator.id)).json()
        proj = db_session.get(Project, uuid.UUID(created["id"]))
        proj.uat_slug = None
        db_session.flush()

        calls = []
        monkeypatch.setattr(
            "backend.services.uat_provisioner.teardown_uat",
            lambda slug, **kw: (calls.append(slug), (True, "OK"))[1],
        )

        resp = router_client.delete(f"/api/v1/projects/{created['id']}")
        assert resp.status_code == 204
        assert calls == []

    # ----------------------------------------------- RAG cleanup (KB ghost Fix 2)

    def test_delete_clears_project_rag_points(self, router_client, creator):
        """Fix 2: deleting a project clears its Qdrant points (tenant icc,
        ``source_file`` under ``projects/<slug>/``) — keyed on the slug — so a
        deleted project leaves no ghost in RAG search, not just on disk."""
        created = router_client.post(
            "/api/v1/projects",
            json=_payload(creator.id, slug="rag-del-proj"),
        ).json()

        spy = MagicMock()
        spy.delete_project_documents.return_value = 3
        router_client.app.dependency_overrides[get_rag_indexer] = lambda: spy

        resp = router_client.delete(f"/api/v1/projects/{created['id']}")
        assert resp.status_code == 204
        spy.delete_project_documents.assert_called_once_with("rag-del-proj")

    def test_delete_best_effort_when_rag_delete_raises(self, router_client, creator):
        """Fix 2: a Qdrant failure during RAG cleanup must NOT undo the already
        committed DB delete — best-effort, never-raise (mirrors the KB / GitHub
        / UAT cleanups)."""
        created = router_client.post("/api/v1/projects", json=_payload(creator.id)).json()

        boom = MagicMock()
        boom.delete_project_documents.side_effect = RuntimeError("qdrant unreachable")
        router_client.app.dependency_overrides[get_rag_indexer] = lambda: boom

        resp = router_client.delete(f"/api/v1/projects/{created['id']}")
        assert resp.status_code == 204  # delete stands despite the RAG failure
        boom.delete_project_documents.assert_called_once()
        # DB row is gone — the committed delete was not undone.
        assert router_client.get(f"/api/v1/projects/{created['id']}").status_code == 404

    # ----------------------------------------------- AI-Agent memory (CR-V2-016)

    def test_create_does_not_write_status_history_to_kb(self, router_client, creator, tmp_path):
        """CR-V2-016 (R-DOUBLEWRITE): create no longer seeds DB-driven
        STATUS.md / HISTORY.md into the KB. Those were a second independent
        writer of project status / history; the single source of truth is now
        the AI Agent's own ``MEMORY.md`` in the project workspace. The KB
        project folder must therefore carry no STATUS.md / HISTORY.md from
        create.
        """
        payload = _payload(creator.id, name="Memory App", slug="memory-app")
        resp = router_client.post("/api/v1/projects", json=payload)
        assert resp.status_code == 201, resp.text

        project_dir = tmp_path / "projects" / "memory-app"
        # No DB-driven live-docs seeding (the retired second writer).
        assert not (project_dir / "STATUS.md").exists()
        assert not (project_dir / "HISTORY.md").exists()
        # ARCHITECT.md was already deprecated — still must not appear.
        assert not (project_dir / "ARCHITECT.md").exists()

    def test_create_seeds_agent_memory_in_workspace(self, router_client, creator, tmp_path, monkeypatch):
        """CR-V2-016: create seeds the AI Agent's ``MEMORY.md`` in the project
        workspace once the workspace exists (the single source of truth). We
        point the workspace root at ``tmp_path`` and pre-create the slug dir so
        the seed (which runs after the Stage-3 init script) has a checkout to
        write into.
        """
        workspace_root = tmp_path / "workspaces"
        monkeypatch.setattr("backend.services.project_memory.PROJECTS_ROOT", workspace_root)
        (workspace_root / "mem-seed").mkdir(parents=True)

        resp = router_client.post(
            "/api/v1/projects",
            json=_payload(creator.id, name="Mem Seed", slug="mem-seed"),
        )
        assert resp.status_code == 201, resp.text

        memory = workspace_root / "mem-seed" / "MEMORY.md"
        assert memory.is_file()
        body = memory.read_text(encoding="utf-8")
        assert "# Mem Seed — AI Agent pamäť" in body
        assert "## Rozhodnutia" in body

    def test_create_auto_creates_v0_1_0_version(self, router_client, creator, db_session):
        """POST auto-creates initial Version v0.1.0 in planned status.

        Per main CLAUDE.md §2 (three-agent architecture): every project must have
        a target version from the moment of creation so Designer's Step 0 VERSION
        binding finds it without manual setup.
        """
        from sqlalchemy import select

        from backend.db.models.versions import Version

        payload = _payload(creator.id, name="Versioned App", slug="versioned-app")
        resp = router_client.post("/api/v1/projects", json=payload)
        assert resp.status_code == 201, resp.text

        project_id = resp.json()["id"]
        versions = db_session.execute(select(Version).where(Version.project_id == project_id)).scalars().all()
        assert len(versions) == 1, f"Expected exactly 1 version, got {len(versions)}"
        v = versions[0]
        assert v.version_number == "0.1.0"
        assert v.status == "planned"
        assert v.name == "Initial prototype"

    def test_create_rolls_back_when_filesystem_write_fails(self, router_client, db_session, creator, monkeypatch):
        """If a create-time filesystem write raises OSError, the project must
        not end up in the DB (the transaction rolls back).

        CR-V2-016: the create-time filesystem write is now the AI Agent's
        ``MEMORY.md`` seed (``project_memory.seed_memory``), which replaced the
        retired STATUS.md / HISTORY.md KB seeding. We make it raise OSError and
        assert the same rollback invariant the old live-docs seed had. The
        ``router_client`` and this test share the SAVEPOINT-isolated
        ``db_session`` (the router's ``get_db`` override yields it).
        """

        def _boom(slug: str, name: str):
            raise OSError("disk full simulation")

        monkeypatch.setattr("backend.api.routes.projects.project_memory.seed_memory", _boom)

        resp = router_client.post(
            "/api/v1/projects",
            json=_payload(creator.id, slug="rollback-test"),
        )

        assert resp.status_code == 500
        assert "Failed to create project filesystem state" in resp.json()["detail"]

        # Nothing landed in the DB — the transaction rolled back.
        remaining = db_session.execute(sa_select_project_by_slug("rollback-test")).scalar_one_or_none()
        assert remaining is None, "Project row must have been rolled back on filesystem-write failure"

    # ------------------------------------------------------ GitHub repo create

    def test_create_calls_github_repo_create_with_slug(self, db_session, creator, tmp_path, monkeypatch):
        """POST forwards repo_url to create_github_repo before inserting the row."""
        from backend.api.dependencies import get_knowledge_base_writer
        from backend.services.knowledge_base_writer import KnowledgeBaseWriter

        calls = []

        def _mock_create(repo, **kwargs):
            calls.append((repo, kwargs))
            return True

        monkeypatch.setattr("backend.services.github_validation.create_github_repo", _mock_create)

        app = FastAPI()
        app.include_router(projects_router, prefix="/api/v1/projects")

        def _override_get_db():
            yield db_session

        def _override_kb_writer() -> KnowledgeBaseWriter:
            return KnowledgeBaseWriter(tmp_path)

        app.dependency_overrides[get_db] = _override_get_db
        app.dependency_overrides[get_knowledge_base_writer] = _override_kb_writer
        app.dependency_overrides[get_rag_indexer] = lambda: None
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
            payload = _payload(
                creator.id,
                slug="github-happy",
                repo_url="rauschiccsk/github-happy",
                description="Happy path for GitHub create",
            )
            resp = client.post("/api/v1/projects", json=payload)

        app.dependency_overrides.clear()

        assert resp.status_code == 201
        assert len(calls) == 1
        repo_arg, kwargs = calls[0]
        assert repo_arg == "rauschiccsk/github-happy"
        assert kwargs.get("description") == "Happy path for GitHub create"
        assert kwargs.get("private") is True

    def test_create_skips_github_when_repo_url_is_null(self, router_client, creator, monkeypatch):
        """A NULL repo_url short-circuits the GitHub call entirely."""
        github_called = {"n": 0}

        def _mock_create(repo, **kwargs):
            github_called["n"] += 1
            return True

        # Re-patch over the router_client's no-op mock to observe invocations.
        monkeypatch.setattr("backend.services.github_validation.create_github_repo", _mock_create)

        payload = _payload(creator.id, slug="no-repo")
        payload.pop("repo_url", None)  # ensure explicitly null in body
        resp = router_client.post("/api/v1/projects", json=payload)

        assert resp.status_code == 201
        assert github_called["n"] == 0

    def test_create_rolls_back_when_github_repo_create_fails(self, db_session, creator, tmp_path, monkeypatch):
        """RuntimeError from create_github_repo → 500 and no DB row."""
        from backend.api.dependencies import get_knowledge_base_writer
        from backend.services.knowledge_base_writer import KnowledgeBaseWriter

        def _raise_runtime(repo, **kwargs):
            raise RuntimeError("token missing or insufficient scope")

        monkeypatch.setattr("backend.services.github_validation.create_github_repo", _raise_runtime)

        app = FastAPI()
        app.include_router(projects_router, prefix="/api/v1/projects")

        def _override_get_db():
            yield db_session

        def _override_kb_writer() -> KnowledgeBaseWriter:
            return KnowledgeBaseWriter(tmp_path)

        app.dependency_overrides[get_db] = _override_get_db
        app.dependency_overrides[get_knowledge_base_writer] = _override_kb_writer
        app.dependency_overrides[get_rag_indexer] = lambda: None
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
            payload = _payload(creator.id, slug="gh-fail", repo_url="rauschiccsk/gh-fail")
            resp = client.post("/api/v1/projects", json=payload)

        app.dependency_overrides.clear()

        assert resp.status_code == 500
        assert "Failed to create GitHub repository" in resp.json()["detail"]

        # No DB row, no KB folder.
        remaining = db_session.execute(sa_select_project_by_slug("gh-fail")).scalar_one_or_none()
        assert remaining is None
        assert not (tmp_path / "projects" / "gh-fail").exists()

    # ------------------------------------------------------------- delete cleanup

    def test_delete_removes_kb_folder(self, router_client, creator, tmp_path):
        """DELETE /{id} removes the project's KB folder (any project-scoped KB
        docs), so a deleted project leaves no orphaned KB tree.

        CR-V2-016: create no longer seeds the KB folder (STATUS.md / HISTORY.md
        DB-driven seeding is retired). We pre-create a project-scoped KB doc to
        stand in for whatever KB content a project may accumulate, then assert
        delete cleans it up.
        """
        payload = _payload(creator.id, slug="delete-cleanup")
        created = router_client.post("/api/v1/projects", json=payload).json()

        # Stand-in project-scoped KB content (the writer is rooted at tmp_path by
        # the router_client override).
        project_dir = tmp_path / "projects" / "delete-cleanup"
        project_dir.mkdir(parents=True, exist_ok=True)
        (project_dir / "notes.md").write_text("# Notes\n", encoding="utf-8")
        assert project_dir.is_dir()

        resp = router_client.delete(f"/api/v1/projects/{created['id']}")
        assert resp.status_code == 204
        assert not project_dir.exists()

    def test_delete_without_flag_does_not_touch_github(self, router_client, creator, tmp_path, monkeypatch):
        """Without ?delete_github=true, the GitHub API is not called."""
        github_delete_calls = []

        def _mock_delete(repo, **kwargs):
            github_delete_calls.append(repo)
            return True

        monkeypatch.setattr("backend.services.github_validation.delete_github_repo", _mock_delete)

        payload = _payload(creator.id, slug="keep-repo", repo_url="rauschiccsk/keep-repo")
        created = router_client.post("/api/v1/projects", json=payload).json()
        resp = router_client.delete(f"/api/v1/projects/{created['id']}")

        assert resp.status_code == 204
        assert github_delete_calls == []

    def test_delete_with_flag_invokes_github_delete(self, router_client, creator, tmp_path, monkeypatch):
        """?delete_github=true passes repo_url to delete_github_repo."""
        github_delete_calls = []

        def _mock_delete(repo, **kwargs):
            github_delete_calls.append(repo)
            return True

        monkeypatch.setattr("backend.services.github_validation.delete_github_repo", _mock_delete)

        payload = _payload(creator.id, slug="also-delete-repo", repo_url="rauschiccsk/also-delete-repo")
        created = router_client.post("/api/v1/projects", json=payload).json()
        resp = router_client.delete(f"/api/v1/projects/{created['id']}?delete_github=true")

        assert resp.status_code == 204
        assert github_delete_calls == ["rauschiccsk/also-delete-repo"]

    def test_delete_github_failure_does_not_block_response(self, router_client, creator, tmp_path, monkeypatch):
        """If the GitHub delete fails, the endpoint still returns 204 — DB is gone."""

        def _raise_runtime(repo, **kwargs):
            raise RuntimeError("simulated GitHub outage")

        monkeypatch.setattr("backend.services.github_validation.delete_github_repo", _raise_runtime)

        payload = _payload(creator.id, slug="gh-del-fail", repo_url="rauschiccsk/gh-del-fail")
        created = router_client.post("/api/v1/projects", json=payload).json()
        resp = router_client.delete(f"/api/v1/projects/{created['id']}?delete_github=true")

        # Project and KB gone; only the repo is left stranded.
        assert resp.status_code == 204
        assert router_client.get(f"/api/v1/projects/{created['id']}").status_code == 404

    def test_create_github_value_error_returns_422(self, db_session, creator, tmp_path, monkeypatch):
        """A ValueError from create_github_repo (unknown org) → 422."""
        from backend.api.dependencies import get_knowledge_base_writer
        from backend.services.knowledge_base_writer import KnowledgeBaseWriter

        def _raise_value(repo, **kwargs):
            raise ValueError("GitHub organisation 'nowhere' not found")

        monkeypatch.setattr("backend.services.github_validation.create_github_repo", _raise_value)

        app = FastAPI()
        app.include_router(projects_router, prefix="/api/v1/projects")

        def _override_get_db():
            yield db_session

        def _override_kb_writer() -> KnowledgeBaseWriter:
            return KnowledgeBaseWriter(tmp_path)

        app.dependency_overrides[get_db] = _override_get_db
        app.dependency_overrides[get_knowledge_base_writer] = _override_kb_writer
        app.dependency_overrides[get_rag_indexer] = lambda: None
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
            payload = _payload(creator.id, slug="gh-no-org", repo_url="nowhere/repo")
            resp = client.post("/api/v1/projects", json=payload)

        app.dependency_overrides.clear()

        assert resp.status_code == 422
        # No DB row.
        assert db_session.execute(sa_select_project_by_slug("gh-no-org")).scalar_one_or_none() is None


def sa_select_project_by_slug(slug: str):
    """Local helper — build a ``SELECT Project WHERE slug=...`` statement."""
    from sqlalchemy import select as _select

    from backend.db.models.projects import Project as _Project

    return _select(_Project).where(_Project.slug == slug)


def _seed_prod_deploy(db_session, project_id) -> None:
    """Seed a successful PROD deploy event (with its required customer) for a project — the state that
    must block deletion (CR-V2-027). ``seq`` is a Postgres IDENTITY, generated on insert."""
    from backend.db.models.customers import Customer
    from backend.db.models.deploy import DeployEvent

    customer = Customer(project_id=project_id, name="ICC s.r.o.", slug=f"cust-{uuid.uuid4().hex[:6]}")
    db_session.add(customer)
    db_session.flush()
    db_session.add(
        DeployEvent(
            customer_id=customer.id,
            project_id=project_id,
            version_number="v1.0.0",
            environment="prod",
            event_type="deploy",
            status="ok",
        )
    )
    db_session.flush()


class TestProjectDeletionGuards:
    """CR-V2-027 — guarded project deletion: admin-only + blocked once PROD-deployed."""

    def test_delete_requires_ri_role(self, router_client, creator, db_session):
        """A non-``ri`` user (here ``shu``) is rejected with 403 — deletion is admin-only."""
        created = router_client.post("/api/v1/projects", json=_payload(creator.id)).json()

        from backend.core.security import get_current_user, require_ri_role

        shu = User(
            username=f"shu_{uuid.uuid4().hex[:8]}",
            email=f"{uuid.uuid4().hex[:8]}@test.local",
            password_hash="x",
            role="shu",
            is_active=True,
        )
        db_session.add(shu)
        db_session.flush()
        # Point auth at the shu user and let the REAL ri-gate run (drop the fixture's blanket ri override).
        router_client.app.dependency_overrides[get_current_user] = lambda: shu
        del router_client.app.dependency_overrides[require_ri_role]

        resp = router_client.delete(f"/api/v1/projects/{created['id']}")
        assert resp.status_code == 403

    def test_delete_blocked_after_prod_deploy_returns_409(self, router_client, creator, db_session):
        """A project with a successful PROD deploy can only be archived — delete returns 409."""
        created = router_client.post("/api/v1/projects", json=_payload(creator.id)).json()
        _seed_prod_deploy(db_session, uuid.UUID(created["id"]))

        resp = router_client.delete(f"/api/v1/projects/{created['id']}")
        assert resp.status_code == 409
        assert "PROD" in resp.json()["detail"]
        # The project row survives the blocked delete.
        assert router_client.get(f"/api/v1/projects/{created['id']}").status_code == 200

    def test_delete_allowed_without_prod_deploy(self, router_client, creator):
        """No PROD deploy → admin delete still succeeds (204) with the new guards in place."""
        created = router_client.post("/api/v1/projects", json=_payload(creator.id)).json()
        resp = router_client.delete(f"/api/v1/projects/{created['id']}")
        assert resp.status_code == 204
        assert router_client.get(f"/api/v1/projects/{created['id']}").status_code == 404

    def test_get_project_exposes_has_prod_deploy_flag(self, router_client, creator, db_session):
        """The detail endpoint computes ``has_prod_deploy`` — False before, True after a PROD deploy."""
        created = router_client.post("/api/v1/projects", json=_payload(creator.id)).json()
        assert router_client.get(f"/api/v1/projects/{created['id']}").json()["has_prod_deploy"] is False

        _seed_prod_deploy(db_session, uuid.UUID(created["id"]))
        assert router_client.get(f"/api/v1/projects/{created['id']}").json()["has_prod_deploy"] is True


def test_workspace_safe_to_remove(tmp_path):
    """The rmtree guard: only an existing dir strictly under the root is removable (CR-V2-027)."""
    from backend.api.routes.projects import _workspace_safe_to_remove

    root = tmp_path / "projects"
    (root / "demo").mkdir(parents=True)
    (tmp_path / "outside").mkdir()

    assert _workspace_safe_to_remove(str(root / "demo"), root) is True  # dir strictly under root
    assert _workspace_safe_to_remove(str(root), root) is False  # the root itself — never
    assert _workspace_safe_to_remove(str(tmp_path / "outside"), root) is False  # exists but outside root
    assert _workspace_safe_to_remove(str(root / "missing"), root) is False  # under root but not a dir
    assert _workspace_safe_to_remove("", root) is False  # empty path
