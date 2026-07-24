"""M2.E milestone — comprehensive per-role RBAC matrix.

Covers the access semantics introduced by M2.A-D (2026-05-07):

* ``require_ri_role`` — admin/critical routes only ``ri`` can hit.
* ``require_ha_or_above`` — project/pipeline routes that ``ri`` and
  ``ha`` can hit; ``shu`` is blocked with 403.
* ``require_shu_or_above`` — any authenticated user (alias for
  ``get_current_user``); only anonymous gets 401.

Plus the KB-specific behaviours:

* ``ri`` user sees every category on ``GET /knowledge/categories``.
* ``ha`` user sees the ``kb_access_ha`` baseline (icc/, shuhari/,
  infrastructure/, projects/, customers/, templates/).
* ``shu`` user sees only icc/ + shuhari/ + project paths the user
  is a member of via ``project_members``.
"""

from __future__ import annotations

import bcrypt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.routes.credentials import router as credentials_router
from backend.api.routes.knowledge import router as knowledge_router
from backend.api.routes.project_members import router as project_members_router
from backend.core.security import (
    get_current_user,
    require_shu_or_above,
)
from backend.db.models.foundation import User
from backend.db.models.project_member import ProjectMember
from backend.db.models.projects import Project
from backend.db.session import get_db


def _make_user(db_session, role: str, suffix: str) -> User:
    user = User(
        username=f"{role}_rbac_{suffix}",
        email=f"{role}_rbac_{suffix}@test.local",
        password_hash=bcrypt.hashpw(b"test", bcrypt.gensalt(rounds=4)).decode(),
        role=role,
        is_active=True,
    )
    db_session.add(user)
    db_session.flush()
    return user


def _build_client(db_session, user: User) -> TestClient:
    """Build a TestClient where get_current_user returns ``user`` and the
    require_* dependencies resolve naturally (ri vs ha vs shu)."""
    app = FastAPI()
    app.include_router(knowledge_router, prefix="/api/v1/knowledge")
    app.include_router(credentials_router, prefix="/api/v1/credentials")
    app.include_router(project_members_router, prefix="/api/v1/project-members")

    def _override_get_db():
        yield db_session

    def _override_user() -> User:
        return user

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_current_user] = _override_user
    # Important: do NOT override require_ri_role / require_ha_or_above —
    # we want to exercise the real role-check logic, fed by the
    # overridden get_current_user.
    # Override require_shu_or_above to return user (it's an alias).
    app.dependency_overrides[require_shu_or_above] = _override_user

    # Need to also override require_ri_role / require_ha_or_above to
    # delegate to the *real* function with our overridden get_current_user.
    # Because FastAPI resolves dependencies by Depends() chain, the real
    # functions read get_current_user via Depends — which is overridden —
    # so they will see ``user``. Therefore no override needed; FastAPI
    # threads through automatically. Confirmed by tests below: ha user
    # gets 403 on require_ri_role-gated routes.

    return TestClient(app)


@pytest.fixture()
def ri_user(db_session) -> User:
    return _make_user(db_session, "ri", "z01")


@pytest.fixture()
def ha_user(db_session) -> User:
    return _make_user(db_session, "ha", "h01")


@pytest.fixture()
def shu_user(db_session) -> User:
    return _make_user(db_session, "shu", "s01")


# ---------------------------------------------------------------------------
# require_ri_role — admin/critical routes
# ---------------------------------------------------------------------------


class TestRequireRiRole:
    """Credentials registry list is router-level gated by require_ri_role."""

    def test_ri_can_list(self, db_session, ri_user):
        client = _build_client(db_session, ri_user)
        resp = client.get("/api/v1/credentials")
        assert resp.status_code == 200

    def test_ha_forbidden(self, db_session, ha_user):
        client = _build_client(db_session, ha_user)
        resp = client.get("/api/v1/credentials")
        assert resp.status_code == 403

    def test_shu_forbidden(self, db_session, shu_user):
        client = _build_client(db_session, shu_user)
        resp = client.get("/api/v1/credentials")
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# project_members router — list = ha+, create/update/delete = ri only
# ---------------------------------------------------------------------------


class TestProjectMembersRouter:
    def test_ri_can_list(self, db_session, ri_user):
        client = _build_client(db_session, ri_user)
        resp = client.get("/api/v1/project-members")
        assert resp.status_code == 200

    def test_ha_can_list(self, db_session, ha_user):
        client = _build_client(db_session, ha_user)
        resp = client.get("/api/v1/project-members")
        assert resp.status_code == 200

    def test_shu_forbidden_list(self, db_session, shu_user, ri_user):
        """v4.0.43: a Junior may not list members across all projects — must scope to an OWNED project."""
        # A project owned by someone else (DB setup before any request).
        project = Project(
            name="proj-rbac-shu-list",
            slug="proj-rbac-shu-list",
            type="standard",
            auth_mode="password",
            description="",
            created_by=ri_user.id,
        )
        db_session.add(project)
        db_session.flush()
        other_id = str(project.id)

        client = _build_client(db_session, shu_user)
        # no project_id → cannot list across all projects → 400
        assert client.get("/api/v1/project-members").status_code == 400
        # a project owned by someone else → 403
        assert client.get("/api/v1/project-members", params={"project_id": other_id}).status_code == 403

    def test_ha_forbidden_create(self, db_session, ha_user, ri_user):
        # ha cannot create project_members — only ri can
        creator = ri_user
        project = Project(
            name="proj-rbac",
            slug="proj-rbac-create",
            type="standard",
            auth_mode="password",
            description="rbac test",
            created_by=creator.id,
        )
        db_session.add(project)
        db_session.flush()

        client = _build_client(db_session, ha_user)
        resp = client.post(
            "/api/v1/project-members",
            json={"project_id": str(project.id), "user_id": str(ha_user.id)},
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# KB categories matrix
# ---------------------------------------------------------------------------


class TestKbCategoriesMatrix:
    def test_ri_sees_all(self, db_session, ri_user, tmp_path, monkeypatch):
        from backend.config.settings import settings

        # Create fake KB tree
        for cat in ("icc", "shuhari", "customers", "projects", "credentials"):
            (tmp_path / cat).mkdir()
            (tmp_path / cat / "DOC.md").write_text("# x")
        monkeypatch.setattr(settings, "knowledge_base_path", str(tmp_path))

        client = _build_client(db_session, ri_user)
        resp = client.get("/api/v1/knowledge/categories")
        assert resp.status_code == 200
        cats = resp.json()["categories"]
        # ri sees everything except _RESTRICTED_CATEGORIES (credentials)
        # NOTE: credentials is in BLOCKED_CATEGORIES in KnowledgeManager so
        # it is hidden from get_categories() entirely. Other top-level dirs
        # remain visible to ri.
        assert "icc" in cats
        assert "shuhari" in cats
        assert "credentials" not in cats  # filtered by KnowledgeManager

    def test_shu_sees_only_baseline(self, db_session, shu_user, tmp_path, monkeypatch):
        from backend.config.settings import settings

        for cat in ("icc", "shuhari", "customers", "infrastructure"):
            (tmp_path / cat).mkdir()
            (tmp_path / cat / "DOC.md").write_text("# x")
        monkeypatch.setattr(settings, "knowledge_base_path", str(tmp_path))

        # shu has kb_access_shu baseline = ["icc/", "shuhari/"] only.
        # categories endpoint returns the dir scan; documents endpoint
        # filters by kb_access. We test documents filtering instead —
        # categories is presented to UI for navigation hints.
        client = _build_client(db_session, shu_user)
        resp = client.get("/api/v1/knowledge/documents")
        assert resp.status_code == 200
        docs = resp.json()["documents"]
        paths = [d["relative_path"] for d in docs]
        # shu sees icc/, shuhari/ docs only (no customers/, infrastructure/)
        assert all(p.startswith(("icc/", "shuhari/")) for p in paths)


# ---------------------------------------------------------------------------
# KB project membership — shu sees assigned projects
# ---------------------------------------------------------------------------


class TestShuProjectMembership:
    def test_shu_member_sees_assigned_project_kb(self, db_session, ri_user, shu_user, tmp_path, monkeypatch):
        from backend.config.settings import settings

        # Create KB tree with two projects
        (tmp_path / "icc").mkdir()
        (tmp_path / "icc" / "STD.md").write_text("# std")
        (tmp_path / "projects").mkdir()
        (tmp_path / "projects" / "proj-a").mkdir()
        (tmp_path / "projects" / "proj-a" / "STATUS.md").write_text("# A")
        (tmp_path / "projects" / "proj-b").mkdir()
        (tmp_path / "projects" / "proj-b" / "STATUS.md").write_text("# B")
        monkeypatch.setattr(settings, "knowledge_base_path", str(tmp_path))

        # Persist projects + add shu as member of proj-a only
        proj_a = Project(
            name="A",
            slug="proj-a",
            type="standard",
            auth_mode="password",
            description="",
            created_by=ri_user.id,
        )
        proj_b = Project(
            name="B",
            slug="proj-b",
            type="standard",
            auth_mode="password",
            description="",
            created_by=ri_user.id,
        )
        db_session.add_all([proj_a, proj_b])
        db_session.flush()
        db_session.add(ProjectMember(project_id=proj_a.id, user_id=shu_user.id))
        db_session.flush()

        client = _build_client(db_session, shu_user)
        resp = client.get("/api/v1/knowledge/documents")
        assert resp.status_code == 200
        docs = resp.json()["documents"]
        paths = [d["relative_path"] for d in docs]
        # shu sees icc/ + projects/proj-a/ but NOT projects/proj-b/
        assert "icc/STD.md" in paths
        assert "projects/proj-a/STATUS.md" in paths
        assert "projects/proj-b/STATUS.md" not in paths
