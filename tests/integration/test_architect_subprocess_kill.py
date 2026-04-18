"""Integration test — Claude subprocess kill on session close mid-stream.

E2E scenarios:
    1. Start SSE stream → close session mid-stream → subprocess killed
    2. Partial message content NOT stored as assistant message
    3. Session transitions to closed despite interrupted stream
"""

from __future__ import annotations

import asyncio
import json
import uuid
from unittest.mock import patch

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
        username=f"kill_ri_{uuid.uuid4().hex[:6]}",
        email=f"kill_{uuid.uuid4().hex[:6]}@isnex.eu",
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
        name=f"Kill Test {suffix}",
        slug=f"kill-test-{suffix}",
        category="multimodule",
        description="Subprocess kill integration test",
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
        content="# Foundation DESIGN\n\nKill test architecture.",
        version=1,
        approved_by=_seed_ri_user.id,
    )
    db_session.add(doc)
    db_session.flush()
    return doc


@pytest.fixture()
def kill_client(db_session):
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
    return {"Authorization": f"Bearer {token}"}


def _create_session(client: TestClient, project_id, headers) -> str:
    """Create an architect session and return its id."""
    resp = client.post(
        f"/api/v1/projects/{project_id}/architect",
        json={},
        headers=headers,
    )
    assert resp.status_code == 201
    return resp.json()["id"]


def _parse_sse_events(response_text: str) -> list[dict]:
    """Parse SSE text into a list of event dicts."""
    events = []
    for line in response_text.splitlines():
        if line.startswith("data: "):
            events.append(json.loads(line[6:]))
    return events


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestSubprocessKillOnDisconnect:
    """Simulate client disconnect / generator cancellation mid-stream.

    Since TestClient consumes the full SSE response synchronously, we
    simulate the "kill" scenario by having the mock generator raise
    a cancellation error after emitting partial chunks, mimicking what
    happens when the ASGI server cancels the generator on client disconnect.
    """

    def test_cancelled_stream_no_assistant_message(
        self,
        kill_client,
        db_session,
        _seed_ri_user,
        _seed_project,
        _seed_foundation_design,
    ):
        """When stream is cancelled (GeneratorExit), no assistant message is stored.

        The SSE generator catches RuntimeError/TimeoutError but
        GeneratorExit propagates up, preventing the persistence block
        from executing. We simulate this by raising an exception mid-stream.
        """
        token = _login(kill_client, _seed_ri_user.username, "TestPass1")
        headers = _auth_headers(token)
        session_id = _create_session(kill_client, _seed_project.id, headers)

        async def mock_stream_then_cancel(prompt, context=None, timeout=None):
            """Yield partial chunks then raise asyncio.CancelledError."""
            yield "Partial response"
            yield " that gets"
            raise asyncio.CancelledError()

        with (
            patch(
                "backend.api.routes.architect.claude_subprocess.run_claude_stream",
                side_effect=mock_stream_then_cancel,
            ),
            patch(
                "backend.api.routes.architect.SessionLocal",
                return_value=db_session,
            ),
        ):
            original_close = db_session.close
            db_session.close = lambda: None
            try:
                kill_client.post(
                    f"/api/v1/architect/sessions/{session_id}/message",
                    json={"content": "Will be cancelled"},
                    headers=headers,
                )
            finally:
                db_session.close = original_close

        # The response may have partial chunks but stream was interrupted
        # Check that no assistant message was persisted
        db_session.expire_all()
        messages = (
            db_session.query(ArchitectMessage)
            .filter(ArchitectMessage.session_id == uuid.UUID(session_id))
            .order_by(ArchitectMessage.created_at.asc())
            .all()
        )
        # Only the user message should exist
        assistant_msgs = [m for m in messages if m.role == "assistant"]
        assert len(assistant_msgs) == 0

    def test_partial_chunks_streamed_before_kill(
        self,
        kill_client,
        db_session,
        _seed_ri_user,
        _seed_project,
        _seed_foundation_design,
    ):
        """Chunks emitted before cancellation are present in SSE output."""
        token = _login(kill_client, _seed_ri_user.username, "TestPass1")
        headers = _auth_headers(token)
        session_id = _create_session(kill_client, _seed_project.id, headers)

        async def mock_stream_partial(prompt, context=None, timeout=None):
            yield "Chunk one"
            yield "Chunk two"
            # Simulate kill via TimeoutError (more realistic than CancelledError
            # since the SSE generator explicitly catches this)
            raise TimeoutError("Process killed after timeout")

        with (
            patch(
                "backend.api.routes.architect.claude_subprocess.run_claude_stream",
                side_effect=mock_stream_partial,
            ),
            patch(
                "backend.api.routes.architect.SessionLocal",
                return_value=db_session,
            ),
        ):
            original_close = db_session.close
            db_session.close = lambda: None
            try:
                resp = kill_client.post(
                    f"/api/v1/architect/sessions/{session_id}/message",
                    json={"content": "Partial stream test"},
                    headers=headers,
                )
            finally:
                db_session.close = original_close

        assert resp.status_code == 200
        events = _parse_sse_events(resp.text)

        # Chunks before kill should be in SSE output
        chunk_events = [e for e in events if e["type"] == "chunk"]
        assert len(chunk_events) == 2
        assert chunk_events[0]["content"] == "Chunk one"
        assert chunk_events[1]["content"] == "Chunk two"

        # Error event should follow
        error_events = [e for e in events if e["type"] == "error"]
        assert len(error_events) == 1
        assert "killed" in error_events[0]["content"].lower() or "timeout" in error_events[0]["content"].lower()

    def test_session_stays_active_after_kill(
        self,
        kill_client,
        db_session,
        _seed_ri_user,
        _seed_project,
        _seed_foundation_design,
    ):
        """Session remains active after subprocess is killed — user can retry."""
        token = _login(kill_client, _seed_ri_user.username, "TestPass1")
        headers = _auth_headers(token)
        session_id = _create_session(kill_client, _seed_project.id, headers)

        async def mock_kill(prompt, context=None, timeout=None):
            yield "Some data"
            raise TimeoutError("Killed")

        with (
            patch(
                "backend.api.routes.architect.claude_subprocess.run_claude_stream",
                side_effect=mock_kill,
            ),
            patch(
                "backend.api.routes.architect.SessionLocal",
                return_value=db_session,
            ),
        ):
            original_close = db_session.close
            db_session.close = lambda: None
            try:
                kill_client.post(
                    f"/api/v1/architect/sessions/{session_id}/message",
                    json={"content": "Kill test"},
                    headers=headers,
                )
            finally:
                db_session.close = original_close

        db_session.expire_all()
        session_obj = db_session.get(ArchitectSession, uuid.UUID(session_id))
        assert session_obj.status == "active"
        assert session_obj.closed_at is None

    def test_close_session_after_failed_stream(
        self,
        kill_client,
        db_session,
        _seed_ri_user,
        _seed_project,
        _seed_foundation_design,
    ):
        """Session can be explicitly closed after a failed/killed stream."""
        token = _login(kill_client, _seed_ri_user.username, "TestPass1")
        headers = _auth_headers(token)
        session_id = _create_session(kill_client, _seed_project.id, headers)

        async def mock_fail(prompt, context=None, timeout=None):
            raise RuntimeError("Claude CLI exited with code 137: killed by signal")
            yield  # pragma: no cover

        with (
            patch(
                "backend.api.routes.architect.claude_subprocess.run_claude_stream",
                side_effect=mock_fail,
            ),
            patch(
                "backend.api.routes.architect.SessionLocal",
                return_value=db_session,
            ),
        ):
            original_close = db_session.close
            db_session.close = lambda: None
            try:
                kill_client.post(
                    f"/api/v1/architect/sessions/{session_id}/message",
                    json={"content": "Will fail"},
                    headers=headers,
                )
            finally:
                db_session.close = original_close

        # Now close the session explicitly
        close_resp = kill_client.post(
            f"/api/v1/architect/sessions/{session_id}/close",
            headers=headers,
        )
        assert close_resp.status_code == 200
        close_data = close_resp.json()
        assert close_data["status"] == "closed"
        assert close_data["closed_at"] is not None

        # Verify only user message exists — no partial assistant message
        db_session.expire_all()
        messages = db_session.query(ArchitectMessage).filter(ArchitectMessage.session_id == uuid.UUID(session_id)).all()
        assert all(m.role == "user" for m in messages)

    def test_retry_after_killed_stream_succeeds(
        self,
        kill_client,
        db_session,
        _seed_ri_user,
        _seed_project,
        _seed_foundation_design,
    ):
        """After a killed stream, sending a new message succeeds normally."""
        token = _login(kill_client, _seed_ri_user.username, "TestPass1")
        headers = _auth_headers(token)
        session_id = _create_session(kill_client, _seed_project.id, headers)

        # First attempt: fails
        async def mock_fail(prompt, context=None, timeout=None):
            raise RuntimeError("Claude CLI exited with code 1: crash")
            yield  # pragma: no cover

        with (
            patch(
                "backend.api.routes.architect.claude_subprocess.run_claude_stream",
                side_effect=mock_fail,
            ),
            patch(
                "backend.api.routes.architect.SessionLocal",
                return_value=db_session,
            ),
        ):
            original_close = db_session.close
            db_session.close = lambda: None
            try:
                kill_client.post(
                    f"/api/v1/architect/sessions/{session_id}/message",
                    json={"content": "First attempt fails"},
                    headers=headers,
                )
            finally:
                db_session.close = original_close

        # Second attempt: succeeds
        async def mock_success(prompt, context=None, timeout=None):
            yield "Success response"

        with (
            patch(
                "backend.api.routes.architect.claude_subprocess.run_claude_stream",
                side_effect=mock_success,
            ),
            patch(
                "backend.api.routes.architect.SessionLocal",
                return_value=db_session,
            ),
        ):
            original_close = db_session.close
            db_session.close = lambda: None
            try:
                resp = kill_client.post(
                    f"/api/v1/architect/sessions/{session_id}/message",
                    json={"content": "Retry after kill"},
                    headers=headers,
                )
            finally:
                db_session.close = original_close

        assert resp.status_code == 200
        events = _parse_sse_events(resp.text)

        done_events = [e for e in events if e["type"] == "done"]
        assert len(done_events) == 1
        assert done_events[0]["content"] == "Success response"

        # Verify DB: 2 user messages + 1 assistant message (only from retry)
        db_session.expire_all()
        messages = (
            db_session.query(ArchitectMessage)
            .filter(ArchitectMessage.session_id == uuid.UUID(session_id))
            .order_by(ArchitectMessage.created_at.asc())
            .all()
        )
        user_msgs = [m for m in messages if m.role == "user"]
        assistant_msgs = [m for m in messages if m.role == "assistant"]
        assert len(user_msgs) == 2
        assert len(assistant_msgs) == 1
        assert assistant_msgs[0].content == "Success response"
