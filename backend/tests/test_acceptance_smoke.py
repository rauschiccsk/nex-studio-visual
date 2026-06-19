"""v0.7.5 CR-1 / v0.7.9 — App-starts smoke (HARD gate at full-flow ``gate_g``) unit tests.

v0.7.9: the smoke is a deterministic BOOT check — build + boot the project's compose and confirm the
deployed app BOOTS and RESPONDS to HTTP (the readiness poll). It NO LONGER runs the acceptance suite
in-container (production images carry no pytest); behavioural depth is the Auditor's release oracle.

Two layers:

* **The runner** (:func:`orchestrator._run_app_starts_smoke` + helpers) — graceful SKIP when the
  project has no compose, a non-zero ``up`` / not-responding → ``(False, reason)``, a ready app →
  ``(True, "app booted + responds")`` with NO pytest run, teardown ALWAYS runs (the ``finally``), and
  the ephemeral override strips ``container_name`` + host ``ports`` via the Compose-Spec ``!reset`` tag.
  ``docker`` itself is never invoked — the single ``_compose_smoke_step`` subprocess seam is faked.

* **The wiring** (:func:`orchestrator.verify_done`) — the smoke runs ONLY at ``gate_g``; a FAIL
  short-circuits BEFORE the Coordinator judgment (the HARD deterministic gate), a PASS feeds a verdict
  line into the judge prompt, and a non-``gate_g`` gate never invokes the smoke at all. Plus the
  structural fast-fix guarantee: ``FAST_FIX_STAGE_ORDER`` has no ``gate_g``.
"""

from __future__ import annotations

import contextlib
import io
import types
import urllib.error
import urllib.request
import uuid

import pytest
from sqlalchemy import select

from backend.db.models.foundation import User
from backend.db.models.pipeline import PipelineMessage, PipelineState
from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.services import orchestrator
from backend.services.pipeline_status import PipelineStatusBlock

COMPOSE_YML = """\
services:
  backend:
    build: .
    container_name: demo-backend
    ports:
      - "10180:10180"
  postgres:
    image: postgres:16-alpine
    container_name: demo-postgres
    ports:
      - "10182:5432"
"""


# ---------------------------------------------------------------------------
# Runner: _run_app_starts_smoke + helpers
# ---------------------------------------------------------------------------


def _make_project(root, slug: str, *, compose: bool = True) -> None:
    """Materialise a fake project tree under *root*/<slug> for the discovery step (v0.7.9: the boot
    check keys ONLY on docker-compose.yml — no acceptance suite is required or run)."""
    proj = root / slug
    proj.mkdir(parents=True, exist_ok=True)
    if compose:
        (proj / "docker-compose.yml").write_text(COMPOSE_YML)


class _StepRecorder:
    """Fake for ``orchestrator._compose_smoke_step``: scripts ``(rc, out)`` per compose step
    (``up`` / ``ready`` = the in-container ``python`` /health boot probe / ``down``) and records every
    command it was asked to run. Unknown steps default to PASS. (v0.7.9: there is no longer a ``pytest``
    step — ``ran("pytest")`` is the regression guard that the in-container acceptance run is gone.)"""

    def __init__(self, results: dict[str, tuple[int, str]]) -> None:
        self._results = results
        self.calls: list[list[str]] = []

    async def __call__(self, cmd: list[str], timeout: int) -> tuple[int, str]:
        self.calls.append(cmd)
        if "python" in cmd:  # the readiness probe runs `exec -T backend python -c …`
            key = "ready"
        elif "up" in cmd:
            key = "up"
        elif "down" in cmd:
            key = "down"
        else:
            key = "other"
        return self._results.get(key, (0, "ok"))

    def ran(self, token: str) -> bool:
        return any(token in cmd for cmd in self.calls)

    def count(self, token: str) -> int:
        return sum(1 for cmd in self.calls if token in cmd)


@pytest.mark.asyncio
async def test_smoke_skips_without_compose(monkeypatch, tmp_path) -> None:
    """No ``docker-compose.yml`` → graceful SKIP (treated as PASS), never spawns docker."""
    monkeypatch.setattr(orchestrator.claude_agent, "PROJECTS_ROOT", tmp_path)
    _make_project(tmp_path, "noc", compose=False)
    rec = _StepRecorder({})
    monkeypatch.setattr(orchestrator, "_compose_smoke_step", rec)

    ok, detail = await orchestrator._run_app_starts_smoke("noc", "v0.7.9")

    assert ok is True
    assert "SKIPPED" in detail and "docker-compose.yml" in detail
    assert rec.calls == [], "a skip must never spawn a docker subprocess"


@pytest.mark.asyncio
async def test_smoke_up_fail_returns_reason_and_tears_down(monkeypatch, tmp_path) -> None:
    """A non-zero ``up`` → ``(False, reason)`` carrying the tail, AND teardown still runs (finally)."""
    monkeypatch.setattr(orchestrator.claude_agent, "PROJECTS_ROOT", tmp_path)
    _make_project(tmp_path, "boom")
    rec = _StepRecorder({"up": (1, "build error: missing base image")})
    monkeypatch.setattr(orchestrator, "_compose_smoke_step", rec)

    ok, detail = await orchestrator._run_app_starts_smoke("boom", "v0.7.9")

    assert ok is False
    assert detail.startswith("up exit 1:")
    assert "build error" in detail
    assert rec.ran("down"), "the isolated stack must be torn down even when 'up' failed"
    assert not rec.ran("python"), "a failed 'up' short-circuits before the readiness poll"


@pytest.mark.asyncio
async def test_smoke_pass_on_ready_no_pytest(monkeypatch, tmp_path) -> None:
    """``up`` ok + app becomes ready → ``(True, "app booted + responds")``, teardown runs, and NO
    in-container acceptance pytest is ever invoked (v0.7.9 — prod images have no pytest)."""
    monkeypatch.setattr(orchestrator.claude_agent, "PROJECTS_ROOT", tmp_path)
    _make_project(tmp_path, "green")
    rec = _StepRecorder({"up": (0, "Started"), "ready": (0, "status 200"), "down": (0, "")})
    monkeypatch.setattr(orchestrator, "_compose_smoke_step", rec)

    ok, detail = await orchestrator._run_app_starts_smoke("green", "v0.7.9")

    assert (ok, detail) == (True, "app booted + responds")
    assert rec.ran("up") and rec.ran("python") and rec.ran("down")
    assert not rec.ran("pytest"), "v0.7.9: the smoke must NOT run the acceptance pytest in the prod container"
    # Isolation: the stack is brought up under the dedicated ``-p <slug>-smoke`` project.
    up_cmd = next(cmd for cmd in rec.calls if "up" in cmd)
    assert "-p" in up_cmd and "green-smoke" in up_cmd


def test_override_strips_container_name_and_ports(tmp_path) -> None:
    """The ephemeral override resets ``container_name`` + ``ports`` for EVERY service via ``!reset``."""
    compose = tmp_path / "docker-compose.yml"
    compose.write_text(COMPOSE_YML)

    override = orchestrator._acceptance_smoke_override(compose)

    assert "  backend:" in override and "  postgres:" in override
    assert override.count("container_name: !reset null") == 2
    assert override.count("ports: !reset null") == 0  # ports reset to an empty list, not null
    assert override.count("ports: !reset []") == 2


@pytest.mark.asyncio
async def test_smoke_not_ready_timeout_returns_clear_fail(monkeypatch, tmp_path) -> None:
    """``up`` ok but ``/health`` never answers within budget → a CLEAR ``(False, "app did not boot /
    not responding …")`` FAIL, and teardown still runs. The probe is polled for the full bounded
    budget."""
    monkeypatch.setattr(orchestrator.claude_agent, "PROJECTS_ROOT", tmp_path)
    _make_project(tmp_path, "slowboot")

    async def _no_sleep(*_a, **_k):  # keep the bounded readiness loop instant
        return None

    monkeypatch.setattr(orchestrator.asyncio, "sleep", _no_sleep)
    rec = _StepRecorder({"up": (0, ""), "ready": (1, "URLError: <urlopen error [Errno 111] Connection refused>")})
    monkeypatch.setattr(orchestrator, "_compose_smoke_step", rec)

    ok, detail = await orchestrator._run_app_starts_smoke("slowboot", "v0.7.9")

    assert ok is False
    assert detail.startswith("app did not boot / not responding within 120s:")
    assert "Connection refused" in detail
    assert rec.ran("down"), "teardown runs even when readiness times out"
    expected = orchestrator.ACCEPTANCE_SMOKE_READY_TIMEOUT // orchestrator.ACCEPTANCE_SMOKE_READY_INTERVAL
    assert rec.count("python") == expected, "the readiness probe is polled for the full bounded budget"


@pytest.mark.asyncio
async def test_smoke_404_health_is_ready_pass(monkeypatch, tmp_path) -> None:
    """LIVE nex-asistent case: the probe path returns 404 (health is at the versioned route) → READY on
    the FIRST poll (no looping) → smoke PASS with NO pytest run. The 404→exit-0 mapping lives in the
    probe; here it is modelled by the readiness step returning rc 0."""
    monkeypatch.setattr(orchestrator.claude_agent, "PROJECTS_ROOT", tmp_path)
    _make_project(tmp_path, "v1health")
    rec = _StepRecorder({"up": (0, ""), "ready": (0, "status 404"), "down": (0, "")})
    monkeypatch.setattr(orchestrator, "_compose_smoke_step", rec)

    ok, detail = await orchestrator._run_app_starts_smoke("v1health", "v0.7.9")

    assert (ok, detail) == (True, "app booted + responds")
    assert rec.count("python") == 1, "a 404 (server up) is READY on the first poll — no looping"
    assert not rec.ran("pytest"), "v0.7.9: ready ⇒ PASS without any acceptance pytest run"


# ---------------------------------------------------------------------------
# Readiness probe classification (v0.7.7): server responded (status < 500) = READY.
# Execute the in-container probe SOURCE locally with a stubbed urlopen so the actual
# status→exit-code mapping is verified (a mocked _compose_smoke_step cannot reach it).
# ---------------------------------------------------------------------------


def _exec_probe(monkeypatch, fake_urlopen) -> tuple[int, str]:
    """Run :func:`orchestrator._readiness_probe_src` with ``urllib.request.urlopen`` stubbed; return
    ``(exit_code, stdout)``. The probe ``import urllib.request`` resolves to the patched module."""
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    buf = io.StringIO()
    code = 0
    try:
        with contextlib.redirect_stdout(buf):
            exec(compile(orchestrator._readiness_probe_src(10180), "<probe>", "exec"), {})  # noqa: S102
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1
    return code, buf.getvalue()


def test_readiness_probe_404_is_ready(monkeypatch) -> None:
    """A 404 (server responding, probe path not a declared route) → READY (exit 0) — the v0.7.7 fix."""

    def _u(*_a, **_k):
        raise urllib.error.HTTPError("http://localhost/health", 404, "Not Found", {}, None)

    code, out = _exec_probe(monkeypatch, _u)
    assert code == 0
    assert "404" in out


def test_readiness_probe_200_is_ready(monkeypatch) -> None:
    """A 2xx success → READY (exit 0) — unchanged happy path."""

    def _u(*_a, **_k):
        return types.SimpleNamespace(status=200)

    code, out = _exec_probe(monkeypatch, _u)
    assert code == 0
    assert "200" in out


def test_readiness_probe_connection_refused_keeps_polling(monkeypatch) -> None:
    """No HTTP response (connection refused — server not accepting yet) → keep polling (exit 1)."""

    def _u(*_a, **_k):
        raise urllib.error.URLError("Connection refused")

    code, _out = _exec_probe(monkeypatch, _u)
    assert code == 1


def test_readiness_probe_5xx_keeps_polling(monkeypatch) -> None:
    """A 5xx (server up but signalling starting/unavailable) → keep polling (exit 1)."""
    for status in (500, 503):

        def _u(*_a, _s=status, **_k):
            raise urllib.error.HTTPError("http://localhost/health", _s, "err", {}, None)

        code, out = _exec_probe(monkeypatch, _u)
        assert code == 1, f"status {status} must keep polling"
        assert str(status) in out


def test_compose_backend_port_extraction(tmp_path) -> None:
    """The readiness target = the ``backend`` service's CONTAINER port (short + long syntax); ``None``
    when undeterminable so the caller skips the poll rather than guess."""
    short = tmp_path / "short.yml"
    short.write_text(COMPOSE_YML)  # backend ports "10180:10180" → container port 10180
    assert orchestrator._compose_backend_port(short) == 10180

    longform = tmp_path / "long.yml"
    longform.write_text("services:\n  backend:\n    ports:\n      - target: 8000\n        published: 18000\n")
    assert orchestrator._compose_backend_port(longform) == 8000

    noports = tmp_path / "noports.yml"
    noports.write_text("services:\n  backend:\n    image: x\n")
    assert orchestrator._compose_backend_port(noports) is None


# ---------------------------------------------------------------------------
# Wiring: verify_done HARD gate (gate_g only) + fast-fix safety
# ---------------------------------------------------------------------------


def _mk_block(stage: str, kind: str = "gate_report") -> PipelineStatusBlock:
    """A status block with empty commits/deliverables → ``verify_mechanical`` is a no-op (PASS)."""
    return PipelineStatusBlock(stage=stage, kind=kind, summary="ok", awaiting="director")


def _seed_version(db, stage: str) -> uuid.UUID:
    creator = User(
        username=f"sm_{uuid.uuid4().hex[:8]}",
        email=f"sm_{uuid.uuid4().hex[:8]}@test.local",
        password_hash="x",
        role="ri",
    )
    db.add(creator)
    db.flush()
    project = Project(
        name="Smoke Fixture",
        slug=f"smoke-{uuid.uuid4().hex[:8]}",
        category="multimodule",
        description="v0.7.5 CR-1 smoke test fixture.",
        created_by=creator.id,
    )
    db.add(project)
    db.flush()
    version = Version(project_id=project.id, version_number="v0.7.5", status="active")
    db.add(version)
    db.flush()
    state = PipelineState(
        version_id=version.id,
        flow_type="new_version",
        current_stage=stage,
        current_actor="auditor",
        status="agent_working",
    )
    db.add(state)
    db.flush()
    return version.id


class _FakeAgent:
    """Scripted stand-in for ``orchestrator.invoke_agent`` recording each call's role + prompt."""

    def __init__(self, result_by_role: dict[str, object]) -> None:
        self._result_by_role = result_by_role
        self.calls: list[tuple[str, str]] = []

    async def __call__(self, db, **kw):  # noqa: ANN001 - mirrors invoke_agent's (db, **kwargs) shape
        self.calls.append((kw["role"], kw.get("prompt", "")))
        return self._result_by_role[kw["role"]]

    @property
    def roles(self) -> list[str]:
        return [r for r, _ in self.calls]


@pytest.mark.asyncio
async def test_verify_done_gate_g_smoke_fail_short_circuits(db_session, monkeypatch) -> None:
    """At ``gate_g`` a smoke FAIL returns a non-None reason BEFORE the judgment (no Coordinator turn),
    and records the evidence as a ``system→director`` message — the HARD deterministic gate."""
    version_id = _seed_version(db_session, "gate_g")

    async def _smoke(slug, version_label):
        return False, "up exit 1: boom"

    monkeypatch.setattr(orchestrator, "_run_app_starts_smoke", _smoke)
    fake = _FakeAgent({"coordinator": _mk_block("gate_g")})
    monkeypatch.setattr(orchestrator, "invoke_agent", fake)

    reason, directive, is_coord_error = await orchestrator.verify_done(db_session, version_id, _mk_block("gate_g"))

    assert reason == "App-starts smoke FAIL: up exit 1: boom"
    assert directive is None and is_coord_error is False
    assert "coordinator" not in fake.roles, "a smoke FAIL must short-circuit BEFORE the judgment turn"
    smoke_msgs = (
        db_session.execute(
            select(PipelineMessage).where(
                PipelineMessage.version_id == version_id,
                PipelineMessage.author == "system",
                PipelineMessage.recipient == "director",
            )
        )
        .scalars()
        .all()
    )
    assert len(smoke_msgs) == 1
    assert smoke_msgs[0].payload == {"smoke": {"pass": False, "detail": "up exit 1: boom"}}
    assert "FAIL" in smoke_msgs[0].content


@pytest.mark.asyncio
async def test_verify_done_gate_g_smoke_pass_runs_judgment_with_verdict(db_session, monkeypatch) -> None:
    """A smoke PASS records the evidence, runs the judgment, and injects a verdict line into its prompt."""
    version_id = _seed_version(db_session, "gate_g")

    async def _smoke(slug, version_label):
        return True, "OK"

    monkeypatch.setattr(orchestrator, "_run_app_starts_smoke", _smoke)
    fake = _FakeAgent({"coordinator": _mk_block("gate_g")})
    monkeypatch.setattr(orchestrator, "invoke_agent", fake)

    reason, directive, is_coord_error = await orchestrator.verify_done(db_session, version_id, _mk_block("gate_g"))

    assert reason is None and directive is None and is_coord_error is False
    assert "coordinator" in fake.roles, "a smoke PASS must proceed to the judgment turn"
    coord_prompt = next(prompt for role, prompt in fake.calls if role == "coordinator")
    assert "app-starts smoke" in coord_prompt.lower(), "the judge prompt must carry the smoke verdict line"


@pytest.mark.asyncio
async def test_verify_done_non_gate_g_never_runs_smoke(db_session, monkeypatch) -> None:
    """At a non-``gate_g`` gate the smoke is never invoked (and the judge prompt has no verdict line)."""
    version_id = _seed_version(db_session, "gate_b")
    called = {"smoke": False}

    async def _smoke(slug, version_label):
        called["smoke"] = True
        return True, "OK"

    monkeypatch.setattr(orchestrator, "_run_app_starts_smoke", _smoke)
    fake = _FakeAgent({"coordinator": _mk_block("gate_b")})
    monkeypatch.setattr(orchestrator, "invoke_agent", fake)

    reason, _directive, _err = await orchestrator.verify_done(db_session, version_id, _mk_block("gate_b"))

    assert reason is None
    assert called["smoke"] is False, "the smoke must run ONLY at gate_g"
    coord_prompt = next(prompt for role, prompt in fake.calls if role == "coordinator")
    assert "app-starts smoke" not in coord_prompt.lower(), "no smoke verdict line outside gate_g"


def test_fast_fix_stage_order_has_no_gate_g() -> None:
    """Structural fast-fix guarantee: the fast-fix lane can never reach the smoke (no ``gate_g``)."""
    assert "gate_g" not in orchestrator.FAST_FIX_STAGE_ORDER
    assert "gate_g" in orchestrator.STAGE_ORDER, "the full-flow lane still owns gate_g"
