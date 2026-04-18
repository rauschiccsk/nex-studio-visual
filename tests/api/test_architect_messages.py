"""Tests for Architect message endpoints — SSE streaming and message list.

Covers the endpoints added to :mod:`backend.api.routes.architect`:

* ``POST /api/v1/architect/sessions/{session_id}/message`` — SSE streaming (ri-only).
* ``GET  /api/v1/architect/sessions/{session_id}/messages`` — paginated message list.

Uses the same private-app + dependency-override pattern as
:mod:`tests.api.test_architect_sessions`.  The Claude subprocess is
mocked to avoid spawning real CLI processes during tests.
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.routes.architect import router as architect_router
from backend.core.security import get_current_user, require_ri_role
from backend.db.models.architect import ArchitectMessage, ArchitectSession
from backend.db.models.foundation import User
from backend.db.models.projects import Project, ProjectModule
from backend.db.models.specifications import DesignDocument
from backend.db.session import get_db

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_user(db_session, *, role: str = "ri") -> User:
    user = User(
        username=f"user_{uuid.uuid4().hex[:8]}",
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        password_hash="hashed_placeholder",
        role=role,
    )
    db_session.add(user)
    db_session.flush()
    return user


def _make_project(db_session, *, owner: User) -> Project:
    suffix = uuid.uuid4().hex[:8]
    project = Project(
        name=f"Project {suffix}",
        slug=f"project-{suffix}",
        category="multimodule",
        description="Test project description",
        created_by=owner.id,
    )
    db_session.add(project)
    db_session.flush()
    return project


def _make_module(db_session, *, project: Project) -> ProjectModule:
    module = ProjectModule(
        project_id=project.id,
        code=f"M{uuid.uuid4().hex[:4].upper()}",
        name=f"Module {uuid.uuid4().hex[:8]}",
        category="General",
    )
    db_session.add(module)
    db_session.flush()
    return module


def _make_session(
    db_session,
    *,
    project: Project,
    user: User,
    module: ProjectModule | None = None,
    status: str = "active",
) -> ArchitectSession:
    session_obj = ArchitectSession(
        project_id=project.id,
        created_by=user.id,
        module_id=module.id if module else None,
        status=status,
    )
    db_session.add(session_obj)
    db_session.flush()
    return session_obj


def _make_design_doc(
    db_session,
    *,
    project: Project,
    module: ProjectModule | None = None,
    doc_type: str = "design",
    content: str = "# Foundation DESIGN.md\n\nTest content.",
) -> DesignDocument:
    doc = DesignDocument(
        project_id=project.id,
        module_id=module.id if module else None,
        doc_type=doc_type,
        content=content,
        version=1,
    )
    db_session.add(doc)
    db_session.flush()
    return doc


def _make_message(
    db_session,
    *,
    session: ArchitectSession,
    role: str = "user",
    content: str = "Hello",
) -> ArchitectMessage:
    msg = ArchitectMessage(
        session_id=session.id,
        role=role,
        content=content,
    )
    db_session.add(msg)
    db_session.flush()
    return msg


# ---------------------------------------------------------------------------
# App builder + fixtures
# ---------------------------------------------------------------------------


def _build_app(db_session, *, current_user: User | None) -> FastAPI:
    """Mount the architect router on a fresh app with overrides applied."""
    app = FastAPI()
    app.include_router(architect_router, prefix="/api/v1")

    def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db

    if current_user is not None:

        def _override_get_current_user() -> User:
            return current_user

        app.dependency_overrides[get_current_user] = _override_get_current_user

        def _override_require_ri_role() -> User:
            if current_user.role != "ri":
                from fastapi import HTTPException, status

                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="This operation requires the 'ri' role",
                )
            return current_user

        app.dependency_overrides[require_ri_role] = _override_require_ri_role

    return app


@pytest.fixture()
def ri_user(db_session) -> User:
    return _make_user(db_session, role="ri")


@pytest.fixture()
def ha_user(db_session) -> User:
    return _make_user(db_session, role="ha")


@pytest.fixture()
def project(db_session, ri_user) -> Project:
    proj = _make_project(db_session, owner=ri_user)
    return proj


@pytest.fixture()
def module(db_session, project) -> ProjectModule:
    return _make_module(db_session, project=project)


@pytest.fixture()
def design_doc(db_session, project) -> DesignDocument:
    return _make_design_doc(db_session, project=project)


@pytest.fixture()
def active_session(db_session, project, ri_user) -> ArchitectSession:
    return _make_session(db_session, project=project, user=ri_user)


@pytest.fixture()
def closed_session(db_session, project, ri_user) -> ArchitectSession:
    return _make_session(db_session, project=project, user=ri_user, status="closed")


@pytest.fixture()
def ri_client(db_session, ri_user):
    app = _build_app(db_session, current_user=ri_user)
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


@pytest.fixture()
def ha_client(db_session, ha_user):
    app = _build_app(db_session, current_user=ha_user)
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


@pytest.fixture()
def anon_client(db_session):
    app = _build_app(db_session, current_user=None)
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Async generator mock helper
# ---------------------------------------------------------------------------


async def _mock_claude_stream(*args, **kwargs):
    """Mock async generator that yields two chunks."""
    yield "Hello "
    yield "from Claude!"


async def _mock_claude_stream_empty(*args, **kwargs):
    """Mock async generator that yields nothing."""
    return
    yield  # noqa: RET504 — makes this an async generator


async def _mock_claude_stream_error(*args, **kwargs):
    """Mock async generator that raises RuntimeError."""
    yield "partial"
    raise RuntimeError("Claude CLI exited with code 1: something broke")


# ---------------------------------------------------------------------------
# GET /architect/sessions/{session_id}/messages — list messages
# ---------------------------------------------------------------------------


class TestListSessionMessages:
    def test_list_empty(self, ri_client, active_session):
        resp = ri_client.get(
            f"/api/v1/architect/sessions/{active_session.id}/messages",
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["items"] == []
        assert body["total"] == 0
        assert body["skip"] == 0
        assert body["limit"] == 50

    def test_list_returns_messages_in_order(self, ri_client, db_session, active_session):
        _make_message(db_session, session=active_session, role="user", content="First")
        _make_message(db_session, session=active_session, role="assistant", content="Second")
        _make_message(db_session, session=active_session, role="user", content="Third")
        db_session.flush()

        resp = ri_client.get(
            f"/api/v1/architect/sessions/{active_session.id}/messages",
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 3
        contents = [m["content"] for m in body["items"]]
        assert contents == ["First", "Second", "Third"]

    def test_list_pagination(self, ri_client, db_session, active_session):
        for i in range(5):
            _make_message(
                db_session,
                session=active_session,
                role="user",
                content=f"Message {i}",
            )
        db_session.flush()

        resp = ri_client.get(
            f"/api/v1/architect/sessions/{active_session.id}/messages",
            params={"skip": 0, "limit": 2},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 5
        assert len(body["items"]) == 2
        assert body["skip"] == 0
        assert body["limit"] == 2

    def test_list_pagination_second_page(self, ri_client, db_session, active_session):
        for i in range(5):
            _make_message(
                db_session,
                session=active_session,
                role="user",
                content=f"Message {i}",
            )
        db_session.flush()

        page1 = ri_client.get(
            f"/api/v1/architect/sessions/{active_session.id}/messages",
            params={"skip": 0, "limit": 3},
        ).json()
        page2 = ri_client.get(
            f"/api/v1/architect/sessions/{active_session.id}/messages",
            params={"skip": 3, "limit": 3},
        ).json()

        page1_ids = {m["id"] for m in page1["items"]}
        page2_ids = {m["id"] for m in page2["items"]}
        assert page1_ids.isdisjoint(page2_ids)

    def test_list_session_not_found(self, ri_client):
        resp = ri_client.get(
            f"/api/v1/architect/sessions/{uuid.uuid4()}/messages",
        )
        assert resp.status_code == 404

    def test_list_limit_over_100_returns_422(self, ri_client, active_session):
        resp = ri_client.get(
            f"/api/v1/architect/sessions/{active_session.id}/messages",
            params={"limit": 101},
        )
        assert resp.status_code == 422

    def test_list_unauthorized(self, anon_client, active_session):
        resp = anon_client.get(
            f"/api/v1/architect/sessions/{active_session.id}/messages",
        )
        assert resp.status_code in (401, 403)


# ---------------------------------------------------------------------------
# POST /architect/sessions/{session_id}/message — SSE streaming
# ---------------------------------------------------------------------------


class TestSendArchitectMessage:
    @patch(
        "backend.api.routes.architect.claude_subprocess.run_claude_stream",
        side_effect=_mock_claude_stream,
    )
    @patch("backend.api.routes.architect.SessionLocal")
    def test_stream_success(
        self,
        mock_session_local,
        mock_stream,
        ri_client,
        db_session,
        active_session,
        design_doc,
    ):
        # Make SessionLocal return the test db_session for persist step
        mock_persist_db = mock_session_local.return_value
        mock_persist_db.commit = db_session.flush  # flush instead of real commit
        mock_persist_db.rollback = lambda: None
        mock_persist_db.close = lambda: None
        # Delegate add_message calls to the real db_session
        mock_persist_db.get = db_session.get
        mock_persist_db.add = db_session.add
        mock_persist_db.flush = db_session.flush
        mock_persist_db.execute = db_session.execute
        mock_persist_db.query = db_session.query

        resp = ri_client.post(
            f"/api/v1/architect/sessions/{active_session.id}/message",
            json={"content": "What is the architecture?"},
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")

        # Parse SSE events
        lines = resp.text.strip().split("\n\n")
        events = []
        for line in lines:
            if line.startswith("data: "):
                import json

                events.append(json.loads(line[6:]))

        # Should have chunk events + done event
        chunk_events = [e for e in events if e["type"] == "chunk"]
        done_events = [e for e in events if e["type"] == "done"]

        assert len(chunk_events) == 2
        assert chunk_events[0]["content"] == "Hello "
        assert chunk_events[1]["content"] == "from Claude!"

        assert len(done_events) == 1
        assert done_events[0]["content"] == "Hello from Claude!"
        assert done_events[0]["tokens"]["input_tokens"] is None
        assert done_events[0]["tokens"]["output_tokens"] is None

    @patch(
        "backend.api.routes.architect.claude_subprocess.run_claude_stream",
        side_effect=_mock_claude_stream,
    )
    @patch("backend.api.routes.architect.SessionLocal")
    def test_stream_persists_user_message(
        self,
        mock_session_local,
        mock_stream,
        ri_client,
        db_session,
        active_session,
        design_doc,
    ):
        mock_persist_db = mock_session_local.return_value
        mock_persist_db.commit = db_session.flush
        mock_persist_db.rollback = lambda: None
        mock_persist_db.close = lambda: None
        mock_persist_db.get = db_session.get
        mock_persist_db.add = db_session.add
        mock_persist_db.flush = db_session.flush
        mock_persist_db.execute = db_session.execute
        mock_persist_db.query = db_session.query

        resp = ri_client.post(
            f"/api/v1/architect/sessions/{active_session.id}/message",
            json={"content": "Hello Architect"},
        )
        assert resp.status_code == 200

        # Consume the stream
        _ = resp.text

        # Check user message was persisted
        from sqlalchemy import select

        msgs = list(
            db_session.execute(
                select(ArchitectMessage)
                .where(ArchitectMessage.session_id == active_session.id)
                .order_by(ArchitectMessage.created_at.asc())
            )
            .scalars()
            .all()
        )
        user_msgs = [m for m in msgs if m.role == "user"]
        assert len(user_msgs) >= 1
        assert user_msgs[0].content == "Hello Architect"

    def test_stream_session_not_found(self, ri_client):
        resp = ri_client.post(
            f"/api/v1/architect/sessions/{uuid.uuid4()}/message",
            json={"content": "Hello"},
        )
        assert resp.status_code == 404

    def test_stream_session_closed(self, ri_client, closed_session, design_doc):
        resp = ri_client.post(
            f"/api/v1/architect/sessions/{closed_session.id}/message",
            json={"content": "Hello"},
        )
        assert resp.status_code == 409

    def test_stream_empty_content_returns_422(self, ri_client, active_session):
        resp = ri_client.post(
            f"/api/v1/architect/sessions/{active_session.id}/message",
            json={"content": ""},
        )
        assert resp.status_code == 422

    def test_stream_missing_content_returns_422(self, ri_client, active_session):
        resp = ri_client.post(
            f"/api/v1/architect/sessions/{active_session.id}/message",
            json={},
        )
        assert resp.status_code == 422

    def test_stream_forbidden_for_ha(self, ha_client, active_session):
        resp = ha_client.post(
            f"/api/v1/architect/sessions/{active_session.id}/message",
            json={"content": "Hello"},
        )
        assert resp.status_code == 403

    def test_stream_unauthorized(self, anon_client, active_session):
        resp = anon_client.post(
            f"/api/v1/architect/sessions/{active_session.id}/message",
            json={"content": "Hello"},
        )
        assert resp.status_code in (401, 403)

    @patch(
        "backend.api.routes.architect.claude_subprocess.run_claude_stream",
        side_effect=_mock_claude_stream_error,
    )
    @patch("backend.api.routes.architect.SessionLocal")
    def test_stream_error_yields_error_event(
        self,
        mock_session_local,
        mock_stream,
        ri_client,
        db_session,
        active_session,
        design_doc,
    ):
        mock_persist_db = mock_session_local.return_value
        mock_persist_db.commit = db_session.flush
        mock_persist_db.rollback = lambda: None
        mock_persist_db.close = lambda: None
        mock_persist_db.get = db_session.get
        mock_persist_db.add = db_session.add
        mock_persist_db.flush = db_session.flush
        mock_persist_db.execute = db_session.execute
        mock_persist_db.query = db_session.query

        resp = ri_client.post(
            f"/api/v1/architect/sessions/{active_session.id}/message",
            json={"content": "Hello"},
        )
        assert resp.status_code == 200

        import json

        lines = resp.text.strip().split("\n\n")
        events = []
        for line in lines:
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))

        error_events = [e for e in events if e["type"] == "error"]
        assert len(error_events) == 1
        assert "something broke" in error_events[0]["content"]

        # Done event should still be emitted with partial content
        done_events = [e for e in events if e["type"] == "done"]
        assert len(done_events) == 1

    def test_stream_no_design_doc_returns_422(self, ri_client, active_session):
        """If no foundation DESIGN.md exists, context build fails → 422."""
        resp = ri_client.post(
            f"/api/v1/architect/sessions/{active_session.id}/message",
            json={"content": "Hello"},
        )
        # architect_context raises ValueError "has no foundation DESIGN.md.
        # Cannot assemble Architect context" — no "not found" → 422
        assert resp.status_code == 422
