"""Integration test — Claude subprocess error handling.

E2E scenarios:
    1. Claude CLI timeout → SSE stream terminates with error event
    2. Claude CLI non-zero exit (RuntimeError) → error event emitted
    3. Error message NOT stored as assistant message in DB
    4. Stderr capture forwarded in error content
"""

from __future__ import annotations

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
        username=f"err_ri_{uuid.uuid4().hex[:6]}",
        email=f"err_{uuid.uuid4().hex[:6]}@isnex.eu",
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
        name=f"Err Test {suffix}",
        slug=f"err-test-{suffix}",
        category="multimodule",
        description="Subprocess error integration test",
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
        content="# Foundation DESIGN\n\nError test architecture.",
        version=1,
        approved_by=_seed_ri_user.id,
    )
    db_session.add(doc)
    db_session.flush()
    return doc


@pytest.fixture()
def error_client(db_session):
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
class TestSubprocessTimeout:
    """Claude CLI timeout → SSE stream terminates gracefully."""

    def test_timeout_emits_error_event_and_done(
        self,
        error_client,
        db_session,
        _seed_ri_user,
        _seed_project,
        _seed_foundation_design,
    ):
        """TimeoutError from subprocess yields SSE error event + done event."""
        token = _login(error_client, _seed_ri_user.username, "TestPass1")
        headers = _auth_headers(token)
        session_id = _create_session(error_client, _seed_project.id, headers)

        async def mock_stream_timeout(prompt, context=None, timeout=None):
            # Yield one chunk then raise TimeoutError
            yield "Partial"
            raise TimeoutError("Claude CLI subprocess exceeded 300s timeout")

        with (
            patch(
                "backend.api.routes.architect.claude_subprocess.run_claude_stream",
                side_effect=mock_stream_timeout,
            ),
            patch(
                "backend.api.routes.architect.SessionLocal",
                return_value=db_session,
            ),
        ):
            original_close = db_session.close
            db_session.close = lambda: None
            try:
                resp = error_client.post(
                    f"/api/v1/architect/sessions/{session_id}/message",
                    json={"content": "Long running prompt"},
                    headers=headers,
                )
            finally:
                db_session.close = original_close

        assert resp.status_code == 200
        events = _parse_sse_events(resp.text)

        # Should have chunk, error, and done events
        chunk_events = [e for e in events if e["type"] == "chunk"]
        error_events = [e for e in events if e["type"] == "error"]
        done_events = [e for e in events if e["type"] == "done"]

        assert len(chunk_events) == 1
        assert chunk_events[0]["content"] == "Partial"

        assert len(error_events) == 1
        assert "timeout" in error_events[0]["content"].lower()

        assert len(done_events) == 1
        # Done content should be empty — error prevents full response
        # The partial content is NOT included because error_occurred = True
        # skips persistence, but done event still fires with partial content
        # collected before the error.

    def test_timeout_no_assistant_message_stored(
        self,
        error_client,
        db_session,
        _seed_ri_user,
        _seed_project,
        _seed_foundation_design,
    ):
        """On timeout, assistant message is NOT persisted to DB."""
        token = _login(error_client, _seed_ri_user.username, "TestPass1")
        headers = _auth_headers(token)
        session_id = _create_session(error_client, _seed_project.id, headers)

        async def mock_stream_timeout(prompt, context=None, timeout=None):
            yield "Some content before timeout"
            raise TimeoutError("Claude CLI subprocess exceeded 300s timeout")

        with (
            patch(
                "backend.api.routes.architect.claude_subprocess.run_claude_stream",
                side_effect=mock_stream_timeout,
            ),
            patch(
                "backend.api.routes.architect.SessionLocal",
                return_value=db_session,
            ),
        ):
            original_close = db_session.close
            db_session.close = lambda: None
            try:
                error_client.post(
                    f"/api/v1/architect/sessions/{session_id}/message",
                    json={"content": "Timeout test"},
                    headers=headers,
                )
            finally:
                db_session.close = original_close

        # Only user message should exist — no assistant message
        db_session.expire_all()
        messages = (
            db_session.query(ArchitectMessage)
            .filter(ArchitectMessage.session_id == uuid.UUID(session_id))
            .order_by(ArchitectMessage.created_at.asc())
            .all()
        )
        assert len(messages) == 1
        assert messages[0].role == "user"
        assert messages[0].content == "Timeout test"


@pytest.mark.integration
class TestSubprocessRuntimeError:
    """Claude CLI non-zero exit → RuntimeError → SSE error event."""

    def test_runtime_error_emits_error_event(
        self,
        error_client,
        db_session,
        _seed_ri_user,
        _seed_project,
        _seed_foundation_design,
    ):
        """RuntimeError from subprocess yields SSE error event with stderr content."""
        token = _login(error_client, _seed_ri_user.username, "TestPass1")
        headers = _auth_headers(token)
        session_id = _create_session(error_client, _seed_project.id, headers)

        async def mock_stream_runtime_error(prompt, context=None, timeout=None):
            # Simulate subprocess that fails immediately with no chunks
            raise RuntimeError("Claude CLI exited with code 1: authentication failed")
            # Make this an async generator by adding unreachable yield
            yield  # pragma: no cover

        with (
            patch(
                "backend.api.routes.architect.claude_subprocess.run_claude_stream",
                side_effect=mock_stream_runtime_error,
            ),
            patch(
                "backend.api.routes.architect.SessionLocal",
                return_value=db_session,
            ),
        ):
            original_close = db_session.close
            db_session.close = lambda: None
            try:
                resp = error_client.post(
                    f"/api/v1/architect/sessions/{session_id}/message",
                    json={"content": "Trigger error"},
                    headers=headers,
                )
            finally:
                db_session.close = original_close

        assert resp.status_code == 200
        events = _parse_sse_events(resp.text)

        error_events = [e for e in events if e["type"] == "error"]
        assert len(error_events) == 1
        assert "exited with code 1" in error_events[0]["content"]
        assert "authentication failed" in error_events[0]["content"]

        # Done event still emitted (with empty content since no chunks arrived)
        done_events = [e for e in events if e["type"] == "done"]
        assert len(done_events) == 1
        assert done_events[0]["content"] == ""

    def test_runtime_error_no_assistant_message_stored(
        self,
        error_client,
        db_session,
        _seed_ri_user,
        _seed_project,
        _seed_foundation_design,
    ):
        """On RuntimeError, assistant message is NOT persisted to DB."""
        token = _login(error_client, _seed_ri_user.username, "TestPass1")
        headers = _auth_headers(token)
        session_id = _create_session(error_client, _seed_project.id, headers)

        async def mock_stream_error(prompt, context=None, timeout=None):
            raise RuntimeError("Claude CLI exited with code 2: segfault")
            yield  # pragma: no cover

        with (
            patch(
                "backend.api.routes.architect.claude_subprocess.run_claude_stream",
                side_effect=mock_stream_error,
            ),
            patch(
                "backend.api.routes.architect.SessionLocal",
                return_value=db_session,
            ),
        ):
            original_close = db_session.close
            db_session.close = lambda: None
            try:
                error_client.post(
                    f"/api/v1/architect/sessions/{session_id}/message",
                    json={"content": "Error test"},
                    headers=headers,
                )
            finally:
                db_session.close = original_close

        db_session.expire_all()
        messages = (
            db_session.query(ArchitectMessage)
            .filter(ArchitectMessage.session_id == uuid.UUID(session_id))
            .order_by(ArchitectMessage.created_at.asc())
            .all()
        )
        assert len(messages) == 1
        assert messages[0].role == "user"

    def test_stderr_content_in_error_event(
        self,
        error_client,
        db_session,
        _seed_ri_user,
        _seed_project,
        _seed_foundation_design,
    ):
        """Stderr output from Claude CLI is captured in the error event content."""
        token = _login(error_client, _seed_ri_user.username, "TestPass1")
        headers = _auth_headers(token)
        session_id = _create_session(error_client, _seed_project.id, headers)

        stderr_text = "FATAL: unable to load claude config from /nonexistent"

        async def mock_stream_stderr(prompt, context=None, timeout=None):
            raise RuntimeError(f"Claude CLI exited with code 1: {stderr_text}")
            yield  # pragma: no cover

        with (
            patch(
                "backend.api.routes.architect.claude_subprocess.run_claude_stream",
                side_effect=mock_stream_stderr,
            ),
            patch(
                "backend.api.routes.architect.SessionLocal",
                return_value=db_session,
            ),
        ):
            original_close = db_session.close
            db_session.close = lambda: None
            try:
                resp = error_client.post(
                    f"/api/v1/architect/sessions/{session_id}/message",
                    json={"content": "Stderr capture test"},
                    headers=headers,
                )
            finally:
                db_session.close = original_close

        events = _parse_sse_events(resp.text)
        error_events = [e for e in events if e["type"] == "error"]
        assert len(error_events) == 1
        assert stderr_text in error_events[0]["content"]


@pytest.mark.integration
class TestSubprocessPartialChunksThenError:
    """Subprocess yields partial chunks before failing."""

    def test_partial_chunks_then_timeout(
        self,
        error_client,
        db_session,
        _seed_ri_user,
        _seed_project,
        _seed_foundation_design,
    ):
        """Partial chunks are streamed as SSE, then error event, then done."""
        token = _login(error_client, _seed_ri_user.username, "TestPass1")
        headers = _auth_headers(token)
        session_id = _create_session(error_client, _seed_project.id, headers)

        async def mock_partial_then_timeout(prompt, context=None, timeout=None):
            yield "First chunk"
            yield "Second chunk"
            raise TimeoutError("Claude CLI subprocess exceeded 300s timeout")

        with (
            patch(
                "backend.api.routes.architect.claude_subprocess.run_claude_stream",
                side_effect=mock_partial_then_timeout,
            ),
            patch(
                "backend.api.routes.architect.SessionLocal",
                return_value=db_session,
            ),
        ):
            original_close = db_session.close
            db_session.close = lambda: None
            try:
                resp = error_client.post(
                    f"/api/v1/architect/sessions/{session_id}/message",
                    json={"content": "Partial then timeout"},
                    headers=headers,
                )
            finally:
                db_session.close = original_close

        events = _parse_sse_events(resp.text)

        # Both partial chunks should have been streamed
        chunk_events = [e for e in events if e["type"] == "chunk"]
        assert len(chunk_events) == 2
        assert chunk_events[0]["content"] == "First chunk"
        assert chunk_events[1]["content"] == "Second chunk"

        # Error event present
        error_events = [e for e in events if e["type"] == "error"]
        assert len(error_events) == 1

        # Done event present (with partial content collected before error)
        done_events = [e for e in events if e["type"] == "done"]
        assert len(done_events) == 1
        assert done_events[0]["content"] == "First chunkSecond chunk"

        # No assistant message persisted (error_occurred = True)
        db_session.expire_all()
        messages = db_session.query(ArchitectMessage).filter(ArchitectMessage.session_id == uuid.UUID(session_id)).all()
        assistant_msgs = [m for m in messages if m.role == "assistant"]
        assert len(assistant_msgs) == 0


@pytest.mark.integration
class TestSessionRemainsActiveAfterError:
    """Session stays active after subprocess error — user can retry."""

    def test_session_active_after_error(
        self,
        error_client,
        db_session,
        _seed_ri_user,
        _seed_project,
        _seed_foundation_design,
    ):
        """Session status remains 'active' after a subprocess error."""
        token = _login(error_client, _seed_ri_user.username, "TestPass1")
        headers = _auth_headers(token)
        session_id = _create_session(error_client, _seed_project.id, headers)

        async def mock_error(prompt, context=None, timeout=None):
            raise RuntimeError("Claude CLI exited with code 1: crash")
            yield  # pragma: no cover

        with (
            patch(
                "backend.api.routes.architect.claude_subprocess.run_claude_stream",
                side_effect=mock_error,
            ),
            patch(
                "backend.api.routes.architect.SessionLocal",
                return_value=db_session,
            ),
        ):
            original_close = db_session.close
            db_session.close = lambda: None
            try:
                error_client.post(
                    f"/api/v1/architect/sessions/{session_id}/message",
                    json={"content": "Should not close session"},
                    headers=headers,
                )
            finally:
                db_session.close = original_close

        # Session should still be active
        db_session.expire_all()
        session_obj = db_session.get(ArchitectSession, uuid.UUID(session_id))
        assert session_obj.status == "active"
        assert session_obj.closed_at is None
