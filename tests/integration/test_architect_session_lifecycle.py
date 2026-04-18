"""Integration test — Architect session lifecycle + SSE streaming.

E2E scenario:
    1. Login as ri → create project → add membership
    2. Create foundation DESIGN.md (prerequisite for Architect)
    3. Create architect session → verify status=active
    4. Send message → verify SSE stream yields chunks + done event
    5. Verify user + assistant messages stored in DB with token counts
    6. Close session → verify status=closed, closed_at stamped
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, patch

import bcrypt
import pytest
from fastapi.testclient import TestClient

from backend.db.models.architect import ArchitectMessage, ArchitectSession
from backend.db.models.foundation import User, UserSession
from backend.db.models.projects import Project
from backend.db.models.specifications import DesignDocument
from backend.db.session import get_db
from backend.main import app

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def _seed_ri_user(db_session) -> User:
    """Seed an ri-role user with bcrypt-hashed password."""
    password_hash = bcrypt.hashpw(b"TestPass1", bcrypt.gensalt(rounds=4)).decode()
    user = User(
        username=f"architect_ri_{uuid.uuid4().hex[:6]}",
        email=f"arch_{uuid.uuid4().hex[:6]}@isnex.eu",
        password_hash=password_hash,
        role="ri",
        is_active=True,
    )
    db_session.add(user)
    db_session.flush()

    session = UserSession(user_id=user.id, token_version=0)
    db_session.add(session)
    db_session.flush()
    return user


@pytest.fixture()
def _seed_project(db_session, _seed_ri_user) -> Project:
    """Create a project owned by the ri user."""
    suffix = uuid.uuid4().hex[:6]
    project = Project(
        name=f"Arch Test {suffix}",
        slug=f"arch-test-{suffix}",
        category="multimodule",
        description="Architect lifecycle integration test",
        created_by=_seed_ri_user.id,
    )
    db_session.add(project)
    db_session.flush()
    return project


@pytest.fixture()
def _seed_foundation_design(db_session, _seed_project, _seed_ri_user) -> DesignDocument:
    """Create foundation DESIGN.md — prerequisite for architect context."""
    doc = DesignDocument(
        project_id=_seed_project.id,
        module_id=None,
        doc_type="design",
        content="# Foundation DESIGN\n\nProject architecture overview.",
        version=1,
        approved_by=_seed_ri_user.id,
    )
    db_session.add(doc)
    db_session.flush()
    return doc


@pytest.fixture()
def lifecycle_client(db_session):
    """TestClient wired to the real app with SAVEPOINT-isolated DB."""

    def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db

    with TestClient(app, raise_server_exceptions=False) as c:
        yield c

    app.dependency_overrides.clear()


def _login(client: TestClient, username: str, password: str) -> str:
    """Login and return the JWT access token."""
    resp = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
    )
    assert resp.status_code == 200, f"Login failed: {resp.text}"
    return resp.json()["access_token"]


def _auth_headers(token: str) -> dict[str, str]:
    """Build Authorization header dict."""
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestArchitectSessionLifecycle:
    """Full lifecycle: create → message → stream → close."""

    def test_create_session_active(
        self,
        lifecycle_client,
        db_session,
        _seed_ri_user,
        _seed_project,
        _seed_foundation_design,
    ):
        """POST /projects/{id}/architect creates an active session."""
        token = _login(lifecycle_client, _seed_ri_user.username, "TestPass1")

        resp = lifecycle_client.post(
            f"/api/v1/projects/{_seed_project.id}/architect",
            json={},
            headers=_auth_headers(token),
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["status"] == "active"
        assert data["project_id"] == str(_seed_project.id)
        assert data["created_by"] == str(_seed_ri_user.id)
        assert data["module_id"] is None
        assert data["closed_at"] is None

        # Verify in DB
        db_session.expire_all()
        session_obj = db_session.get(ArchitectSession, uuid.UUID(data["id"]))
        assert session_obj is not None
        assert session_obj.status == "active"

    def test_full_lifecycle_create_message_stream_close(
        self,
        lifecycle_client,
        db_session,
        _seed_ri_user,
        _seed_project,
        _seed_foundation_design,
    ):
        """E2E: create session → send message (mock Claude) → SSE chunks → close."""
        token = _login(lifecycle_client, _seed_ri_user.username, "TestPass1")
        headers = _auth_headers(token)

        # Step 1: Create session
        create_resp = lifecycle_client.post(
            f"/api/v1/projects/{_seed_project.id}/architect",
            json={},
            headers=headers,
        )
        assert create_resp.status_code == 201
        session_id = create_resp.json()["id"]

        # Step 2: Send message with mocked Claude subprocess
        mock_chunks = ["Hello", ", this is", " the Architect", " response."]

        async def mock_stream(prompt, context=None, timeout=None):
            for chunk in mock_chunks:
                yield chunk

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
            # Prevent the SSE generator from calling close() on our test session
            original_close = db_session.close
            db_session.close = lambda: None
            try:
                stream_resp = lifecycle_client.post(
                    f"/api/v1/architect/sessions/{session_id}/message",
                    json={"content": "Vygeneruj implementacny plan"},
                    headers=headers,
                )
            finally:
                db_session.close = original_close

        assert stream_resp.status_code == 200
        assert "text/event-stream" in stream_resp.headers.get("content-type", "")

        # Parse SSE events
        events = []
        for line in stream_resp.text.splitlines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))

        # Verify chunk events
        chunk_events = [e for e in events if e["type"] == "chunk"]
        assert len(chunk_events) == len(mock_chunks)
        for i, evt in enumerate(chunk_events):
            assert evt["content"] == mock_chunks[i]

        # Verify done event
        done_events = [e for e in events if e["type"] == "done"]
        assert len(done_events) == 1
        assert done_events[0]["content"] == "".join(mock_chunks)
        assert "tokens" in done_events[0]

        # Step 3: Verify messages stored in DB
        db_session.expire_all()
        messages = (
            db_session.query(ArchitectMessage)
            .filter(ArchitectMessage.session_id == uuid.UUID(session_id))
            .order_by(ArchitectMessage.created_at.asc())
            .all()
        )
        assert len(messages) == 2

        user_msg = messages[0]
        assert user_msg.role == "user"
        assert user_msg.content == "Vygeneruj implementacny plan"

        assistant_msg = messages[1]
        assert assistant_msg.role == "assistant"
        assert assistant_msg.content == "Hello, this is the Architect response."

        # Step 4: Close session
        close_resp = lifecycle_client.post(
            f"/api/v1/architect/sessions/{session_id}/close",
            headers=headers,
        )
        assert close_resp.status_code == 200
        close_data = close_resp.json()
        assert close_data["status"] == "closed"
        assert close_data["closed_at"] is not None

        # Verify in DB
        db_session.expire_all()
        closed_session = db_session.get(ArchitectSession, uuid.UUID(session_id))
        assert closed_session.status == "closed"
        assert closed_session.closed_at is not None

    def test_close_session_sets_closed_status(
        self,
        lifecycle_client,
        db_session,
        _seed_ri_user,
        _seed_project,
        _seed_foundation_design,
    ):
        """Closing a session transitions status to closed and stamps closed_at."""
        token = _login(lifecycle_client, _seed_ri_user.username, "TestPass1")
        headers = _auth_headers(token)

        # Create session
        create_resp = lifecycle_client.post(
            f"/api/v1/projects/{_seed_project.id}/architect",
            json={},
            headers=headers,
        )
        session_id = create_resp.json()["id"]

        # Close it
        close_resp = lifecycle_client.post(
            f"/api/v1/architect/sessions/{session_id}/close",
            headers=headers,
        )
        assert close_resp.status_code == 200
        data = close_resp.json()
        assert data["status"] == "closed"
        assert data["closed_at"] is not None

        # Verify cannot send message to closed session
        with patch(
            "backend.api.routes.architect.claude_subprocess.run_claude_stream",
            new_callable=AsyncMock,
        ):
            msg_resp = lifecycle_client.post(
                f"/api/v1/architect/sessions/{session_id}/message",
                json={"content": "Should fail"},
                headers=headers,
            )
        assert msg_resp.status_code == 409
        assert "closed" in msg_resp.json()["detail"].lower()

    def test_list_session_messages(
        self,
        lifecycle_client,
        db_session,
        _seed_ri_user,
        _seed_project,
        _seed_foundation_design,
    ):
        """Messages endpoint returns conversation in chronological order."""
        token = _login(lifecycle_client, _seed_ri_user.username, "TestPass1")
        headers = _auth_headers(token)

        # Create session
        create_resp = lifecycle_client.post(
            f"/api/v1/projects/{_seed_project.id}/architect",
            json={},
            headers=headers,
        )
        session_id = create_resp.json()["id"]

        # Send message (mock Claude)
        async def mock_stream(prompt, context=None, timeout=None):
            yield "AI answer"

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
                lifecycle_client.post(
                    f"/api/v1/architect/sessions/{session_id}/message",
                    json={"content": "Question"},
                    headers=headers,
                )
            finally:
                db_session.close = original_close

        # List messages
        list_resp = lifecycle_client.get(
            f"/api/v1/architect/sessions/{session_id}/messages",
            headers=headers,
        )
        assert list_resp.status_code == 200
        items = list_resp.json()["items"]
        assert len(items) == 2
        assert items[0]["role"] == "user"
        assert items[0]["content"] == "Question"
        assert items[1]["role"] == "assistant"
        assert items[1]["content"] == "AI answer"

    def test_get_session_detail(
        self,
        lifecycle_client,
        db_session,
        _seed_ri_user,
        _seed_project,
        _seed_foundation_design,
    ):
        """GET /architect/sessions/{id} returns the session detail."""
        token = _login(lifecycle_client, _seed_ri_user.username, "TestPass1")
        headers = _auth_headers(token)

        create_resp = lifecycle_client.post(
            f"/api/v1/projects/{_seed_project.id}/architect",
            json={},
            headers=headers,
        )
        session_id = create_resp.json()["id"]

        detail_resp = lifecycle_client.get(
            f"/api/v1/architect/sessions/{session_id}",
            headers=headers,
        )
        assert detail_resp.status_code == 200
        data = detail_resp.json()
        assert data["id"] == session_id
        assert data["status"] == "active"
        assert data["project_id"] == str(_seed_project.id)

    def test_list_project_sessions(
        self,
        lifecycle_client,
        db_session,
        _seed_ri_user,
        _seed_project,
        _seed_foundation_design,
    ):
        """GET /projects/{id}/architect lists sessions filtered by status."""
        token = _login(lifecycle_client, _seed_ri_user.username, "TestPass1")
        headers = _auth_headers(token)

        # Create two sessions
        lifecycle_client.post(
            f"/api/v1/projects/{_seed_project.id}/architect",
            json={},
            headers=headers,
        )
        resp2 = lifecycle_client.post(
            f"/api/v1/projects/{_seed_project.id}/architect",
            json={},
            headers=headers,
        )
        session2_id = resp2.json()["id"]

        # Close one
        lifecycle_client.post(
            f"/api/v1/architect/sessions/{session2_id}/close",
            headers=headers,
        )

        # List active only
        active_resp = lifecycle_client.get(
            f"/api/v1/projects/{_seed_project.id}/architect",
            params={"status": "active"},
            headers=headers,
        )
        assert active_resp.status_code == 200
        assert active_resp.json()["total"] == 1

        # List closed only
        closed_resp = lifecycle_client.get(
            f"/api/v1/projects/{_seed_project.id}/architect",
            params={"status": "closed"},
            headers=headers,
        )
        assert closed_resp.status_code == 200
        assert closed_resp.json()["total"] == 1
