"""DEV-262 — the cockpit asks for the ONE pinned project by its slug, not by paging the whole list.

30.10.2026 the Director moved a project to another user as admin; it stayed pinned for its former owner, who no longer
had it. The pin lives in the browser and nothing compared it with what the person may open. The frontend now asks
``GET /projects?slug=…`` — the same per-user filter as the list, so a project moved away answers ``total: 0`` — and a
check that paged the list (``limit: 100``) would unpin a project of an admin who owns more than one page of them.
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


def _by_slug(client, token, slug):
    resp = client.get("/api/v1/projects", params={"slug": slug}, headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    return resp.json()


def test_the_owner_finds_the_pinned_project_by_its_slug_and_only_it(client, db_session):
    owner = seed_user(db_session, username="pin-owner", password="InitPass1", role="shu")
    mine = _project(db_session, owner)
    _project(db_session, owner)

    found = _by_slug(client, login_user(client, username="pin-owner", password="InitPass1"), mine.slug)

    assert found["total"] == 1 and [row["slug"] for row in found["items"]] == [mine.slug]


def test_a_project_moved_to_another_user_is_not_found_for_the_former_owner(client, db_session):
    former = seed_user(db_session, username="pin-former", password="InitPass1", role="shu")
    new_owner = seed_user(db_session, username="pin-new", password="InitPass1", role="shu")
    moved = _project(db_session, former)
    _project(db_session, former)  # the former owner still has another project — the list is not empty
    _project(db_session, new_owner)
    moved.created_by = new_owner.id  # what the admin's move does to the owner (project_assignments keeps history)
    db_session.flush()

    gone = _by_slug(client, login_user(client, username="pin-former", password="InitPass1"), moved.slug)
    kept = _by_slug(client, login_user(client, username="pin-new", password="InitPass1"), moved.slug)

    assert gone == {**gone, "total": 0, "items": []}
    assert kept["total"] == 1


def test_the_admin_finds_any_project_by_its_slug_beyond_one_page(client, db_session):
    junior = seed_user(db_session, username="pin-jr", password="InitPass1", role="shu")
    seed_user(db_session, username="admin", password="InitPass1", role="ri")
    theirs = _project(db_session, junior)
    _project(db_session, junior)

    found = _by_slug(client, login_user(client, username="admin", password="InitPass1"), theirs.slug)

    assert found["total"] == 1 and found["items"][0]["slug"] == theirs.slug
