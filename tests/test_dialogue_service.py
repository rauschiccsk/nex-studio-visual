"""Tests for :mod:`backend.services.dialogue` (post-2026-05-16 rework).

Strategy
--------
The PTY-based orchestration was replaced with ``claude -p --resume``
subprocess invocations. Tests monkey-patch ``_invoke_agent`` to return
canned strings instead of actually calling claude — fast, deterministic,
no external dependency.

Coverage:
* create_session happy path → DB row with both claude_session_id fields
  populated + 2 init invocations recorded
* invalid slug / missing project / missing agent charter rejected
* add_message bumps message_count + persists row with default status
* approve / reject / mark_delivered enforce state machine
* trigger_customer_next_question / forward_approved_message /
  director_inject — high-level orchestration helpers
* mark_orphaned_on_startup finalizes leftover active rows
* schema validation (Pydantic)
"""

from __future__ import annotations

import uuid

import pytest

from backend.db.models.dialogue import DialogueMessage, DialogueSession
from backend.schemas.dialogue import (
    DialogueSessionCreate,
    DirectorInjectMessage,
)
from backend.services import dialogue as svc

from .api.conftest import seed_user

# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def fake_project_with_agents(tmp_path, monkeypatch):
    """Scaffold ``<tmp>/sample-project/.claude/agents/{customer,designer}/CLAUDE.md``
    so :func:`_resolve_agent_spec` succeeds for both roles.
    """
    slug = "sample-project"
    project_root = tmp_path / slug
    for role in ("customer", "designer"):
        agent_dir = project_root / ".claude" / "agents" / role
        agent_dir.mkdir(parents=True)
        (agent_dir / "CLAUDE.md").write_text(
            f"# {role} agent (test fake)\nDummy prompt for {role}.\n",
        )
    monkeypatch.setattr(svc, "PROJECTS_ROOT", tmp_path)
    yield slug


@pytest.fixture
def fake_invoke_agent(monkeypatch):
    """Replace ``_invoke_agent`` with a recorder that returns canned text.

    Returns the ``calls`` list so tests can assert what prompts went to
    which agent session. Each call is a dict
    ``{"session_id": ..., "prompt": ..., "is_init": ...}``.
    """
    calls: list[dict] = []

    async def fake(
        *,
        project_slug: str,
        claude_session_id: uuid.UUID,
        prompt: str,
        charter_path=None,
    ) -> str:
        calls.append(
            {
                "project_slug": project_slug,
                "session_id": claude_session_id,
                "prompt": prompt,
                "is_init": charter_path is not None,
            },
        )
        # Canned responses keyed on prompt content for predictability.
        if "Customer ready" in prompt or "Designer ready" in prompt:
            return "(ack)"
        # Match both legacy English wording and current Slovak prompt
        # (`_NEXT_QUESTION_PROMPT` was rewritten to Slovak in dialogue
        # iteration 2026-05-16+ with hard rules for turn-1 + persona +
        # subject-of-audit). The trigger entry point sends the next-Q
        # prompt; we recognize it by the stable phrase "ďalšiu otázku"
        # (or the legacy English "Generate the next question").
        if "ďalšiu otázku" in prompt or "Generate the next question" in prompt:
            return "Test Customer question?"
        return f"(fake response to: {prompt[:50]})"

    monkeypatch.setattr(svc, "_invoke_agent", fake)
    yield calls


# ── create_session ────────────────────────────────────────────────────


class TestCreateSession:
    @pytest.mark.asyncio
    async def test_creates_row_with_both_claude_session_ids(
        self,
        db_session,
        fake_project_with_agents,
        fake_invoke_agent,
    ):
        user = seed_user(db_session, username="ri_create", role="ri")

        row = await svc.create_session(
            user_id=user.id,
            project_slug=fake_project_with_agents,
            version_id=None,
            db=db_session,
        )

        assert row.id is not None
        assert row.status == "active"
        assert row.message_count == 0
        assert row.customer_session_id is not None
        assert row.designer_session_id is not None
        assert row.customer_session_id != row.designer_session_id

        # Both init invocations happened: each with charter_path set.
        assert len(fake_invoke_agent) == 2
        assert all(call["is_init"] for call in fake_invoke_agent)
        assert {call["session_id"] for call in fake_invoke_agent} == {
            row.customer_session_id,
            row.designer_session_id,
        }

    @pytest.mark.asyncio
    async def test_invalid_slug_rejected(self, db_session, fake_project_with_agents):
        user = seed_user(db_session, username="ri_invalid_slug", role="ri")
        with pytest.raises(svc.DialogueError, match="Invalid slug"):
            await svc.create_session(
                user_id=user.id,
                project_slug="../escape",
                version_id=None,
                db=db_session,
            )

    @pytest.mark.asyncio
    async def test_missing_project_rejected(
        self,
        db_session,
        fake_project_with_agents,
    ):
        user = seed_user(db_session, username="ri_missing_proj", role="ri")
        with pytest.raises(svc.DialogueError, match="Project not found"):
            await svc.create_session(
                user_id=user.id,
                project_slug="nonexistent-project",
                version_id=None,
                db=db_session,
            )

    @pytest.mark.asyncio
    async def test_missing_customer_charter_rejected(
        self,
        db_session,
        tmp_path,
        monkeypatch,
    ):
        slug = "designer-only-project"
        designer_dir = tmp_path / slug / ".claude" / "agents" / "designer"
        designer_dir.mkdir(parents=True)
        (designer_dir / "CLAUDE.md").write_text("designer only")
        monkeypatch.setattr(svc, "PROJECTS_ROOT", tmp_path)

        user = seed_user(db_session, username="ri_missing_customer", role="ri")
        with pytest.raises(svc.DialogueError, match="Agent spec missing"):
            await svc.create_session(
                user_id=user.id,
                project_slug=slug,
                version_id=None,
                db=db_session,
            )

    @pytest.mark.asyncio
    async def test_init_agent_failure_marks_session_ended(
        self,
        db_session,
        fake_project_with_agents,
        monkeypatch,
    ):
        """If claude --print fails during init, the half-initialised
        session row is marked ended (rollback)."""

        async def failing(**kwargs):
            raise svc.DialogueAgentError("simulated init failure")

        monkeypatch.setattr(svc, "_invoke_agent", failing)
        user = seed_user(db_session, username="ri_init_fail", role="ri")
        with pytest.raises(svc.DialogueAgentError, match="simulated"):
            await svc.create_session(
                user_id=user.id,
                project_slug=fake_project_with_agents,
                version_id=None,
                db=db_session,
            )

        db_session.expire_all()
        # The row was created before the failure — find it and check
        # the rollback marked it as ended.
        rows = db_session.query(DialogueSession).all()
        assert len(rows) == 1
        assert rows[0].status == "ended"
        assert rows[0].terminated_by == "user"


# ── Message lifecycle ─────────────────────────────────────────────────


class TestMessageLifecycle:
    def test_add_message_bumps_count(
        self,
        db_session,
        fake_project_with_agents,
    ):
        user = seed_user(db_session, username="ri_msg", role="ri")
        sess = DialogueSession(
            user_id=user.id,
            project_slug=fake_project_with_agents,
            status="active",
            message_count=0,
        )
        db_session.add(sess)
        db_session.commit()

        svc.add_message(
            session_id=sess.id,
            author="customer",
            content="First question",
            status="pending",
            db=db_session,
        )
        db_session.expire_all()
        assert db_session.get(DialogueSession, sess.id).message_count == 1

        svc.add_message(
            session_id=sess.id,
            author="designer",
            content="First answer",
            status="pending",
            db=db_session,
        )
        db_session.expire_all()
        assert db_session.get(DialogueSession, sess.id).message_count == 2

    def test_approve_flow(self, db_session, fake_project_with_agents):
        user = seed_user(db_session, username="ri_approve", role="ri")
        sess = DialogueSession(
            user_id=user.id,
            project_slug=fake_project_with_agents,
            status="active",
        )
        db_session.add(sess)
        db_session.commit()
        msg = svc.add_message(
            session_id=sess.id,
            author="customer",
            content="Q",
            status="pending",
            db=db_session,
        )

        svc.approve_message(msg.id, db_session)
        db_session.expire_all()
        assert db_session.get(DialogueMessage, msg.id).status == "approved"

        svc.mark_delivered(msg.id, db_session)
        db_session.expire_all()
        assert db_session.get(DialogueMessage, msg.id).status == "delivered"

    def test_approve_rejects_non_pending(
        self,
        db_session,
        fake_project_with_agents,
    ):
        user = seed_user(db_session, username="ri_approve_nonpending", role="ri")
        sess = DialogueSession(
            user_id=user.id,
            project_slug=fake_project_with_agents,
            status="active",
        )
        db_session.add(sess)
        db_session.commit()
        msg = svc.add_message(
            session_id=sess.id,
            author="director",
            content="Director inject",
            status="delivered",
            db=db_session,
        )
        with pytest.raises(svc.DialogueError, match="from 'delivered'"):
            svc.approve_message(msg.id, db_session)

    def test_reject_flow(self, db_session, fake_project_with_agents):
        user = seed_user(db_session, username="ri_reject", role="ri")
        sess = DialogueSession(
            user_id=user.id,
            project_slug=fake_project_with_agents,
            status="active",
        )
        db_session.add(sess)
        db_session.commit()
        msg = svc.add_message(
            session_id=sess.id,
            author="customer",
            content="Q",
            status="pending",
            db=db_session,
        )
        svc.reject_message(msg.id, db_session)
        db_session.expire_all()
        assert db_session.get(DialogueMessage, msg.id).status == "rejected"

    def test_mark_delivered_rejects_non_approved(
        self,
        db_session,
        fake_project_with_agents,
    ):
        user = seed_user(db_session, username="ri_delivered_invalid", role="ri")
        sess = DialogueSession(
            user_id=user.id,
            project_slug=fake_project_with_agents,
            status="active",
        )
        db_session.add(sess)
        db_session.commit()
        msg = svc.add_message(
            session_id=sess.id,
            author="customer",
            content="Q",
            status="pending",
            db=db_session,
        )
        with pytest.raises(svc.DialogueError, match="from 'pending'"):
            svc.mark_delivered(msg.id, db_session)


# ── High-level orchestration ──────────────────────────────────────────


class TestOrchestration:
    @pytest.mark.asyncio
    async def test_trigger_customer_next_question(
        self,
        db_session,
        fake_project_with_agents,
        fake_invoke_agent,
    ):
        user = seed_user(db_session, username="ri_trigger", role="ri")
        row = await svc.create_session(
            user_id=user.id,
            project_slug=fake_project_with_agents,
            version_id=None,
            db=db_session,
        )
        fake_invoke_agent.clear()  # discard init calls

        msg = await svc.trigger_customer_next_question(session=row, db=db_session)

        assert msg.author == "customer"
        assert msg.status == "pending"
        assert msg.content == "Test Customer question?"

        # Exactly one invocation, targeting Customer's session_id.
        assert len(fake_invoke_agent) == 1
        call = fake_invoke_agent[0]
        assert call["session_id"] == row.customer_session_id
        assert call["is_init"] is False
        assert "Generate the next question" in call["prompt"]

    @pytest.mark.asyncio
    async def test_forward_approved_customer_message_to_designer(
        self,
        db_session,
        fake_project_with_agents,
        fake_invoke_agent,
    ):
        user = seed_user(db_session, username="ri_forward", role="ri")
        row = await svc.create_session(
            user_id=user.id,
            project_slug=fake_project_with_agents,
            version_id=None,
            db=db_session,
        )
        fake_invoke_agent.clear()

        customer_msg = svc.add_message(
            session_id=row.id,
            author="customer",
            content="What about edge case X?",
            status="approved",
            db=db_session,
        )

        designer_msg = await svc.forward_approved_message(
            session=row,
            approved_message=customer_msg,
            db=db_session,
        )

        assert designer_msg.author == "designer"
        assert designer_msg.status == "pending"

        assert len(fake_invoke_agent) == 1
        call = fake_invoke_agent[0]
        assert call["session_id"] == row.designer_session_id
        assert call["prompt"] == "What about edge case X?"

    @pytest.mark.asyncio
    async def test_forward_approved_designer_message_to_customer(
        self,
        db_session,
        fake_project_with_agents,
        fake_invoke_agent,
    ):
        user = seed_user(db_session, username="ri_forward_d2c", role="ri")
        row = await svc.create_session(
            user_id=user.id,
            project_slug=fake_project_with_agents,
            version_id=None,
            db=db_session,
        )
        fake_invoke_agent.clear()

        designer_msg = svc.add_message(
            session_id=row.id,
            author="designer",
            content="Here is how it works...",
            status="approved",
            db=db_session,
        )

        customer_msg = await svc.forward_approved_message(
            session=row,
            approved_message=designer_msg,
            db=db_session,
        )

        assert customer_msg.author == "customer"
        assert fake_invoke_agent[0]["session_id"] == row.customer_session_id

    @pytest.mark.asyncio
    async def test_director_inject_to_designer(
        self,
        db_session,
        fake_project_with_agents,
        fake_invoke_agent,
    ):
        user = seed_user(db_session, username="ri_inject", role="ri")
        row = await svc.create_session(
            user_id=user.id,
            project_slug=fake_project_with_agents,
            version_id=None,
            db=db_session,
        )
        fake_invoke_agent.clear()

        director_msg, recipient_msg = await svc.director_inject(
            session=row,
            recipient="designer",
            content="Doplnenie ku Customer's question Z",
            db=db_session,
        )

        # Director's own message is persisted as delivered.
        assert director_msg.author == "director"
        assert director_msg.status == "delivered"
        # Recipient (designer) generated a pending response.
        assert recipient_msg.author == "designer"
        assert recipient_msg.status == "pending"

        # One invocation to Designer.
        assert len(fake_invoke_agent) == 1
        assert fake_invoke_agent[0]["session_id"] == row.designer_session_id
        assert fake_invoke_agent[0]["prompt"] == "Doplnenie ku Customer's question Z"


# ── End session ───────────────────────────────────────────────────────


class TestEndSession:
    @pytest.mark.asyncio
    async def test_end_finalizes_row(
        self,
        db_session,
        fake_project_with_agents,
        fake_invoke_agent,
    ):
        user = seed_user(db_session, username="ri_end", role="ri")
        row = await svc.create_session(
            user_id=user.id,
            project_slug=fake_project_with_agents,
            version_id=None,
            db=db_session,
        )

        await svc.end_session(
            session_id=row.id,
            terminated_by="user",
            db=db_session,
        )

        db_session.expire_all()
        fresh = db_session.get(DialogueSession, row.id)
        assert fresh.status == "ended"
        assert fresh.ended_at is not None
        assert fresh.terminated_by == "user"


# ── Startup orphan cleanup ────────────────────────────────────────────


class TestStartupOrphans:
    def test_mark_orphaned_finalizes_active_rows(
        self,
        db_session,
        fake_project_with_agents,
    ):
        user = seed_user(db_session, username="ri_orphan", role="ri")
        orphan = DialogueSession(
            user_id=user.id,
            project_slug=fake_project_with_agents,
            status="active",
        )
        db_session.add(orphan)
        db_session.commit()

        count = svc.mark_orphaned_on_startup(db_session)
        assert count >= 1

        db_session.expire_all()
        fresh = db_session.get(DialogueSession, orphan.id)
        assert fresh.status == "ended"
        assert fresh.terminated_by == "server_restart"


# ── Schema validation ─────────────────────────────────────────────────


class TestSchemas:
    def test_create_request_accepts_valid(self):
        req = DialogueSessionCreate(project_slug="nex-inbox")
        assert req.project_slug == "nex-inbox"

    def test_create_request_accepts_version_id(self):
        vid = uuid.uuid4()
        req = DialogueSessionCreate(project_slug="nex-inbox", version_id=vid)
        assert req.version_id == vid

    def test_create_request_rejects_empty_slug(self):
        with pytest.raises(ValueError):
            DialogueSessionCreate(project_slug="")

    def test_inject_message_recipient_validated(self):
        with pytest.raises(ValueError):
            DirectorInjectMessage(recipient="auditor", content="x")  # type: ignore[arg-type]

    def test_inject_message_rejects_empty_content(self):
        with pytest.raises(ValueError):
            DirectorInjectMessage(recipient="designer", content="")
