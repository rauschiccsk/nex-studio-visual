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
* Filter by ``status``, ``category`` and ``created_by``.
* 404 on missing id (get, patch, delete).
* 409 on duplicate ``name`` / ``slug``.
* 422 on schema validation failure (e.g. invalid category or status,
  limit > 100).
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.dependencies import get_knowledge_base_writer
from backend.api.routes.projects import router as projects_router
from backend.db.models.foundation import User
from backend.db.session import get_db
from backend.services.knowledge_base_writer import KnowledgeBaseWriter


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
    app.dependency_overrides[get_knowledge_base_writer] = _override_kb_writer

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
        "category": "singlemodule",
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
            category="multimodule",
        )
        resp = router_client.post("/api/v1/projects", json=payload)
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["name"] == "Alpha"
        assert body["slug"] == "alpha"
        assert body["category"] == "multimodule"
        assert body["status"] == "active"
        assert body["guardian_enabled"] is False
        assert body["created_by"] == str(creator.id)
        assert body["id"]
        assert body["created_at"]
        assert body["updated_at"]

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

    def test_create_invalid_category_returns_422(self, router_client, creator):
        payload = _payload(creator.id, category="bogus")
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

    def test_list_filter_by_category(self, router_client, creator):
        router_client.post(
            "/api/v1/projects",
            json=_payload(creator.id, category="singlemodule"),
        ).raise_for_status()
        router_client.post(
            "/api/v1/projects",
            json=_payload(creator.id, category="multimodule"),
        ).raise_for_status()

        resp = router_client.get(
            "/api/v1/projects",
            params={"category": "multimodule"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] >= 1
        assert all(item["category"] == "multimodule" for item in body["items"])

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
        assert body["category"] == created["category"]
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

    # ---------------------------------------------------------------- live docs

    def test_create_seeds_three_live_documents(self, router_client, creator, tmp_path):
        """POST creates STATUS.md, HISTORY.md and ARCHITECT.md in the KB."""
        payload = _payload(creator.id, name="Live Docs App", slug="live-docs-app")
        resp = router_client.post("/api/v1/projects", json=payload)
        assert resp.status_code == 201, resp.text

        project_dir = tmp_path / "projects" / "live-docs-app"
        assert (project_dir / "STATUS.md").is_file()
        assert (project_dir / "HISTORY.md").is_file()
        assert (project_dir / "ARCHITECT.md").is_file()

        # STATUS.md reflects the fresh state — no epics yet but header present.
        status_md = (project_dir / "STATUS.md").read_text(encoding="utf-8")
        assert "# Live Docs App — Status" in status_md
        assert "No epics planned yet." in status_md

        # HISTORY / ARCHITECT start as bare headers.
        assert (project_dir / "HISTORY.md").read_text(encoding="utf-8") == (
            "# live-docs-app — History\n\n"
        )
        assert (project_dir / "ARCHITECT.md").read_text(encoding="utf-8") == (
            "# live-docs-app — Architecture Log\n\n"
        )

    def test_create_rolls_back_when_kb_write_fails(
        self, db_session, creator, tmp_path, monkeypatch
    ):
        """If KB write raises OSError, the project must not end up in the DB."""
        from backend.api.dependencies import get_knowledge_base_writer
        from backend.services.knowledge_base_writer import KnowledgeBaseWriter

        monkeypatch.setattr(
            "backend.services.github_validation.create_github_repo",
            lambda repo, **kwargs: True,
        )

        class _FailingWriter(KnowledgeBaseWriter):
            def save(self, *args, **kwargs):  # type: ignore[override]
                raise OSError("disk full simulation")

        app = FastAPI()
        app.include_router(projects_router, prefix="/api/v1/projects")

        def _override_get_db():
            yield db_session

        def _override_kb_writer() -> KnowledgeBaseWriter:
            return _FailingWriter(tmp_path)

        app.dependency_overrides[get_db] = _override_get_db
        app.dependency_overrides[get_knowledge_base_writer] = _override_kb_writer

        with TestClient(app) as client:
            resp = client.post(
                "/api/v1/projects",
                json=_payload(creator.id, slug="rollback-test"),
            )

        app.dependency_overrides.clear()

        assert resp.status_code == 500
        assert "Failed to initialise live documents" in resp.json()["detail"]

        # And verify nothing landed in the DB.

        remaining = db_session.execute(
            sa_select_project_by_slug("rollback-test")
        ).scalar_one_or_none()
        assert remaining is None, "Project row must have been rolled back on KB failure"

    # ------------------------------------------------------ GitHub repo create

    def test_create_calls_github_repo_create_with_slug(
        self, db_session, creator, tmp_path, monkeypatch
    ):
        """POST forwards repo_url to create_github_repo before inserting the row."""
        from backend.api.dependencies import get_knowledge_base_writer
        from backend.services.knowledge_base_writer import KnowledgeBaseWriter

        calls = []

        def _mock_create(repo, **kwargs):
            calls.append((repo, kwargs))
            return True

        monkeypatch.setattr(
            "backend.services.github_validation.create_github_repo", _mock_create
        )

        app = FastAPI()
        app.include_router(projects_router, prefix="/api/v1/projects")

        def _override_get_db():
            yield db_session

        def _override_kb_writer() -> KnowledgeBaseWriter:
            return KnowledgeBaseWriter(tmp_path)

        app.dependency_overrides[get_db] = _override_get_db
        app.dependency_overrides[get_knowledge_base_writer] = _override_kb_writer

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

    def test_create_skips_github_when_repo_url_is_null(
        self, router_client, creator, monkeypatch
    ):
        """A NULL repo_url short-circuits the GitHub call entirely."""
        github_called = {"n": 0}

        def _mock_create(repo, **kwargs):
            github_called["n"] += 1
            return True

        # Re-patch over the router_client's no-op mock to observe invocations.
        monkeypatch.setattr(
            "backend.services.github_validation.create_github_repo", _mock_create
        )

        payload = _payload(creator.id, slug="no-repo")
        payload.pop("repo_url", None)  # ensure explicitly null in body
        resp = router_client.post("/api/v1/projects", json=payload)

        assert resp.status_code == 201
        assert github_called["n"] == 0

    def test_create_rolls_back_when_github_repo_create_fails(
        self, db_session, creator, tmp_path, monkeypatch
    ):
        """RuntimeError from create_github_repo → 500 and no DB row."""
        from backend.api.dependencies import get_knowledge_base_writer
        from backend.services.knowledge_base_writer import KnowledgeBaseWriter

        def _raise_runtime(repo, **kwargs):
            raise RuntimeError("token missing or insufficient scope")

        monkeypatch.setattr(
            "backend.services.github_validation.create_github_repo", _raise_runtime
        )

        app = FastAPI()
        app.include_router(projects_router, prefix="/api/v1/projects")

        def _override_get_db():
            yield db_session

        def _override_kb_writer() -> KnowledgeBaseWriter:
            return KnowledgeBaseWriter(tmp_path)

        app.dependency_overrides[get_db] = _override_get_db
        app.dependency_overrides[get_knowledge_base_writer] = _override_kb_writer

        with TestClient(app) as client:
            payload = _payload(
                creator.id, slug="gh-fail", repo_url="rauschiccsk/gh-fail"
            )
            resp = client.post("/api/v1/projects", json=payload)

        app.dependency_overrides.clear()

        assert resp.status_code == 500
        assert "Failed to create GitHub repository" in resp.json()["detail"]

        # No DB row, no KB folder.
        remaining = db_session.execute(
            sa_select_project_by_slug("gh-fail")
        ).scalar_one_or_none()
        assert remaining is None
        assert not (tmp_path / "projects" / "gh-fail").exists()

    def test_create_github_value_error_returns_422(
        self, db_session, creator, tmp_path, monkeypatch
    ):
        """A ValueError from create_github_repo (unknown org) → 422."""
        from backend.api.dependencies import get_knowledge_base_writer
        from backend.services.knowledge_base_writer import KnowledgeBaseWriter

        def _raise_value(repo, **kwargs):
            raise ValueError("GitHub organisation 'nowhere' not found")

        monkeypatch.setattr(
            "backend.services.github_validation.create_github_repo", _raise_value
        )

        app = FastAPI()
        app.include_router(projects_router, prefix="/api/v1/projects")

        def _override_get_db():
            yield db_session

        def _override_kb_writer() -> KnowledgeBaseWriter:
            return KnowledgeBaseWriter(tmp_path)

        app.dependency_overrides[get_db] = _override_get_db
        app.dependency_overrides[get_knowledge_base_writer] = _override_kb_writer

        with TestClient(app) as client:
            payload = _payload(
                creator.id, slug="gh-no-org", repo_url="nowhere/repo"
            )
            resp = client.post("/api/v1/projects", json=payload)

        app.dependency_overrides.clear()

        assert resp.status_code == 422
        # No DB row.
        assert (
            db_session.execute(sa_select_project_by_slug("gh-no-org")).scalar_one_or_none()
            is None
        )


def sa_select_project_by_slug(slug: str):
    """Local helper — build a ``SELECT Project WHERE slug=...`` statement."""
    from sqlalchemy import select as _select

    from backend.db.models.projects import Project as _Project

    return _select(_Project).where(_Project.slug == slug)
