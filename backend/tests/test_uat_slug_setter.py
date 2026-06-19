"""Tests for the uat_slug write path (v0.9.0 Phase 2, CR-3).

``project_service.set_uat_slug`` is the persistent setter the engine (Phase 3) calls at
first-release: derive-when-None, idempotent, non-destructive to a manual mapping unless forced.
"""

from __future__ import annotations

import uuid

import pytest

from backend.db.models.foundation import User
from backend.db.models.projects import Project
from backend.services import project as project_service


def _make_project(db, *, slug: str, uat_slug: str | None = None) -> Project:
    creator = User(
        username=f"u_{uuid.uuid4().hex[:8]}",
        email=f"u_{uuid.uuid4().hex[:8]}@test.local",
        password_hash="x",
        role="ri",
    )
    db.add(creator)
    db.flush()
    project = Project(
        name=f"Proj {uuid.uuid4().hex[:6]}",
        slug=slug,
        category="multimodule",
        description="uat_slug setter fixture.",
        uat_slug=uat_slug,
        created_by=creator.id,
    )
    db.add(project)
    db.flush()
    return project


def test_set_uat_slug_derives_when_none(db_session):
    project = _make_project(db_session, slug="nex-ledger")
    result = project_service.set_uat_slug(db_session, project)
    assert result.uat_slug == "ledger"


def test_set_uat_slug_strips_only_leading_nex(db_session):
    project = _make_project(db_session, slug="nex-asistent")
    project_service.set_uat_slug(db_session, project)
    assert project.uat_slug == "asistent"


def test_set_uat_slug_no_prefix_unchanged(db_session):
    project = _make_project(db_session, slug="demo")
    project_service.set_uat_slug(db_session, project)
    assert project.uat_slug == "demo"


def test_set_uat_slug_explicit_value(db_session):
    project = _make_project(db_session, slug="nex-inbox")
    project_service.set_uat_slug(db_session, project, "mager")
    assert project.uat_slug == "mager"


def test_set_uat_slug_idempotent(db_session):
    project = _make_project(db_session, slug="nex-ledger")
    project_service.set_uat_slug(db_session, project)
    project_service.set_uat_slug(db_session, project)  # second call, same value
    assert project.uat_slug == "ledger"


def test_set_uat_slug_does_not_overwrite_manual_without_force(db_session):
    """A deliberate manual mapping survives the engine's auto-derive."""
    project = _make_project(db_session, slug="nex-inbox", uat_slug="mager")
    project_service.set_uat_slug(db_session, project)  # would derive "inbox"
    assert project.uat_slug == "mager"  # preserved


def test_set_uat_slug_force_overwrites_manual(db_session):
    project = _make_project(db_session, slug="nex-inbox", uat_slug="mager")
    project_service.set_uat_slug(db_session, project, force=True)  # derives "inbox"
    assert project.uat_slug == "inbox"


def test_set_uat_slug_force_with_explicit_value(db_session):
    project = _make_project(db_session, slug="nex-inbox", uat_slug="mager")
    project_service.set_uat_slug(db_session, project, "staging", force=True)
    assert project.uat_slug == "staging"


def test_set_uat_slug_rejects_invalid(db_session):
    project = _make_project(db_session, slug="nex-ledger")
    with pytest.raises(ValueError):
        project_service.set_uat_slug(db_session, project, "BAD/slug")


def test_set_uat_slug_persists_to_row(db_session):
    project = _make_project(db_session, slug="nex-ledger")
    pid = project.id
    project_service.set_uat_slug(db_session, project)
    db_session.expire_all()
    reloaded = db_session.get(Project, pid)
    assert reloaded.uat_slug == "ledger"
