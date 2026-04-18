"""Integration test — Architect context assembly includes Foundation + Module documents.

Verifies that the context assembled for an Architect session correctly
includes Foundation DESIGN.md, Foundation BEHAVIOR.md, Module DESIGN.md,
Module BEHAVIOR.md, and the module registry — matching the assembly logic
in ``backend.services.architect_context.build_architect_context``.

Tests exercise the full HTTP path: login → create project → create docs →
create session → send message → inspect the context passed to Claude.
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import bcrypt
import pytest
from fastapi.testclient import TestClient

from backend.db.models.foundation import User, UserSession
from backend.db.models.projects import Project, ProjectModule
from backend.db.models.specifications import DesignDocument
from backend.db.session import get_db
from backend.main import app

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def ri_user(db_session) -> User:
    """Create an ri-role user with bcrypt password."""
    password_hash = bcrypt.hashpw(b"CtxPass1", bcrypt.gensalt(rounds=4)).decode()
    user = User(
        username=f"ctx_ri_{uuid.uuid4().hex[:6]}",
        email=f"ctx_{uuid.uuid4().hex[:6]}@isnex.eu",
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
def project(db_session, ri_user) -> Project:
    """Create a project with membership."""
    suffix = uuid.uuid4().hex[:6]
    proj = Project(
        name=f"Ctx Test {suffix}",
        slug=f"ctx-test-{suffix}",
        category="multimodule",
        description="Context assembly integration test",
        created_by=ri_user.id,
    )
    db_session.add(proj)
    db_session.flush()
    return proj


@pytest.fixture()
def foundation_design(db_session, project, ri_user) -> DesignDocument:
    """Foundation DESIGN.md document."""
    doc = DesignDocument(
        project_id=project.id,
        module_id=None,
        doc_type="design",
        content="# Foundation DESIGN\n\nCore architecture for the project.",
        version=1,
        approved_by=ri_user.id,
    )
    db_session.add(doc)
    db_session.flush()
    return doc


@pytest.fixture()
def foundation_behavior(db_session, project, ri_user) -> DesignDocument:
    """Foundation BEHAVIOR.md document."""
    doc = DesignDocument(
        project_id=project.id,
        module_id=None,
        doc_type="behavior",
        content="# Foundation BEHAVIOR\n\nWorkflow descriptions and actor definitions.",
        version=1,
        approved_by=ri_user.id,
    )
    db_session.add(doc)
    db_session.flush()
    return doc


@pytest.fixture()
def module_gsc(db_session, project) -> ProjectModule:
    """GSC module — status done."""
    mod = ProjectModule(
        project_id=project.id,
        code="GSC",
        name="General System Config",
        category="infrastructure",
        status="done",
    )
    db_session.add(mod)
    db_session.flush()
    return mod


@pytest.fixture()
def module_stk(db_session, project) -> ProjectModule:
    """STK module — status in_design."""
    mod = ProjectModule(
        project_id=project.id,
        code="STK",
        name="Stakeholder Management",
        category="business",
        status="in_design",
    )
    db_session.add(mod)
    db_session.flush()
    return mod


@pytest.fixture()
def stk_design(db_session, project, module_stk, ri_user) -> DesignDocument:
    """STK module DESIGN.md."""
    doc = DesignDocument(
        project_id=project.id,
        module_id=module_stk.id,
        doc_type="design",
        content="# STK Module DESIGN\n\nStakeholder management architecture.",
        version=1,
        approved_by=ri_user.id,
    )
    db_session.add(doc)
    db_session.flush()
    return doc


@pytest.fixture()
def stk_behavior(db_session, project, module_stk, ri_user) -> DesignDocument:
    """STK module BEHAVIOR.md."""
    doc = DesignDocument(
        project_id=project.id,
        module_id=module_stk.id,
        doc_type="behavior",
        content="# STK Module BEHAVIOR\n\nStakeholder workflows.",
        version=1,
        approved_by=ri_user.id,
    )
    db_session.add(doc)
    db_session.flush()
    return doc


@pytest.fixture()
def ctx_client(db_session):
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


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestArchitectContextAssemblyFoundation:
    """Context for a Foundation (project-level) session."""

    def test_foundation_context_includes_design_and_behavior(
        self,
        ctx_client,
        db_session,
        ri_user,
        project,
        foundation_design,
        foundation_behavior,
        module_gsc,
        module_stk,
    ):
        """Foundation session context contains Foundation docs + module registry."""
        token = _login(ctx_client, ri_user.username, "CtxPass1")
        headers = _auth_headers(token)

        # Create foundation session (no module_id)
        create_resp = ctx_client.post(
            f"/api/v1/projects/{project.id}/architect",
            json={},
            headers=headers,
        )
        assert create_resp.status_code == 201
        session_id = create_resp.json()["id"]

        # Capture context passed to Claude
        captured_context = {}

        async def mock_stream(prompt, context=None, timeout=None):
            captured_context["context"] = context
            captured_context["prompt"] = prompt
            yield "OK"

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
                resp = ctx_client.post(
                    f"/api/v1/architect/sessions/{session_id}/message",
                    json={"content": "Analyze project structure"},
                    headers=headers,
                )
            finally:
                db_session.close = original_close

        assert resp.status_code == 200

        # The full_context is passed as the context kwarg to run_claude_stream
        full_context = captured_context["context"]
        assert full_context is not None

        # Foundation DESIGN.md must be present
        assert "Foundation DESIGN" in full_context
        assert "Core architecture for the project" in full_context

        # Foundation BEHAVIOR.md must be present
        assert "Foundation BEHAVIOR" in full_context
        assert "Workflow descriptions" in full_context

        # Module registry must be present
        assert "Module Registry" in full_context
        assert "GSC" in full_context
        assert "STK" in full_context
        assert "done" in full_context
        assert "in_design" in full_context

        # Module-specific docs should NOT be in foundation session context
        # (no module_id was set)
        assert "STK Module DESIGN" not in full_context

    def test_foundation_context_without_behavior(
        self,
        ctx_client,
        db_session,
        ri_user,
        project,
        foundation_design,
    ):
        """Context works with only DESIGN.md (BEHAVIOR.md optional)."""
        token = _login(ctx_client, ri_user.username, "CtxPass1")
        headers = _auth_headers(token)

        create_resp = ctx_client.post(
            f"/api/v1/projects/{project.id}/architect",
            json={},
            headers=headers,
        )
        session_id = create_resp.json()["id"]

        captured_context = {}

        async def mock_stream(prompt, context=None, timeout=None):
            captured_context["context"] = context
            yield "OK"

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
                ctx_client.post(
                    f"/api/v1/architect/sessions/{session_id}/message",
                    json={"content": "Hello"},
                    headers=headers,
                )
            finally:
                db_session.close = original_close

        full_context = captured_context["context"]
        assert "Foundation DESIGN" in full_context
        # BEHAVIOR should not appear (not created)
        assert "Foundation BEHAVIOR" not in full_context


@pytest.mark.integration
class TestArchitectContextAssemblyModule:
    """Context for a module-scoped session includes module docs."""

    def test_module_context_includes_foundation_and_module_docs(
        self,
        ctx_client,
        db_session,
        ri_user,
        project,
        foundation_design,
        foundation_behavior,
        module_gsc,
        module_stk,
        stk_design,
        stk_behavior,
    ):
        """Module session context contains Foundation + Module docs + registry."""
        token = _login(ctx_client, ri_user.username, "CtxPass1")
        headers = _auth_headers(token)

        # Create module-scoped session for STK
        create_resp = ctx_client.post(
            f"/api/v1/projects/{project.id}/architect",
            json={"module_id": str(module_stk.id)},
            headers=headers,
        )
        assert create_resp.status_code == 201
        session_id = create_resp.json()["id"]
        assert create_resp.json()["module_id"] == str(module_stk.id)

        # Capture context
        captured_context = {}

        async def mock_stream(prompt, context=None, timeout=None):
            captured_context["context"] = context
            yield "Module response"

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
                ctx_client.post(
                    f"/api/v1/architect/sessions/{session_id}/message",
                    json={"content": "Design STK module"},
                    headers=headers,
                )
            finally:
                db_session.close = original_close

        full_context = captured_context["context"]

        # Foundation docs must be present (always included)
        assert "Foundation DESIGN" in full_context
        assert "Foundation BEHAVIOR" in full_context

        # Module DESIGN.md must be present
        assert "STK Module DESIGN" in full_context
        assert "Stakeholder management architecture" in full_context

        # Module BEHAVIOR.md must be present
        assert "STK Module BEHAVIOR" in full_context
        assert "Stakeholder workflows" in full_context

        # Module registry must be present
        assert "Module Registry" in full_context
        assert "GSC" in full_context
        assert "done" in full_context

    def test_module_context_includes_conversation_history(
        self,
        ctx_client,
        db_session,
        ri_user,
        project,
        foundation_design,
        module_stk,
        stk_design,
    ):
        """Second message includes conversation history in context."""
        token = _login(ctx_client, ri_user.username, "CtxPass1")
        headers = _auth_headers(token)

        # Create session
        create_resp = ctx_client.post(
            f"/api/v1/projects/{project.id}/architect",
            json={"module_id": str(module_stk.id)},
            headers=headers,
        )
        session_id = create_resp.json()["id"]

        # First message
        async def mock_stream_1(prompt, context=None, timeout=None):
            yield "First AI reply"

        with (
            patch(
                "backend.api.routes.architect.claude_subprocess.run_claude_stream",
                side_effect=mock_stream_1,
            ),
            patch(
                "backend.api.routes.architect.SessionLocal",
                return_value=db_session,
            ),
        ):
            original_close = db_session.close
            db_session.close = lambda: None
            try:
                ctx_client.post(
                    f"/api/v1/architect/sessions/{session_id}/message",
                    json={"content": "First question"},
                    headers=headers,
                )
            finally:
                db_session.close = original_close

        # Second message — capture the full context
        captured_context = {}

        async def mock_stream_2(prompt, context=None, timeout=None):
            captured_context["context"] = context
            yield "Second AI reply"

        with (
            patch(
                "backend.api.routes.architect.claude_subprocess.run_claude_stream",
                side_effect=mock_stream_2,
            ),
            patch(
                "backend.api.routes.architect.SessionLocal",
                return_value=db_session,
            ),
        ):
            original_close = db_session.close
            db_session.close = lambda: None
            try:
                ctx_client.post(
                    f"/api/v1/architect/sessions/{session_id}/message",
                    json={"content": "Follow-up question"},
                    headers=headers,
                )
            finally:
                db_session.close = original_close

        full_context = captured_context["context"]

        # Conversation history should include the first exchange
        assert "Conversation History" in full_context
        assert "First question" in full_context
        assert "First AI reply" in full_context
        assert "Follow-up question" in full_context

    def test_context_requires_foundation_design(
        self,
        ctx_client,
        db_session,
        ri_user,
        project,
    ):
        """Sending a message without foundation DESIGN.md returns error."""
        token = _login(ctx_client, ri_user.username, "CtxPass1")
        headers = _auth_headers(token)

        # Create session via generic CRUD endpoint (bypasses context check on create)
        # The context check happens on message send, not session create
        create_resp = ctx_client.post(
            f"/api/v1/projects/{project.id}/architect",
            json={},
            headers=headers,
        )
        assert create_resp.status_code == 201
        session_id = create_resp.json()["id"]

        # Try to send message — should fail because no foundation DESIGN.md
        resp = ctx_client.post(
            f"/api/v1/architect/sessions/{session_id}/message",
            json={"content": "This should fail"},
            headers=headers,
        )
        # build_architect_context raises ValueError → 422
        assert resp.status_code == 422
        assert "DESIGN.md" in resp.json()["detail"]
