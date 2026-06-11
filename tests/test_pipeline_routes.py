"""Tests for the pipeline cockpit REST routes + broadcast wiring (CR-NS-018 Phase 3)."""

import json
import uuid

import bcrypt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.api.routes.pipeline import router as pipeline_router
from backend.core.security import get_current_user, require_ri_role
from backend.db.models.foundation import User
from backend.db.models.pipeline import PipelineMessage, PipelineState
from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.db.session import get_db
from backend.services import orchestrator, pipeline_runner
from backend.services.pipeline_ws import registry


def _block(stage="kickoff", kind="done", summary="ok", awaiting="director", **extra) -> str:
    body = {"stage": stage, "kind": kind, "summary": summary, "awaiting": awaiting}
    body.update(extra)
    return f"<<<PIPELINE_STATUS>>>\n{json.dumps(body)}\n<<<END_PIPELINE_STATUS>>>"


class _FakeClaude:
    def __init__(self):
        self.response = _block()

    async def __call__(self, **kwargs):
        return self.response


def _seed_user(db_session, role="ri") -> User:
    u = User(
        username=f"u_{uuid.uuid4().hex[:8]}",
        email=f"{uuid.uuid4().hex[:8]}@test.local",
        password_hash=bcrypt.hashpw(b"test", bcrypt.gensalt(rounds=4)).decode(),
        role=role,
        is_active=True,
    )
    db_session.add(u)
    db_session.flush()
    return u


def _settle_status(db_session, version_id, status="awaiting_director") -> None:
    """Settle the pipeline so a Director action passes the agent_working guard
    (CR-NS-018). schedule_dispatch is mocked, so without this the state stays
    agent_working after start and advancing actions would be rejected."""
    st = db_session.execute(select(PipelineState).where(PipelineState.version_id == version_id)).scalar_one()
    st.status = status
    db_session.flush()


def _make_version(db_session, user) -> Version:
    project = Project(
        name=f"P {uuid.uuid4().hex[:8]}",
        slug=f"p-{uuid.uuid4().hex[:8]}",
        category="singlemodule",
        description="d",
        created_by=user.id,
    )
    db_session.add(project)
    db_session.flush()
    version = Version(project_id=project.id, version_number=f"1.{uuid.uuid4().hex[:4]}.0")
    db_session.add(version)
    db_session.flush()
    return version


@pytest.fixture()
def client(db_session, monkeypatch):
    # No live claude / git in route tests.
    fake = _FakeClaude()
    monkeypatch.setattr(orchestrator, "invoke_claude", fake)
    monkeypatch.setattr(orchestrator, "verify_mechanical", lambda slug, block: None)

    # Async dispatch: capture scheduling instead of spawning a real bg task
    # (the agent run is unit-tested directly in test_orchestrator / runner).
    scheduled: list = []
    scheduled_directives: list = []

    def _capture_dispatch(vid, directive=None, *, gate_e_dispatch=None):
        scheduled.append(vid)
        scheduled_directives.append(directive)

    monkeypatch.setattr(pipeline_runner, "schedule_dispatch", _capture_dispatch)

    app = FastAPI()
    app.include_router(pipeline_router, prefix="/api/v1/pipeline")

    ri = _seed_user(db_session, "ri")

    def _override_db():
        yield db_session

    def _override_user():
        return ri

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = _override_user
    app.dependency_overrides[require_ri_role] = _override_user

    with TestClient(app) as c:
        c._ri = ri
        c._fake = fake
        c._scheduled = scheduled
        c._scheduled_directives = scheduled_directives
        yield c
    app.dependency_overrides.clear()
    registry._conns.clear()


# ── GET board / messages ──────────────────────────────────────────────────────


def test_board_none_before_start(client, db_session):
    version = _make_version(db_session, client._ri)
    r = client.get(f"/api/v1/pipeline/{version.id}")
    assert r.status_code == 200
    body = r.json()
    assert body["state"] is None
    assert body["recent_messages"] == []


def test_board_unknown_version_404(client):
    r = client.get(f"/api/v1/pipeline/{uuid.uuid4()}")
    assert r.status_code == 404


def test_start_then_board_populated(client, db_session):
    version = _make_version(db_session, client._ri)
    r = client.post(f"/api/v1/pipeline/{version.id}/action", json={"action": "start"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["state"]["current_stage"] == "kickoff"
    assert len(body["recent_messages"]) >= 1
    # board GET reflects it
    g = client.get(f"/api/v1/pipeline/{version.id}").json()
    assert g["state"]["current_stage"] == "kickoff"
    assert g["current_task"] is None  # WS-C2 (CR-NS-035): no current task outside build


def test_board_current_task_at_build(client, db_session):
    # WS-C2 (CR-NS-035): the board exposes the in-focus build task (#N + title) for the "kto je na rade" board.
    from backend.db.models.tasks import Epic, Feat, Task

    version = _make_version(db_session, client._ri)
    project_id = db_session.get(Version, version.id).project_id
    db_session.add(
        PipelineState(
            version_id=version.id,
            flow_type="new_version",
            current_stage="build",
            current_actor="implementer",
            status="agent_working",
            next_action="",
        )
    )
    epic = Epic(project_id=project_id, version_id=version.id, number=1, title="E", status="in_progress")
    db_session.add(epic)
    db_session.flush()
    feat = Feat(epic_id=epic.id, number=1, title="F", description="", status="in_progress")
    db_session.add(feat)
    db_session.flush()
    db_session.add(Task(feat_id=feat.id, number=2, title="AP tables", task_type="backend", status="in_progress"))
    db_session.flush()

    body = client.get(f"/api/v1/pipeline/{version.id}").json()
    assert body["current_task"] == {"number": 2, "title": "AP tables"}


def test_start_returns_agent_working_and_schedules_dispatch(client, db_session):
    """Async dispatch: POST returns instantly in ``agent_working`` and the
    agent run is scheduled in the background (not awaited in the request)."""
    version = _make_version(db_session, client._ri)
    r = client.post(f"/api/v1/pipeline/{version.id}/action", json={"action": "start"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["state"]["status"] == "agent_working"
    # No agent ran in-request — only the director kickoff message exists.
    assert [m["author"] for m in body["recent_messages"]] == ["director"]
    # A background dispatch was scheduled for this version.
    assert version.id in client._scheduled
    # A fresh-stage dispatch (start) carries no Director-specific directive.
    assert client._scheduled_directives[-1] is None


def test_return_threads_director_comment_into_dispatch(client, db_session):
    """CR-NS-018: the Director's ``return`` comment is framed and threaded into
    the re-dispatch so the agent acts on it (not a blind generic re-run)."""
    version = _make_version(db_session, client._ri)
    client.post(f"/api/v1/pipeline/{version.id}/action", json={"action": "start"})
    _settle_status(db_session, version.id, "awaiting_director")
    r = client.post(
        f"/api/v1/pipeline/{version.id}/action",
        json={"action": "return", "payload": {"comment": "Zlaď rozpor v kontrole súčtov"}},
    )
    assert r.status_code == 200, r.text
    directive = client._scheduled_directives[-1]
    assert directive is not None
    assert "Zlaď rozpor v kontrole súčtov" in directive


def test_answer_threads_director_text_into_dispatch(client, db_session):
    """CR-NS-018: ``answer`` content reaches the agent (no re-ask loop)."""
    version = _make_version(db_session, client._ri)
    client.post(f"/api/v1/pipeline/{version.id}/action", json={"action": "start"})
    _settle_status(db_session, version.id, "blocked")  # answer needs an open question
    r = client.post(
        f"/api/v1/pipeline/{version.id}/action",
        json={"action": "answer", "payload": {"text": "Schvaľujem port 8080"}},
    )
    assert r.status_code == 200, r.text
    directive = client._scheduled_directives[-1]
    assert directive is not None
    assert "Schvaľujem port 8080" in directive


def test_apply_coordinator_recommendation_threads_report(client, db_session):
    """CR-NS-018: the latest Coordinator gate_report is framed + threaded into the
    re-dispatch so the Director accepts it one-click, without retyping."""
    version = _make_version(db_session, client._ri)
    client.post(f"/api/v1/pipeline/{version.id}/action", json={"action": "start"})
    db_session.add(
        PipelineMessage(
            version_id=version.id,
            stage="kickoff",
            author="coordinator",
            recipient="director",
            kind="gate_report",
            content="Oprav riadok 3 — DPH.",
        )
    )
    db_session.flush()
    _settle_status(db_session, version.id, "awaiting_director")
    r = client.post(
        f"/api/v1/pipeline/{version.id}/action",
        json={"action": "apply_coordinator_recommendation"},
    )
    assert r.status_code == 200, r.text
    directive = client._scheduled_directives[-1]
    assert directive is not None
    assert "Oprav riadok 3 — DPH." in directive


def test_apply_coordinator_recommendation_no_report_400(client, db_session):
    """No Coordinator report to apply → 400 (FE also hides the button)."""
    version = _make_version(db_session, client._ri)
    client.post(f"/api/v1/pipeline/{version.id}/action", json={"action": "start"})
    _settle_status(db_session, version.id, "awaiting_director")  # past the guard → exercise no-report path
    r = client.post(
        f"/api/v1/pipeline/{version.id}/action",
        json={"action": "apply_coordinator_recommendation"},
    )
    assert r.status_code == 400


def test_messages_paginated(client, db_session):
    version = _make_version(db_session, client._ri)
    client.post(f"/api/v1/pipeline/{version.id}/action", json={"action": "start"})
    r = client.get(f"/api/v1/pipeline/{version.id}/messages?skip=0&limit=1")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] >= 1
    assert len(body["items"]) == 1
    assert body["skip"] == 0 and body["limit"] == 1


# ── POST action errors ────────────────────────────────────────────────────────


def test_double_start_409(client, db_session):
    version = _make_version(db_session, client._ri)
    client.post(f"/api/v1/pipeline/{version.id}/action", json={"action": "start"})
    r = client.post(f"/api/v1/pipeline/{version.id}/action", json={"action": "start"})
    assert r.status_code == 409


def test_unknown_action_400(client, db_session):
    version = _make_version(db_session, client._ri)
    client.post(f"/api/v1/pipeline/{version.id}/action", json={"action": "start"})
    r = client.post(f"/api/v1/pipeline/{version.id}/action", json={"action": "teleport"})
    assert r.status_code == 400


def test_action_unknown_version_404(client):
    r = client.post(f"/api/v1/pipeline/{uuid.uuid4()}/action", json={"action": "start"})
    assert r.status_code == 404


# ── auth ──────────────────────────────────────────────────────────────────────


def test_non_ri_forbidden(db_session, monkeypatch):
    monkeypatch.setattr(orchestrator, "invoke_claude", _FakeClaude())
    app = FastAPI()
    app.include_router(pipeline_router, prefix="/api/v1/pipeline")
    ha = _seed_user(db_session, "ha")
    version = _make_version(db_session, ha)

    def _override_db():
        yield db_session

    # get_current_user resolves the ha user; require_ri_role NOT overridden →
    # the real gate runs and rejects ha with 403.
    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = lambda: ha

    with TestClient(app) as c:
        r = c.get(f"/api/v1/pipeline/{version.id}")
        assert r.status_code == 403
    app.dependency_overrides.clear()


# ── broadcast wiring (POST → registry) ────────────────────────────────────────


class _FakeWS:
    def __init__(self):
        self.received = []

    async def send_json(self, data):
        self.received.append(data)


def test_post_action_broadcasts_to_registered_socket(client, db_session):
    version = _make_version(db_session, client._ri)
    ws = _FakeWS()
    # Register a live socket directly (test setup — bypass the async connect lock).
    registry._conns[version.id].add((ws, client._ri.id))

    client.post(f"/api/v1/pipeline/{version.id}/action", json={"action": "start"})

    types = [e["type"] for e in ws.received]
    assert "state_changed" in types
    assert "message_added" in types


# ── debug-terminal attach (CR-NS-018 Phase 4, F-007 §10) ──────────────────────


def test_debug_terminal_resumes_orchestrator_session(client, db_session, monkeypatch):
    from backend.db.models.agent_terminal import AgentTerminalSession
    from backend.db.models.orchestrator import OrchestratorSession
    from backend.db.models.projects import Project
    from backend.services import agent_terminal as agent_terminal_module

    version = _make_version(db_session, client._ri)
    slug = db_session.get(Project, db_session.get(Version, version.id).project_id).slug
    orch_uuid = uuid.uuid4()
    db_session.add(OrchestratorSession(project_slug=slug, role="implementer", claude_session_id=orch_uuid))
    db_session.flush()

    captured = {}

    async def _fake_spawn(*, user_id, role, project_slug, db, claude_session_id=None):
        captured["claude_session_id"] = claude_session_id
        captured["role"] = role
        captured["project_slug"] = project_slug
        row = AgentTerminalSession(
            user_id=user_id,
            role=role,
            project_slug=project_slug,
            pid=4321,
            claude_session_id=claude_session_id,
        )
        db.add(row)
        db.flush()
        return row

    monkeypatch.setattr(agent_terminal_module, "spawn", _fake_spawn)

    r = client.post(f"/api/v1/pipeline/{version.id}/debug-terminal?role=implementer")
    assert r.status_code == 200, r.text
    # the existing orchestrator UUID is resumed, not a fresh one
    assert captured["claude_session_id"] == orch_uuid
    assert captured["role"] == "implementer"
    assert captured["project_slug"] == slug
    assert r.json()["role"] == "implementer"


def test_debug_terminal_no_orchestrator_session_404(client, db_session):
    version = _make_version(db_session, client._ri)
    r = client.post(f"/api/v1/pipeline/{version.id}/debug-terminal?role=implementer")
    assert r.status_code == 404


def test_debug_terminal_unknown_version_404(client):
    r = client.post(f"/api/v1/pipeline/{uuid.uuid4()}/debug-terminal?role=implementer")
    assert r.status_code == 404


def test_board_exposes_deterministic_gate_e_open_findings(client, db_session):
    """The board carries the deterministic open-finding count (CR-NS-018 §5), not the
    Customer's self-reported findings array — so the FE close-gate reads it from here."""
    version = _make_version(db_session, client._ri)
    client.post(f"/api/v1/pipeline/{version.id}/action", json={"action": "start"})
    # one unresolved Designer gap + a Customer report whose findings array is (wrongly) full
    db_session.add(
        PipelineMessage(
            version_id=version.id,
            stage="gate_e",
            author="designer",
            recipient="coordinator",
            kind="answer",
            content="medzera",
            payload={"gap_found": True},
        )
    )
    db_session.add(
        PipelineMessage(
            version_id=version.id,
            stage="gate_e",
            author="customer",
            recipient="director",
            kind="gate_report",
            content="súhrn",
            payload={"coverage_complete": True, "findings": ["a", "b", "c"]},
        )
    )
    db_session.flush()
    body = client.get(f"/api/v1/pipeline/{version.id}").json()
    assert body["gate_e_open_findings"] == 1  # deterministic (1 raised, 0 resolved), not 3 from the array
