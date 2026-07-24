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


class TestIsOwnerOrPrivileged:
    def test_ri_and_ha_always_privileged(self, db_session):
        owner = _user(db_session, "shu")
        other_created_by = owner.id
        assert authz.is_owner_or_privileged(_user(db_session, "ri"), other_created_by) is True
        assert authz.is_owner_or_privileged(_user(db_session, "ha"), other_created_by) is True

    def test_shu_only_owns_own(self, db_session):
        owner = _user(db_session, "shu")
        other = _user(db_session, "shu")
        assert authz.is_owner_or_privileged(owner, owner.id) is True
        assert authz.is_owner_or_privileged(other, owner.id) is False


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

    def test_privileged_ok_on_others_project(self, db_session):
        owner = _user(db_session, "shu")
        project = _project(db_session, owner)
        assert authz.assert_project_id_access(db_session, _user(db_session, "ha"), project.id).id == project.id
        assert authz.assert_project_id_access(db_session, _user(db_session, "ri"), project.id).id == project.id

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
        # privileged sees it
        assert authz.assert_version_access(db_session, _user(db_session, "ri"), version.id).id == version.id

    def test_missing_version_404(self, db_session):
        with pytest.raises(HTTPException) as exc:
            authz.assert_version_access(db_session, _user(db_session, "ri"), uuid.uuid4())
        assert exc.value.status_code == 404
