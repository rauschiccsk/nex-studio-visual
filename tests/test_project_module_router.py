"""Tests for the ProjectModule REST router.

Verifies the CRUD surface exposed by
:mod:`backend.api.routes.project_modules` against the SAVEPOINT-isolated
test database. The router is mounted at ``/api/v1/project-modules`` —
the same prefix it will have in production via ``backend/main.py`` —
but since this router is not yet wired into ``main.py`` we mount it on
a dedicated ``TestClient`` app here (same pattern as
:mod:`tests.test_bug_router`, :mod:`tests.test_project_router` and
:mod:`tests.test_guardian_precedent_router`).

Covers:

* Create / get / list / patch / delete happy paths.
* ``PaginatedResponse`` envelope (items / total / skip / limit).
* Pagination via ``skip`` and ``limit``.
* Filter by ``project_id``, ``status`` and ``category``.
* 404 on missing id (get, patch, delete).
* 409 on duplicate ``(project_id, code)`` natural key, either on
  create or when renaming via PATCH to collide with a sibling.
* 422 on schema validation failure (missing required field, invalid
  status literal, ``limit > 100``).
* PATCH happy path — updates mutable fields and preserves the
  immutable ``project_id`` / ``id`` / ``created_at``.
* Same ``code`` is allowed across different projects.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.dependencies import get_knowledge_base_writer
from backend.api.routes.project_modules import router as project_modules_router
from backend.db.models.foundation import User
from backend.db.models.projects import Project
from backend.db.session import get_db
from backend.services.knowledge_base_writer import KnowledgeBaseWriter


@pytest.fixture()
def router_client(db_session, tmp_path):
    """Mount the project_modules router on a fresh app with DB + KB overrides.

    The KnowledgeBaseWriter is redirected to ``tmp_path`` so module
    create / status-change / delete hooks (N3) append to an isolated
    HISTORY.md instead of the real ``/home/icc/knowledge`` tree.
    """
    app = FastAPI()
    app.include_router(project_modules_router, prefix="/api/v1/project-modules")

    def _override_get_db():
        yield db_session

    def _override_kb_writer() -> KnowledgeBaseWriter:
        return KnowledgeBaseWriter(tmp_path)

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_knowledge_base_writer] = _override_kb_writer

    with TestClient(app) as client:
        yield client

    app.dependency_overrides.clear()


def _make_user(db_session, **overrides) -> User:
    """Persist a user to satisfy FK references on Project."""
    defaults = {
        "username": f"user_{uuid.uuid4().hex[:8]}",
        "email": f"{uuid.uuid4().hex[:8]}@example.com",
        "password_hash": "hashed_password_placeholder",
        "role": "ri",
    }
    defaults.update(overrides)
    user = User(**defaults)
    db_session.add(user)
    db_session.flush()
    return user


def _make_project(db_session, *, user: User | None = None, **overrides) -> Project:
    """Persist a project to satisfy the FK on ProjectModule.project_id."""
    if user is None:
        user = _make_user(db_session)
    suffix = uuid.uuid4().hex[:8]
    defaults = {
        "name": f"Project {suffix}",
        "slug": f"project-{suffix}",
        "category": "multimodule",
        "description": "Test project description",
        "created_by": user.id,
    }
    defaults.update(overrides)
    project = Project(**defaults)
    db_session.add(project)
    db_session.flush()
    return project


@pytest.fixture()
def creator(db_session) -> User:
    """Persist a user that will own fixture projects."""
    return _make_user(db_session)


@pytest.fixture()
def project(db_session, creator) -> Project:
    """Persist a project to satisfy the FK on ProjectModule.project_id."""
    return _make_project(db_session, user=creator)


def _payload(project_id, **overrides) -> dict:
    """Return a project-module-create payload as a JSON-compatible dict."""
    suffix = uuid.uuid4().hex[:4].upper()
    data = {
        "project_id": str(project_id),
        "code": f"M{suffix}",
        "name": f"Module {suffix}",
        "category": "General",
    }
    data.update(overrides)
    return data


class TestProjectModuleRouter:
    """End-to-end HTTP coverage for the router."""

    # ---------------------------------------------------------------- create
    def test_create_project_module(self, router_client, project):
        payload = _payload(project.id, code="PAB", name="Katalóg partnerov", category="Katalógy")
        resp = router_client.post("/api/v1/project-modules", json=payload)
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["project_id"] == str(project.id)
        assert body["code"] == "PAB"
        assert body["name"] == "Katalóg partnerov"
        assert body["category"] == "Katalógy"
        # server_default 'planned'
        assert body["status"] == "planned"
        assert body["design_doc_path"] is None
        assert body["id"]
        assert body["created_at"]
        assert body["updated_at"]

    def test_create_with_explicit_status_and_path(self, router_client, project):
        payload = _payload(
            project.id,
            code="GSC",
            name="General Settings",
            category="Katalógy",
            status="in_design",
            design_doc_path="/kb/nex-horizont/modules/GSC/DESIGN.md",
        )
        resp = router_client.post("/api/v1/project-modules", json=payload)
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["status"] == "in_design"
        assert body["design_doc_path"] == "/kb/nex-horizont/modules/GSC/DESIGN.md"

    def test_create_duplicate_code_returns_409(self, router_client, project):
        payload = _payload(project.id, code="PAB")
        first = router_client.post("/api/v1/project-modules", json=payload)
        assert first.status_code == 201

        second_payload = _payload(project.id, code="PAB", name="Another Name")
        second = router_client.post("/api/v1/project-modules", json=second_payload)
        assert second.status_code == 409

    def test_same_code_allowed_across_projects(self, router_client, db_session, creator):
        project_a = _make_project(db_session, user=creator)
        project_b = _make_project(db_session, user=creator)

        first = router_client.post(
            "/api/v1/project-modules",
            json=_payload(project_a.id, code="PAB"),
        )
        assert first.status_code == 201

        second = router_client.post(
            "/api/v1/project-modules",
            json=_payload(project_b.id, code="PAB"),
        )
        assert second.status_code == 201

    def test_create_missing_project_id_returns_422(self, router_client):
        resp = router_client.post(
            "/api/v1/project-modules",
            json={"code": "PAB", "name": "Module", "category": "General"},
        )
        assert resp.status_code == 422

    def test_create_missing_code_returns_422(self, router_client, project):
        resp = router_client.post(
            "/api/v1/project-modules",
            json={"project_id": str(project.id), "name": "Module", "category": "General"},
        )
        assert resp.status_code == 422

    def test_create_invalid_status_returns_422(self, router_client, project):
        payload = _payload(project.id, status="not-a-status")
        resp = router_client.post("/api/v1/project-modules", json=payload)
        assert resp.status_code == 422

    def test_create_invalid_uuid_returns_422(self, router_client):
        resp = router_client.post(
            "/api/v1/project-modules",
            json={
                "project_id": "not-a-uuid",
                "code": "PAB",
                "name": "Module",
                "category": "General",
            },
        )
        assert resp.status_code == 422

    # ------------------------------------------------------------------- get
    def test_get_by_id(self, router_client, project):
        created = router_client.post(
            "/api/v1/project-modules",
            json=_payload(project.id, code="PAB"),
        ).json()
        resp = router_client.get(f"/api/v1/project-modules/{created['id']}")
        assert resp.status_code == 200
        assert resp.json()["id"] == created["id"]

    def test_get_missing_returns_404(self, router_client):
        resp = router_client.get(f"/api/v1/project-modules/{uuid.uuid4()}")
        assert resp.status_code == 404

    # ------------------------------------------------------------------ list
    def test_list_envelope_and_pagination(self, router_client, project):
        for i in range(3):
            router_client.post(
                "/api/v1/project-modules",
                json=_payload(project.id, code=f"M{i:03d}"),
            ).raise_for_status()

        resp = router_client.get(
            "/api/v1/project-modules",
            params={"skip": 0, "limit": 2, "project_id": str(project.id)},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert set(body.keys()) >= {"items", "total", "skip", "limit"}
        assert body["skip"] == 0
        assert body["limit"] == 2
        assert body["total"] >= 3
        assert len(body["items"]) == 2

        page2 = router_client.get(
            "/api/v1/project-modules",
            params={"skip": 2, "limit": 2, "project_id": str(project.id)},
        ).json()
        page1_ids = {row["id"] for row in body["items"]}
        page2_ids = {row["id"] for row in page2["items"]}
        assert page1_ids.isdisjoint(page2_ids)

    def test_list_filter_by_project(self, router_client, db_session, creator):
        project_a = _make_project(db_session, user=creator)
        project_b = _make_project(db_session, user=creator)
        router_client.post(
            "/api/v1/project-modules",
            json=_payload(project_a.id, code="AAA"),
        ).raise_for_status()
        router_client.post(
            "/api/v1/project-modules",
            json=_payload(project_b.id, code="BBB"),
        ).raise_for_status()

        resp = router_client.get(
            "/api/v1/project-modules",
            params={"project_id": str(project_a.id)},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] >= 1
        assert all(item["project_id"] == str(project_a.id) for item in body["items"])

    def test_list_filter_by_status(self, router_client, project):
        router_client.post(
            "/api/v1/project-modules",
            json=_payload(project.id, code="P01", status="planned"),
        ).raise_for_status()
        router_client.post(
            "/api/v1/project-modules",
            json=_payload(project.id, code="D01", status="done"),
        ).raise_for_status()

        resp = router_client.get(
            "/api/v1/project-modules",
            params={"project_id": str(project.id), "status": "done"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] >= 1
        assert all(item["status"] == "done" for item in body["items"])

    def test_list_filter_by_category(self, router_client, project):
        router_client.post(
            "/api/v1/project-modules",
            json=_payload(project.id, code="KAT", category="Katalógy"),
        ).raise_for_status()
        router_client.post(
            "/api/v1/project-modules",
            json=_payload(project.id, code="SKL", category="Sklad"),
        ).raise_for_status()

        resp = router_client.get(
            "/api/v1/project-modules",
            params={"project_id": str(project.id), "category": "Katalógy"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] >= 1
        assert all(item["category"] == "Katalógy" for item in body["items"])

    def test_list_limit_over_100_returns_422(self, router_client):
        resp = router_client.get(
            "/api/v1/project-modules",
            params={"limit": 101},
        )
        assert resp.status_code == 422

    # ---------------------------------------------------------------- patch
    def test_patch_updates_mutable_fields(self, router_client, project):
        created = router_client.post(
            "/api/v1/project-modules",
            json=_payload(project.id, code="PAB", name="Old Name", category="Katalógy"),
        ).json()

        resp = router_client.patch(
            f"/api/v1/project-modules/{created['id']}",
            json={
                "name": "New Name",
                "status": "in_development",
                "design_doc_path": "/kb/new/DESIGN.md",
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["id"] == created["id"]
        # Immutable
        assert body["project_id"] == created["project_id"]
        assert body["created_at"] == created["created_at"]
        assert body["code"] == "PAB"
        # Mutated
        assert body["name"] == "New Name"
        assert body["status"] == "in_development"
        assert body["design_doc_path"] == "/kb/new/DESIGN.md"

    def test_patch_empty_payload_is_noop(self, router_client, project):
        created = router_client.post(
            "/api/v1/project-modules",
            json=_payload(project.id, code="PAB"),
        ).json()

        resp = router_client.patch(
            f"/api/v1/project-modules/{created['id']}",
            json={},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == created["id"]
        assert body["code"] == created["code"]
        assert body["name"] == created["name"]

    def test_patch_duplicate_code_returns_409(self, router_client, project):
        router_client.post(
            "/api/v1/project-modules",
            json=_payload(project.id, code="PAB"),
        ).raise_for_status()
        other = router_client.post(
            "/api/v1/project-modules",
            json=_payload(project.id, code="GSC"),
        ).json()

        resp = router_client.patch(
            f"/api/v1/project-modules/{other['id']}",
            json={"code": "PAB"},
        )
        assert resp.status_code == 409

    def test_patch_invalid_status_returns_422(self, router_client, project):
        created = router_client.post(
            "/api/v1/project-modules",
            json=_payload(project.id, code="PAB"),
        ).json()

        resp = router_client.patch(
            f"/api/v1/project-modules/{created['id']}",
            json={"status": "not-a-status"},
        )
        assert resp.status_code == 422

    def test_patch_missing_returns_404(self, router_client):
        resp = router_client.patch(
            f"/api/v1/project-modules/{uuid.uuid4()}",
            json={"name": "X"},
        )
        assert resp.status_code == 404

    # --------------------------------------------------------------- delete
    def test_delete_returns_204(self, router_client, project):
        created = router_client.post(
            "/api/v1/project-modules",
            json=_payload(project.id, code="PAB"),
        ).json()
        resp = router_client.delete(f"/api/v1/project-modules/{created['id']}")
        assert resp.status_code == 204
        # Second read confirms removal.
        assert router_client.get(f"/api/v1/project-modules/{created['id']}").status_code == 404

    def test_delete_missing_returns_404(self, router_client):
        resp = router_client.delete(f"/api/v1/project-modules/{uuid.uuid4()}")
        assert resp.status_code == 404

    def test_delete_one_module_leaves_others_intact(self, router_client, project):
        module_a = router_client.post(
            "/api/v1/project-modules",
            json=_payload(project.id, code="PAB"),
        ).json()
        module_b = router_client.post(
            "/api/v1/project-modules",
            json=_payload(project.id, code="GSC"),
        ).json()

        resp = router_client.delete(f"/api/v1/project-modules/{module_a['id']}")
        assert resp.status_code == 204

        # Module B is still retrievable.
        remaining = router_client.get(f"/api/v1/project-modules/{module_b['id']}")
        assert remaining.status_code == 200
        assert remaining.json()["id"] == module_b["id"]


class TestModuleLiveDocsHook:
    """Verify N3 — module CRUD append entries to HISTORY.md + regenerate STATUS."""

    def test_create_appends_history_entry(self, router_client, project, tmp_path):
        payload = _payload(project.id, code="PAB", name="Katalóg partnerov", category="Katalógy")
        resp = router_client.post("/api/v1/project-modules", json=payload)
        assert resp.status_code == 201

        history = (tmp_path / "projects" / project.slug / "HISTORY.md").read_text(encoding="utf-8")
        assert "Module PAB created — Katalóg partnerov (Katalógy)" in history

    def test_status_change_appends_history_entry(self, router_client, project, tmp_path):
        created = router_client.post(
            "/api/v1/project-modules",
            json=_payload(project.id, code="PAB", name="Katalóg partnerov"),
        ).json()

        resp = router_client.patch(
            f"/api/v1/project-modules/{created['id']}",
            json={"status": "in_development"},
        )
        assert resp.status_code == 200

        history = (tmp_path / "projects" / project.slug / "HISTORY.md").read_text(encoding="utf-8")
        assert "Module PAB status planned → in_development" in history

    def test_non_status_patch_does_not_fire_event(self, router_client, project, tmp_path):
        """A rename / category change does NOT spam HISTORY.md with events."""
        created = router_client.post(
            "/api/v1/project-modules",
            json=_payload(project.id, code="PAB", name="Old Name"),
        ).json()

        router_client.patch(
            f"/api/v1/project-modules/{created['id']}",
            json={"name": "New Name"},
        )

        history = (tmp_path / "projects" / project.slug / "HISTORY.md").read_text(encoding="utf-8")
        # One created entry, no status entries.
        assert history.count("Module PAB") == 1
        assert "status" not in history.lower() or "status_changed" not in history.lower()

    def test_delete_appends_history_entry(self, router_client, project, tmp_path):
        created = router_client.post(
            "/api/v1/project-modules",
            json=_payload(project.id, code="PAB", name="Katalóg partnerov"),
        ).json()

        resp = router_client.delete(f"/api/v1/project-modules/{created['id']}")
        assert resp.status_code == 204

        history = (tmp_path / "projects" / project.slug / "HISTORY.md").read_text(encoding="utf-8")
        assert "Module PAB deleted — Katalóg partnerov" in history
