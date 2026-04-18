"""Integration test — Architect authorization + role guards.

E2E scenario:
    1. ha attempts to create session → 403
    2. ri creates session → 201
    3. ha attempts to send message → 403
    4. ri sends message → 200
    5. ha reads messages → 200 (read-only access allowed)
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import patch

import bcrypt
import pytest
from fastapi.testclient import TestClient

from backend.db.models.foundation import User, UserSession
from backend.db.models.projects import Project
from backend.db.models.specifications import DesignDocument
from backend.db.session import get_db
from backend.main import app

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PASSWORD = "TestPass1"


def _make_user(db_session, *, role: str, prefix: str) -> User:
    """Seed a user with bcrypt-hashed password and UserSession."""
    pw_hash = bcrypt.hashpw(_PASSWORD.encode(), bcrypt.gensalt(rounds=4)).decode()
    user = User(
        username=f"{prefix}_{uuid.uuid4().hex[:6]}",
        email=f"{prefix}_{uuid.uuid4().hex[:6]}@isnex.eu",
        password_hash=pw_hash,
        role=role,
        is_active=True,
    )
    db_session.add(user)
    db_session.flush()

    session = UserSession(user_id=user.id, token_version=0)
    db_session.add(session)
    db_session.flush()
    return user


def _login(client: TestClient, username: str) -> str:
    """Login and return the JWT access token."""
    resp = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": _PASSWORD},
    )
    assert resp.status_code == 200, f"Login failed: {resp.text}"
    return resp.json()["access_token"]


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def ri_user(db_session) -> User:
    return _make_user(db_session, role="ri", prefix="authz_ri")


@pytest.fixture()
def ha_user(db_session) -> User:
    return _make_user(db_session, role="ha", prefix="authz_ha")


@pytest.fixture()
def project_with_members(db_session, ri_user, ha_user) -> Project:
    """Project with both ri and ha as members + foundation DESIGN.md."""
    suffix = uuid.uuid4().hex[:6]
    project = Project(
        name=f"Authz Test {suffix}",
        slug=f"authz-test-{suffix}",
        category="multimodule",
        description="Authorization integration test",
        created_by=ri_user.id,
    )
    db_session.add(project)
    db_session.flush()

    # Foundation DESIGN.md — prerequisite for Architect context
    db_session.add(
        DesignDocument(
            project_id=project.id,
            module_id=None,
            doc_type="design",
            content="# Foundation DESIGN\n\nAuthorization test project.",
            version=1,
            approved_by=ri_user.id,
        )
    )
    db_session.flush()
    return project


@pytest.fixture()
def authz_client(db_session):
    """TestClient wired to the real app with SAVEPOINT-isolated DB."""

    def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db

    with TestClient(app, raise_server_exceptions=False) as c:
        yield c

    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestArchitectRoleGuards:
    """Verify ri-only guards and ha read-only access across Architect endpoints."""

    def test_ha_cannot_create_session(
        self,
        authz_client,
        ha_user,
        project_with_members,
    ):
        """ha role → POST /projects/{id}/architect → 403."""
        token = _login(authz_client, ha_user.username)
        resp = authz_client.post(
            f"/api/v1/projects/{project_with_members.id}/architect",
            json={},
            headers=_auth_headers(token),
        )
        assert resp.status_code == 403

    def test_ri_creates_session(
        self,
        authz_client,
        ri_user,
        project_with_members,
    ):
        """ri role → POST /projects/{id}/architect → 201."""
        token = _login(authz_client, ri_user.username)
        resp = authz_client.post(
            f"/api/v1/projects/{project_with_members.id}/architect",
            json={},
            headers=_auth_headers(token),
        )
        assert resp.status_code == 201
        assert resp.json()["status"] == "active"

    def test_ha_cannot_send_message(
        self,
        authz_client,
        db_session,
        ri_user,
        ha_user,
        project_with_members,
    ):
        """ha role → POST .../message → 403 (ri creates session first)."""
        ri_token = _login(authz_client, ri_user.username)

        # ri creates session
        create_resp = authz_client.post(
            f"/api/v1/projects/{project_with_members.id}/architect",
            json={},
            headers=_auth_headers(ri_token),
        )
        assert create_resp.status_code == 201
        session_id = create_resp.json()["id"]

        # ha attempts to send a message → 403
        ha_token = _login(authz_client, ha_user.username)
        msg_resp = authz_client.post(
            f"/api/v1/architect/sessions/{session_id}/message",
            json={"content": "Should be denied"},
            headers=_auth_headers(ha_token),
        )
        assert msg_resp.status_code == 403

    def test_ri_sends_message(
        self,
        authz_client,
        db_session,
        ri_user,
        project_with_members,
    ):
        """ri role → POST .../message → 200 (SSE stream)."""
        token = _login(authz_client, ri_user.username)
        headers = _auth_headers(token)

        # Create session
        create_resp = authz_client.post(
            f"/api/v1/projects/{project_with_members.id}/architect",
            json={},
            headers=headers,
        )
        session_id = create_resp.json()["id"]

        # Send message with mocked Claude
        async def mock_stream(prompt, context=None, timeout=None):
            yield "Authz test response"

        with (
            patch(
                "backend.api.routes.architect.claude_subprocess.run_claude_stream",
                side_effect=mock_stream,
            ),
            patch(
                "backend.api.routes.architect.SessionLocal",
                return_value=db_session,
            ),
        ):
            original_close = db_session.close
            db_session.close = lambda: None
            try:
                resp = authz_client.post(
                    f"/api/v1/architect/sessions/{session_id}/message",
                    json={"content": "Test message from ri"},
                    headers=headers,
                )
            finally:
                db_session.close = original_close

        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers.get("content-type", "")

        # Verify done event present
        events = []
        for line in resp.text.splitlines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
        done_events = [e for e in events if e["type"] == "done"]
        assert len(done_events) == 1
        assert done_events[0]["content"] == "Authz test response"

    def test_ha_reads_messages(
        self,
        authz_client,
        db_session,
        ri_user,
        ha_user,
        project_with_members,
    ):
        """ha role → GET .../messages → 200 (read-only access allowed)."""
        ri_token = _login(authz_client, ri_user.username)
        ri_headers = _auth_headers(ri_token)

        # ri creates session and sends message
        create_resp = authz_client.post(
            f"/api/v1/projects/{project_with_members.id}/architect",
            json={},
            headers=ri_headers,
        )
        session_id = create_resp.json()["id"]

        async def mock_stream(prompt, context=None, timeout=None):
            yield "Visible to ha"

        with (
            patch(
                "backend.api.routes.architect.claude_subprocess.run_claude_stream",
                side_effect=mock_stream,
            ),
            patch(
                "backend.api.routes.architect.SessionLocal",
                return_value=db_session,
            ),
        ):
            original_close = db_session.close
            db_session.close = lambda: None
            try:
                authz_client.post(
                    f"/api/v1/architect/sessions/{session_id}/message",
                    json={"content": "Message from ri"},
                    headers=ri_headers,
                )
            finally:
                db_session.close = original_close

        # ha reads messages → 200
        ha_token = _login(authz_client, ha_user.username)
        list_resp = authz_client.get(
            f"/api/v1/architect/sessions/{session_id}/messages",
            headers=_auth_headers(ha_token),
        )
        assert list_resp.status_code == 200
        items = list_resp.json()["items"]
        assert len(items) == 2
        assert items[0]["role"] == "user"
        assert items[1]["role"] == "assistant"
        assert items[1]["content"] == "Visible to ha"

    def test_ha_cannot_close_session(
        self,
        authz_client,
        ri_user,
        ha_user,
        project_with_members,
    ):
        """ha role → POST .../close → 403."""
        ri_token = _login(authz_client, ri_user.username)

        # ri creates session
        create_resp = authz_client.post(
            f"/api/v1/projects/{project_with_members.id}/architect",
            json={},
            headers=_auth_headers(ri_token),
        )
        session_id = create_resp.json()["id"]

        # ha attempts to close → 403
        ha_token = _login(authz_client, ha_user.username)
        close_resp = authz_client.post(
            f"/api/v1/architect/sessions/{session_id}/close",
            headers=_auth_headers(ha_token),
        )
        assert close_resp.status_code == 403

    def test_ha_can_list_sessions(
        self,
        authz_client,
        ri_user,
        ha_user,
        project_with_members,
    ):
        """ha role → GET /projects/{id}/architect → 200 (read access)."""
        ri_token = _login(authz_client, ri_user.username)

        # ri creates a session
        authz_client.post(
            f"/api/v1/projects/{project_with_members.id}/architect",
            json={},
            headers=_auth_headers(ri_token),
        )

        # ha lists sessions → 200
        ha_token = _login(authz_client, ha_user.username)
        list_resp = authz_client.get(
            f"/api/v1/projects/{project_with_members.id}/architect",
            headers=_auth_headers(ha_token),
        )
        assert list_resp.status_code == 200
        assert list_resp.json()["total"] >= 1

    def test_ha_can_get_session_detail(
        self,
        authz_client,
        ri_user,
        ha_user,
        project_with_members,
    ):
        """ha role → GET /architect/sessions/{id} → 200 (read access)."""
        ri_token = _login(authz_client, ri_user.username)

        create_resp = authz_client.post(
            f"/api/v1/projects/{project_with_members.id}/architect",
            json={},
            headers=_auth_headers(ri_token),
        )
        session_id = create_resp.json()["id"]

        ha_token = _login(authz_client, ha_user.username)
        detail_resp = authz_client.get(
            f"/api/v1/architect/sessions/{session_id}",
            headers=_auth_headers(ha_token),
        )
        assert detail_resp.status_code == 200
        assert detail_resp.json()["id"] == session_id
