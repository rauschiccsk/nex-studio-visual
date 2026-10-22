"""Tests for the UserSession REST router.

Verifies the CRUD surface exposed by
:mod:`backend.api.routes.user_sessions` against the SAVEPOINT-isolated
test database. The router is mounted at ``/api/v1/user-sessions`` —
the same prefix it will have in production via ``backend/main.py`` —
but since this router is not yet wired into ``main.py`` (Task 4.27),
we mount it on a dedicated ``TestClient`` app here (same pattern as
:mod:`tests.test_execution_log_router`,
:mod:`tests.test_guardian_review_router` and the other sibling router
tests in this feat).

Covers (sessions are made by the login; the router lists and revokes them — DEV-155 removed its create, read-one
and patch, which no screen called):

* ``PaginatedResponse`` envelope (items / total / skip / limit).
* Pagination via ``skip`` and ``limit``.
* Filter by ``user_id``.
* 404 on a missing id (delete).
* 422 on schema validation failure (invalid ``user_id`` filter, ``limit > 100``).
* DELETE returns 204 and the row is gone afterwards.
* Deleting a session leaves sibling sessions on the same user intact.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.api.routes.user_sessions import router as user_sessions_router
from backend.db.models.foundation import User, UserSession
from backend.db.session import get_db


@pytest.fixture()
def router_client(db_session):
    """Mount the user-sessions router on a fresh app with the DB override.

    Keeping this fixture local to the router tests avoids coupling to
    the global ``main.app``, which does not yet include this router
    (Task 4.27).
    """
    app = FastAPI()
    app.include_router(user_sessions_router, prefix="/api/v1/user-sessions")

    def _override_get_db():
        yield db_session

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
        username=f"ri_m2_{_suffix_m2}",
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

    with TestClient(app) as client:
        yield client

    app.dependency_overrides.clear()


def _make_user(db_session, **overrides) -> User:
    """Persist and return a :class:`User` for FK references."""
    defaults = {
        "username": f"user_{uuid.uuid4().hex[:8]}",
        "email": f"{uuid.uuid4().hex[:8]}@example.com",
        "password_hash": "hashed_password_placeholder",
        "role": "ri",
    }
    defaults.update(overrides)
    user = User(**defaults)
    db_session.add(user)
    db_session.flush()
    return user


@pytest.fixture()
def user(db_session) -> User:
    """Persist a user that owns the test sessions."""
    return _make_user(db_session)


def _session(db_session, user_id) -> UserSession:
    """A session as the login makes it (``backend/services/auth.py``) — the router only lists and revokes them."""
    session = UserSession(user_id=user_id, token_version=1)
    db_session.add(session)
    db_session.flush()
    return session


def _exists(db_session, session_id) -> bool:
    db_session.expire_all()
    return db_session.execute(select(UserSession.id).where(UserSession.id == session_id)).first() is not None


class TestUserSessionRouter:
    """End-to-end HTTP coverage for the router."""

    # ----------------------------------------------------------- create

    # --------------------------------------------------------------- get

    # -------------------------------------------------------------- list
    def test_list_envelope_and_pagination(self, router_client, db_session, user):
        for _ in range(3):
            _session(db_session, user.id)

        resp = router_client.get(
            "/api/v1/user-sessions",
            params={"user_id": str(user.id), "skip": 0, "limit": 2},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert set(body.keys()) >= {"items", "total", "skip", "limit"}
        assert body["skip"] == 0
        assert body["limit"] == 2
        assert body["total"] == 3
        assert len(body["items"]) == 2

        page2 = router_client.get(
            "/api/v1/user-sessions",
            params={"user_id": str(user.id), "skip": 2, "limit": 2},
        ).json()
        page1_ids = {row["id"] for row in body["items"]}
        page2_ids = {row["id"] for row in page2["items"]}
        assert page1_ids.isdisjoint(page2_ids)
        assert len(page2["items"]) == 1

    def test_list_filter_by_user_id(self, router_client, db_session, user):
        # Persist a second, unrelated user to ensure the filter narrows the results.
        other = _make_user(db_session)

        _session(db_session, user.id)
        _session(db_session, other.id)

        resp = router_client.get(
            "/api/v1/user-sessions",
            params={"user_id": str(user.id)},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] >= 1
        assert all(item["user_id"] == str(user.id) for item in body["items"])

    def test_list_carries_the_owner_username(self, router_client, db_session):
        """Each listed session names its owner.

        The Relácie tab renders a "Používateľ" column and a per-row "Odvolať". It resolved the id through
        the user directory, and ``GET /users`` is ri-only while this tab is ha+ — so a Medior was shown a
        column of bare UUIDs and had to decide whether to cut someone's session without knowing whose it
        was. Resolving the name here keeps the directory closed and the column readable.
        """
        owner = _make_user(db_session, username=f"owner_{uuid.uuid4().hex[:8]}")
        _session(db_session, owner.id)

        resp = router_client.get("/api/v1/user-sessions", params={"user_id": str(owner.id)})

        assert resp.status_code == 200, resp.text
        items = resp.json()["items"]
        assert items and all(item["username"] == owner.username for item in items)

    def test_list_limit_over_100_returns_422(self, router_client):
        resp = router_client.get("/api/v1/user-sessions", params={"limit": 101})
        assert resp.status_code == 422

    def test_list_invalid_user_id_filter_returns_422(self, router_client):
        resp = router_client.get("/api/v1/user-sessions", params={"user_id": "not-a-uuid"})
        assert resp.status_code == 422

    # -------------------------------------------------------------- patch

    # ------------------------------------------------------------- delete
    def test_delete_returns_204(self, router_client, db_session, user):
        created = _session(db_session, user.id)
        resp = router_client.delete(f"/api/v1/user-sessions/{created.id}")
        assert resp.status_code == 204
        assert resp.content == b""
        assert not _exists(db_session, created.id)

    def test_delete_missing_returns_404(self, router_client):
        resp = router_client.delete(f"/api/v1/user-sessions/{uuid.uuid4()}")
        assert resp.status_code == 404

    def test_delete_leaves_sibling_sessions_intact(self, router_client, db_session, user):
        """Deleting one session of a user does not affect other sessions
        on the same user."""
        first = _session(db_session, user.id)
        second = _session(db_session, user.id)

        router_client.delete(f"/api/v1/user-sessions/{first.id}").raise_for_status()

        assert (_exists(db_session, first.id), _exists(db_session, second.id)) == (False, True)
