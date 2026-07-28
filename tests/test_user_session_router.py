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

Covers:

* Create / get / list / patch / delete happy paths.
* ``PaginatedResponse`` envelope (items / total / skip / limit).
* Pagination via ``skip`` and ``limit``.
* Filter by ``user_id``.
* 404 on missing id (get, patch, delete).
* 422 on schema validation failure (invalid ``user_id`` UUID,
  ``limit > 100``, negative ``token_version``).
* Default values applied at create time (``token_version=0``,
  ``last_seen_at`` defaults to ``NOW()`` via DB ``server_default``).
* PATCH preserves immutable identity (``id``, ``user_id``,
  ``created_at``).
* DELETE returns 204 and the row becomes unreachable afterwards.
* Deleting a session leaves sibling sessions on the same user intact.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.routes.user_sessions import router as user_sessions_router
from backend.db.models.foundation import User
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


def _payload(*, user_id, **overrides) -> dict:
    """Return a user-session create payload with sensible defaults."""
    body: dict = {"user_id": str(user_id)}
    body.update(overrides)
    return body


class TestUserSessionRouter:
    """End-to-end HTTP coverage for the router."""

    # ----------------------------------------------------------- create
    def test_create_minimal(self, router_client, user):
        resp = router_client.post("/api/v1/user-sessions", json=_payload(user_id=user.id))
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["user_id"] == str(user.id)
        # Schema / DB default.
        assert body["token_version"] == 0
        # Server-generated identifiers and timestamps.
        assert body["id"]
        assert body["created_at"]
        assert body["updated_at"]
        assert body["last_seen_at"]

    def test_create_with_explicit_token_version(self, router_client, user):
        resp = router_client.post(
            "/api/v1/user-sessions",
            json=_payload(user_id=user.id, token_version=5),
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["token_version"] == 5

    def test_create_with_explicit_last_seen_at(self, router_client, user):
        ts = datetime.now(timezone.utc) - timedelta(hours=2)
        resp = router_client.post(
            "/api/v1/user-sessions",
            json=_payload(user_id=user.id, last_seen_at=ts.isoformat()),
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        # Parse the response timestamp and compare to the supplied one.
        returned = datetime.fromisoformat(body["last_seen_at"].replace("Z", "+00:00"))
        assert abs((returned - ts).total_seconds()) < 1

    def test_create_missing_user_id_returns_422(self, router_client):
        # ``user_id`` is required by the schema.
        resp = router_client.post("/api/v1/user-sessions", json={})
        assert resp.status_code == 422

    def test_create_invalid_user_id_returns_422(self, router_client):
        # Non-UUID ``user_id`` rejected by the request schema.
        resp = router_client.post(
            "/api/v1/user-sessions",
            json={"user_id": "not-a-uuid"},
        )
        assert resp.status_code == 422

    def test_create_negative_token_version_returns_422(self, router_client, user):
        # ``token_version`` is constrained to ``ge=0`` by the schema.
        resp = router_client.post(
            "/api/v1/user-sessions",
            json=_payload(user_id=user.id, token_version=-1),
        )
        assert resp.status_code == 422

    def test_create_multiple_sessions_same_user_allowed(self, router_client, user):
        """``user_sessions`` has no UNIQUE constraint beyond the PK — a
        single user may hold many concurrent sessions (multi-device
        login)."""
        first = router_client.post("/api/v1/user-sessions", json=_payload(user_id=user.id))
        second = router_client.post("/api/v1/user-sessions", json=_payload(user_id=user.id))
        assert first.status_code == 201
        assert second.status_code == 201
        assert first.json()["id"] != second.json()["id"]

    # --------------------------------------------------------------- get
    def test_get_by_id(self, router_client, user):
        created = router_client.post("/api/v1/user-sessions", json=_payload(user_id=user.id)).json()
        resp = router_client.get(f"/api/v1/user-sessions/{created['id']}")
        assert resp.status_code == 200
        assert resp.json()["id"] == created["id"]

    def test_get_missing_returns_404(self, router_client):
        resp = router_client.get(f"/api/v1/user-sessions/{uuid.uuid4()}")
        assert resp.status_code == 404

    # -------------------------------------------------------------- list
    def test_list_envelope_and_pagination(self, router_client, user):
        for _ in range(3):
            router_client.post("/api/v1/user-sessions", json=_payload(user_id=user.id)).raise_for_status()

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

        router_client.post("/api/v1/user-sessions", json=_payload(user_id=user.id)).raise_for_status()
        router_client.post("/api/v1/user-sessions", json=_payload(user_id=other.id)).raise_for_status()

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
        router_client.post("/api/v1/user-sessions", json=_payload(user_id=owner.id)).raise_for_status()

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
    def test_patch_token_version_bump(self, router_client, user):
        """Logout path — bump ``token_version`` to invalidate outstanding JWTs."""
        created = router_client.post("/api/v1/user-sessions", json=_payload(user_id=user.id)).json()
        assert created["token_version"] == 0

        resp = router_client.patch(
            f"/api/v1/user-sessions/{created['id']}",
            json={"token_version": 1},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["token_version"] == 1
        # Immutable identity preserved.
        assert body["id"] == created["id"]
        assert body["user_id"] == created["user_id"]
        assert body["created_at"] == created["created_at"]

    def test_patch_last_seen_refresh(self, router_client, user):
        """Authenticated-request path — refresh ``last_seen_at``."""
        created = router_client.post("/api/v1/user-sessions", json=_payload(user_id=user.id)).json()
        new_ts = datetime.now(timezone.utc) + timedelta(minutes=5)

        resp = router_client.patch(
            f"/api/v1/user-sessions/{created['id']}",
            json={"last_seen_at": new_ts.isoformat()},
        )
        assert resp.status_code == 200
        body = resp.json()
        returned = datetime.fromisoformat(body["last_seen_at"].replace("Z", "+00:00"))
        assert abs((returned - new_ts).total_seconds()) < 1
        # token_version left alone.
        assert body["token_version"] == created["token_version"]

    def test_patch_omitted_fields_unchanged(self, router_client, user):
        created = router_client.post(
            "/api/v1/user-sessions",
            json=_payload(user_id=user.id, token_version=3),
        ).json()
        # Empty patch — nothing changes except the auto-stamped updated_at.
        resp = router_client.patch(
            f"/api/v1/user-sessions/{created['id']}",
            json={},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["token_version"] == 3
        assert body["last_seen_at"] == created["last_seen_at"]

    def test_patch_negative_token_version_returns_422(self, router_client, user):
        created = router_client.post("/api/v1/user-sessions", json=_payload(user_id=user.id)).json()
        resp = router_client.patch(
            f"/api/v1/user-sessions/{created['id']}",
            json={"token_version": -1},
        )
        assert resp.status_code == 422

    def test_patch_missing_returns_404(self, router_client):
        resp = router_client.patch(
            f"/api/v1/user-sessions/{uuid.uuid4()}",
            json={"token_version": 1},
        )
        assert resp.status_code == 404

    # ------------------------------------------------------------- delete
    def test_delete_returns_204(self, router_client, user):
        created = router_client.post("/api/v1/user-sessions", json=_payload(user_id=user.id)).json()
        resp = router_client.delete(f"/api/v1/user-sessions/{created['id']}")
        assert resp.status_code == 204
        assert resp.content == b""
        # Second read confirms removal.
        assert router_client.get(f"/api/v1/user-sessions/{created['id']}").status_code == 404

    def test_delete_missing_returns_404(self, router_client):
        resp = router_client.delete(f"/api/v1/user-sessions/{uuid.uuid4()}")
        assert resp.status_code == 404

    def test_delete_leaves_sibling_sessions_intact(self, router_client, user):
        """Deleting one session of a user does not affect other sessions
        on the same user."""
        first = router_client.post("/api/v1/user-sessions", json=_payload(user_id=user.id)).json()
        second = router_client.post("/api/v1/user-sessions", json=_payload(user_id=user.id)).json()

        router_client.delete(f"/api/v1/user-sessions/{first['id']}").raise_for_status()

        # Sibling is still reachable.
        resp = router_client.get(f"/api/v1/user-sessions/{second['id']}")
        assert resp.status_code == 200
