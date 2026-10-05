"""Per-user agent model/effort config API (CR-NS-040, E3(b/c)).

Covers the table round-trip + per-user isolation + enum validation. Each call is scoped to the
authenticated user (no user_id in the path), so editing another user's config is structurally
impossible — the isolation test proves user B never sees / affects user A's rows.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.routes.user_agent_settings import router as user_agent_settings_router
from backend.core.security import get_current_user
from backend.db.models.foundation import User
from backend.db.models.pipeline import PipelineMessage
from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.db.session import get_db

# v2.0.0 (CR-V2-007 follow-up): PipelineAgentRole Literal is now {ai_agent, auditor} (matches the v2 DB
# CHECK migration 069+073); the roles below use the v2 set.


def _make_user(db_session: Any, role: str = "ri") -> User:
    user = User(
        username=f"user_{uuid.uuid4().hex[:8]}",
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        password_hash="hashed_password_placeholder",
        role=role,
    )
    db_session.add(user)
    db_session.flush()
    return user


def _client_for(db_session: Any, current: User) -> TestClient:
    app = FastAPI()
    app.include_router(user_agent_settings_router, prefix="/api/v1/user-agent-settings")

    def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_current_user] = lambda: current
    return TestClient(app)


def test_put_then_get_roundtrip(db_session):
    user = _make_user(db_session)
    client = _client_for(db_session, user)

    r = client.put(
        "/api/v1/user-agent-settings/ai_agent",
        json={"model": "sonnet", "effort": "high"},
    )
    assert r.status_code == 200, r.text
    assert r.json() == {
        "agent_role": "ai_agent",
        "model": "sonnet",
        "effort": "high",
        "helper_model": None,
    }

    rows = client.get("/api/v1/user-agent-settings").json()
    assert rows == [{"agent_role": "ai_agent", "model": "sonnet", "effort": "high", "helper_model": None}]


def test_put_is_upsert(db_session):
    user = _make_user(db_session)
    client = _client_for(db_session, user)

    client.put("/api/v1/user-agent-settings/auditor", json={"model": "opus", "effort": "low"})
    r = client.put("/api/v1/user-agent-settings/auditor", json={"model": "haiku", "effort": "max"})
    assert r.status_code == 200, r.text

    rows = client.get("/api/v1/user-agent-settings").json()
    assert rows == [{"agent_role": "auditor", "model": "haiku", "effort": "max", "helper_model": None}]


def test_per_user_isolation(db_session):
    user_a = _make_user(db_session)
    user_b = _make_user(db_session)
    client_a = _client_for(db_session, user_a)
    client_b = _client_for(db_session, user_b)

    client_a.put("/api/v1/user-agent-settings/ai_agent", json={"model": "opus", "effort": "xhigh"})

    # B sees nothing; A's config is unaffected by B existing.
    assert client_b.get("/api/v1/user-agent-settings").json() == []
    assert client_a.get("/api/v1/user-agent-settings").json() == [
        {"agent_role": "ai_agent", "model": "opus", "effort": "xhigh", "helper_model": None}
    ]

    # B sets their OWN ai_agent config — independent of A's.
    client_b.put("/api/v1/user-agent-settings/ai_agent", json={"model": "sonnet", "effort": "medium"})
    assert client_a.get("/api/v1/user-agent-settings").json()[0]["effort"] == "xhigh"
    assert client_b.get("/api/v1/user-agent-settings").json()[0]["effort"] == "medium"


def test_nullable_fields_accepted(db_session):
    user = _make_user(db_session)
    client = _client_for(db_session, user)
    r = client.put("/api/v1/user-agent-settings/ai_agent", json={"model": None, "effort": "max"})
    assert r.status_code == 200, r.text
    assert r.json() == {"agent_role": "ai_agent", "model": None, "effort": "max", "helper_model": None}


def test_helper_model_roundtrips(db_session):
    # CR-V2-038: the AI Agent's helper_model is settable + round-trips (Opus option over the Haiku default).
    user = _make_user(db_session)
    client = _client_for(db_session, user)
    r = client.put(
        "/api/v1/user-agent-settings/ai_agent",
        json={"model": "opus", "effort": "max", "helper_model": "opus"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["helper_model"] == "opus"
    assert client.get("/api/v1/user-agent-settings").json()[0]["helper_model"] == "opus"


def test_helper_model_rejects_non_dispatchable(db_session):
    user = _make_user(db_session)
    client = _client_for(db_session, user)
    r = client.put("/api/v1/user-agent-settings/ai_agent", json={"helper_model": "gpt-4"})
    assert r.status_code == 422


@pytest.mark.parametrize(
    "path,body",
    [
        ("/api/v1/user-agent-settings/bogus", {"effort": "high"}),  # unknown role
        ("/api/v1/user-agent-settings/ai_agent", {"effort": "ultracode"}),  # not a CLI effort level
        ("/api/v1/user-agent-settings/ai_agent", {"model": "gpt-4"}),  # not a dispatchable model
        # ICCINT-167: a pinned VERSION is no longer a choice — the cockpit stores the family and the CLI
        # runs its newest version, so a model roll never needs an application fix.
        ("/api/v1/user-agent-settings/ai_agent", {"model": "claude-opus-5"}),
        ("/api/v1/user-agent-settings/ai_agent", {"helper_model": "claude-haiku-4-5-20251001"}),
    ],
)
def test_invalid_inputs_rejected_422(db_session, path, body):
    user = _make_user(db_session)
    client = _client_for(db_session, user)
    assert client.put(path, json=body).status_code == 422


# ── ICCINT-167: the model choices + which version last really ran ─────────────


def _usage_msg(db_session, version_id, model, when):
    m = PipelineMessage(
        version_id=version_id,
        stage="programovanie",
        author="ai_agent",
        recipient="manazer",
        kind="gate_report",
        content="x",
        payload={"usage": {"input_tokens": 1, "output_tokens": 1, "model": model}},
        created_at=when,
    )
    db_session.add(m)
    db_session.flush()


def _version(db_session, owner):
    project = Project(
        name=f"P {uuid.uuid4().hex[:8]}",
        slug=f"p-{uuid.uuid4().hex[:8]}",
        type="standard",
        auth_mode="password",
        description="d",
        created_by=owner.id,
    )
    db_session.add(project)
    db_session.flush()
    version = Version(project_id=project.id, version_number=f"1.{uuid.uuid4().hex[:4]}.0")
    db_session.add(version)
    db_session.flush()
    return version


def test_models_lists_every_family_in_order(db_session):
    user = _make_user(db_session)
    client = _client_for(db_session, user)
    r = client.get("/api/v1/user-agent-settings/models")
    assert r.status_code == 200, r.text
    assert [m["id"] for m in r.json()] == ["opus", "sonnet", "haiku"]


def test_models_report_the_newest_version_that_really_ran(db_session):
    # The label must say what the CLI REALLY ran (payload.usage.model comes from the CLI's modelUsage),
    # newest first — not a version written into the code.
    user = _make_user(db_session)
    version = _version(db_session, user)
    future = datetime(2099, 1, 1, tzinfo=timezone.utc)
    _usage_msg(db_session, version.id, "claude-opus-5", future)
    _usage_msg(db_session, version.id, "claude-opus-5-5", future + timedelta(hours=1))
    _usage_msg(db_session, version.id, "claude-haiku-4-5-20251001", future + timedelta(minutes=5))

    by_id = {m["id"]: m for m in _client_for(db_session, user).get("/api/v1/user-agent-settings/models").json()}
    assert by_id["opus"]["last_run_model"] == "claude-opus-5-5"
    assert by_id["opus"]["last_run_at"].startswith("2099-01-01T01:00")
    assert by_id["haiku"]["last_run_model"] == "claude-haiku-4-5-20251001"
