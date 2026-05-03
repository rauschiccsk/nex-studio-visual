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

from backend.api.dependencies import get_knowledge_base_writer
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
    app.dependency_overrides[get_knowledge_base_writer] = _override_kb_writer

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
        category="multimodule",
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

    # ------------------------------------------------------------- live docs

    def test_patch_to_done_appends_phase_summary_and_status(self, router_client, epic, project, tmp_path):
        """Transition to done writes HISTORY phase summary and refreshes STATUS."""
        created = router_client.post(
            "/api/v1/feats",
            json=_payload(epic_id=epic.id, title="Foundation"),
        ).json()

        # actual_minutes is only accepted via PATCH (backfill / correction
        # path per FeatUpdate); bundle it with the status flip.
        resp = router_client.patch(
            f"/api/v1/feats/{created['id']}",
            json={"status": "done", "actual_minutes": 15},
        )
        assert resp.status_code == 200, resp.text

        project_dir = tmp_path / "projects" / project.slug
        history = (project_dir / "HISTORY.md").read_text(encoding="utf-8")
        status_md = (project_dir / "STATUS.md").read_text(encoding="utf-8")

        # Phase summary shape: "Feat N COMPLETE — title\n  Tasks: X | Duration: ... | Audit: NA | CI: N/A"
        # feat.number is always 1 (first feat under the fixture epic).
        assert "Feat 1 COMPLETE" in history
        assert "Foundation" in history
        assert "Tasks: 0" in history  # no tasks attached in this test
        assert "Duration: 15m0s" in history  # 15 actual minutes
        assert "Audit: NA" in history
        assert "CI: N/A" in history
        assert "=" * 50 in history  # the 50-equals divider

        # STATUS reflects the done feat — epic number is random (the test
        # fixture derives it from uuid4) so match on the feat segment only.
        assert f"### Feat {epic.number}.1: Foundation — DONE" in status_md

    def test_patch_to_done_counts_tasks_in_summary(self, db_session, router_client, epic, project, tmp_path):
        """Phase summary's Tasks count comes from COUNT(tasks WHERE feat_id=...)."""
        created = router_client.post(
            "/api/v1/feats",
            json=_payload(epic_id=epic.id, title="F"),
        ).json()
        feat_id = uuid.UUID(created["id"])

        # Seed two tasks against the feat.
        from backend.db.models.tasks import Task

        for i in (1, 2):
            db_session.add(Task(feat_id=feat_id, number=i, title=f"T{i}", task_type="backend"))
        db_session.flush()

        router_client.patch(
            f"/api/v1/feats/{feat_id}",
            json={"status": "done", "actual_minutes": 30},
        )

        history = (tmp_path / "projects" / project.slug / "HISTORY.md").read_text(encoding="utf-8")
        assert "Tasks: 2" in history

    def test_patch_to_done_with_estimated_only_uses_estimate(self, router_client, epic, project, tmp_path):
        """When actual_minutes is NULL, duration falls back to estimated_minutes."""
        created = router_client.post(
            "/api/v1/feats",
            json=_payload(
                epic_id=epic.id,
                title="Planned",
                estimated_minutes=120,
            ),
        ).json()

        router_client.patch(
            f"/api/v1/feats/{created['id']}",
            json={"status": "done"},
        )

        history = (tmp_path / "projects" / project.slug / "HISTORY.md").read_text(encoding="utf-8")
        # 120 minutes → 2h0m
        assert "Duration: 2h0m" in history

    def test_patch_status_to_in_progress_does_not_fire_hook(self, router_client, epic, project, tmp_path):
        created = router_client.post(
            "/api/v1/feats",
            json=_payload(epic_id=epic.id),
        ).json()

        router_client.patch(
            f"/api/v1/feats/{created['id']}",
            json={"status": "in_progress"},
        )

        assert not (tmp_path / "projects" / project.slug).exists()

    def test_patch_title_only_does_not_fire_hook(self, router_client, epic, project, tmp_path):
        created = router_client.post(
            "/api/v1/feats",
            json=_payload(epic_id=epic.id, title="Old"),
        ).json()

        router_client.patch(
            f"/api/v1/feats/{created['id']}",
            json={"title": "New"},
        )

        assert not (tmp_path / "projects" / project.slug).exists()

    def test_patch_to_done_replayed_is_idempotent(self, router_client, epic, project, tmp_path):
        """Second PATCH to done skips the hook (previous_status already done)."""
        created = router_client.post(
            "/api/v1/feats",
            json=_payload(epic_id=epic.id, title="Once done"),
        ).json()

        router_client.patch(
            f"/api/v1/feats/{created['id']}",
            json={"status": "done"},
        )
        router_client.patch(
            f"/api/v1/feats/{created['id']}",
            json={"status": "done"},
        )

        history = (tmp_path / "projects" / project.slug / "HISTORY.md").read_text(encoding="utf-8")
        assert history.count("Feat 1 COMPLETE — Once done") == 1

    def test_patch_rolls_back_when_kb_write_fails(self, db_session, project, epic):
        """OSError on KB write → 500 + feat.status unchanged in DB."""
        from backend.api.dependencies import get_knowledge_base_writer
        from backend.db.models.tasks import Feat as _Feat
        from backend.services.knowledge_base_writer import KnowledgeBaseWriter

        class _FailingWriter(KnowledgeBaseWriter):
            def append(self, *args, **kwargs):  # type: ignore[override]
                raise OSError("disk full simulation")

        app = FastAPI()
        app.include_router(feats_router, prefix="/api/v1/feats")

        def _override_get_db():
            yield db_session

        def _override_kb_writer() -> KnowledgeBaseWriter:
            return _FailingWriter("/tmp/unused")

        app.dependency_overrides[get_db] = _override_get_db
        app.dependency_overrides[get_knowledge_base_writer] = _override_kb_writer

        with TestClient(app) as client:
            created = client.post(
                "/api/v1/feats",
                json=_payload(epic_id=epic.id, title="Will fail"),
            ).json()
            resp = client.patch(
                f"/api/v1/feats/{created['id']}",
                json={"status": "done"},
            )

        app.dependency_overrides.clear()

        assert resp.status_code == 500
        assert "Failed to update live documents" in resp.json()["detail"]

        # The feat must remain in its pre-PATCH status.
        from sqlalchemy import select as _select

        reloaded = db_session.execute(_select(_Feat).where(_Feat.id == uuid.UUID(created["id"]))).scalar_one()
        assert reloaded.status == "todo"
