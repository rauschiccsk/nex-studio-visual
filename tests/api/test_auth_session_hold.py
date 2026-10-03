"""Tests for GET /api/v1/auth/session-hold — is a build the user can see still alive? (ICCINT-162)

Director, 03.10.2026: *„pokiaľ agent aktívne pracuje, treba predlžovať."* Programovanie runs for days
and the Manažér mostly watches it; the keep-alive used to renew only on recent mouse/keyboard input, so
an open cockpit with a working agent bounced him to the login screen exactly 8 h after the last token.

The server answers one question for the open cockpit tab: *since ``since`` (the current token's issue
time), did anything happen on a build this user can see?* — an agent working right now, or a message in
the build's conversation after ``since``. The visibility rule is the projects list's: the creator sees
his own projects, the single ``admin`` account sees all.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from backend.db.models.pipeline import PipelineMessage, PipelineState
from backend.db.models.projects import Project
from backend.db.models.versions import Version

from .conftest import login_user, seed_user


def _build(db_session, owner, *, status="awaiting_manazer"):
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
    version = Version(project_id=project.id, version_number="0.1.0")
    db_session.add(version)
    db_session.flush()
    db_session.add(
        PipelineState(
            version_id=version.id,
            flow_type="new_version",
            current_stage="programovanie",
            current_actor="ai_agent",
            status=status,
            next_action="—",
        )
    )
    db_session.flush()
    return version


def _message(db_session, version, created_at):
    db_session.add(
        PipelineMessage(
            version_id=version.id,
            stage="programovanie",
            author="ai_agent",
            recipient="manazer",
            kind="gate_report",
            content="hotovo",
            status="delivered",
            created_at=created_at,
        )
    )
    db_session.flush()


def _ask(client, token, since):
    return client.get(
        "/api/v1/auth/session-hold",
        params={"since": since.isoformat()},
        headers={"Authorization": f"Bearer {token}"},
    )


NOW = datetime.now(UTC)


class TestHold:
    def test_agent_working_now_holds_the_session(self, client, db_session):
        owner = seed_user(db_session)
        _build(db_session, owner, status="agent_working")
        token = login_user(client)

        resp = _ask(client, token, NOW - timedelta(minutes=1))

        assert resp.status_code == 200
        assert resp.json()["hold"] is True
        assert resp.json()["reason"]

    def test_a_message_after_since_holds_the_session(self, client, db_session):
        # The agent stopped (waits for the Manažér) — but it worked AFTER the token was issued, so the
        # 8 hours count from then, not from the Manažér's last click.
        owner = seed_user(db_session)
        version = _build(db_session, owner, status="awaiting_manazer")
        _message(db_session, version, NOW - timedelta(hours=1))
        token = login_user(client)

        resp = _ask(client, token, NOW - timedelta(hours=2))

        assert resp.status_code == 200
        assert resp.json()["hold"] is True


class TestNoHold:
    def test_nothing_since_does_not_hold(self, client, db_session):
        # A forgotten tab on a quiet build must still expire — the security half of the rule.
        owner = seed_user(db_session)
        version = _build(db_session, owner, status="awaiting_manazer")
        _message(db_session, version, NOW - timedelta(hours=3))
        token = login_user(client)

        resp = _ask(client, token, NOW - timedelta(hours=2))

        assert resp.status_code == 200
        assert resp.json()["hold"] is False
        assert resp.json()["reason"]

    def test_no_builds_at_all_does_not_hold(self, client, db_session):
        seed_user(db_session)
        token = login_user(client)

        resp = _ask(client, token, NOW - timedelta(hours=2))

        assert resp.status_code == 200
        assert resp.json()["hold"] is False

    def test_someone_elses_working_build_does_not_hold(self, client, db_session):
        # Visibility = the projects list's rule: a build the user cannot see cannot keep him logged in.
        stranger = seed_user(db_session, username="cudzi")
        seed_user(db_session, username="tibor", role="ha")
        version = _build(db_session, stranger, status="agent_working")
        _message(db_session, version, NOW - timedelta(minutes=5))
        token = login_user(client, username="tibor")

        resp = _ask(client, token, NOW - timedelta(hours=2))

        assert resp.status_code == 200
        assert resp.json()["hold"] is False

    def test_admin_sees_every_build(self, client, db_session):
        stranger = seed_user(db_session, username="cudzi")
        seed_user(db_session)  # "admin"
        _build(db_session, stranger, status="agent_working")
        token = login_user(client)

        resp = _ask(client, token, NOW - timedelta(minutes=1))

        assert resp.status_code == 200
        assert resp.json()["hold"] is True


class TestGate:
    def test_without_a_token_is_401(self, client, db_session):
        resp = client.get("/api/v1/auth/session-hold", params={"since": NOW.isoformat()})
        assert resp.status_code == 401

    def test_since_is_required(self, client, db_session):
        seed_user(db_session)
        token = login_user(client)
        resp = client.get("/api/v1/auth/session-hold", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 422
