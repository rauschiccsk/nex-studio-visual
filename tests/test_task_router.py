"""Tests for the Task REST router.

Verifies the CRUD surface exposed by :mod:`backend.api.routes.tasks`
against the SAVEPOINT-isolated test database. The router is mounted at
``/api/v1/tasks`` — the same prefix it will have in production via
``backend/main.py`` — but since this router is not yet wired into
``main.py`` we mount it on a dedicated ``TestClient`` app here (same
pattern as :mod:`tests.test_feat_router` and
:mod:`tests.test_epic_router`).

Covers:

* Create / get / list / patch / delete happy paths.
* ``PaginatedResponse`` envelope (items / total / skip / limit).
* Pagination via ``skip`` and ``limit``.
* Filter by ``feat_id``, ``status`` and ``task_type``.
* 404 on missing id (get, patch, delete).
* 422 on schema validation failure (invalid status, invalid task_type,
  limit > 100, blank title).
* Auto-assignment of ``number`` per feat (1, 2, 3 …) and independent
  numbering across feats.
* ``description`` and ``status`` default to DB ``server_default`` when
  omitted.
* List ordering is ``number ASC``.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select as sa_select

from backend.api.dependencies import get_knowledge_base_writer, get_rag_indexer
from backend.api.routes.tasks import router as tasks_router
from backend.db.models.foundation import User
from backend.db.models.projects import Project
from backend.db.models.tasks import Epic, Feat
from backend.db.session import get_db
from backend.services.knowledge_base_writer import KnowledgeBaseWriter


@pytest.fixture()
def router_client(db_session, tmp_path):
    """Mount the tasks router on a fresh app with DB + KB overrides.

    Keeping this fixture local to the router tests avoids coupling to
    the global ``main.app``, which does not yet include this router.
    The :class:`KnowledgeBaseWriter` is redirected to the test's
    ``tmp_path`` so the live-document hook on ``PATCH`` (task
    completion) writes into an isolated KB tree instead of the real
    ``/home/icc/knowledge``.
    """
    app = FastAPI()
    app.include_router(tasks_router, prefix="/api/v1/tasks")

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


def _make_feat(db_session, epic) -> Feat:
    """Persist a fresh feat within ``epic`` and return it."""
    next_number = (
        db_session.execute(
            sa_select(Feat.number).where(Feat.epic_id == epic.id).order_by(Feat.number.desc()).limit(1)
        ).scalar()
        or 0
    ) + 1
    feat = Feat(
        epic_id=epic.id,
        number=next_number,
        title=f"Feat {uuid.uuid4().hex[:8]}",
        status="todo",
    )
    db_session.add(feat)
    db_session.flush()
    return feat


@pytest.fixture()
def epic(db_session, project) -> Epic:
    """Persist a single epic that feats may be filed against."""
    return _make_epic(db_session, project)


@pytest.fixture()
def feat(db_session, epic) -> Feat:
    """Persist a single feat that tasks may be filed against."""
    return _make_feat(db_session, epic)


def _payload(*, feat_id, **overrides) -> dict:
    """Return a task-create payload with deterministic-ish defaults."""
    body = {
        "feat_id": str(feat_id),
        "title": f"Task {uuid.uuid4().hex[:8]}",
        "task_type": "backend",
    }
    body.update(overrides)
    return body


class TestTaskRouter:
    """End-to-end HTTP coverage for the router."""

    # ----------------------------------------------------------- create
    def test_create_task(self, router_client, feat):
        payload = _payload(
            feat_id=feat.id,
            title="Implement the widget",
            status="in_progress",
            description="Wire the widget into the dashboard.",
            estimated_minutes=60,
            task_type="frontend",
            checklist_type="router",
        )
        resp = router_client.post("/api/v1/tasks", json=payload)
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["title"] == "Implement the widget"
        assert body["status"] == "in_progress"
        assert body["description"] == "Wire the widget into the dashboard."
        assert body["feat_id"] == str(feat.id)
        assert body["number"] == 1
        assert body["estimated_minutes"] == 60
        assert body["actual_minutes"] is None
        assert body["task_type"] == "frontend"
        assert body["checklist_type"] == "router"
        assert body["id"]
        assert body["created_at"]
        assert body["updated_at"]

    def test_create_status_defaults_to_todo(self, router_client, feat):
        resp = router_client.post(
            "/api/v1/tasks",
            json=_payload(feat_id=feat.id),
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["status"] == "todo"
        assert body["description"] == ""

    def test_create_assigns_sequential_numbers_per_feat(self, router_client, feat):
        first = router_client.post(
            "/api/v1/tasks",
            json=_payload(feat_id=feat.id),
        ).json()
        second = router_client.post(
            "/api/v1/tasks",
            json=_payload(feat_id=feat.id),
        ).json()
        third = router_client.post(
            "/api/v1/tasks",
            json=_payload(feat_id=feat.id),
        ).json()
        assert (first["number"], second["number"], third["number"]) == (1, 2, 3)

    def test_create_numbering_is_independent_per_feat(self, router_client, db_session, epic):
        f1 = _make_feat(db_session, epic)
        f2 = _make_feat(db_session, epic)

        t1_f1 = router_client.post(
            "/api/v1/tasks",
            json=_payload(feat_id=f1.id),
        ).json()
        t2_f1 = router_client.post(
            "/api/v1/tasks",
            json=_payload(feat_id=f1.id),
        ).json()
        t1_f2 = router_client.post(
            "/api/v1/tasks",
            json=_payload(feat_id=f2.id),
        ).json()

        assert t1_f1["number"] == 1
        assert t2_f1["number"] == 2
        assert t1_f2["number"] == 1

    def test_create_invalid_status_returns_422(self, router_client, feat):
        payload = _payload(feat_id=feat.id, status="bogus")
        resp = router_client.post("/api/v1/tasks", json=payload)
        assert resp.status_code == 422

    def test_create_invalid_task_type_returns_422(self, router_client, feat):
        payload = _payload(feat_id=feat.id, task_type="bogus")
        resp = router_client.post("/api/v1/tasks", json=payload)
        assert resp.status_code == 422

    def test_create_blank_title_returns_422(self, router_client, feat):
        payload = _payload(feat_id=feat.id, title="")
        resp = router_client.post("/api/v1/tasks", json=payload)
        assert resp.status_code == 422

    # --------------------------------------------------------------- get
    def test_get_by_id(self, router_client, feat):
        created = router_client.post(
            "/api/v1/tasks",
            json=_payload(feat_id=feat.id),
        ).json()
        resp = router_client.get(f"/api/v1/tasks/{created['id']}")
        assert resp.status_code == 200
        assert resp.json()["id"] == created["id"]

    def test_get_missing_returns_404(self, router_client):
        resp = router_client.get(f"/api/v1/tasks/{uuid.uuid4()}")
        assert resp.status_code == 404

    # -------------------------------------------------------------- list
    def test_list_envelope_and_pagination(self, router_client, feat):
        for _ in range(3):
            router_client.post(
                "/api/v1/tasks",
                json=_payload(feat_id=feat.id),
            ).raise_for_status()

        resp = router_client.get(
            "/api/v1/tasks",
            params={"feat_id": str(feat.id), "skip": 0, "limit": 2},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert set(body.keys()) >= {"items", "total", "skip", "limit"}
        assert body["skip"] == 0
        assert body["limit"] == 2
        assert body["total"] == 3
        assert len(body["items"]) == 2

        page2 = router_client.get(
            "/api/v1/tasks",
            params={"feat_id": str(feat.id), "skip": 2, "limit": 2},
        ).json()
        page1_ids = {row["id"] for row in body["items"]}
        page2_ids = {row["id"] for row in page2["items"]}
        assert page1_ids.isdisjoint(page2_ids)

    def test_list_orders_by_number_asc(self, router_client, feat):
        for _ in range(3):
            router_client.post(
                "/api/v1/tasks",
                json=_payload(feat_id=feat.id),
            ).raise_for_status()

        resp = router_client.get(
            "/api/v1/tasks",
            params={"feat_id": str(feat.id)},
        )
        assert resp.status_code == 200
        numbers = [row["number"] for row in resp.json()["items"]]
        assert numbers == sorted(numbers)
        assert numbers == [1, 2, 3]

    def test_list_filter_by_feat_id(self, router_client, db_session, epic):
        f1 = _make_feat(db_session, epic)
        f2 = _make_feat(db_session, epic)

        router_client.post(
            "/api/v1/tasks",
            json=_payload(feat_id=f1.id),
        ).raise_for_status()
        router_client.post(
            "/api/v1/tasks",
            json=_payload(feat_id=f2.id),
        ).raise_for_status()

        resp = router_client.get(
            "/api/v1/tasks",
            params={"feat_id": str(f2.id)},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] >= 1
        assert all(item["feat_id"] == str(f2.id) for item in body["items"])

    def test_list_filter_by_status(self, router_client, feat):
        router_client.post(
            "/api/v1/tasks",
            json=_payload(feat_id=feat.id, status="todo"),
        ).raise_for_status()
        router_client.post(
            "/api/v1/tasks",
            json=_payload(feat_id=feat.id, status="in_progress"),
        ).raise_for_status()

        resp = router_client.get(
            "/api/v1/tasks",
            params={"feat_id": str(feat.id), "status": "in_progress"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] >= 1
        assert all(item["status"] == "in_progress" for item in body["items"])

    def test_list_filter_by_task_type(self, router_client, feat):
        router_client.post(
            "/api/v1/tasks",
            json=_payload(feat_id=feat.id, task_type="backend"),
        ).raise_for_status()
        router_client.post(
            "/api/v1/tasks",
            json=_payload(feat_id=feat.id, task_type="frontend"),
        ).raise_for_status()

        resp = router_client.get(
            "/api/v1/tasks",
            params={"feat_id": str(feat.id), "task_type": "frontend"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] >= 1
        assert all(item["task_type"] == "frontend" for item in body["items"])

    def test_list_limit_over_100_returns_422(self, router_client):
        resp = router_client.get("/api/v1/tasks", params={"limit": 101})
        assert resp.status_code == 422

    # -------------------------------------------------------------- patch
    def test_patch_partial_update(self, router_client, feat):
        created = router_client.post(
            "/api/v1/tasks",
            json=_payload(
                feat_id=feat.id,
                title="Original title",
                status="todo",
                estimated_minutes=30,
            ),
        ).json()

        resp = router_client.patch(
            f"/api/v1/tasks/{created['id']}",
            json={
                "status": "in_progress",
                "title": "Updated title",
                "actual_minutes": 45,
                "task_type": "frontend",
                "checklist_type": "service",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "in_progress"
        assert body["title"] == "Updated title"
        assert body["actual_minutes"] == 45
        assert body["task_type"] == "frontend"
        assert body["checklist_type"] == "service"
        # Immutable fields unchanged.
        assert body["id"] == created["id"]
        assert body["feat_id"] == created["feat_id"]
        assert body["number"] == created["number"]
        assert body["created_at"] == created["created_at"]
        # Untouched mutable fields preserved.
        assert body["estimated_minutes"] == 30

    def test_patch_omitted_fields_unchanged(self, router_client, feat):
        created = router_client.post(
            "/api/v1/tasks",
            json=_payload(
                feat_id=feat.id,
                title="Keep me",
                description="Keep this description",
                status="todo",
            ),
        ).json()

        resp = router_client.patch(
            f"/api/v1/tasks/{created['id']}",
            json={"status": "done"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "done"
        assert body["title"] == "Keep me"
        assert body["description"] == "Keep this description"

    def test_patch_invalid_status_returns_422(self, router_client, feat):
        created = router_client.post(
            "/api/v1/tasks",
            json=_payload(feat_id=feat.id),
        ).json()
        resp = router_client.patch(
            f"/api/v1/tasks/{created['id']}",
            json={"status": "bogus"},
        )
        assert resp.status_code == 422

    def test_patch_invalid_task_type_returns_422(self, router_client, feat):
        created = router_client.post(
            "/api/v1/tasks",
            json=_payload(feat_id=feat.id),
        ).json()
        resp = router_client.patch(
            f"/api/v1/tasks/{created['id']}",
            json={"task_type": "bogus"},
        )
        assert resp.status_code == 422

    def test_patch_missing_returns_404(self, router_client):
        resp = router_client.patch(
            f"/api/v1/tasks/{uuid.uuid4()}",
            json={"status": "in_progress"},
        )
        assert resp.status_code == 404

    # ------------------------------------------------------------- delete
    def test_delete_returns_204(self, router_client, feat):
        created = router_client.post(
            "/api/v1/tasks",
            json=_payload(feat_id=feat.id),
        ).json()
        resp = router_client.delete(f"/api/v1/tasks/{created['id']}")
        assert resp.status_code == 204
        # Second read confirms removal.
        assert router_client.get(f"/api/v1/tasks/{created['id']}").status_code == 404

    def test_delete_missing_returns_404(self, router_client):
        resp = router_client.delete(f"/api/v1/tasks/{uuid.uuid4()}")
        assert resp.status_code == 404

    # ------------------------------------------- AI-Agent memory (CR-V2-016)

    def test_patch_to_done_writes_no_status_history(self, router_client, feat, project, tmp_path):
        """CR-V2-016 (R-DOUBLEWRITE): a task -> done transition no longer writes
        STATUS.md / HISTORY.md into the KB. That DB-driven side-effect was a
        second independent writer of project status / history; the single source
        of truth is now the AI Agent's own MEMORY.md plus the Vyvoj phase tabs.
        The PATCH is now a pure DB update and must touch no KB project folder.
        """
        created = router_client.post(
            "/api/v1/tasks",
            json=_payload(feat_id=feat.id, title="Implement login"),
        ).json()

        resp = router_client.patch(
            f"/api/v1/tasks/{created['id']}",
            json={"status": "done"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "done"

        project_dir = tmp_path / "projects" / project.slug
        assert not project_dir.exists()
