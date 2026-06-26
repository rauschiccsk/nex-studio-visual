"""Tests for :mod:`backend.services.agent_terminal`.

Strategy
--------
Each test stubs the spawn command to ``cat`` so we exercise the real
PTY plumbing without depending on a working ``claude`` CLI auth state.
``PROJECTS_ROOT`` is redirected to a ``tmp_path`` so we can create
fake projects with ``.claude/agents/<role>/CLAUDE.md`` and assert the
service's validation logic.

Covers:

* spawn happy path → DB row + in-memory registry entry
* duplicate active session → ``SessionConflictError`` (409 surface)
* invalid slug / role / missing project / missing spec → ``AgentTerminalError``
* ``end_session`` finalizes the DB row + drops the registry entry
* ``mark_orphaned_on_startup`` finalizes leftover rows
* ``idle_cleanup`` kills sessions past the TTL
* schema validators reject invalid slugs / roles
"""

from __future__ import annotations

import time
import uuid

import pytest

from backend.db.models.agent_terminal import AgentTerminalSession
from backend.schemas.agent_terminal import AgentTerminalSpawnRequest
from backend.services import agent_terminal as service

from .api.conftest import seed_user


@pytest.fixture
def fake_project(tmp_path, monkeypatch):
    """Create a ``<tmp>/sample-project/.claude/agents/{role}/CLAUDE.md`` tree.

    Redirects ``service.PROJECTS_ROOT`` so spawn() resolves the spec.
    Yields the slug.
    """
    slug = "sample-project"
    project_root = tmp_path / slug
    # CR-V2-007: two v2 agents, charter-path slugs (hyphen) ai-agent + auditor.
    for role in ("ai-agent", "auditor"):
        agent_dir = project_root / ".claude" / "agents" / role
        agent_dir.mkdir(parents=True)
        (agent_dir / "CLAUDE.md").write_text(f"# {role} agent\nTest prompt.\n")

    monkeypatch.setattr(service, "PROJECTS_ROOT", tmp_path)
    # Redirect durable PTY log dir to tmp_path/terminal-logs so the
    # service can mkdir + write without root permission (the production
    # path /var/lib/nex-studio/terminal-logs is owned by andros via
    # Dockerfile chown, but CI runners have no such dir).
    monkeypatch.setattr(service, "TERMINAL_LOG_DIR", tmp_path / "terminal-logs")
    yield slug


@pytest.fixture
def cat_spawn(monkeypatch):
    """Replace ``ptyprocess.PtyProcess.spawn`` so it runs ``cat`` instead.

    ``cat`` is the cleanest stand-in for claude: it has a TTY,
    echoes stdin → stdout, and exits cleanly on EOF. We keep the kwargs
    (cwd, env, dimensions) so the rest of the spawn pipeline (env vars,
    dimensions) is exercised.
    """
    import ptyprocess

    original = ptyprocess.PtyProcess.spawn

    def fake_spawn(_argv, **kwargs):
        return original(["cat"], **kwargs)

    monkeypatch.setattr(ptyprocess.PtyProcess, "spawn", fake_spawn)


@pytest.fixture(autouse=True)
def clear_registry():
    """Reset the in-memory registry before every test to prevent leaks."""
    service._sessions.clear()
    yield
    service._sessions.clear()


class TestSpawn:
    """Happy paths + DB row creation."""

    @pytest.mark.asyncio
    async def test_spawn_creates_db_row_and_registry_entry(
        self,
        db_session,
        fake_project,
        cat_spawn,
    ):
        user = seed_user(db_session, username="ri_spawn", role="ri")

        row = await service.spawn(
            user_id=user.id,
            role="ai-agent",
            project_slug=fake_project,
            db=db_session,
        )

        assert row.id is not None
        assert row.user_id == user.id
        assert row.role == "ai-agent"
        assert row.project_slug == fake_project
        assert row.pid > 0
        assert row.ended_at is None

        runtime = service._get_runtime_for_test(row.id)
        assert runtime is not None
        assert runtime.process.isalive()

        # Cleanup: end so cat exits cleanly.
        await service.end_session(row.id, terminated_by="user", db=db_session)

    @pytest.mark.asyncio
    async def test_spawn_ai_agent_role(
        self,
        db_session,
        fake_project,
        cat_spawn,
    ):
        """The AI Agent role (CR-V2-007) is the spawnable interactive terminal."""
        user = seed_user(db_session, username="ri_spawn_aiagent", role="ri")

        row = await service.spawn(
            user_id=user.id,
            role="ai-agent",
            project_slug=fake_project,
            db=db_session,
        )

        assert row.id is not None
        assert row.role == "ai-agent"
        assert row.ended_at is None

        runtime = service._get_runtime_for_test(row.id)
        assert runtime is not None
        assert runtime.process.isalive()

        await service.end_session(row.id, terminated_by="user", db=db_session)

    @pytest.mark.asyncio
    async def test_spawn_duplicate_role_raises_conflict(
        self,
        db_session,
        fake_project,
        cat_spawn,
    ):
        user = seed_user(db_session, username="ri_dup", role="ri")

        first = await service.spawn(
            user_id=user.id,
            role="ai-agent",
            project_slug=fake_project,
            db=db_session,
        )

        with pytest.raises(service.SessionConflictError):
            await service.spawn(
                user_id=user.id,
                role="ai-agent",
                project_slug=fake_project,
                db=db_session,
            )

        await service.end_session(first.id, terminated_by="user", db=db_session)

    # E3(a) (CR-NS-039): removed test_spawn_different_roles_coexist — only the Coordinator interactive
    # terminal is spawnable now, so "multiple roles per user coexist" is no longer a supported behavior.


class TestValidation:
    """Service-level validation: slug, role, project, agent spec."""

    @pytest.mark.asyncio
    async def test_invalid_role_rejected(self, db_session, fake_project, cat_spawn):
        user = seed_user(db_session, username="ri_invalid_role", role="ri")
        with pytest.raises(service.AgentTerminalError):
            await service.spawn(
                user_id=user.id,
                role="evil",
                project_slug=fake_project,
                db=db_session,
            )

    @pytest.mark.asyncio
    async def test_invalid_slug_rejected(self, db_session, fake_project, cat_spawn):
        user = seed_user(db_session, username="ri_invalid_slug", role="ri")
        with pytest.raises(service.AgentTerminalError):
            await service.spawn(
                user_id=user.id,
                role="ai-agent",
                project_slug="../escape",
                db=db_session,
            )

    @pytest.mark.asyncio
    async def test_missing_project_rejected(self, db_session, fake_project, cat_spawn):
        user = seed_user(db_session, username="ri_missing_proj", role="ri")
        with pytest.raises(service.AgentTerminalError):
            await service.spawn(
                user_id=user.id,
                role="ai-agent",
                project_slug="nonexistent-project",
                db=db_session,
            )

    @pytest.mark.asyncio
    async def test_missing_agent_spec_rejected(
        self,
        db_session,
        fake_project,
        cat_spawn,
        tmp_path,
        monkeypatch,
    ):
        """Project exists but missing ``.claude/agents/<role>/CLAUDE.md`` → 400."""
        # Create a barebones project without the agents directory.
        bare = tmp_path / "bare-project"
        bare.mkdir()
        monkeypatch.setattr(service, "PROJECTS_ROOT", tmp_path)

        user = seed_user(db_session, username="ri_missing_spec", role="ri")
        with pytest.raises(service.AgentTerminalError):
            await service.spawn(
                user_id=user.id,
                role="ai-agent",
                project_slug="bare-project",
                db=db_session,
            )


class TestEndSession:
    """Explicit End + DB row finalization."""

    @pytest.mark.asyncio
    async def test_end_session_finalizes_row_and_drops_runtime(
        self,
        db_session,
        fake_project,
        cat_spawn,
    ):
        user = seed_user(db_session, username="ri_end", role="ri")
        row = await service.spawn(
            user_id=user.id,
            role="ai-agent",
            project_slug=fake_project,
            db=db_session,
        )

        await service.end_session(row.id, terminated_by="user", db=db_session)

        # Runtime entry gone.
        assert service._get_runtime_for_test(row.id) is None

        # DB row finalized.
        db_session.expire_all()
        fresh = db_session.get(AgentTerminalSession, row.id)
        assert fresh.ended_at is not None
        assert fresh.terminated_by == "user"

    @pytest.mark.asyncio
    async def test_end_session_idempotent(
        self,
        db_session,
        fake_project,
        cat_spawn,
    ):
        user = seed_user(db_session, username="ri_end_idem", role="ri")
        row = await service.spawn(
            user_id=user.id,
            role="ai-agent",
            project_slug=fake_project,
            db=db_session,
        )
        await service.end_session(row.id, terminated_by="user", db=db_session)
        # Second call must not raise; row stays finalized.
        await service.end_session(row.id, terminated_by="user", db=db_session)
        db_session.expire_all()
        fresh = db_session.get(AgentTerminalSession, row.id)
        assert fresh.ended_at is not None


class TestStartupOrphans:
    """``mark_orphaned_on_startup`` finalizes leftover rows from prior boots."""

    def test_mark_orphaned_on_startup_finalizes_active_rows(
        self,
        db_session,
    ):
        user = seed_user(db_session, username="ri_orphan", role="ri")

        # Simulate leftover row from a prior boot.
        orphan = AgentTerminalSession(
            user_id=user.id,
            role="ai-agent",
            project_slug="any-project",
            pid=99999,
        )
        db_session.add(orphan)
        db_session.commit()

        count = service.mark_orphaned_on_startup(db_session)
        assert count >= 1

        db_session.expire_all()
        fresh = db_session.get(AgentTerminalSession, orphan.id)
        assert fresh.ended_at is not None
        assert fresh.terminated_by == "server_restart"


class TestDebugAttachResume:
    """CR-V2-007 (was CR-NS-039 BE decouple): a non-spawn debug-attach session resumes.

    The spawn API is AI-Agent-only, but ``_respawn_for_resume`` (WS reconnect
    after a BE restart) must re-attach a debug session for the independent Auditor.
    The spawn-only gate lives at the spawn-API entry, not in ``_resolve_agent_spec``,
    so both debug-attach roles (ai-agent, auditor) resolve + resume.
    """

    @pytest.mark.asyncio
    async def test_respawn_resumes_auditor_session(
        self,
        db_session,
        fake_project,
        cat_spawn,
    ):
        user = seed_user(db_session, username="ri_dbg_resume", role="ri")
        # A debug-attach row for the independent Auditor session (non-spawn role).
        row = AgentTerminalSession(
            user_id=user.id,
            role="auditor",
            project_slug=fake_project,
            pid=12345,
            claude_session_id=uuid.uuid4(),
        )
        db_session.add(row)
        db_session.commit()

        runtime = await service._respawn_for_resume(row=row, db=db_session)

        assert runtime is not None
        assert runtime.role == "auditor"
        assert runtime.process.isalive()
        assert row.pid == runtime.process.pid  # pid updated to the new PTY

        # Cleanup: end so cat exits cleanly.
        await service.end_session(row.id, terminated_by="user", db=db_session)


class TestIdleCleanup:
    """``idle_cleanup`` kills sessions past the TTL window."""

    @pytest.mark.asyncio
    async def test_idle_cleanup_kills_old_sessions(
        self,
        db_session,
        fake_project,
        cat_spawn,
        monkeypatch,
    ):
        user = seed_user(db_session, username="ri_idle", role="ri")
        row = await service.spawn(
            user_id=user.id,
            role="ai-agent",
            project_slug=fake_project,
            db=db_session,
        )

        runtime = service._get_runtime_for_test(row.id)
        assert runtime is not None
        # Force the session to look idle: backdate the input timestamp
        # beyond the TTL.
        runtime.last_input_at = time.time() - service.IDLE_TTL_SECONDS - 60

        killed = await service.idle_cleanup(db_session)
        assert killed >= 1

        db_session.expire_all()
        fresh = db_session.get(AgentTerminalSession, row.id)
        assert fresh.ended_at is not None
        assert fresh.terminated_by == "idle"

    @pytest.mark.asyncio
    async def test_idle_cleanup_skips_active_sessions(
        self,
        db_session,
        fake_project,
        cat_spawn,
    ):
        user = seed_user(db_session, username="ri_active", role="ri")
        row = await service.spawn(
            user_id=user.id,
            role="ai-agent",
            project_slug=fake_project,
            db=db_session,
        )

        killed = await service.idle_cleanup(db_session)
        assert killed == 0

        await service.end_session(row.id, terminated_by="user", db=db_session)


class TestSchemas:
    """Pydantic input validation."""

    def test_spawn_request_accepts_valid(self):
        req = AgentTerminalSpawnRequest(role="ai-agent", project_slug="nex-inbox")
        assert req.role == "ai-agent"
        assert req.project_slug == "nex-inbox"

    def test_spawn_request_rejects_invalid_slug(self):
        with pytest.raises(ValueError):
            AgentTerminalSpawnRequest(role="ai-agent", project_slug="../escape")

    def test_spawn_request_rejects_invalid_role(self):
        with pytest.raises(ValueError):
            AgentTerminalSpawnRequest(role="hacker", project_slug="nex-inbox")


# ─── CR-NS-014 — per-project role charter availability ───────────────────────


class TestAvailableRoles:
    """``available_roles`` reports per-role charter presence (non-raising)."""

    def test_reports_present_and_absent_roles(self, tmp_path, monkeypatch):
        # CR-V2-007: available_roles reports ONLY the AI Agent (the sole spawnable role).
        # The Auditor charter present but the AI-Agent charter deliberately absent → {"ai-agent": False}.
        slug = "partial-project"
        project_root = tmp_path / slug
        agent_dir = project_root / ".claude" / "agents" / "auditor"
        agent_dir.mkdir(parents=True)
        (agent_dir / "CLAUDE.md").write_text("# auditor\n")
        monkeypatch.setattr(service, "PROJECTS_ROOT", tmp_path)

        result = service.available_roles(slug)

        assert result == {"ai-agent": False}

    def test_all_present(self, tmp_path, monkeypatch):
        slug = "full-project"
        project_root = tmp_path / slug
        for role in ("ai-agent", "auditor"):
            agent_dir = project_root / ".claude" / "agents" / role
            agent_dir.mkdir(parents=True)
            (agent_dir / "CLAUDE.md").write_text(f"# {role}\n")
        monkeypatch.setattr(service, "PROJECTS_ROOT", tmp_path)

        result = service.available_roles(slug)

        assert result["ai-agent"] is True
        assert all(result.values())

    def test_unknown_project_raises(self, tmp_path, monkeypatch):
        monkeypatch.setattr(service, "PROJECTS_ROOT", tmp_path)
        with pytest.raises(service.AgentTerminalError):
            service.available_roles("does-not-exist")

    def test_invalid_slug_raises(self, tmp_path, monkeypatch):
        monkeypatch.setattr(service, "PROJECTS_ROOT", tmp_path)
        with pytest.raises(service.AgentTerminalError):
            service.available_roles("../escape")
