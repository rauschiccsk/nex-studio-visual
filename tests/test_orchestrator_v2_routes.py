"""Milestone-I — live coverage of the v2 pipeline cockpit HTTP route layer (TestClient).

The SERVICE-level rejections are covered in ``test_orchestrator_v2_state_machine.py`` (apply_action raises
``OrchestratorError``), but no live v2 test drove the actual FastAPI routes via ``TestClient`` to assert the
HTTP status MAPPING (``_map_orch_error``), the fast-fix route status codes + response shape, pagination,
the RBAC guard on the cockpit surface, the post-action socket broadcast, the debug-terminal session resume,
or the board ``current_task`` population. The v1 ``test_pipeline_routes.py`` was the only HTTP-route test
for the cockpit; it is deferred (v1 vocabulary). This file re-expresses those route assertions against the
real v2 branch DB + the v2 route module.
"""

import uuid

import bcrypt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.api.routes.pipeline import router as pipeline_router
from backend.core.security import get_current_user, require_ri_role
from backend.db.models.foundation import User
from backend.db.models.orchestrator import OrchestratorSession
from backend.db.models.pipeline import PipelineMessage, PipelineState
from backend.db.models.projects import Project
from backend.db.models.tasks import Epic, Feat, Task
from backend.db.models.versions import Version
from backend.db.session import get_db
from backend.services import orchestrator, pipeline_runner
from backend.services.pipeline_ws import _Conn, registry


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


def _make_version(db_session, user) -> Version:
    project = Project(
        name=f"P {uuid.uuid4().hex[:8]}",
        slug=f"p-{uuid.uuid4().hex[:8]}",
        type="standard",
        auth_mode="password",
        description="d",
        created_by=user.id,
    )
    db_session.add(project)
    db_session.flush()
    version = Version(project_id=project.id, version_number=f"1.{uuid.uuid4().hex[:4]}.0")
    db_session.add(version)
    db_session.flush()
    return version


def _make_project_with_semver(db_session, user, version_number="0.3.0") -> Project:
    project = Project(
        name=f"P {uuid.uuid4().hex[:8]}",
        slug=f"p-{uuid.uuid4().hex[:8]}",
        type="standard",
        auth_mode="password",
        description="d",
        created_by=user.id,
    )
    db_session.add(project)
    db_session.flush()
    db_session.add(Version(project_id=project.id, version_number=version_number))
    db_session.flush()
    return project


@pytest.fixture()
def client(db_session, monkeypatch):
    """A TestClient over the v2 pipeline router with the RI gate satisfied + the background dispatch
    captured (the agent run is unit-tested directly elsewhere)."""

    async def _fake_claude(**_kw):
        return ""

    monkeypatch.setattr(orchestrator, "invoke_claude", _fake_claude)
    monkeypatch.setattr(orchestrator, "verify_mechanical", lambda slug, block, baseline_sha=None: None)

    scheduled: list = []
    scheduled_directives: list = []

    def _capture_dispatch(vid, directive=None):
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
        c._scheduled = scheduled
        c._scheduled_directives = scheduled_directives
        yield c
    app.dependency_overrides.clear()
    registry._conns.clear()


# ── POST action error → HTTP status mapping (_map_orch_error) ────────────────────


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


def test_board_unknown_version_404(client):
    r = client.get(f"/api/v1/pipeline/{uuid.uuid4()}")
    assert r.status_code == 404


# ── fast-fix route status codes + response shape ────────────────────────────────


def test_fast_fix_route_creates_patch_version_and_starts(client, db_session):
    project = _make_project_with_semver(db_session, client._ri, "0.3.0")
    r = client.post(
        "/api/v1/pipeline/fast-fix",
        json={"project_id": str(project.id), "directive": "Oprav preklep v hlavičke faktúry"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    new_version_id = uuid.UUID(body["version_id"])
    assert body["board"]["state"]["flow_type"] == "fast_fix"
    # the bumped PATCH version was created (vX.Y.Z+1)
    v = db_session.execute(select(Version).where(Version.id == new_version_id)).scalar_one()
    assert v.version_number == "0.3.1"
    # the v2 kickoff (stage=priprava, author=manazer) carries the directive; the dispatch was scheduled
    kickoff = db_session.execute(
        select(PipelineMessage).where(PipelineMessage.version_id == new_version_id, PipelineMessage.kind == "kickoff")
    ).scalar_one()
    assert kickoff.payload["directive"] == "Oprav preklep v hlavičke faktúry"
    assert new_version_id in client._scheduled


def test_fast_fix_route_unknown_project_404(client):
    r = client.post(
        "/api/v1/pipeline/fast-fix",
        json={"project_id": str(uuid.uuid4()), "directive": "x"},
    )
    assert r.status_code == 404


def test_fast_fix_route_no_semver_base_400(client, db_session):
    # A project with no semver-parseable version cannot be patched → 400 (precondition), not 500.
    project = _make_project_with_semver(db_session, client._ri, "pilot-x")
    r = client.post(
        "/api/v1/pipeline/fast-fix",
        json={"project_id": str(project.id), "directive": "x"},
    )
    assert r.status_code == 400


# ── messages pagination ─────────────────────────────────────────────────────────


def test_messages_paginated(client, db_session):
    version = _make_version(db_session, client._ri)
    client.post(f"/api/v1/pipeline/{version.id}/action", json={"action": "start"})
    r = client.get(f"/api/v1/pipeline/{version.id}/messages?skip=0&limit=1")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] >= 1
    assert len(body["items"]) == 1
    assert body["skip"] == 0 and body["limit"] == 1


# ── post-action fans out state_changed + message_added to a live socket ──────────


class _FakeWS:
    def __init__(self):
        self.received = []

    async def send_json(self, data):
        self.received.append(data)


def test_post_action_broadcasts_to_registered_socket(client, db_session):
    version = _make_version(db_session, client._ri)
    ws = _FakeWS()
    # Register a live socket directly (test setup — bypass the async connect lock).
    registry._conns[version.id][ws] = _Conn(user_id=client._ri.id)

    client.post(f"/api/v1/pipeline/{version.id}/action", json={"action": "start"})

    types = [e["type"] for e in ws.received]
    assert "state_changed" in types
    assert "message_added" in types


# ── RBAC: the cockpit surface is RI-only ────────────────────────────────────────


def test_non_ri_forbidden(db_session, monkeypatch):
    async def _fake_claude(**_kw):
        return ""

    monkeypatch.setattr(orchestrator, "invoke_claude", _fake_claude)
    app = FastAPI()
    app.include_router(pipeline_router, prefix="/api/v1/pipeline")
    ha = _seed_user(db_session, "ha")
    version = _make_version(db_session, ha)

    def _override_db():
        yield db_session

    # get_current_user resolves the ha user; require_ri_role NOT overridden → the real gate rejects ha 403.
    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = lambda: ha

    with TestClient(app) as c:
        r = c.get(f"/api/v1/pipeline/{version.id}")
        assert r.status_code == 403
    app.dependency_overrides.clear()


# ── debug-terminal resumes the existing orchestrator session ────────────────────


def test_debug_terminal_resumes_orchestrator_session(client, db_session, monkeypatch):
    from backend.db.models.agent_terminal import AgentTerminalSession
    from backend.services import agent_terminal as agent_terminal_module

    version = _make_version(db_session, client._ri)
    slug = db_session.get(Project, db_session.get(Version, version.id).project_id).slug
    orch_uuid = uuid.uuid4()
    db_session.add(OrchestratorSession(project_slug=slug, role="ai_agent", claude_session_id=orch_uuid))
    db_session.flush()

    captured = {}

    async def _fake_spawn(*, user_id, role, project_slug, db, claude_session_id=None):
        captured["claude_session_id"] = claude_session_id
        captured["role"] = role
        captured["project_slug"] = project_slug
        row = AgentTerminalSession(
            user_id=user_id, role=role, project_slug=project_slug, pid=4321, claude_session_id=claude_session_id
        )
        db.add(row)
        db.flush()
        return row

    monkeypatch.setattr(agent_terminal_module, "spawn", _fake_spawn)

    r = client.post(f"/api/v1/pipeline/{version.id}/debug-terminal?role=ai-agent")
    assert r.status_code == 200, r.text
    # the EXISTING orchestrator UUID is resumed, not a fresh one
    assert captured["claude_session_id"] == orch_uuid
    assert captured["role"] == "ai-agent"
    assert captured["project_slug"] == slug
    assert r.json()["role"] == "ai-agent"


def test_debug_terminal_no_orchestrator_session_404(client, db_session):
    version = _make_version(db_session, client._ri)
    r = client.post(f"/api/v1/pipeline/{version.id}/debug-terminal?role=ai-agent")
    assert r.status_code == 404


def test_debug_terminal_unknown_version_404(client):
    r = client.post(f"/api/v1/pipeline/{uuid.uuid4()}/debug-terminal?role=ai-agent")
    assert r.status_code == 404


# ── board exposes the in-focus build task during Programovanie ──────────────────


def test_board_current_task_at_programovanie(client, db_session):
    # WS-C2 (CR-NS-035): the board exposes the in-focus build task (#N + title) for "kto je na rade" —
    # populated only during the Programovanie phase from the in_progress task.
    version = _make_version(db_session, client._ri)
    project_id = db_session.get(Version, version.id).project_id
    db_session.add(
        PipelineState(
            version_id=version.id,
            flow_type="new_version",
            current_stage="programovanie",
            current_actor="ai_agent",
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


# ── CR-V2-037: a settled Návrh hides "Schváliť" while the task plan is empty ─────


def test_board_hides_schvalit_when_navrh_plan_empty(client, db_session):
    # At a settled Návrh the dial-governed "Schváliť" is normally offered — but NOT while the task plan is
    # still EMPTY (a per-feat pass crashed past its retries → 0 tasks). The board drops the dead button so
    # the Manažér can't advance into Programovanie with nothing to build (apply_action enforces it too).
    version = _make_version(db_session, client._ri)
    db_session.add(
        PipelineState(
            version_id=version.id,
            flow_type="new_version",
            current_stage="navrh",
            current_actor="ai_agent",
            status="awaiting_manazer",
            next_action="",
        )
    )
    db_session.flush()
    actions = client.get(f"/api/v1/pipeline/{version.id}").json()["available_actions"]
    assert "schvalit" not in actions  # dead button hidden
    assert "uprav" in actions  # the re-work recovery is still offered


def test_board_offers_schvalit_when_navrh_plan_present(client, db_session):
    # Complement: once a plan has materialized (≥1 task), the settled-Návrh "Schváliť" IS offered again.
    version = _make_version(db_session, client._ri)
    project_id = db_session.get(Version, version.id).project_id
    db_session.add(
        PipelineState(
            version_id=version.id,
            flow_type="new_version",
            current_stage="navrh",
            current_actor="ai_agent",
            status="awaiting_manazer",
            next_action="",
        )
    )
    epic = Epic(project_id=project_id, version_id=version.id, number=1, title="E", status="planned")
    db_session.add(epic)
    db_session.flush()
    feat = Feat(epic_id=epic.id, number=1, title="F", description="", status="todo")
    db_session.add(feat)
    db_session.flush()
    db_session.add(Task(feat_id=feat.id, number=1, title="T", task_type="backend", status="todo"))
    db_session.flush()
    actions = client.get(f"/api/v1/pipeline/{version.id}").json()["available_actions"]
    assert "schvalit" in actions
