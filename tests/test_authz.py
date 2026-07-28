"""Adversarial ownership-authorization tests (v4.0.35).

The security core: a Junior (`shu`) may access ONLY projects they created; ri/ha access every project.
These test `backend.core.authz` directly (owner OK, non-owner 403, privileged OK, missing 404) so a missing
guard on any route shows up as a failure here as well as in the per-router tests.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException

from backend.core import authz
from backend.db.models.foundation import User
from backend.db.models.projects import Project
from backend.db.models.versions import Version


def _user(db_session, role: str) -> User:
    u = User(
        username=f"u_{uuid.uuid4().hex[:8]}",
        email=f"{uuid.uuid4().hex[:8]}@ex.com",
        password_hash="x",
        role=role,
    )
    db_session.add(u)
    db_session.flush()
    return u


def _project(db_session, owner: User) -> Project:
    p = Project(
        name=f"P {uuid.uuid4().hex[:6]}",
        slug=f"p-{uuid.uuid4().hex[:8]}",
        type="standard",
        auth_mode="password",
        description="",
        created_by=owner.id,
    )
    db_session.add(p)
    db_session.flush()
    return p


def _version(db_session, project: Project) -> Version:
    v = Version(project_id=project.id, version_number="0.1.0", status="planned")
    db_session.add(v)
    db_session.flush()
    return v


class TestIsOwnerOrAdmin:
    def test_no_role_grants_anything_over_a_foreign_project(self, db_session):
        """The old rule was "ri and ha are always privileged". Roles now govern the Knowledge Base and
        nothing else, so holding one confers no standing on somebody else's project."""
        owner = _user(db_session, "shu")
        assert authz.is_owner_or_admin(_user(db_session, "ri"), owner.id) is False
        assert authz.is_owner_or_admin(_user(db_session, "ha"), owner.id) is False

    def test_owner_may_touch_his_own_whatever_his_role(self, db_session):
        owner = _user(db_session, "shu")
        other = _user(db_session, "shu")
        assert authz.is_owner_or_admin(owner, owner.id) is True
        assert authz.is_owner_or_admin(other, owner.id) is False

    def test_only_the_admin_account_reaches_a_foreign_project(self, db_session):
        owner = _user(db_session, "shu")
        admin = _user(db_session, "shu")
        admin.username = authz.ADMIN_USERNAME  # the ACCOUNT — note its role is shu, and it does not matter
        db_session.flush()
        assert authz.is_owner_or_admin(admin, owner.id) is True


class TestAssertProjectAccess:
    def test_owner_shu_ok(self, db_session):
        owner = _user(db_session, "shu")
        project = _project(db_session, owner)
        assert authz.assert_project_id_access(db_session, owner, project.id).id == project.id

    def test_other_shu_forbidden(self, db_session):
        owner = _user(db_session, "shu")
        other = _user(db_session, "shu")
        project = _project(db_session, owner)
        with pytest.raises(HTTPException) as exc:
            authz.assert_project_id_access(db_session, other, project.id)
        assert exc.value.status_code == 403

    def test_a_role_holder_is_refused_on_a_foreign_project(self, db_session):
        owner = _user(db_session, "shu")
        project = _project(db_session, owner)
        for role in ("ha", "ri"):
            with pytest.raises(HTTPException) as exc:
                authz.assert_project_id_access(db_session, _user(db_session, role), project.id)
            assert exc.value.status_code == 403

    def test_the_admin_account_is_allowed_on_a_foreign_project(self, db_session):
        owner = _user(db_session, "shu")
        project = _project(db_session, owner)
        admin = _user(db_session, "shu")
        admin.username = authz.ADMIN_USERNAME
        db_session.flush()
        assert authz.assert_project_id_access(db_session, admin, project.id).id == project.id

    def test_missing_project_404(self, db_session):
        with pytest.raises(HTTPException) as exc:
            authz.assert_project_id_access(db_session, _user(db_session, "ri"), uuid.uuid4())
        assert exc.value.status_code == 404

    def test_by_slug(self, db_session):
        owner = _user(db_session, "shu")
        other = _user(db_session, "shu")
        project = _project(db_session, owner)
        assert authz.assert_project_slug_access(db_session, owner, project.slug).id == project.id
        with pytest.raises(HTTPException) as exc:
            authz.assert_project_slug_access(db_session, other, project.slug)
        assert exc.value.status_code == 403


class TestAssertVersionAccess:
    def test_version_walks_up_to_project_owner(self, db_session):
        owner = _user(db_session, "shu")
        other = _user(db_session, "shu")
        version = _version(db_session, _project(db_session, owner))
        assert authz.assert_version_access(db_session, owner, version.id).id == version.id
        with pytest.raises(HTTPException) as exc:
            authz.assert_version_access(db_session, other, version.id)
        assert exc.value.status_code == 403
        # the admin ACCOUNT sees it; the ri ROLE does not — the walk up to the project ends at the same
        # single rule as everything else.
        admin = _user(db_session, "shu")
        admin.username = authz.ADMIN_USERNAME
        db_session.flush()
        assert authz.assert_version_access(db_session, admin, version.id).id == version.id
        with pytest.raises(HTTPException):
            authz.assert_version_access(db_session, _user(db_session, "ri"), version.id)

    def test_missing_version_404(self, db_session):
        admin = _user(db_session, "shu")
        admin.username = authz.ADMIN_USERNAME
        db_session.flush()
        with pytest.raises(HTTPException) as exc:
            authz.assert_version_access(db_session, admin, uuid.uuid4())
        assert exc.value.status_code == 404
