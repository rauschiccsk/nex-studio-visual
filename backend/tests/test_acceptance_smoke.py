"""App-starts smoke + release acceptance (HARD gate at full-flow ``gate_g``) unit tests.

gate-g-hardening GAP 1 (2026-06-23) split the gate_g runtime floor into TWO legs of ONE up/down cycle:

* **boot leg** (:func:`orchestrator._run_app_starts_smoke`) — build + boot the project's compose under an
  ISOLATED ``-p <slug>-smoke`` project and confirm the deployed app BOOTS and RESPONDS to HTTP (the v0.7.7
  path-agnostic readiness poll). NO pytest in the prod image.
* **release-acceptance leg** (:func:`orchestrator._run_release_acceptance`) — run the project's black-box
  host-executable ``release_smoke_test.sh`` against the SAME running stack, requiring exit-0 + a non-zero
  ``ASSERTIONS_RUN`` (the anti-empty floor). A web app (a ``backend`` service present) with NO script →
  **FAIL** ("required but missing"), never a silent SKIP; a pure lib/worker → legit SKIP.

The two legs share ONE boot/teardown via :func:`orchestrator._boot_smoke_stack`, driven by
:func:`orchestrator._run_release_smoke`. ``docker`` itself is never invoked here — the single
``_compose_smoke_step`` subprocess seam (boot/teardown/readiness) and the ``_run_acceptance_script`` seam
(the host script) are faked.

The wiring (:func:`orchestrator.verify_done`) runs both legs ONLY at ``gate_g``: a boot FAIL short-circuits
BEFORE the Coordinator judgment; the acceptance outcome is recorded as a ``release_acceptance`` notification
and fed into the judge but does NOT short-circuit (the PASS verdict guard + the disabled FE button enforce
it). :func:`orchestrator._release_acceptance_satisfied` + the ``verdict`` PASS guard refuse a PASS until the
acceptance reached exit-0 / a legit SKIP this iteration.
"""

from __future__ import annotations

import contextlib
import io
import types
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.services import orchestrator

# v2.0.0-dev: the acceptance-smoke DRIVER + boot/readiness/compose-port helpers below are the SURVIVING
# behavioural release-oracle (§2.5) — they stay live and GREEN on the v2 schema (the v2 Verifikácia round
# REUSES _run_release_smoke against internal fixtures). The tests that wired the driver into the v1 ENGINE
# gate_g verdict flow (verify_done at gate_g, _release_acceptance_satisfied gating of the v1 Director verdict)
# were SUPERSEDED by CR-V2-014 (the smoke now feeds the independent Auditor verdict on the v2 verifikacia
# phase) and were removed in the Milestone-I cleanup — their v2 replacement is
# tests/test_orchestrator_v2_verifikacia.py.

COMPOSE_YML = """\
services:
  backend:
    build: .
    container_name: demo-backend
    ports:
      - "10180:10180"
  frontend:
    build: ./frontend
    container_name: demo-frontend
    ports:
      - "10181:80"
  postgres:
    image: postgres:16-alpine
    container_name: demo-postgres
    ports:
      - "10182:5432"
"""

# A compose with a backend web app but NO frontend service — the nex-asistent "no FE emitted" bug.
COMPOSE_YML_NO_FRONTEND = """\
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

# A pure worker/lib stack — NO backend web app, NO frontend (a release acceptance script is NOT required).
COMPOSE_YML_WORKER_ONLY = """\
services:
  worker:
    build: .
    container_name: demo-worker
  redis:
    image: redis:7-alpine
    container_name: demo-redis
"""


def _seed_aktualizacie_frontend(proj: Path) -> None:
    """Seed the scaffolded *Aktualizácie* FE tab (obs-2 Part B Part 2 gate 2b): an Updates page, a ``/updates``
    route in the router, and an "Aktualizácie" sidebar entry — so ``_check_aktualizacie_frontend`` passes."""
    (proj / "frontend" / "src" / "pages").mkdir(parents=True, exist_ok=True)
    (proj / "frontend" / "src" / "components" / "layout").mkdir(parents=True, exist_ok=True)
    (proj / "frontend" / "src" / "pages" / "UpdatesPage.tsx").write_text(
        "export default function UpdatesPage() { return <div>Aktualizácie</div>; }\n"
    )
    (proj / "frontend" / "src" / "App.tsx").write_text(
        'import UpdatesPage from "./pages/UpdatesPage";\n<Route path="updates" element={<UpdatesPage />} />\n'
    )
    (proj / "frontend" / "src" / "components" / "layout" / "Sidebar.tsx").write_text(
        '<NavItem label="Aktualizácie" onClick={() => navigate("/updates")} />\n'
    )


def _make_project(
    root,
    slug: str,
    *,
    compose: bool = True,
    compose_yml: str = COMPOSE_YML,
    script: bool = False,
    frontend_tab: bool = False,
    env: str | None = None,
    env_example: str | None = None,
) -> Path:
    """Materialise a fake project tree under *root*/<slug>; optionally seed ``release_smoke_test.sh``, the
    scaffolded Aktualizácie FE tab (``frontend_tab``), and the ``.env`` / ``.env.example`` pair (release-smoke
    boot fix A: the live ``.env`` may be INCOMPLETE while ``.env.example`` carries the complete dev default)."""
    proj = root / slug
    proj.mkdir(parents=True, exist_ok=True)
    if compose:
        (proj / "docker-compose.yml").write_text(compose_yml)
    if script:
        (proj / "release_smoke_test.sh").write_text("#!/usr/bin/env bash\necho ASSERTIONS_RUN=1\n")
    if frontend_tab:
        _seed_aktualizacie_frontend(proj)
    if env is not None:
        (proj / ".env").write_text(env)
    if env_example is not None:
        (proj / ".env.example").write_text(env_example)
    return proj


def _mk_stack(
    tmp_path, *, compose_yml: str = COMPOSE_YML, slug: str = "demo", up_rc: int = 0
) -> orchestrator._SmokeStack:
    """A booted :class:`_SmokeStack` pointing at a real compose file (the readiness + env helpers read it)."""
    import yaml

    compose = tmp_path / f"{slug}-compose.yml"
    compose.write_text(compose_yml)
    services = (yaml.safe_load(compose_yml) or {}).get("services") or {}
    roles = orchestrator.uat_provisioner.identify_service_roles(services)
    override = tmp_path / "smoke.override.yml"
    base = ["docker", "compose", "-p", f"{slug}-smoke", "-f", str(compose), "-f", str(override)]
    return orchestrator._SmokeStack(
        base=base, compose=compose, override=override, project=f"{slug}-smoke", roles=roles, up_rc=up_rc, up_detail=""
    )


class _StepRecorder:
    """Fake for ``orchestrator._compose_smoke_step``: scripts ``(rc, out)`` per compose step
    (``up`` / the in-container ``python`` /health boot probe / ``down``) and records every command it ran.
    Unknown steps default to PASS. ``ran("pytest")`` is the regression guard that no in-container pytest is
    ever invoked."""

    def __init__(self, results: dict[str, tuple[int, str]]) -> None:
        self._results = results
        self.calls: list[list[str]] = []

    async def __call__(self, cmd: list[str], timeout: int) -> tuple[int, str]:
        self.calls.append(cmd)
        if "python" in cmd:  # the readiness probe runs `exec -T <svc> python -c …`
            joined = " ".join(cmd)
            key = "ready" if "localhost" in joined else "ready_fe"
            if key not in self._results:
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


# ---------------------------------------------------------------------------
# Driver: _run_release_smoke (boot + acceptance in ONE up/down cycle)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_driver_skips_without_compose(monkeypatch, tmp_path) -> None:
    """No ``docker-compose.yml`` → both legs graceful SKIP (treated as PASS), never spawns docker."""
    monkeypatch.setattr(orchestrator.claude_agent, "PROJECTS_ROOT", tmp_path)
    _make_project(tmp_path, "noc", compose=False)
    rec = _StepRecorder({})
    monkeypatch.setattr(orchestrator, "_compose_smoke_step", rec)

    (boot_ok, boot_detail), acceptance = await orchestrator._run_release_smoke("noc", "v1.0.0", (0, 0))

    assert boot_ok is True and "SKIPPED" in boot_detail and "docker-compose.yml" in boot_detail
    assert acceptance == (True, "SKIPPED — no docker-compose.yml", True)
    assert rec.calls == [], "a skip must never spawn a docker subprocess"


@pytest.mark.asyncio
async def test_driver_fails_when_backend_present_no_frontend(monkeypatch, tmp_path) -> None:
    """icc-deploy §5.6 #1: a backend web app with NO frontend service → structural FAIL BEFORE any ``up``."""
    monkeypatch.setattr(orchestrator.claude_agent, "PROJECTS_ROOT", tmp_path)
    _make_project(tmp_path, "nofe", compose_yml=COMPOSE_YML_NO_FRONTEND)
    rec = _StepRecorder({})
    monkeypatch.setattr(orchestrator, "_compose_smoke_step", rec)

    (boot_ok, boot_detail), acceptance = await orchestrator._run_release_smoke("nofe", "v1.0.0", (0, 0))

    assert boot_ok is False
    assert boot_detail == "compose has a backend web app but no frontend service"
    assert acceptance is None
    assert rec.calls == [], "the structural FAIL must short-circuit before spawning docker (no wasted build)"


@pytest.mark.asyncio
async def test_driver_up_fail_returns_reason_and_tears_down(monkeypatch, tmp_path) -> None:
    """A non-zero ``up`` → boot ``(False, reason)`` carrying the tail, acceptance None, AND teardown runs."""
    monkeypatch.setattr(orchestrator.claude_agent, "PROJECTS_ROOT", tmp_path)
    _make_project(tmp_path, "boom")
    rec = _StepRecorder({"up": (1, "build error: missing base image")})
    monkeypatch.setattr(orchestrator, "_compose_smoke_step", rec)

    (boot_ok, boot_detail), acceptance = await orchestrator._run_release_smoke("boom", "v1.0.0", (0, 0))

    assert boot_ok is False
    assert boot_detail.startswith("up exit 1:") and "build error" in boot_detail
    assert acceptance is None
    assert rec.ran("down"), "the isolated stack must be torn down even when 'up' failed"
    assert not rec.ran("python"), "a failed 'up' short-circuits before the readiness poll"


@pytest.mark.asyncio
async def test_driver_pass_runs_acceptance_no_pytest(monkeypatch, tmp_path) -> None:
    """``up`` ok + app ready + the host acceptance script exit-0 with assertions → boot PASS + acceptance
    PASS, teardown runs, and NO in-container pytest is ever invoked."""
    monkeypatch.setattr(orchestrator.claude_agent, "PROJECTS_ROOT", tmp_path)
    _make_project(tmp_path, "green", script=True, frontend_tab=True)
    rec = _StepRecorder({"up": (0, "Started"), "ready": (0, "status 200"), "down": (0, "")})
    monkeypatch.setattr(orchestrator, "_compose_smoke_step", rec)

    async def _script(script, env):
        return 0, "ASSERTIONS_RUN=3\nFEATURE_ASSERTIONS_RUN=1\n"

    monkeypatch.setattr(orchestrator, "_run_acceptance_script", _script)

    # obs-2 Part B Part 2: the Aktualizácie 2a behavioural probe is exercised by its own tests; here stub it
    # green so the driver's boot→acceptance happy path (FE tab seeded → 2b passes) stays focused.
    async def _akt_probe(*_a, **_k):
        return True, "Aktualizácie OK"

    monkeypatch.setattr(orchestrator, "_probe_release_notes", _akt_probe)

    (boot_ok, boot_detail), acceptance = await orchestrator._run_release_smoke("green", "v1.0.0", (1, 0))

    # ICCINT-44: the detail now REPORTS what was measured instead of a constant; the probe verdict is the
    # invariant, its wording is not.
    assert boot_ok is True and orchestrator._APP_RESPONDS in boot_detail.lower()
    # CR-V2-051: no declaration → (0,0) floor; PASS detail now carries the feature/negative breakdown.
    assert acceptance == (
        True,
        "release acceptance PASS — 3 assertions (1 feature / 0 negative; declared 1 feature / 0 safety)",
        False,
    )
    assert rec.ran("up") and rec.ran("python") and rec.ran("down")
    assert not rec.ran("pytest"), "the smoke must NOT run pytest in the prod container"
    up_cmd = next(cmd for cmd in rec.calls if "up" in cmd)
    assert "-p" in up_cmd and "green-smoke" in up_cmd  # isolation under the dedicated project


@pytest.mark.asyncio
async def test_driver_boot_fail_skips_acceptance(monkeypatch, tmp_path) -> None:
    """``up`` ok but the app never answers /health → boot FAIL, acceptance NOT run (None), teardown runs."""
    monkeypatch.setattr(orchestrator.claude_agent, "PROJECTS_ROOT", tmp_path)
    _make_project(tmp_path, "slowboot", script=True)

    async def _no_sleep(*_a, **_k):
        return None

    monkeypatch.setattr(orchestrator.asyncio, "sleep", _no_sleep)
    rec = _StepRecorder({"up": (0, ""), "ready": (1, "URLError: Connection refused"), "down": (0, "")})
    monkeypatch.setattr(orchestrator, "_compose_smoke_step", rec)

    called = {"script": False}

    async def _script(script, env):
        called["script"] = True
        return 0, "ASSERTIONS_RUN=1"

    monkeypatch.setattr(orchestrator, "_run_acceptance_script", _script)

    (boot_ok, boot_detail), acceptance = await orchestrator._run_release_smoke("slowboot", "v1.0.0", (0, 0))

    assert boot_ok is False and boot_detail.startswith("app did not boot / not responding within 120s:")
    assert acceptance is None
    assert called["script"] is False, "acceptance must NOT run when the boot leg failed"
    assert rec.ran("down")


# ---------------------------------------------------------------------------
# A (release-smoke-boot-and-batch-fixes.md): the smoke boots with a COMPLETE, .env.example-derived env-file
# ---------------------------------------------------------------------------


def test_render_smoke_env_completes_incomplete_example(tmp_path) -> None:
    """``_render_smoke_env`` mirrors ``scripts/ci_render_dotenv.py``: ``DATABASE_URL`` host/password rewritten
    to the compose ``db`` service (scheme PRESERVED), ``POSTGRES_PASSWORD`` GUARANTEED present (appended when
    the example omits it, forced to the known value when present), other lines verbatim. No example → False."""
    src = tmp_path / ".env.example"
    dst = tmp_path / "smoke.env"

    # An example WITH a driver URL but WITHOUT POSTGRES_PASSWORD → it must be APPENDED (the fail-fast guard).
    src.write_text("DATABASE_URL=postgresql+pg8000://appuser:CHANGE_ME@localhost:5432/appdb\nJWT=abc\n")
    assert orchestrator._render_smoke_env(src, dst) is True
    out = dst.read_text()
    assert "POSTGRES_PASSWORD=ci" in out, "the missing POSTGRES_PASSWORD must be appended"
    assert "postgresql+pg8000://appuser:ci@db:5432/appdb" in out, "host+password → db/ci, scheme preserved"
    assert "JWT=abc" in out, "unrelated lines pass through verbatim"

    # An example WITH POSTGRES_PASSWORD → forced to the known value, exactly once (no duplicate append).
    src.write_text("POSTGRES_PASSWORD=nexpay_dev\nDB_PASSWORD=nexpay_dev\n")
    assert orchestrator._render_smoke_env(src, dst) is True
    out = dst.read_text()
    assert out.count("POSTGRES_PASSWORD=ci") == 1 and "nexpay_dev" not in out
    assert "DB_PASSWORD=ci" in out

    # No .env.example → False → the caller boots WITHOUT an --env-file (unchanged legacy behaviour).
    assert orchestrator._render_smoke_env(tmp_path / "missing.example", dst) is False


def test_acceptance_smoke_override_injects_env_file_per_service(tmp_path) -> None:
    """A (fix): the override injects the rendered *smoke_env* as an ``env_file`` for EVERY service, so a
    service reading ``env_file: .env`` (migrate/backend) gets the COMPLETE env INSIDE the container —
    ``--env-file`` feeds only compose interpolation, never the containers (that was the missing DATABASE_URL).
    Without *smoke_env* the override is the legacy strip-only (no env_file)."""
    compose = tmp_path / "docker-compose.yml"
    compose.write_text(
        "services:\n"
        "  db:\n    image: postgres:16\n    environment:\n      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?}\n"
        "  migrate:\n    image: app\n    env_file: .env\n"
        "  backend:\n    image: app\n    env_file: .env\n"
    )
    smoke_env = tmp_path / "smoke.env"
    smoke_env.write_text("POSTGRES_PASSWORD=ci\nDATABASE_URL=postgresql+pg8000://u:ci@db:5432/app\n")

    with_env = orchestrator._acceptance_smoke_override(compose, smoke_env)
    # Injected under EVERY service; compose concatenates env_file lists so the later rendered file wins.
    assert with_env.count(f"      - {smoke_env}") == 3, "rendered env_file injected per service"
    assert with_env.count("    env_file:") == 3

    without = orchestrator._acceptance_smoke_override(compose)
    assert "env_file:" not in without, "no smoke_env → legacy strip-only override (unchanged)"


class _EnvFileCapturingRecorder(_StepRecorder):
    """A ``_StepRecorder`` that also captures the ``--env-file`` path + its CONTENT at ``up`` time (before the
    ``finally`` teardown rmtree's the smoke tmpdir), so the test can assert the rendered env COMPLETED the
    missing ``POSTGRES_PASSWORD``."""

    def __init__(self, results: dict[str, tuple[int, str]]) -> None:
        super().__init__(results)
        self.env_file_path: str | None = None
        self.env_file_content: str | None = None

    async def __call__(self, cmd: list[str], timeout: int) -> tuple[int, str]:
        if "up" in cmd and "--env-file" in cmd:
            path = Path(cmd[cmd.index("--env-file") + 1])
            self.env_file_path = str(path)
            self.env_file_content = path.read_text()
        return await super().__call__(cmd, timeout)


@pytest.mark.asyncio
async def test_driver_renders_env_file_when_live_env_incomplete(monkeypatch, tmp_path) -> None:
    """A: the live ``.env`` LACKS ``POSTGRES_PASSWORD`` (the compose's ``${POSTGRES_PASSWORD:?…}`` guard would
    fail interpolation → the app never boots — the nex-payables 1.1.0 blocker) but ``.env.example`` HAS it → the
    boot renders a COMPLETE throwaway env and hands it to ``docker compose --env-file``, so the stack boots. The
    live ``.env`` is NEVER clobbered, and the rendered env is a throwaway (not the project's ``.env``)."""
    monkeypatch.setattr(orchestrator.claude_agent, "PROJECTS_ROOT", tmp_path)
    live_env = "DATABASE_URL=postgresql+pg8000://app:x@db:5432/appdb\n"  # deliberately NO POSTGRES_PASSWORD
    example = "DATABASE_URL=postgresql+pg8000://app:CHANGE_ME@db:5432/appdb\nPOSTGRES_PASSWORD=nexpay_dev\n"
    proj = _make_project(tmp_path, "envr", script=True, frontend_tab=True, env=live_env, env_example=example)

    rec = _EnvFileCapturingRecorder({"up": (0, "Started"), "ready": (0, "status 200"), "down": (0, "")})
    monkeypatch.setattr(orchestrator, "_compose_smoke_step", rec)

    async def _script(script, env):
        return 0, "ASSERTIONS_RUN=1"

    monkeypatch.setattr(orchestrator, "_run_acceptance_script", _script)

    async def _akt_probe(*_a, **_k):
        return True, "Aktualizácie OK"

    monkeypatch.setattr(orchestrator, "_probe_release_notes", _akt_probe)

    (boot_ok, boot_detail), acceptance = await orchestrator._run_release_smoke("envr", "v1.1.0", (0, 0))

    assert boot_ok is True, boot_detail
    up_cmd = next(cmd for cmd in rec.calls if "up" in cmd)
    assert "--env-file" in up_cmd, "the up command must carry the rendered --env-file"
    assert rec.env_file_content is not None, "the smoke rendered no env-file despite a .env.example"
    assert "POSTGRES_PASSWORD=ci" in rec.env_file_content, "the rendered env completes the missing guard var"
    # The live .env is untouched (still lacks POSTGRES_PASSWORD) and the rendered env is a THROWAWAY (not .env).
    assert "POSTGRES_PASSWORD" not in (proj / ".env").read_text()
    assert rec.env_file_path is not None and not rec.env_file_path.endswith("/.env")


# ---------------------------------------------------------------------------
# Boot leg: _run_app_starts_smoke(stack)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_boot_leg_pass_on_ready(monkeypatch, tmp_path) -> None:
    """The boot leg passes when BE + FE both answer, and SAYS what it saw (ICCINT-44)."""
    rec = _StepRecorder({"ready": (0, "status 200"), "ready_fe": (0, "status 404")})
    monkeypatch.setattr(orchestrator, "_compose_smoke_step", rec)
    stack = _mk_stack(tmp_path)

    ok, detail = await orchestrator._run_app_starts_smoke(stack)

    assert ok is True and orchestrator._APP_RESPONDS in detail.lower()
    assert rec.count("python") == 2, "BE self-probe + FE cross-probe, each ready on the first poll"
    assert not rec.ran("up") and not rec.ran("down"), "the boot leg never owns up/down (the CM does)"


@pytest.mark.asyncio
async def test_boot_leg_not_ready_timeout(monkeypatch, tmp_path) -> None:
    """BE never answers /health within budget → a CLEAR ``(False, "app did not boot …")`` FAIL."""

    async def _no_sleep(*_a, **_k):
        return None

    monkeypatch.setattr(orchestrator.asyncio, "sleep", _no_sleep)
    rec = _StepRecorder({"ready": (1, "URLError: <urlopen error [Errno 111] Connection refused>")})
    monkeypatch.setattr(orchestrator, "_compose_smoke_step", rec)
    stack = _mk_stack(tmp_path)

    ok, detail = await orchestrator._run_app_starts_smoke(stack)

    assert ok is False
    assert detail.startswith("app did not boot / not responding within 120s:") and "Connection refused" in detail
    expected = orchestrator.ACCEPTANCE_SMOKE_READY_TIMEOUT // orchestrator.ACCEPTANCE_SMOKE_READY_INTERVAL
    assert rec.count("python") == expected, "the readiness probe is polled for the full bounded budget"


@pytest.mark.asyncio
async def test_boot_leg_frontend_unreachable(monkeypatch, tmp_path) -> None:
    """BE boots but the frontend never serves within the budget → a CLEAR FAIL naming the frontend."""

    async def _no_sleep(*_a, **_k):
        return None

    monkeypatch.setattr(orchestrator.asyncio, "sleep", _no_sleep)
    rec = _StepRecorder({"ready": (0, "status 200"), "ready_fe": (1, "URLError: connection refused")})
    monkeypatch.setattr(orchestrator, "_compose_smoke_step", rec)
    stack = _mk_stack(tmp_path)

    ok, detail = await orchestrator._run_app_starts_smoke(stack)

    assert ok is False
    assert detail.startswith("frontend 'frontend' not serving within 120s:") and "connection refused" in detail


# ---------------------------------------------------------------------------
# Release-acceptance leg: _run_release_acceptance(stack, slug) — the archetype-conditional gate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_acceptance_web_app_missing_script_is_fail(monkeypatch, tmp_path) -> None:
    """A web app (backend present) with NO ``release_smoke_test.sh`` → FAIL ("required but missing"),
    NEVER a silent SKIP — the blanket-SKIP-bypasses-the-oracle risk."""
    monkeypatch.setattr(orchestrator.claude_agent, "PROJECTS_ROOT", tmp_path)
    _make_project(tmp_path, "web", script=False)
    stack = _mk_stack(tmp_path)  # COMPOSE_YML has a backend → web app

    ok, detail, skipped = await orchestrator._run_release_acceptance(stack, "web", (1, 0))

    assert ok is False and skipped is False
    assert "required but missing" in detail


@pytest.mark.asyncio
async def test_acceptance_worker_missing_script_is_skip(monkeypatch, tmp_path) -> None:
    """A pure lib/worker stack (NO backend service) with no script → legit SKIP (acceptance not required)."""
    monkeypatch.setattr(orchestrator.claude_agent, "PROJECTS_ROOT", tmp_path)
    _make_project(tmp_path, "wk", compose_yml=COMPOSE_YML_WORKER_ONLY, script=False)
    stack = _mk_stack(tmp_path, compose_yml=COMPOSE_YML_WORKER_ONLY, slug="wk")

    ok, detail, skipped = await orchestrator._run_release_acceptance(stack, "wk", (1, 0))

    assert ok is True and skipped is True
    assert "SKIPPED" in detail


@pytest.mark.asyncio
async def test_acceptance_script_pass_with_assertions(monkeypatch, tmp_path) -> None:
    """Script present + exit-0 + ASSERTIONS_RUN>0 → PASS naming the assertion count; the smoke env is passed."""
    monkeypatch.setattr(orchestrator.claude_agent, "PROJECTS_ROOT", tmp_path)
    _make_project(tmp_path, "ok", script=True)
    stack = _mk_stack(tmp_path, slug="ok")
    seen = {}

    async def _script(script, env):
        seen["script"] = script
        seen["env"] = env
        return 0, "some output\nASSERTIONS_RUN=5\nFEATURE_ASSERTIONS_RUN=1\n"

    monkeypatch.setattr(orchestrator, "_run_acceptance_script", _script)

    ok, detail, skipped = await orchestrator._run_release_acceptance(stack, "ok", (1, 0))

    assert (ok, skipped) == (True, False)
    # CR-V2-051: PASS detail now reports the feature/negative breakdown + the declared floor (0/0 here — no
    # declaration → degrades to the anti-empty floor).
    assert detail == "release acceptance PASS — 5 assertions (1 feature / 0 negative; declared 1 feature / 0 safety)"
    assert seen["script"].name == "release_smoke_test.sh"
    assert seen["env"]["SMOKE_PROJECT"] == "ok-smoke" and seen["env"]["SMOKE_BACKEND"] == "backend"
    assert seen["env"]["SMOKE_BACKEND_PORT"] == "10180"


@pytest.mark.asyncio
async def test_acceptance_script_empty_is_anti_empty_fail(monkeypatch, tmp_path) -> None:
    """Script exit-0 but ASSERTIONS_RUN=0 / no sentinel → FAIL (anti-empty floor: a false green)."""
    monkeypatch.setattr(orchestrator.claude_agent, "PROJECTS_ROOT", tmp_path)
    _make_project(tmp_path, "empty", script=True)
    stack = _mk_stack(tmp_path, slug="empty")

    async def _zero(script, env):
        return 0, "ASSERTIONS_RUN=0\n"

    async def _none(script, env):
        return 0, "ran nothing, exited clean\n"

    monkeypatch.setattr(orchestrator, "_run_acceptance_script", _zero)
    ok, detail, skipped = await orchestrator._run_release_acceptance(stack, "empty", (1, 0))
    assert ok is False and skipped is False and "anti-empty floor" in detail

    monkeypatch.setattr(orchestrator, "_run_acceptance_script", _none)
    ok2, detail2, _ = await orchestrator._run_release_acceptance(stack, "empty", (1, 0))
    assert ok2 is False and "anti-empty floor" in detail2


@pytest.mark.asyncio
async def test_acceptance_script_nonzero_exit_is_fail(monkeypatch, tmp_path) -> None:
    """Script non-zero exit → FAIL carrying the output tail."""
    monkeypatch.setattr(orchestrator.claude_agent, "PROJECTS_ROOT", tmp_path)
    _make_project(tmp_path, "bad", script=True)
    stack = _mk_stack(tmp_path, slug="bad")

    async def _script(script, env):
        return 1, "ASSERTION FAILED: GET /api/v1/x missing field\nASSERTIONS_RUN=2"

    monkeypatch.setattr(orchestrator, "_run_acceptance_script", _script)

    ok, detail, skipped = await orchestrator._run_release_acceptance(stack, "bad", (1, 0))

    assert ok is False and skipped is False
    assert detail.startswith("release_smoke_test.sh exit 1:") and "ASSERTION FAILED" in detail


def test_parse_assertions_run() -> None:
    """The LAST ``ASSERTIONS_RUN=<n>`` wins; missing sentinel → None."""
    assert orchestrator._parse_assertions_run("ASSERTIONS_RUN=3") == 3
    assert orchestrator._parse_assertions_run("noise\nASSERTIONS_RUN=1\nmore\nASSERTIONS_RUN=4\n") == 4
    assert orchestrator._parse_assertions_run("ASSERTIONS_RUN=0") == 0
    assert orchestrator._parse_assertions_run("nothing here") is None


def test_parse_assertions_run_ignores_named_sentinels() -> None:
    """CR-V2-051: the ``\\b`` anchor makes ``_parse_assertions_run`` match ONLY the bare total — a line with
    ONLY ``FEATURE_ASSERTIONS_RUN=`` / ``NEGATIVE_ASSERTIONS_RUN=`` must NOT be mis-read as the total
    (the ``_`` before ``ASSERTIONS`` is a word char → no boundary)."""
    out = "FEATURE_ASSERTIONS_RUN=2\nNEGATIVE_ASSERTIONS_RUN=1\n"
    assert orchestrator._parse_assertions_run(out) is None  # no bare ASSERTIONS_RUN= present
    # the three sentinels coexisting: each parser picks its own value
    full = "ASSERTIONS_RUN=4\nFEATURE_ASSERTIONS_RUN=2\nNEGATIVE_ASSERTIONS_RUN=1\n"
    assert orchestrator._parse_assertions_run(full) == 4
    assert orchestrator._parse_last_sentinel(full, orchestrator._FEATURE_ASSERTIONS_RUN_RE) == 2
    assert orchestrator._parse_last_sentinel(full, orchestrator._NEGATIVE_ASSERTIONS_RUN_RE) == 1
    assert orchestrator._parse_last_sentinel(full, orchestrator._FEATURE_ASSERTIONS_RUN_RE) == 2


def test_evaluate_release_coverage_risk_floor() -> None:
    """CR-V2-051 pure floor: a green boot alone is not a pass — declared flagship features need FEATURE
    assertions and declared safety properties need NEGATIVE assertions; missing coverage is a FAIL."""
    ev = orchestrator._evaluate_release_coverage
    # anti-empty floor (no declaration)
    assert ev(total=None, feature=0, negative=0, coverage_req=(0, 0))[0] is False
    assert ev(total=0, feature=0, negative=0, coverage_req=(0, 0))[0] is False
    # no declaration → FAIL, whatever the script printed. This used to PASS on a single assertion, so a
    # design that declared nothing bought itself the weakest possible floor; an undeclared release is not
    # a low-risk release, it is an unverifiable one.
    ok, detail = ev(total=1, feature=0, negative=0, coverage_req=(0, 0))
    assert ok is False and "missing declaration" in detail
    ok, detail = ev(total=9, feature=9, negative=9, coverage_req=(0, 0))
    assert ok is False and "missing declaration" in detail
    # a declared feature with no safety property is legitimate and still passes on its feature assertion
    ok, detail = ev(total=1, feature=1, negative=0, coverage_req=(1, 0))
    assert ok is True
    # declared 2 features, only 1 feature assertion → FAIL (missing behavioural coverage)
    ok, detail = ev(total=3, feature=1, negative=1, coverage_req=(2, 1))
    assert ok is False and "missing behavioural coverage" in detail and "2 flagship" in detail
    # declared 1 safety property, 0 negative assertions → FAIL (missing safety coverage) — THE dogfood shape
    ok, detail = ev(total=3, feature=2, negative=0, coverage_req=(2, 1))
    assert ok is False and "missing safety coverage" in detail and "risky op MUST be rejected" in detail
    # full declared coverage met → PASS
    ok, detail = ev(total=4, feature=2, negative=1, coverage_req=(2, 1))
    assert ok is True and "2 feature / 1 negative" in detail


@pytest.mark.asyncio
async def test_acceptance_missing_safety_coverage_is_fail(monkeypatch, tmp_path) -> None:
    """End-to-end through ``_run_release_acceptance``: the script exits 0 with a green boot + feature assertion
    but ZERO negative assertions while the design declared a safety property → FAIL (the NEX Agents shape:
    a boot-green build with an unproven safety invariant must not pass)."""
    monkeypatch.setattr(orchestrator.claude_agent, "PROJECTS_ROOT", tmp_path)
    _make_project(tmp_path, "sec", script=True)
    stack = _mk_stack(tmp_path, slug="sec")

    async def _script(script, env):
        return 0, "ASSERTIONS_RUN=2\nFEATURE_ASSERTIONS_RUN=1\nNEGATIVE_ASSERTIONS_RUN=0\n"

    monkeypatch.setattr(orchestrator, "_run_acceptance_script", _script)
    # declared 1 flagship feature + 1 safety property
    ok, detail, skipped = await orchestrator._run_release_acceptance(stack, "sec", coverage_req=(1, 1))
    assert ok is False and skipped is False and "missing safety coverage" in detail


@pytest.mark.asyncio
async def test_acceptance_full_declared_coverage_passes(monkeypatch, tmp_path) -> None:
    """The same script but with the negative assertion present → the declared (1 feature, 1 safety) floor is
    met → PASS."""
    monkeypatch.setattr(orchestrator.claude_agent, "PROJECTS_ROOT", tmp_path)
    _make_project(tmp_path, "sec2", script=True)
    stack = _mk_stack(tmp_path, slug="sec2")

    async def _script(script, env):
        return 0, "ASSERTIONS_RUN=3\nFEATURE_ASSERTIONS_RUN=1\nNEGATIVE_ASSERTIONS_RUN=1\n"

    monkeypatch.setattr(orchestrator, "_run_acceptance_script", _script)
    ok, detail, skipped = await orchestrator._run_release_acceptance(stack, "sec2", coverage_req=(1, 1))
    assert ok is True and skipped is False and "1 feature / 1 negative" in detail


# ---------------------------------------------------------------------------
# Override + port helpers (unchanged by the GAP 1 refactor)
# ---------------------------------------------------------------------------


def test_override_strips_container_name_and_ports(tmp_path) -> None:
    """The ephemeral override resets ``container_name`` + ``ports`` for EVERY service via ``!reset``."""
    compose = tmp_path / "docker-compose.yml"
    compose.write_text(COMPOSE_YML)

    override = orchestrator._acceptance_smoke_override(compose)

    assert "  backend:" in override and "  frontend:" in override and "  postgres:" in override
    assert override.count("container_name: !reset null") == 3
    assert override.count("ports: !reset null") == 0  # ports reset to an empty list, not null
    assert override.count("ports: !reset []") == 3


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


def test_compose_frontend_port_extraction(tmp_path) -> None:
    """The frontend reachability target = the ``frontend`` service's CONTAINER port (nginx analog of the
    backend-port helper); ``None`` when undeterminable so the caller falls back to nginx 80."""
    short = tmp_path / "short.yml"
    short.write_text(COMPOSE_YML)  # frontend ports "10181:80" → container port 80
    assert orchestrator._compose_frontend_port(short) == 80

    longform = tmp_path / "long.yml"
    longform.write_text("services:\n  frontend:\n    ports:\n      - target: 80\n        published: 18081\n")
    assert orchestrator._compose_frontend_port(longform) == 80

    nofe = tmp_path / "nofe.yml"
    nofe.write_text(COMPOSE_YML_NO_FRONTEND)
    assert orchestrator._compose_frontend_port(nofe) is None


# ---------------------------------------------------------------------------
# Readiness probe classification (v0.7.7): server responded (status < 500) = READY.
# ---------------------------------------------------------------------------


def _exec_probe(monkeypatch, fake_urlopen) -> tuple[int, str]:
    """Run :func:`orchestrator._readiness_probe_src` with ``urllib.request.urlopen`` stubbed; return
    ``(exit_code, stdout)``."""
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


def test_fast_fix_stage_order_reaches_verifikacia_no_v1_gate_g() -> None:
    """v2 re-key (CR-V2-014): the v1 ``gate_g`` stage is GONE. Both lanes now end at the 4-phase Verifikácia
    (the release smoke + Auditor verdict) → ``done``; no ``gate_g`` survives anywhere. The fast-fix lane skips
    Návrh but STILL passes through Verifikácia (a light end check), then ``done`` (Hotovo = verified; deploy is
    OUT — OQ-3/D6)."""
    assert "gate_g" not in orchestrator.FAST_FIX_STAGE_ORDER
    assert "gate_g" not in orchestrator.STAGE_ORDER, "v1 gate_g is replaced by the 4-phase Verifikácia"
    assert "verifikacia" in orchestrator.FAST_FIX_STAGE_ORDER and orchestrator.FAST_FIX_STAGE_ORDER[-1] == "done"
    assert "verifikacia" in orchestrator.STAGE_ORDER and orchestrator.STAGE_ORDER[-1] == "done"


# ─── ICCINT-127: the floor must check WHICH safety properties are covered, not how many ──────────────
#
# The forbidden operation this guards (stated as the invariant's own risky_op): a release passes the
# acceptance gate while a DECLARED safety property has no assertion that actually ran. Counting was
# satisfiable by any assertions at all — NEX Inbox v1.5.0 shipped 15 honest rejection tests, met the
# count, and still left 2 of 14 declared invariants unguarded. The red proof below runs that exact
# operation, not a generic failure.


def test_declared_safety_property_without_its_assertion_fails_the_gate() -> None:
    """The named binding is the floor: a declared invariant whose assertion never ran is a FAIL."""
    ok, detail = orchestrator._evaluate_release_coverage(
        total=20,
        feature=3,
        negative=15,  # the COUNT is satisfied — this is the exact hole ICCINT-127 closes
        coverage_req=(3, 2),
        declared_assertions={"nezaregistrovany-dodavatel-neprejde", "dodavatel-len-z-odosielatela"},
        ran_assertions={"nezaregistrovany-dodavatel-neprejde"},
    )
    assert ok is False
    # It must NAME the uncovered invariant — "too few assertions" is what let this through.
    assert "dodavatel-len-z-odosielatela" in detail


def test_declared_assertion_that_ran_passes() -> None:
    ok, detail = orchestrator._evaluate_release_coverage(
        total=20,
        feature=3,
        negative=2,
        coverage_req=(3, 2),
        declared_assertions={"a", "b"},
        ran_assertions={"a", "b", "c"},
    )
    assert ok is True, detail


def test_without_declared_bindings_the_count_floor_still_applies() -> None:
    """Backward compatibility: a project that declares no bindings keeps the old count floor."""
    ok, _ = orchestrator._evaluate_release_coverage(
        total=5,
        feature=3,
        negative=1,
        coverage_req=(3, 2),
        declared_assertions=set(),
        ran_assertions=set(),
    )
    assert ok is False  # negative(1) < declared safety(2)


# ─── ICCINT-127b: the binding is written LATER than the declaration, and must still be read ──────────
#
# The forbidden operation, stated as the invariant's own risky_op: the gate reads a declaration whose
# safety properties carry no assertion names — and therefore degrades to the count floor — although a
# NEWER gate_report has since bound every one of them. That is exactly what happened on NEX Inbox
# v1.5.0: the agent bound all 14, the gate read the plan report (0 bindings), counted 15 >= 14 and
# passed. The fix must not let the invariant LIST move — only the bindings attach to it.


def _msg(seq: int, payload: dict) -> SimpleNamespace:
    return SimpleNamespace(seq=seq, payload=payload, stage="programovanie", author="ai_agent", kind="gate_report")


def test_bindings_from_a_newer_report_are_read(monkeypatch) -> None:
    """A binding written after the plan close still reaches the gate."""
    plan = {"plan": [{"id": "T1"}], "safety_properties": [{"name": "A", "risky_op": "x"}]}
    later = {"safety_properties": [{"name": "A", "risky_op": "x", "assertion": "a-neprejde"}]}
    monkeypatch.setattr(orchestrator, "_release_declaration_payload", lambda db, vid: plan)
    monkeypatch.setattr(orchestrator, "_gate_report_payloads_newest_first", lambda db, vid: [later, plan])
    assert orchestrator._declared_safety_assertions(None, None) == {"a-neprejde"}


def test_a_later_report_cannot_drop_a_declared_invariant(monkeypatch) -> None:
    """Bindings attach to the DECLARED list; a later report must not shrink it to escape coverage."""
    plan = {
        "plan": [{"id": "T1"}],
        "safety_properties": [{"name": "A", "risky_op": "x"}, {"name": "B", "risky_op": "y"}],
    }
    later = {"safety_properties": [{"name": "A", "risky_op": "x", "assertion": "a-neprejde"}]}
    monkeypatch.setattr(orchestrator, "_release_declaration_payload", lambda db, vid: plan)
    monkeypatch.setattr(orchestrator, "_gate_report_payloads_newest_first", lambda db, vid: [later, plan])
    # B stays declared and unbound — the count floor still guards it; A's binding is honoured.
    assert orchestrator._declared_safety_assertions(None, None) == {"a-neprejde"}


# ─── ICCINT-127c: a safety property needs a STABLE key — a human sentence is not an identity ─────────
#
# The forbidden operation: a later gate_report rewrites the invariant NAMES (a build rephrases its own
# list every run) and the bindings silently stop matching — or worse, match a SHORTENED list, so a
# build escapes coverage it already promised. NEX Inbox v1.5.0 did exactly this: 14 declared, 14
# re-declared under different wording, 3 of the original invariants quietly gone.


def test_binding_matches_on_key_not_on_wording(monkeypatch) -> None:
    """The name is for humans and may be rewritten; the key is the identity and must carry the binding."""
    plan = {
        "plan": [{"id": "T1"}],
        "safety_properties": [{"key": "K1", "name": "Faktúra bez XML sa nesmie označiť", "risky_op": "x"}],
    }
    later = {"safety_properties": [{"key": "K1", "name": "bez XML neprejde", "assertion": "a-neprejde"}]}
    monkeypatch.setattr(orchestrator, "_release_declaration_payload", lambda db, vid: plan)
    monkeypatch.setattr(orchestrator, "_gate_report_payloads_newest_first", lambda db, vid: [later, plan])
    assert orchestrator._declared_safety_assertions(None, None) == {"a-neprejde"}


def test_a_later_report_cannot_quietly_shorten_the_list(monkeypatch) -> None:
    """A key the design declared and a later report omits stays unbound — it cannot vanish."""
    plan = {
        "plan": [{"id": "T1"}],
        "safety_properties": [
            {"key": "K1", "name": "A", "risky_op": "x"},
            {"key": "K2", "name": "B", "risky_op": "y"},
        ],
    }
    later = {"safety_properties": [{"key": "K1", "name": "A inak", "assertion": "a-neprejde"}]}
    monkeypatch.setattr(orchestrator, "_release_declaration_payload", lambda db, vid: plan)
    monkeypatch.setattr(orchestrator, "_gate_report_payloads_newest_first", lambda db, vid: [later, plan])
    # K2 was dropped by the later report — it must NOT disappear; it simply has no binding.
    assert orchestrator._declared_safety_assertions(None, None) == {"a-neprejde"}
    assert orchestrator._unbound_safety_keys(None, None) == {"K2"}


# ─── ICCINT-135: a binding that matches NOTHING must say so — the loop that cannot break ─────────────
#
# The forbidden operation: a newer report's binding resolves to no declared invariant, is dropped without
# a word, an OLDER binding keeps winning, and the gate therefore prints the SAME failure however many
# times the agent corrects itself. NEX Inbox v1.5.1 burned five verification rounds on this: the
# declaration carried key="" so only the exact Slovak sentence could match, the agent rephrased the
# sentence while fixing the assertion, and the message kept naming the assertion text from round one.


def test_an_unmatchable_binding_is_reported_not_swallowed(monkeypatch) -> None:
    """The agent must be able to SEE that its binding attached to nothing. Silence here is what made the
    failure text constant: correcting the assertion could never change a message driven by an older row."""
    plan = {"plan": [{"id": "T1"}], "safety_properties": [{"name": "Pôvodná veta zo zadania", "risky_op": "x"}]}
    stary = {"safety_properties": [{"name": "Pôvodná veta zo zadania", "assertion": "veta — test_a"}]}
    novy = {"safety_properties": [{"key": "test_a", "name": "preformulovaná veta", "assertion": "test_a"}]}
    monkeypatch.setattr(orchestrator, "_release_declaration_payload", lambda db, vid: plan)
    monkeypatch.setattr(orchestrator, "_gate_report_payloads_newest_first", lambda db, vid: [novy, stary, plan])

    poznamka = orchestrator._unmatched_binding_note(None, None)

    assert "preformulovaná veta" in poznamka, "nepriradené naviazanie sa nikde nespomína"
    assert "Pôvodná veta zo zadania" in poznamka, "chýba znenie, ktoré zadanie čaká"


def test_nothing_is_reported_when_every_binding_lands(monkeypatch) -> None:
    """Protiváha: keď sa všetko priradí, poznámka musí byť prázdna — inak by zavádzala pri zdravej stavbe."""
    plan = {"plan": [{"id": "T1"}], "safety_properties": [{"key": "K1", "name": "A", "risky_op": "x"}]}
    novy = {"safety_properties": [{"key": "K1", "name": "A inak", "assertion": "test_a"}]}
    monkeypatch.setattr(orchestrator, "_release_declaration_payload", lambda db, vid: plan)
    monkeypatch.setattr(orchestrator, "_gate_report_payloads_newest_first", lambda db, vid: [novy, plan])

    assert orchestrator._unmatched_binding_note(None, None) == ""


def test_a_keyless_declaration_still_gets_a_stable_handle(monkeypatch) -> None:
    """Zadanie bez kľúča nesmie nechať párovanie visieť na doslovnom znení vety. Kokpit kľúč odvodí —
    deterministicky, takže platí aj pre stavbu, ktorá už beží."""
    plan = {"plan": [{"id": "T1"}], "safety_properties": [{"name": "Pôvodná veta zo zadania", "risky_op": "x"}]}
    handle = orchestrator._mint_safety_key("Pôvodná veta zo zadania")
    novy = {"safety_properties": [{"key": handle, "name": "úplne inak povedané", "assertion": "test_a"}]}
    monkeypatch.setattr(orchestrator, "_release_declaration_payload", lambda db, vid: plan)
    monkeypatch.setattr(orchestrator, "_gate_report_payloads_newest_first", lambda db, vid: [novy, plan])

    assert orchestrator._declared_safety_assertions(None, None) == {"test_a"}
    assert orchestrator._unbound_safety_keys(None, None) == set()


def test_the_minted_handle_is_stable_and_distinguishes() -> None:
    a = orchestrator._mint_safety_key("XML od iného programu sa nesmie vydávať za faktúru")
    assert a == orchestrator._mint_safety_key("XML od iného programu sa nesmie vydávať za faktúru")
    assert a != orchestrator._mint_safety_key("PDF príloha sa nesmie dostať do vetvy pre XML")
    assert " " not in a and a == a.lower()


def test_the_brief_shows_the_handle_the_agent_must_echo(monkeypatch) -> None:
    """Kľúč, ktorý agent nevidí, je na nič — práve preto ICCINT-127c nezabralo."""
    plan = {
        "plan": [{"id": "T1"}],
        "flagship_features": ["F"],
        "safety_properties": [{"name": "Pôvodná veta zo zadania", "risky_op": "spusti zakázané"}],
    }
    monkeypatch.setattr(orchestrator, "_release_declaration_payload", lambda db, vid: plan)

    brief = orchestrator._release_coverage_brief(None, None)

    assert orchestrator._mint_safety_key("Pôvodná veta zo zadania") in brief


def test_the_failure_names_the_unmatched_binding() -> None:
    """Hlásenie musí ukázať, čo sa nepriradilo — inak agent opravuje meno testu, ktoré je v poriadku."""
    ok, detail = orchestrator._evaluate_release_coverage(
        total=5,
        feature=1,
        negative=1,
        coverage_req=(1, 1),
        declared_assertions={"stare-meno"},
        ran_assertions={"test_a"},
        unmatched_note="POZOR: naviazanie 'preformulovaná veta' sa nepriradilo.",
    )

    assert not ok
    assert "preformulovaná veta" in detail


# ─── ICCINT-127d: a binding must reach a declaration written BEFORE keys existed ─────────────────────
#
# The forbidden operation: the plan close predates keys, so its entries are identified by name only;
# the binding report adds a key AND keeps the name, and matches neither — the gate reports every
# invariant unguarded even though all of them are bound. Seen live on NEX Inbox v1.5.0: 14 declared,
# 14 bound with SP-01..SP-14 and the original wording, 0 matched.


def test_binding_with_a_new_key_reaches_a_keyless_declaration(monkeypatch) -> None:
    plan = {"plan": [{"id": "T1"}], "safety_properties": [{"name": "Faktúra bez XML", "risky_op": "x"}]}
    later = {"safety_properties": [{"key": "SP-01", "name": "Faktúra bez XML", "assertion": "a-neprejde"}]}
    monkeypatch.setattr(orchestrator, "_release_declaration_payload", lambda db, vid: plan)
    monkeypatch.setattr(orchestrator, "_gate_report_payloads_newest_first", lambda db, vid: [later, plan])
    assert orchestrator._declared_safety_assertions(None, None) == {"a-neprejde"}
    assert orchestrator._unbound_safety_keys(None, None) == set()


def test_a_binding_for_an_undeclared_invariant_is_still_refused(monkeypatch) -> None:
    """Widening the match must not let a later report smuggle in an invariant nobody declared."""
    plan = {"plan": [{"id": "T1"}], "safety_properties": [{"name": "A", "risky_op": "x"}]}
    later = {"safety_properties": [{"key": "SP-99", "name": "Cudzia poistka", "assertion": "cudzia"}]}
    monkeypatch.setattr(orchestrator, "_release_declaration_payload", lambda db, vid: plan)
    monkeypatch.setattr(orchestrator, "_gate_report_payloads_newest_first", lambda db, vid: [later, plan])
    assert orchestrator._declared_safety_assertions(None, None) == set()
    assert orchestrator._unbound_safety_keys(None, None) == {"A"}
