"""Milestone-I — live coverage of the v2 dispatch lifecycle + the UAT deploy helpers.

The dispatch-lifecycle SEAMS (R-BLAST safeguards) are exercised at 11+ live v2 dispatch sites but every
phase test STUBS them to a no-op, so their own contracts had no live test once v1 ``test_orchestrator.py``
/ ``test_pipeline_runner.py`` were deferred. The UAT deploy helpers (``_run_uat_deploy`` /
``_verify_uat_serves`` / ``_fe_app_version``) are still live v2 source (called by ``deploy.py``) but
``test_deploy_service.py`` FAKES the runner above them, so their subprocess / exit-code / serve-verify /
git-count behaviours had no live test either. This file re-expresses those v1 assertions in v2 vocabulary,
run against the real v2 branch DB:

  * ``_begin_dispatch`` — captures the baseline + arms the durable flag; freezes the baseline on re-entry.
  * settle clears the flag + baseline (ORM listener); dispatch baseline ≠ per-task Task.baseline_sha.
  * ``apply_action`` durable single-flight guard ("Dispečer už beží") — survives a restart.
  * ``run_dispatch`` timeout → awaiting_manazer with the commit count, idempotent across parse-retries.
  * ``recover_orphaned_builds_on_startup`` — crash recovery of an orphaned agent_working build.
  * ``cleanup_old_orchestrator_sessions`` — TTL prune of idle OrchestratorSession rows.
  * ``_run_uat_deploy`` / ``_verify_uat_serves`` / ``_fe_app_version`` — the deploy-helper primitives.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, update

from backend.db.models.foundation import User
from backend.db.models.orchestrator import OrchestratorSession
from backend.db.models.pipeline import PipelineMessage, PipelineState
from backend.db.models.projects import Project
from backend.db.models.tasks import Epic, Feat, Task
from backend.db.models.versions import Version
from backend.services import orchestrator

# (pytest ``asyncio_mode = auto`` — async tests run without an explicit mark.)


def _make_version(db_session):
    user = User(
        username=f"u_{uuid.uuid4().hex[:8]}",
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        password_hash="x",
        role="ri",
    )
    db_session.add(user)
    db_session.flush()
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
    return version, project


@pytest.fixture
def fake_claude(monkeypatch):
    """``invoke_claude`` is never reached by the apply_action / begin_dispatch units (the dispatch runs in
    the background runner), but the orchestrator imports it — stub so nothing hits a live CLI."""

    async def _fake(**_kw):
        return ""

    monkeypatch.setattr(orchestrator, "invoke_claude", _fake)
    return _fake


def _msgs(db_session, version_id):
    return (
        db_session.execute(
            select(PipelineMessage).where(PipelineMessage.version_id == version_id).order_by(PipelineMessage.seq.asc())
        )
        .scalars()
        .all()
    )


def _arm_dispatch_state(db_session, version, stage="programovanie", actor="ai_agent", baseline="h" * 40):
    """Seed a PipelineState as a live dispatch (agent_working + a frozen dispatch baseline)."""
    state = PipelineState(
        version_id=version.id,
        flow_type="new_version",
        current_stage=stage,
        current_actor=actor,
        status="agent_working",
        next_action="working",
    )
    db_session.add(state)
    db_session.flush()
    state.dispatch_baseline_sha = baseline  # set AFTER construction so the settle listener keeps it
    state.dispatch_in_flight = True
    db_session.flush()
    return state


def _lost_work_notifs(db_session, version_id):
    return [
        m
        for m in _msgs(db_session, version_id)
        if m.author == "system" and m.kind == "notification" and (m.payload or {}).get("lost_work_audit")
    ]


def _seed_one_feat(db_session, version, project, titles):
    epic = Epic(project_id=project.id, version_id=version.id, number=1, title="E", status="planned")
    db_session.add(epic)
    db_session.flush()
    feat = Feat(epic_id=epic.id, number=1, title="F", status="todo")
    db_session.add(feat)
    db_session.flush()
    tasks = []
    for i, t in enumerate(titles, start=1):
        task = Task(feat_id=feat.id, number=i, title=t, task_type="backend", status="todo")
        db_session.add(task)
        tasks.append(task)
    db_session.flush()
    return epic, feat, tasks


# ── _begin_dispatch: baseline capture + arm + freeze-on-reentry (Seam #4) ───────


async def test_begin_dispatch_captures_baseline_and_arms_flag(db_session, fake_claude, monkeypatch):
    monkeypatch.setattr(orchestrator, "_repo_head", lambda root: "b" * 40)
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")  # → _begin_dispatch
    state = orchestrator._get_state(db_session, version.id)
    assert state.dispatch_baseline_sha == "b" * 40
    assert state.dispatch_in_flight is True
    assert state.status == "agent_working"
    # Seam #4: a re-entry (parse-retry) does NOT overwrite the frozen baseline.
    monkeypatch.setattr(orchestrator, "_repo_head", lambda root: "c" * 40)
    orchestrator._begin_dispatch(db_session, state)
    assert state.dispatch_baseline_sha == "b" * 40  # frozen across the dispatch


async def test_settle_clears_dispatch_flag_and_baseline(db_session, fake_claude, monkeypatch):
    # The ORM status-set listener clears the flag + baseline on every settle ("settle paths").
    monkeypatch.setattr(orchestrator, "_repo_head", lambda root: "b" * 40)
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    state = orchestrator._get_state(db_session, version.id)
    assert state.dispatch_in_flight is True and state.dispatch_baseline_sha == "b" * 40
    state.status = "awaiting_manazer"  # settle
    assert state.dispatch_in_flight is False
    assert state.dispatch_baseline_sha is None


async def test_dispatch_baseline_independent_of_task_baseline(db_session, fake_claude, monkeypatch):
    # Seam #7: the dispatch-level baseline (PipelineState) and the per-task Task.baseline_sha are
    # independent — settling clears the dispatch baseline but NEVER touches the task baseline.
    monkeypatch.setattr(orchestrator, "_repo_head", lambda root: "d" * 40)
    version, project = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    state = orchestrator._get_state(db_session, version.id)
    assert state.dispatch_baseline_sha == "d" * 40
    _epic, _feat, (task,) = _seed_one_feat(db_session, version, project, ["T"])
    task.baseline_sha = "t" * 40
    db_session.flush()

    state.status = "awaiting_manazer"  # settle → dispatch baseline reset
    db_session.flush()
    db_session.refresh(task)
    assert state.dispatch_baseline_sha is None  # dispatch baseline cleared
    assert task.baseline_sha == "t" * 40  # per-task verify anchor untouched


# ── apply_action durable single-flight guard ────────────────────────────────────


async def test_apply_action_durable_single_flight_guard(db_session, fake_claude):
    # A dispatching action while dispatch_in_flight=True raises (the durable guard that survives a restart,
    # beyond the in-memory _ACTIVE_DISPATCH). Set the flag AFTER the settle so the listener doesn't clear it
    # (simulates a stale in-flight flag a restart left before orphan recovery).
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    state = orchestrator._get_state(db_session, version.id)
    state.status = "awaiting_manazer"
    state.dispatch_in_flight = True
    db_session.flush()
    with pytest.raises(orchestrator.OrchestratorError, match="Dispečer už beží"):
        await orchestrator.apply_action(db_session, version_id=version.id, action="approve_spec")
    # not mutated past the guard (still in the first phase — Príprava)
    assert orchestrator._get_state(db_session, version.id).current_stage == "priprava"


# ── run_dispatch timeout integration (awaiting_manazer + commit count, idempotent) ─


async def test_run_dispatch_timeout_with_commits_surfaces_lost_work(db_session, monkeypatch):
    # A timeout during a live dispatch with commits → audit recorded, awaiting_manazer, next_action names the
    # commit count; the audit is recorded ONCE despite the parse-retries (idempotent).
    async def _boom(**kwargs):
        raise orchestrator.ClaudeAgentError("claude invocation timed out after 900s")

    monkeypatch.setattr(orchestrator, "_repo_head", lambda root: "h" * 40)
    monkeypatch.setattr(orchestrator, "_rev_list_count", lambda root, baseline: 2)
    monkeypatch.setattr(orchestrator, "invoke_claude", _boom)
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")  # arms baseline=h*40

    state = await orchestrator.run_dispatch(db_session, version.id)

    assert state.status == "awaiting_manazer"  # never a bare blocked, never auto-proceeds
    assert "2 commitov" in state.next_action
    assert len(_lost_work_notifs(db_session, version.id)) == 1  # idempotent across parse-retries


async def test_run_dispatch_timeout_no_commits_surfaces_no_change(db_session, monkeypatch):
    # A timeout with no commits → "žiadna zmena", still awaiting_manazer.
    async def _boom(**kwargs):
        raise orchestrator.ClaudeAgentError("claude invocation timed out after 900s")

    monkeypatch.setattr(orchestrator, "_repo_head", lambda root: "h" * 40)
    monkeypatch.setattr(orchestrator, "_rev_list_count", lambda root, baseline: 0)
    monkeypatch.setattr(orchestrator, "invoke_claude", _boom)
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")

    state = await orchestrator.run_dispatch(db_session, version.id)

    assert state.status == "awaiting_manazer"
    assert "žiadna zmena" in state.next_action


# ── startup crash recovery of an orphaned build ─────────────────────────────────


async def test_recover_orphaned_release_with_commits(db_session, monkeypatch):
    # A restart at verifikacia/agent_working → recovery flips to awaiting_manazer, records the commit audit,
    # clears the durable flag + baseline, and returns the recovered count.
    monkeypatch.setattr(orchestrator, "_repo_head", lambda root: "h" * 40)
    monkeypatch.setattr(orchestrator, "_rev_list_count", lambda root, baseline: 4)
    monkeypatch.setattr(db_session, "commit", db_session.flush)  # SAVEPOINT-safe (recover commits)
    version, _ = _make_version(db_session)
    _arm_dispatch_state(db_session, version, stage="verifikacia", actor="auditor", baseline="h" * 40)

    assert orchestrator.recover_orphaned_builds_on_startup(db_session) == 1

    state = orchestrator._get_state(db_session, version.id)
    assert state.current_stage == "verifikacia"
    assert state.status == "awaiting_manazer"
    assert state.dispatch_in_flight is False
    assert state.dispatch_baseline_sha is None
    assert "verifikacia" in state.next_action and "4 commitov" in state.next_action
    notif = [m for m in _msgs(db_session, version.id) if (m.payload or {}).get("recovery_audit")]
    assert notif and notif[-1].payload["detected_commit_count"] == 4


# ── OrchestratorSession TTL prune ───────────────────────────────────────────────


def test_cleanup_old_orchestrator_sessions_prunes_idle(db_session, monkeypatch):
    # Rows untouched > 7d on last_input_at are pruned; fresh rows survive.
    old = OrchestratorSession(project_slug="p-old", role="ai_agent", claude_session_id=uuid.uuid4())
    fresh = OrchestratorSession(project_slug="p-fresh", role="ai_agent", claude_session_id=uuid.uuid4())
    db_session.add_all([old, fresh])
    db_session.flush()
    db_session.execute(
        update(OrchestratorSession)
        .where(OrchestratorSession.project_slug == "p-old")
        .values(last_input_at=datetime.now(timezone.utc) - timedelta(days=8))
    )
    db_session.flush()
    monkeypatch.setattr(db_session, "commit", db_session.flush)  # SAVEPOINT-safe

    n = orchestrator.cleanup_old_orchestrator_sessions(db_session)

    assert n == 1
    remaining = db_session.execute(select(OrchestratorSession.project_slug)).scalars().all()
    assert "p-old" not in remaining and "p-fresh" in remaining


# ── _run_uat_deploy (plain redeploy of the existing compose) ────────────────────


class _FakeProc:
    """Minimal async-subprocess stand-in for ``orchestrator._run_uat_deploy`` tests."""

    def __init__(self, returncode: int, output: bytes = b""):
        self.returncode = returncode
        self._output = output

    async def communicate(self):
        return self._output, b""

    def kill(self):
        pass


async def test_run_uat_deploy_redeploys_existing_compose_with_version(monkeypatch):
    # Plain redeploy of the EXISTING compose (NOT uat-deploy.py) — exactly
    # `docker compose -f /opt/uat/<slug>/docker-compose.yml up -d --build --force-recreate`, with the FE
    # build-arg stamped via VITE_APP_VERSION. Exit 0 + serve-verify pass → (True, "OK").
    captured = {}

    async def _fake_exec(*cmd, stdout=None, stderr=None, env=None):
        captured["cmd"] = cmd
        captured["env"] = env
        return _FakeProc(0, b"deploy log tail")

    async def _serves_ok(project_slug, uat_slug):
        return True, "OK"

    monkeypatch.setattr(orchestrator.asyncio, "create_subprocess_exec", _fake_exec)
    monkeypatch.setattr(orchestrator, "_fe_app_version", lambda slug: "0.1.42")
    monkeypatch.setattr(orchestrator, "_verify_uat_serves", _serves_ok)  # serve-verify is its own unit
    ok, detail = await orchestrator._run_uat_deploy("nex-ledger", "ledger")

    assert ok is True and detail == "OK"
    assert list(captured["cmd"]) == [
        "docker",
        "compose",
        "-f",
        "/opt/uat/ledger/docker-compose.yml",
        "up",
        "-d",
        "--build",
        "--force-recreate",
    ]
    assert captured["env"]["VITE_APP_VERSION"] == "0.1.42"  # FE build-arg stamped
    assert "uat-deploy.py" not in " ".join(captured["cmd"])  # no provisioner invocation


async def test_run_uat_deploy_nonzero_exit_returns_failure(monkeypatch):
    async def _fake_exec(*cmd, stdout=None, stderr=None, env=None):
        return _FakeProc(2, b"boom: docker build failed")

    monkeypatch.setattr(orchestrator.asyncio, "create_subprocess_exec", _fake_exec)
    ok, detail = await orchestrator._run_uat_deploy("nex-ledger", "ledger")

    assert ok is False and "exit 2" in detail and "docker build failed" in detail


async def test_run_uat_deploy_spawn_failure_returns_failure(monkeypatch):
    async def _fake_exec(*cmd, stdout=None, stderr=None, env=None):
        raise OSError("docker not found")

    monkeypatch.setattr(orchestrator.asyncio, "create_subprocess_exec", _fake_exec)
    ok, detail = await orchestrator._run_uat_deploy("nex-ledger", "ledger")

    assert ok is False and "nepodarilo spustiť" in detail


async def test_run_uat_deploy_blocks_when_serve_verify_fails(monkeypatch):
    # icc-deploy §5.6 #2: ``up`` exit 0 is NOT success — a failed post-up serve-verify settles the deploy
    # to (False, reason) so the caller blocks rather than reporting a false success.
    async def _fake_exec(*cmd, stdout=None, stderr=None, env=None):
        return _FakeProc(0, b"Started")

    async def _serves_fail(project_slug, uat_slug):
        return False, "backend 'backend' /api not responding within 120s: connection refused"

    monkeypatch.setattr(orchestrator.asyncio, "create_subprocess_exec", _fake_exec)
    monkeypatch.setattr(orchestrator, "_fe_app_version", lambda slug: "0.1.0")
    monkeypatch.setattr(orchestrator, "_verify_uat_serves", _serves_fail)
    ok, detail = await orchestrator._run_uat_deploy("nex-ledger", "ledger")

    assert ok is False and "not responding" in detail


# ── _verify_uat_serves (post-up readiness gate) ─────────────────────────────────

_VERIFY_SRC_COMPOSE = """\
services:
  backend:
    build: .
    ports:
      - "10200:8000"
  frontend:
    build: ./frontend
    ports:
      - "10202:80"
  db:
    image: postgres:16-alpine
"""


class _ProbeRecorder:
    """Fake for ``orchestrator._compose_smoke_step`` scripting the BE self-probe (targets localhost) vs the
    FE cross-probe (targets the FE container name) results, recording every command."""

    def __init__(self, be: tuple[int, str], fe: tuple[int, str]) -> None:
        self._be, self._fe = be, fe
        self.calls: list[list[str]] = []

    async def __call__(self, cmd: list[str], timeout: int) -> tuple[int, str]:
        self.calls.append(cmd)
        if "python" in cmd:
            return self._be if "localhost" in " ".join(cmd) else self._fe
        return (0, "ok")


def _setup_verify(monkeypatch, tmp_path, *, uat_compose: bool = True) -> None:
    """Point UAT_ROOT + PROJECTS_ROOT at tmp dirs with a source compose (BE+FE) and (optionally) a UAT
    compose (presence-only — its ports are stripped in reality, so the source compose drives detection)."""
    uat_root = tmp_path / "uat"
    projects_root = tmp_path / "projects"
    led_uat = uat_root / "ledger"
    led_uat.mkdir(parents=True)
    if uat_compose:
        (led_uat / "docker-compose.yml").write_text("services: {}\n")
    src = projects_root / "nex-ledger"
    src.mkdir(parents=True)
    (src / "docker-compose.yml").write_text(_VERIFY_SRC_COMPOSE)
    monkeypatch.setattr(orchestrator, "UAT_ROOT", uat_root)
    monkeypatch.setattr(orchestrator.claude_agent, "PROJECTS_ROOT", projects_root)

    async def _no_sleep(*_a, **_k):
        return None

    monkeypatch.setattr(orchestrator.asyncio, "sleep", _no_sleep)


async def test_verify_uat_serves_pass_when_be_and_fe_respond(monkeypatch, tmp_path):
    _setup_verify(monkeypatch, tmp_path)
    rec = _ProbeRecorder(be=(0, "status 404"), fe=(0, "status 200"))
    monkeypatch.setattr(orchestrator, "_compose_smoke_step", rec)

    ok, detail = await orchestrator._verify_uat_serves("nex-ledger", "ledger")

    assert (ok, detail) == (True, "OK")
    # Both the backend (localhost) and the frontend (by its unique UAT container name) were probed.
    assert any("localhost:8000/api" in " ".join(c) for c in rec.calls), "backend /api probed on localhost"
    assert any("uat-ledger-frontend:80/" in " ".join(c) for c in rec.calls), "frontend probed by container name"


async def test_verify_uat_serves_fails_when_backend_silent(monkeypatch, tmp_path):
    _setup_verify(monkeypatch, tmp_path)
    rec = _ProbeRecorder(be=(1, "URLError: connection refused"), fe=(0, "status 200"))
    monkeypatch.setattr(orchestrator, "_compose_smoke_step", rec)

    ok, detail = await orchestrator._verify_uat_serves("nex-ledger", "ledger")

    assert ok is False
    assert detail.startswith("backend 'backend' /api not responding within 120s:")
    # A backend FAIL short-circuits the FE probe.
    assert not any("uat-ledger-frontend" in " ".join(c) for c in rec.calls)


async def test_verify_uat_serves_fails_when_frontend_silent(monkeypatch, tmp_path):
    _setup_verify(monkeypatch, tmp_path)
    rec = _ProbeRecorder(be=(0, "status 200"), fe=(1, "URLError: connection refused"))
    monkeypatch.setattr(orchestrator, "_compose_smoke_step", rec)

    ok, detail = await orchestrator._verify_uat_serves("nex-ledger", "ledger")

    assert ok is False
    assert detail.startswith("frontend 'frontend' not serving within 120s:")


async def test_verify_uat_serves_skips_when_no_uat_compose(monkeypatch, tmp_path):
    # Defensive skip (caller already guards existence) — never a NEW false FAIL.
    _setup_verify(monkeypatch, tmp_path, uat_compose=False)
    rec = _ProbeRecorder(be=(0, ""), fe=(0, ""))
    monkeypatch.setattr(orchestrator, "_compose_smoke_step", rec)

    ok, detail = await orchestrator._verify_uat_serves("nex-ledger", "ledger")

    assert (ok, detail) == (True, "OK")
    assert rec.calls == [], "a skip never spawns a probe"


# ── _fe_app_version (VITE_APP_VERSION = 0.1.<git rev-list --count HEAD>) ─────────


def test_fe_app_version_from_git_count(monkeypatch):
    class _R:
        returncode = 0
        stdout = "123\n"

    monkeypatch.setattr(orchestrator.subprocess, "run", lambda *a, **k: _R())
    assert orchestrator._fe_app_version("nex-ledger") == "0.1.123"


def test_fe_app_version_falls_back_when_git_unavailable(monkeypatch):
    def _boom(*a, **k):
        raise OSError("git not found")

    monkeypatch.setattr(orchestrator.subprocess, "run", _boom)
    assert orchestrator._fe_app_version("nex-ledger") == "0.1.0"


def test_fe_app_version_falls_back_on_nonzero_git(monkeypatch):
    class _R:
        returncode = 128
        stdout = ""

    monkeypatch.setattr(orchestrator.subprocess, "run", lambda *a, **k: _R())
    assert orchestrator._fe_app_version("missing-repo") == "0.1.0"
