"""HTTP ownership tests for the projects surface (v4.0.35).

A Junior (`shu`) sees + reaches ONLY their own projects; a privileged user (ri/ha) reaches every project.
Exercises the real route layer (role floor + authz ownership) end-to-end through the FastAPI client.
"""

from __future__ import annotations

import uuid

from backend.db.models.projects import Project

from .conftest import login_user, seed_user


def _project(db_session, owner) -> Project:
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


def test_junior_lists_only_own_projects(client, db_session):
    owner = seed_user(db_session, username="owner", password="InitPass1", role="shu")
    other = seed_user(db_session, username="other", password="InitPass1", role="shu")
    mine = _project(db_session, owner)
    theirs = _project(db_session, other)

    token = login_user(client, username="owner", password="InitPass1")
    resp = client.get("/api/v1/projects", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    slugs = {row["slug"] for row in resp.json()["items"]}
    assert mine.slug in slugs
    assert theirs.slug not in slugs  # never leaks another Junior's project


def test_junior_cannot_get_another_users_project(client, db_session):
    owner = seed_user(db_session, username="owner2", password="InitPass1", role="shu")
    seed_user(db_session, username="other2", password="InitPass1", role="shu")
    theirs = _project(db_session, owner)

    token = login_user(client, username="other2", password="InitPass1")
    resp = client.get(f"/api/v1/projects/{theirs.id}", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403


def test_junior_gets_own_project(client, db_session):
    owner = seed_user(db_session, username="owner3", password="InitPass1", role="shu")
    mine = _project(db_session, owner)

    token = login_user(client, username="owner3", password="InitPass1")
    resp = client.get(f"/api/v1/projects/{mine.id}", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["slug"] == mine.slug


def test_admin_sees_every_project(client, db_session):
    junior = seed_user(db_session, username="jr", password="InitPass1", role="shu")
    # The ACCOUNT named admin — the test was seeding "boss" with role ri, which used to mean the same
    # thing and now means nothing about projects at all.
    seed_user(db_session, username="admin", password="InitPass1", role="ri")
    theirs = _project(db_session, junior)

    token = login_user(client, username="admin", password="InitPass1")
    # admin lists all
    listed = client.get("/api/v1/projects", headers={"Authorization": f"Bearer {token}"})
    assert theirs.slug in {row["slug"] for row in listed.json()["items"]}
    # admin gets any
    got = client.get(f"/api/v1/projects/{theirs.id}", headers={"Authorization": f"Bearer {token}"})
    assert got.status_code == 200


def test_junior_cannot_read_another_projects_specs(client, db_session):
    """v4.0.43: /project-specs/content is owner-scoped — a non-owner Junior gets 403 before any fs read."""
    owner = seed_user(db_session, username="specowner", password="InitPass1", role="shu")
    seed_user(db_session, username="specother", password="InitPass1", role="shu")
    theirs = _project(db_session, owner)

    token = login_user(client, username="specother", password="InitPass1")
    resp = client.get(
        "/api/v1/project-specs/content",
        params={"slug": theirs.slug, "path": "docs/specs/versions/v0.1.0/specification.md"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403
