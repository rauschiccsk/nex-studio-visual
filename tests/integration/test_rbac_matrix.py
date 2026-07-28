"""The access matrix — what the roles still decide, and what they no longer touch.

Rewritten for the ownership model (Director, 2026-07-28). The file previously specified a three-role
tier system over projects; that system is gone, and a test asserting it would now be asserting a rule
the product deliberately abolished. What replaced it is deliberately small enough to state in full:

* PROJECTS know nothing about roles. A project belongs to the one user who created it
  (``projects.created_by``); he may do everything on it; nobody else sees it; the single account named
  ``admin`` sees and may do everything everywhere. That is the whole rule, and
  ``backend/core/authz.is_owner_or_admin`` is the whole implementation.
* THE KNOWLEDGE BASE is the one place the Shuhari roles still decide anything, and it is UNCHANGED:
  ``ri`` sees every category, ``ha`` the configured baseline, ``shu`` only ``icc/`` + ``shuhari/``.

The class that used to sit at the bottom of this file — a ``shu`` user gaining KB access to a project
he was enrolled in — is gone with ``project_members``. A junior now works under his manager's login, so
there is no second account to enrol; a junior's OWN account sees the ``shu`` baseline and nothing more,
which is asserted below so the loss is a stated rule rather than a silent one.
"""

from __future__ import annotations

import bcrypt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.routes.credentials import router as credentials_router
from backend.api.routes.knowledge import router as knowledge_router
from backend.core import authz
from backend.core.security import (
    get_current_user,
    require_shu_or_above,
)
from backend.db.models.foundation import User
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
# Project ownership — the whole rule
# ---------------------------------------------------------------------------


class TestProjectOwnership:
    """The whole project rule, asserted directly on the predicate every route funnels through.

    Deliberately tested at ``authz.is_owner_or_admin`` rather than through a router: there is exactly
    one rule now, ~150 call sites reach it, and pinning it here means a change to the model breaks one
    obvious test instead of scattering failures across the suite.
    """

    def test_owner_may_touch_his_own_project(self, db_session, shu_user):
        # Role is irrelevant — this is a Junior, and it is his project.
        assert authz.is_owner_or_admin(shu_user, shu_user.id) is True

    def test_a_stranger_may_not(self, db_session, shu_user, ha_user):
        # A Medior has no standing on somebody else's project. Under the old tier model he did.
        assert authz.is_owner_or_admin(ha_user, shu_user.id) is False

    def test_the_ri_ROLE_grants_nothing_over_projects(self, db_session, ri_user, shu_user):
        """The role 'ri' used to mean "may touch every project". It no longer means anything here —
        only the ACCOUNT named 'admin' does. A user may hold ri (for the Knowledge Base) and still have
        no standing on a project he did not create."""
        assert ri_user.role == "ri"
        assert ri_user.username != authz.ADMIN_USERNAME
        assert authz.is_owner_or_admin(ri_user, shu_user.id) is False

    def test_the_admin_ACCOUNT_may_touch_everything(self, db_session, shu_user):
        admin = _make_user(db_session, "shu", "adm")
        admin.username = authz.ADMIN_USERNAME  # the account, not the role — note the role here is shu
        db_session.flush()
        assert authz.is_admin(admin) is True
        assert authz.is_owner_or_admin(admin, shu_user.id) is True

    def test_ownership_reads_created_by_not_owner_id(self, db_session, ri_user, shu_user):
        """``projects.owner_id`` is the TELEGRAM NOTIFICATION target, not the owner. If rights ever
        follow it, a project becomes unowned the moment that user is removed (it is SET NULL), and the
        permission follows whoever receives the messages."""
        project = Project(
            name="Ownership Probe",
            slug="ownership-probe",
            type="standard",
            auth_mode="password",
            description="",
            status="active",
            created_by=shu_user.id,
            owner_id=ri_user.id,  # notifications go elsewhere ON PURPOSE
        )
        db_session.add(project)
        db_session.flush()

        assert authz.project_owner_id(project) == shu_user.id
        authz.authorize_project(shu_user, project)  # the creator: allowed, must not raise
        with pytest.raises(Exception):  # the notification target: not the owner
            authz.authorize_project(ri_user, project)


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


class TestJuniorKbAccessWithoutMembership:
    """A junior's own account sees the ``shu`` baseline and NOTHING project-specific.

    This is the one user-visible consequence of dropping ``project_members``, so it is asserted rather
    than left to be discovered: there is no longer any mechanism to widen a junior's Knowledge-Base
    access to a single project. Under the shared-login model he reads the KB as his manager.
    """

    def test_junior_sees_only_the_baseline(self, db_session, shu_user):
        from backend.utils.kb_access import get_allowed_kb_categories

        allowed = get_allowed_kb_categories(shu_user, db_session)

        assert allowed == ["icc/", "shuhari/"]
        assert not any(p.startswith("projects/") for p in allowed)
