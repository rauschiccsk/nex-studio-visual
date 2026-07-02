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


def _make_project(
    root, slug: str, *, compose: bool = True, compose_yml: str = COMPOSE_YML, script: bool = False
) -> Path:
    """Materialise a fake project tree under *root*/<slug>; optionally seed ``release_smoke_test.sh``."""
    proj = root / slug
    proj.mkdir(parents=True, exist_ok=True)
    if compose:
        (proj / "docker-compose.yml").write_text(compose_yml)
    if script:
        (proj / "release_smoke_test.sh").write_text("#!/usr/bin/env bash\necho ASSERTIONS_RUN=1\n")
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

    (boot_ok, boot_detail), acceptance = await orchestrator._run_release_smoke("noc", "v1.0.0")

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

    (boot_ok, boot_detail), acceptance = await orchestrator._run_release_smoke("nofe", "v1.0.0")

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

    (boot_ok, boot_detail), acceptance = await orchestrator._run_release_smoke("boom", "v1.0.0")

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
    _make_project(tmp_path, "green", script=True)
    rec = _StepRecorder({"up": (0, "Started"), "ready": (0, "status 200"), "down": (0, "")})
    monkeypatch.setattr(orchestrator, "_compose_smoke_step", rec)

    async def _script(script, env):
        return 0, "ASSERTIONS_RUN=3"

    monkeypatch.setattr(orchestrator, "_run_acceptance_script", _script)

    (boot_ok, boot_detail), acceptance = await orchestrator._run_release_smoke("green", "v1.0.0")

    assert (boot_ok, boot_detail) == (True, "app booted + responds")
    # CR-V2-051: no declaration → (0,0) floor; PASS detail now carries the feature/negative breakdown.
    assert acceptance == (
        True,
        "release acceptance PASS — 3 assertions (0 feature / 0 negative; declared 0 feature / 0 safety)",
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

    (boot_ok, boot_detail), acceptance = await orchestrator._run_release_smoke("slowboot", "v1.0.0")

    assert boot_ok is False and boot_detail.startswith("app did not boot / not responding within 120s:")
    assert acceptance is None
    assert called["script"] is False, "acceptance must NOT run when the boot leg failed"
    assert rec.ran("down")


# ---------------------------------------------------------------------------
# Boot leg: _run_app_starts_smoke(stack)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_boot_leg_pass_on_ready(monkeypatch, tmp_path) -> None:
    """The boot leg returns ``(True, "app booted + responds")`` when BE + FE both answer."""
    rec = _StepRecorder({"ready": (0, "status 200"), "ready_fe": (0, "status 404")})
    monkeypatch.setattr(orchestrator, "_compose_smoke_step", rec)
    stack = _mk_stack(tmp_path)

    ok, detail = await orchestrator._run_app_starts_smoke(stack)

    assert (ok, detail) == (True, "app booted + responds")
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

    ok, detail, skipped = await orchestrator._run_release_acceptance(stack, "web")

    assert ok is False and skipped is False
    assert "required but missing" in detail


@pytest.mark.asyncio
async def test_acceptance_worker_missing_script_is_skip(monkeypatch, tmp_path) -> None:
    """A pure lib/worker stack (NO backend service) with no script → legit SKIP (acceptance not required)."""
    monkeypatch.setattr(orchestrator.claude_agent, "PROJECTS_ROOT", tmp_path)
    _make_project(tmp_path, "wk", compose_yml=COMPOSE_YML_WORKER_ONLY, script=False)
    stack = _mk_stack(tmp_path, compose_yml=COMPOSE_YML_WORKER_ONLY, slug="wk")

    ok, detail, skipped = await orchestrator._run_release_acceptance(stack, "wk")

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
        return 0, "some output\nASSERTIONS_RUN=5\n"

    monkeypatch.setattr(orchestrator, "_run_acceptance_script", _script)

    ok, detail, skipped = await orchestrator._run_release_acceptance(stack, "ok")

    assert (ok, skipped) == (True, False)
    # CR-V2-051: PASS detail now reports the feature/negative breakdown + the declared floor (0/0 here — no
    # declaration → degrades to the anti-empty floor).
    assert detail == "release acceptance PASS — 5 assertions (0 feature / 0 negative; declared 0 feature / 0 safety)"
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
    ok, detail, skipped = await orchestrator._run_release_acceptance(stack, "empty")
    assert ok is False and skipped is False and "anti-empty floor" in detail

    monkeypatch.setattr(orchestrator, "_run_acceptance_script", _none)
    ok2, detail2, _ = await orchestrator._run_release_acceptance(stack, "empty")
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

    ok, detail, skipped = await orchestrator._run_release_acceptance(stack, "bad")

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
    # no declaration + ≥1 assertion → PASS (backward compatible)
    ok, detail = ev(total=1, feature=0, negative=0, coverage_req=(0, 0))
    assert ok is True and "1 assertions" in detail
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
