"""Tests for the Feat REST router.

Verifies the CRUD surface exposed by :mod:`backend.api.routes.feats`
against the SAVEPOINT-isolated test database. The router is mounted at
``/api/v1/feats`` — the same prefix it will have in production via
``backend/main.py`` — but since this router is not yet wired into
``main.py`` we mount it on a dedicated ``TestClient`` app here (same
pattern as :mod:`tests.test_epic_router` and
:mod:`tests.test_bug_router`).

Covers:

* Create / get / list / patch / delete happy paths.
* ``PaginatedResponse`` envelope (items / total / skip / limit).
* Pagination via ``skip`` and ``limit``.
* Filter by ``epic_id`` and ``status``.
* 404 on missing id (get, patch, delete).
* 422 on schema validation failure (invalid status, limit > 100, blank
  title).
* Auto-assignment of ``number`` per epic (1, 2, 3 …) and independent
  numbering across epics.
* ``description`` and ``status`` default to DB ``server_default`` when
  omitted.
* List ordering is ``number ASC``.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.dependencies import get_knowledge_base_writer, get_rag_indexer
from backend.api.routes.feats import router as feats_router
from backend.db.models.foundation import User
from backend.db.models.projects import Project
from backend.db.models.tasks import Epic
from backend.db.session import get_db
from backend.services.knowledge_base_writer import KnowledgeBaseWriter


@pytest.fixture()
def router_client(db_session, tmp_path):
    """Mount the feats router on a fresh app with DB + KB overrides.

    Keeping this fixture local to the router tests avoids coupling to
    the global ``main.app``, which does not yet include this router.
    The :class:`KnowledgeBaseWriter` is redirected to the test's
    ``tmp_path`` so the live-document hook on ``PATCH`` (feat
    completion) writes into an isolated KB tree instead of the real
    ``/home/icc/knowledge``.
    """
    app = FastAPI()
    app.include_router(feats_router, prefix="/api/v1/feats")

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
        # The ACCOUNT named admin — the only user who may operate a project he did not create.
        # Was role="ri", which meant that under the abolished tier model and means nothing now.
        username="admin",
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
def owner(db_session) -> User:
    """Persist a user that owns the test project."""
    user = User(
        username=f"owner_{uuid.uuid4().hex[:8]}",
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        password_hash="hashed_password_placeholder",
        role="ri",
    )
    db_session.add(user)
    db_session.flush()
    return user


@pytest.fixture()
def project(db_session, owner) -> Project:
    """Persist a project that epics may be filed against."""
    proj = Project(
        slug=f"proj-{uuid.uuid4().hex[:8]}",
        name=f"Project {uuid.uuid4().hex[:8]}",
        type="standard",
        auth_mode="password",
        description="Test project description",
        created_by=owner.id,
    )
    db_session.add(proj)
    db_session.flush()
    return proj


def _make_epic(db_session, project) -> Epic:
    """Persist a fresh epic within ``project`` and return it."""
    # Each epic needs a unique ``number`` per project — derive from a
    # short UUID hex so concurrent fixtures within a single test session
    # do not collide.
    number = int(uuid.uuid4().int % 1_000_000) + 1
    epic = Epic(
        project_id=project.id,
        number=number,
        title=f"Epic {uuid.uuid4().hex[:8]}",
        status="planned",
    )
    db_session.add(epic)
    db_session.flush()
    return epic


@pytest.fixture()
def epic(db_session, project) -> Epic:
    """Persist a single epic that feats may be filed against."""
    return _make_epic(db_session, project)


def _payload(*, epic_id, **overrides) -> dict:
    """Return a feat-create payload with deterministic-ish defaults."""
    body = {
        "epic_id": str(epic_id),
        "title": f"Feat {uuid.uuid4().hex[:8]}",
    }
    body.update(overrides)
    return body


class TestFeatRouter:
    """End-to-end HTTP coverage for the router."""

    # ----------------------------------------------------------- create
    def test_create_feat(self, router_client, epic):
        payload = _payload(
            epic_id=epic.id,
            title="Implement the widget",
            status="in_progress",
            description="Wire the widget into the dashboard.",
            estimated_minutes=60,
        )
        resp = router_client.post("/api/v1/feats", json=payload)
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["title"] == "Implement the widget"
        assert body["status"] == "in_progress"
        assert body["description"] == "Wire the widget into the dashboard."
        assert body["epic_id"] == str(epic.id)
        assert body["number"] == 1
        assert body["estimated_minutes"] == 60
        assert body["actual_minutes"] is None
        assert body["task_count"] == 0
        assert body["auto_fix_count"] == 0
        assert body["id"]
        assert body["created_at"]
        assert body["updated_at"]

    def test_create_status_defaults_to_todo(self, router_client, epic):
        resp = router_client.post(
            "/api/v1/feats",
            json=_payload(epic_id=epic.id),
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["status"] == "todo"
        assert body["description"] == ""

    def test_create_assigns_sequential_numbers_per_epic(self, router_client, epic):
        first = router_client.post(
            "/api/v1/feats",
            json=_payload(epic_id=epic.id),
        ).json()
        second = router_client.post(
            "/api/v1/feats",
            json=_payload(epic_id=epic.id),
        ).json()
        third = router_client.post(
            "/api/v1/feats",
            json=_payload(epic_id=epic.id),
        ).json()
        assert (first["number"], second["number"], third["number"]) == (1, 2, 3)

    def test_create_numbering_is_independent_per_epic(self, router_client, db_session, project):
        e1 = _make_epic(db_session, project)
        e2 = _make_epic(db_session, project)

        f1_e1 = router_client.post(
            "/api/v1/feats",
            json=_payload(epic_id=e1.id),
        ).json()
        f2_e1 = router_client.post(
            "/api/v1/feats",
            json=_payload(epic_id=e1.id),
        ).json()
        f1_e2 = router_client.post(
            "/api/v1/feats",
            json=_payload(epic_id=e2.id),
        ).json()

        assert f1_e1["number"] == 1
        assert f2_e1["number"] == 2
        assert f1_e2["number"] == 1

    def test_create_invalid_status_returns_422(self, router_client, epic):
        payload = _payload(epic_id=epic.id, status="bogus")
        resp = router_client.post("/api/v1/feats", json=payload)
        assert resp.status_code == 422

    def test_create_blank_title_returns_422(self, router_client, epic):
        payload = _payload(epic_id=epic.id, title="")
        resp = router_client.post("/api/v1/feats", json=payload)
        assert resp.status_code == 422

    # --------------------------------------------------------------- get
    def test_get_by_id(self, router_client, epic):
        created = router_client.post(
            "/api/v1/feats",
            json=_payload(epic_id=epic.id),
        ).json()
        resp = router_client.get(f"/api/v1/feats/{created['id']}")
        assert resp.status_code == 200
        assert resp.json()["id"] == created["id"]

    def test_get_missing_returns_404(self, router_client):
        resp = router_client.get(f"/api/v1/feats/{uuid.uuid4()}")
        assert resp.status_code == 404

    # -------------------------------------------------------------- list
    def test_list_envelope_and_pagination(self, router_client, epic):
        for _ in range(3):
            router_client.post(
                "/api/v1/feats",
                json=_payload(epic_id=epic.id),
            ).raise_for_status()

        resp = router_client.get(
            "/api/v1/feats",
            params={"epic_id": str(epic.id), "skip": 0, "limit": 2},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert set(body.keys()) >= {"items", "total", "skip", "limit"}
        assert body["skip"] == 0
        assert body["limit"] == 2
        assert body["total"] == 3
        assert len(body["items"]) == 2

        page2 = router_client.get(
            "/api/v1/feats",
            params={"epic_id": str(epic.id), "skip": 2, "limit": 2},
        ).json()
        page1_ids = {row["id"] for row in body["items"]}
        page2_ids = {row["id"] for row in page2["items"]}
        assert page1_ids.isdisjoint(page2_ids)

    def test_list_orders_by_number_asc(self, router_client, epic):
        for _ in range(3):
            router_client.post(
                "/api/v1/feats",
                json=_payload(epic_id=epic.id),
            ).raise_for_status()

        resp = router_client.get(
            "/api/v1/feats",
            params={"epic_id": str(epic.id)},
        )
        assert resp.status_code == 200
        numbers = [row["number"] for row in resp.json()["items"]]
        assert numbers == sorted(numbers)
        assert numbers == [1, 2, 3]

    def test_list_filter_by_epic_id(self, router_client, db_session, project):
        e1 = _make_epic(db_session, project)
        e2 = _make_epic(db_session, project)

        router_client.post(
            "/api/v1/feats",
            json=_payload(epic_id=e1.id),
        ).raise_for_status()
        router_client.post(
            "/api/v1/feats",
            json=_payload(epic_id=e2.id),
        ).raise_for_status()

        resp = router_client.get(
            "/api/v1/feats",
            params={"epic_id": str(e2.id)},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] >= 1
        assert all(item["epic_id"] == str(e2.id) for item in body["items"])

    def test_list_filter_by_status(self, router_client, epic):
        router_client.post(
            "/api/v1/feats",
            json=_payload(epic_id=epic.id, status="todo"),
        ).raise_for_status()
        router_client.post(
            "/api/v1/feats",
            json=_payload(epic_id=epic.id, status="in_progress"),
        ).raise_for_status()

        resp = router_client.get(
            "/api/v1/feats",
            params={"epic_id": str(epic.id), "status": "in_progress"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] >= 1
        assert all(item["status"] == "in_progress" for item in body["items"])

    def test_list_limit_over_100_returns_422(self, router_client):
        resp = router_client.get("/api/v1/feats", params={"limit": 101})
        assert resp.status_code == 422

    # -------------------------------------------------------------- patch
    def test_patch_partial_update(self, router_client, epic):
        created = router_client.post(
            "/api/v1/feats",
            json=_payload(
                epic_id=epic.id,
                title="Original title",
                status="todo",
                estimated_minutes=30,
            ),
        ).json()

        resp = router_client.patch(
            f"/api/v1/feats/{created['id']}",
            json={
                "status": "in_progress",
                "title": "Updated title",
                "actual_minutes": 45,
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "in_progress"
        assert body["title"] == "Updated title"
        assert body["actual_minutes"] == 45
        # Immutable fields unchanged.
        assert body["id"] == created["id"]
        assert body["epic_id"] == created["epic_id"]
        assert body["number"] == created["number"]
        assert body["created_at"] == created["created_at"]
        # Untouched mutable fields preserved.
        assert body["estimated_minutes"] == 30

    def test_patch_omitted_fields_unchanged(self, router_client, epic):
        created = router_client.post(
            "/api/v1/feats",
            json=_payload(
                epic_id=epic.id,
                title="Keep me",
                description="Keep this description",
                status="todo",
            ),
        ).json()

        resp = router_client.patch(
            f"/api/v1/feats/{created['id']}",
            json={"status": "done"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "done"
        assert body["title"] == "Keep me"
        assert body["description"] == "Keep this description"

    def test_patch_invalid_status_returns_422(self, router_client, epic):
        created = router_client.post(
            "/api/v1/feats",
            json=_payload(epic_id=epic.id),
        ).json()
        resp = router_client.patch(
            f"/api/v1/feats/{created['id']}",
            json={"status": "bogus"},
        )
        assert resp.status_code == 422

    def test_patch_missing_returns_404(self, router_client):
        resp = router_client.patch(
            f"/api/v1/feats/{uuid.uuid4()}",
            json={"status": "in_progress"},
        )
        assert resp.status_code == 404

    # ------------------------------------------------------------- delete
    def test_delete_returns_204(self, router_client, epic):
        created = router_client.post(
            "/api/v1/feats",
            json=_payload(epic_id=epic.id),
        ).json()
        resp = router_client.delete(f"/api/v1/feats/{created['id']}")
        assert resp.status_code == 204
        # Second read confirms removal.
        assert router_client.get(f"/api/v1/feats/{created['id']}").status_code == 404

    def test_delete_missing_returns_404(self, router_client):
        resp = router_client.delete(f"/api/v1/feats/{uuid.uuid4()}")
        assert resp.status_code == 404

    # ------------------------------------------- AI-Agent memory (CR-V2-016)

    def test_patch_to_done_writes_no_status_history(self, router_client, epic, project, tmp_path):
        """CR-V2-016 (R-DOUBLEWRITE): a feat -> done transition no longer writes
        STATUS.md / HISTORY.md into the KB. That DB-driven phase-summary
        side-effect was a second independent writer of project status / history;
        the single source of truth is now the AI Agent's own MEMORY.md plus the
        Vyvoj phase tabs. The PATCH is now a pure DB update and must touch no KB
        project folder.
        """
        created = router_client.post(
            "/api/v1/feats",
            json=_payload(epic_id=epic.id, title="Foundation"),
        ).json()

        resp = router_client.patch(
            f"/api/v1/feats/{created['id']}",
            json={"status": "done", "actual_minutes": 15},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "done"

        project_dir = tmp_path / "projects" / project.slug
        assert not project_dir.exists()
