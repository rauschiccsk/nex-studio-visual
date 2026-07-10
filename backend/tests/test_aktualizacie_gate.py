"""obs-2 Part B Part 2 — the per-app *Aktualizácie* changelog release gate (per-app-changelog-part2-gate.md).

Two NEX-Studio-OWNED release blockers, both surfaced through the release smoke's boot leg so a build cannot
reach ``done``/deploy without a working per-app changelog:

* **2a — behavioural** (:func:`orchestrator._evaluate_release_notes` + :func:`orchestrator._probe_release_notes`):
  the booted backend actually SERVES ``GET /api/v1/release-notes`` — HTTP 200 AND a JSON list INCLUDING the
  completing version (``v``-normalised).
* **2b — static** (:func:`orchestrator._check_aktualizacie_frontend`): the generated app's ``frontend/src``
  still wires the scaffolded tab — an Updates page + a ``/updates`` route + an "Aktualizácie" nav entry.

The two are wired into :func:`orchestrator._run_release_smoke` (2b cheap → 2a) as a boot-leg blocker; ``docker``
is never invoked here — the ``_compose_smoke_step`` subprocess seam + the ``_run_acceptance_script`` seam are
faked, exactly like ``test_acceptance_smoke.py``.
"""

from __future__ import annotations

import contextlib
import io
import json
import urllib.error
import urllib.request
from pathlib import Path

import yaml

from backend.services import orchestrator

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

COMPOSE_YML_WORKER_ONLY = """\
services:
  worker:
    build: .
    container_name: demo-worker
  redis:
    image: redis:7-alpine
    container_name: demo-redis
"""


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------
def _seed_frontend(proj: Path, *, page=True, route=True, nav=True) -> None:
    """Seed the scaffolded Aktualizácie FE tab under ``<proj>/frontend/src``; each piece can be dropped to
    exercise a specific missing-piece message (mirrors nex-payables dropping all three)."""
    src = proj / "frontend" / "src"
    (src / "pages").mkdir(parents=True, exist_ok=True)
    (src / "components" / "layout").mkdir(parents=True, exist_ok=True)
    if page:
        (src / "pages" / "UpdatesPage.tsx").write_text(
            "export default function UpdatesPage() { return <div>Aktualizácie</div>; }\n"
        )
    # App.tsx is the router; Sidebar.tsx is the nav. Seed both files always so a dropped piece is the ABSENCE
    # of the pattern, not the absence of the file (closer to a real agent-built app).
    (src / "App.tsx").write_text(
        'import Home from "./pages/Home";\n'
        + (
            '<Route path="updates" element={<UpdatesPage />} />\n'
            if route
            else '<Route path="home" element={<Home />} />\n'
        )
    )
    (src / "components" / "layout" / "Sidebar.tsx").write_text(
        (
            '<NavItem label="Aktualizácie" onClick={() => navigate("/updates")} />\n'
            if nav
            else '<NavItem label="Domov" />\n'
        )
    )


def _make_project(root: Path, slug: str, *, compose_yml: str = COMPOSE_YML, script: bool = True, **fe) -> Path:
    proj = root / slug
    proj.mkdir(parents=True, exist_ok=True)
    (proj / "docker-compose.yml").write_text(compose_yml)
    if script:
        (proj / "release_smoke_test.sh").write_text("#!/usr/bin/env bash\necho ASSERTIONS_RUN=1\n")
    if compose_yml == COMPOSE_YML:  # only a full web app has the FE tab requirement
        _seed_frontend(proj, **fe)
    return proj


def _rn_probe_out(status: int, versions: list[str]) -> str:
    """The in-container probe stdout for a 200 + JSON list of ``{version}`` entries (the serving contract)."""
    body = json.dumps([{"version": v, "released_at": "2026-07-08", "markdown": f"## {v}"} for v in versions])
    return f"RELEASE_NOTES_STATUS {status}\nRELEASE_NOTES_BODY {body}"


class _Recorder:
    """Fake ``orchestrator._compose_smoke_step`` routing up/down/readiness/release-notes-probe steps. The
    release-notes probe is distinguished by the ``release-notes`` URL baked into its ``python -c`` source."""

    def __init__(self, *, ready=(0, "status 200"), release_notes=(0, "")) -> None:
        self._ready = ready
        self._release_notes = release_notes
        self.calls: list[list[str]] = []

    async def __call__(self, cmd: list[str], timeout: int) -> tuple[int, str]:
        self.calls.append(cmd)
        joined = " ".join(cmd)
        if "release-notes" in joined:
            return self._release_notes
        if "python" in cmd:
            return self._ready
        return (0, "ok")

    def ran(self, token: str) -> bool:
        return any(token in cmd for cmd in self.calls)


class _SeqRecorder(_Recorder):
    """A ``_compose_smoke_step`` fake that returns a SEQUENCE of release-notes probe outputs — one per probe
    call (the last is repeated once exhausted) — so the 2a retry path (Fix 1) can be exercised: a cold/slow
    endpoint whose FIRST probe yields no status line but a LATER one succeeds. ``rn_calls`` counts the probes."""

    def __init__(self, outputs: list[tuple[int, str]], *, ready=(0, "status 200")) -> None:
        super().__init__(ready=ready)
        self._outputs = list(outputs)
        self.rn_calls = 0

    async def __call__(self, cmd: list[str], timeout: int) -> tuple[int, str]:
        self.calls.append(cmd)
        if "release-notes" in " ".join(cmd):
            out = self._outputs[min(self.rn_calls, len(self._outputs) - 1)]
            self.rn_calls += 1
            return out
        if "python" in cmd:
            return self._ready
        return (0, "ok")


async def _no_sleep(*_a, **_k) -> None:
    return None


def _mk_stack(tmp_path, *, compose_yml=COMPOSE_YML, slug="demo") -> orchestrator._SmokeStack:
    compose = tmp_path / f"{slug}-compose.yml"
    compose.write_text(compose_yml)
    services = (yaml.safe_load(compose_yml) or {}).get("services") or {}
    roles = orchestrator.uat_provisioner.identify_service_roles(services)
    override = tmp_path / "smoke.override.yml"
    base = ["docker", "compose", "-p", f"{slug}-smoke", "-f", str(compose), "-f", str(override)]
    return orchestrator._SmokeStack(
        base=base, compose=compose, override=override, project=f"{slug}-smoke", roles=roles, up_rc=0, up_detail=""
    )


# ===========================================================================
# 2a — pure _evaluate_release_notes
# ===========================================================================
def test_2a_pass_when_list_includes_version() -> None:
    """200 + a list including the completing version (bare match against a ``v``-prefixed served entry) → None."""
    body = json.dumps([{"version": "v1.2.0", "markdown": "x"}, {"version": "v1.1.0", "markdown": "y"}])
    assert orchestrator._evaluate_release_notes(200, body, "1.2.0") is None
    # the completing label itself may carry a leading v — still matches
    assert orchestrator._evaluate_release_notes(200, body, "v1.2.0") is None


def test_2a_blocks_when_version_missing_from_list() -> None:
    """200 + a list that does NOT include the completing version → a specific blocker naming the version."""
    body = json.dumps([{"version": "v1.1.0", "markdown": "y"}])
    msg = orchestrator._evaluate_release_notes(200, body, "1.2.0")
    assert msg is not None and "neobsahuje verziu v1.2.0" in msg
    assert "1.1.0" in msg  # the served versions are surfaced for diagnosis


def test_2a_blocks_on_non_200() -> None:
    """A 404/500 (endpoint dropped) → blocker naming the status."""
    for status in (404, 500):
        msg = orchestrator._evaluate_release_notes(status, "not json", "1.2.0")
        assert msg is not None and f"HTTP {status}" in msg


def test_2a_blocks_on_non_list_or_bad_json() -> None:
    """A non-list JSON body, or unparseable body → blocker (never a silent pass)."""
    assert "nevrátil JSON zoznam" in orchestrator._evaluate_release_notes(200, '{"version": "v1.2.0"}', "1.2.0")
    assert "nevrátil platný JSON" in orchestrator._evaluate_release_notes(200, "<html>oops</html>", "1.2.0")


def test_2a_probe_parse_roundtrip() -> None:
    """``_parse_release_notes_probe`` extracts status + single-line JSON body, robust to interleaved warnings."""
    out = "WARN: something\n" + _rn_probe_out(200, ["v1.2.0"])
    status, body = orchestrator._parse_release_notes_probe(out)
    assert status == 200 and json.loads(body)[0]["version"] == "v1.2.0"
    # no status line at all → (None, "")
    assert orchestrator._parse_release_notes_probe("RELEASE_NOTES_ERR connection refused") == (None, "")


def test_2a_probe_src_reports_status_and_body(monkeypatch) -> None:
    """The in-container probe source prints the STATUS + BODY for a 200 response (exec'd like the real one)."""

    class _Resp:
        status = 200

        def read(self):
            return b'[{"version": "v1.2.0", "markdown": "x"}]'

    monkeypatch.setattr(urllib.request, "urlopen", lambda *_a, **_k: _Resp())
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        with contextlib.suppress(SystemExit):
            exec(compile(orchestrator._release_notes_probe_src(10180), "<probe>", "exec"), {})  # noqa: S102
    status, body = orchestrator._parse_release_notes_probe(buf.getvalue())
    assert status == 200 and orchestrator._evaluate_release_notes(status, body, "1.2.0") is None


async def test_2a_probe_pass_and_block(tmp_path, monkeypatch) -> None:
    """``_probe_release_notes`` end-to-end over the faked exec seam: a 200 including the version → pass; a 200
    missing it → blocker. Both are a REAL HTTP status → the verdict is final on the FIRST probe (no retry)."""
    stack = _mk_stack(tmp_path)
    monkeypatch.setattr(
        orchestrator, "_compose_smoke_step", _Recorder(release_notes=(0, _rn_probe_out(200, ["v1.2.0"])))
    )
    ok, detail = await orchestrator._probe_release_notes(stack.base, "backend", 10180, "1.2.0")
    assert ok is True and "Aktualizácie OK" in detail

    monkeypatch.setattr(
        orchestrator, "_compose_smoke_step", _Recorder(release_notes=(0, _rn_probe_out(200, ["v1.1.0"])))
    )
    ok, detail = await orchestrator._probe_release_notes(stack.base, "backend", 10180, "1.2.0")
    assert ok is False and "neobsahuje verziu v1.2.0" in detail


# --- Fix 1 (per-app-changelog-part2-followup.md): 2a probe retry vs immediate-real-status ------------------
async def test_2a_probe_retries_then_recovers(tmp_path, monkeypatch) -> None:
    """A cold/slow endpoint whose FIRST probes time out (no status line) but a LATER probe returns 200 + the
    version → PASS (NOT a false block). The retry sleep is neutralised so the test is instant."""
    monkeypatch.setattr(orchestrator.asyncio, "sleep", _no_sleep)
    stack = _mk_stack(tmp_path)
    seq = _SeqRecorder(
        [
            (0, "RELEASE_NOTES_ERR read timed out"),  # cold start — handler still reading files, > 10s probe
            (0, "RELEASE_NOTES_ERR read timed out"),
            (0, _rn_probe_out(200, ["v1.2.0"])),  # warm — now serves the completing version
        ]
    )
    monkeypatch.setattr(orchestrator, "_compose_smoke_step", seq)

    ok, detail = await orchestrator._probe_release_notes(stack.base, "backend", 10180, "1.2.0")
    assert ok is True and "Aktualizácie OK" in detail
    assert seq.rn_calls == 3, "retried past the two transient failures, succeeded on the third"


async def test_2a_probe_real_404_is_immediate_no_retry(tmp_path, monkeypatch) -> None:
    """A REAL HTTP 404 (endpoint dropped) is an immediate blocker — NO wasteful retry (it won't self-heal).
    ``asyncio.sleep`` is booby-trapped so any retry would fail the test loudly."""

    async def _boom(*_a, **_k):
        raise AssertionError("must not retry/sleep — a real HTTP status blocks immediately")

    monkeypatch.setattr(orchestrator.asyncio, "sleep", _boom)
    stack = _mk_stack(tmp_path)
    seq = _SeqRecorder([(0, "RELEASE_NOTES_STATUS 404\nRELEASE_NOTES_BODY Not Found")])
    monkeypatch.setattr(orchestrator, "_compose_smoke_step", seq)

    ok, detail = await orchestrator._probe_release_notes(stack.base, "backend", 10180, "1.2.0")
    assert ok is False and "HTTP 404" in detail
    assert seq.rn_calls == 1, "a real status is evaluated on the FIRST response — no retry"


async def test_2a_probe_transient_fail_after_full_budget(tmp_path, monkeypatch) -> None:
    """A probe that NEVER gets a response (transport error every attempt) → the transient-fail 'neodpovedalo'
    message AFTER exhausting the bounded retry budget (never a silent pass, never an unbounded loop)."""
    monkeypatch.setattr(orchestrator.asyncio, "sleep", _no_sleep)
    stack = _mk_stack(tmp_path)
    seq = _SeqRecorder([(1, "RELEASE_NOTES_ERR connection refused")])
    monkeypatch.setattr(orchestrator, "_compose_smoke_step", seq)

    ok, detail = await orchestrator._probe_release_notes(stack.base, "backend", 10180, "1.2.0")
    assert ok is False and "neodpovedalo" in detail
    expected = max(1, orchestrator.RELEASE_NOTES_PROBE_TIMEOUT // orchestrator.RELEASE_NOTES_PROBE_INTERVAL)
    assert seq.rn_calls == expected, "retried the full bounded budget before declaring transient-fail"


# ===========================================================================
# 2b — pure _check_aktualizacie_frontend
# ===========================================================================
def test_2b_none_when_all_present(tmp_path) -> None:
    """Page + route + nav all present → None (compliant)."""
    proj = tmp_path / "app"
    proj.mkdir()
    _seed_frontend(proj)
    assert orchestrator._check_aktualizacie_frontend(proj) is None


def test_2b_missing_page(tmp_path) -> None:
    proj = tmp_path / "app"
    proj.mkdir()
    _seed_frontend(proj, page=False)
    msg = orchestrator._check_aktualizacie_frontend(proj)
    assert msg is not None and "UpdatesPage.tsx" in msg


def test_2b_missing_route(tmp_path) -> None:
    proj = tmp_path / "app"
    proj.mkdir()
    _seed_frontend(proj, route=False)
    msg = orchestrator._check_aktualizacie_frontend(proj)
    assert msg is not None and "/updates route" in msg


def test_2b_missing_nav(tmp_path) -> None:
    proj = tmp_path / "app"
    proj.mkdir()
    _seed_frontend(proj, nav=False)
    msg = orchestrator._check_aktualizacie_frontend(proj)
    assert msg is not None and "menu" in msg


def test_2b_all_missing_mirrors_nex_payables(tmp_path) -> None:
    """The flagship-app drop (nex-payables): page + route + nav all missing → the first (page) blocker."""
    proj = tmp_path / "app"
    (proj / "frontend" / "src").mkdir(parents=True)  # a frontend, but no Aktualizácie tab
    msg = orchestrator._check_aktualizacie_frontend(proj)
    assert msg is not None and "UpdatesPage.tsx" in msg


def test_2b_commented_out_route_is_not_wired(tmp_path) -> None:
    """A commented-out route/nav must NOT count as present (comment-stripping): the tab is dropped."""
    proj = tmp_path / "app"
    src = proj / "frontend" / "src"
    (src / "pages").mkdir(parents=True)
    (src / "components" / "layout").mkdir(parents=True)
    (src / "pages" / "UpdatesPage.tsx").write_text("export default function UpdatesPage() {}\n")
    (src / "App.tsx").write_text('{/* <Route path="updates" element={<UpdatesPage />} /> */}\n')
    (src / "components" / "layout" / "Sidebar.tsx").write_text('// <NavItem label="Aktualizácie" />\n')
    msg = orchestrator._check_aktualizacie_frontend(proj)
    assert msg is not None and "/updates route" in msg


# --- Fix 2 (per-app-changelog-part2-followup.md): detector robustness -------------------------------------
def _seed_page_and_scaffold(proj: Path) -> Path:
    """Seed a valid Updates page + the two wiring files' dirs; the caller writes the router / sidebar bodies to
    exercise a specific detector. Returns the ``frontend/src`` dir."""
    src = proj / "frontend" / "src"
    (src / "pages").mkdir(parents=True, exist_ok=True)
    (src / "components" / "layout").mkdir(parents=True, exist_ok=True)
    (src / "pages" / "UpdatesPage.tsx").write_text("export default function UpdatesPage() {}\n")
    return src


def test_2b_nav_data_router_prefixed_path_passes(tmp_path) -> None:
    """Regression (nex-payables 2026-07-10): a data-router nav — an object entry
    ``{ to: "/admin/updates", label: "Aktualizácie" }`` driven by ``navigate(item.to)`` under a PREFIXED
    ``/admin`` path — is a valid wired nav. The old regex matched only ``to="/updates"`` (attribute ``=``,
    exact path) and false-FAILED it. The check must now PASS."""
    proj = tmp_path / "app"
    src = _seed_page_and_scaffold(proj)
    (src / "App.tsx").write_text('<Route path="updates" element={<UpdatesPage />} />\n')
    (src / "components" / "layout" / "Sidebar.tsx").write_text(
        'const items = [{ to: "/admin/updates", label: "Aktualizácie", icon: "✨" }];\n'
        "items.map((item) => <NavItem onClick={() => navigate(item.to)} />);\n"
    )
    assert orchestrator._check_aktualizacie_frontend(proj) is None


def test_2b_nav_unrelated_updates_log_does_not_satisfy(tmp_path) -> None:
    """A distinct ``/settings/updates-log`` nav must NOT satisfy the ``…/updates`` nav check (the trailing
    lookahead keeps ``updates-log`` from matching) — else the tab could false-PASS on an unrelated route."""
    proj = tmp_path / "app"
    src = _seed_page_and_scaffold(proj)
    (src / "App.tsx").write_text('<Route path="updates" element={<UpdatesPage />} />\n')
    (src / "components" / "layout" / "Sidebar.tsx").write_text('<NavItem to="/settings/updates-log" />\n')
    msg = orchestrator._check_aktualizacie_frontend(proj)
    assert msg is not None and "menu" in msg


def test_2b_object_router_path_detected(tmp_path) -> None:
    """Route (false-FAIL fix): the data-router object form ``{ path: "updates" }`` counts as a wired route (not
    only JSX ``path="updates"``); an English "Updates" nav label still passes (language-agnostic)."""
    proj = tmp_path / "app"
    src = _seed_page_and_scaffold(proj)
    (src / "router.tsx").write_text(
        'export const router = createBrowserRouter([{ path: "updates", element: <UpdatesPage /> }]);\n'
    )
    (src / "components" / "layout" / "Sidebar.tsx").write_text(
        '<NavItem label="Updates" onClick={() => navigate("/updates")} />\n'
    )
    assert orchestrator._check_aktualizacie_frontend(proj) is None


def test_2b_filepath_string_is_not_a_route(tmp_path) -> None:
    """Route (anchor): a stray ``const filepath = "updates"`` must NOT be mistaken for a wired ``/updates``
    route — with the real route dropped, the gate blocks."""
    proj = tmp_path / "app"
    src = _seed_page_and_scaffold(proj)
    (src / "App.tsx").write_text('const filepath = "updates";\n<Route path="home" element={<Home />} />\n')
    (src / "components" / "layout" / "Sidebar.tsx").write_text('<NavItem onClick={() => navigate("/updates")} />\n')
    msg = orchestrator._check_aktualizacie_frontend(proj)
    assert msg is not None and "/updates route" in msg


def test_2b_renamed_page_detected(tmp_path) -> None:
    """Page (false-FAIL fix): a validly-renamed page ``pages/Changelog/UpdatesView.tsx`` (still ``Updates*.tsx``)
    counts — not blocked."""
    proj = tmp_path / "app"
    src = proj / "frontend" / "src"
    (src / "pages" / "Changelog").mkdir(parents=True)
    (src / "components" / "layout").mkdir(parents=True)
    (src / "pages" / "Changelog" / "UpdatesView.tsx").write_text("export default function UpdatesView() {}\n")
    (src / "App.tsx").write_text('<Route path="updates" element={<UpdatesView />} />\n')
    (src / "components" / "layout" / "Sidebar.tsx").write_text('<NavItem onClick={() => navigate("/updates")} />\n')
    assert orchestrator._check_aktualizacie_frontend(proj) is None


def test_2b_imported_updates_page_detected(tmp_path) -> None:
    """Page (false-FAIL fix): a page module NOT named ``Updates*.tsx`` but a source IMPORTS an updates page
    module (``from "./pages/updates/…"``) → page detected."""
    proj = tmp_path / "app"
    src = proj / "frontend" / "src"
    (src / "pages" / "updates").mkdir(parents=True)
    (src / "components" / "layout").mkdir(parents=True)
    (src / "pages" / "updates" / "ChangelogView.tsx").write_text("export default function ChangelogView() {}\n")
    (src / "App.tsx").write_text(
        'import ChangelogView from "./pages/updates/ChangelogView";\n'
        '<Route path="updates" element={<ChangelogView />} />\n'
    )
    (src / "components" / "layout" / "Sidebar.tsx").write_text('<NavItem onClick={() => navigate("/updates")} />\n')
    assert orchestrator._check_aktualizacie_frontend(proj) is None


def test_2b_unrelated_aktualizovane_does_not_false_pass_nav(tmp_path) -> None:
    """Nav (false-PASS fix — THE worst case): the sidebar entry is DROPPED but an unrelated "Naposledy
    aktualizované" label lives elsewhere. The old accent-stem check false-PASSED on it (defeating the whole
    gate); the route-anchored check keys on the ``/updates`` target → nav NOT detected → blocker."""
    proj = tmp_path / "app"
    src = _seed_page_and_scaffold(proj)
    (src / "App.tsx").write_text('<Route path="updates" element={<UpdatesPage />} />\n')
    # the sidebar dropped the Aktualizácie nav; an unrelated dashboard merely shows "Naposledy aktualizované: …"
    (src / "components" / "layout" / "Sidebar.tsx").write_text('<NavItem label="Domov" onClick={() => nav("/")} />\n')
    (src / "pages" / "Dashboard.tsx").write_text("<span>Naposledy aktualizované: 2026-07-08</span>\n")
    msg = orchestrator._check_aktualizacie_frontend(proj)
    assert msg is not None and "menu" in msg, "an unrelated 'aktualizované' string must NOT satisfy the nav gate"


# ===========================================================================
# _run_aktualizacie_gate — the archetype condition
# ===========================================================================
async def test_gate_skips_for_non_web_app(tmp_path) -> None:
    """A pure worker stack (no backend + no frontend) has no Aktualizácie tab requirement → SKIP (a pass)."""
    stack = _mk_stack(tmp_path, compose_yml=COMPOSE_YML_WORKER_ONLY, slug="wk")
    ok, detail = await orchestrator._run_aktualizacie_gate(stack, tmp_path / "wk", "1.0.0")
    assert ok is True and "SKIP" in detail


# ===========================================================================
# Integration through _run_release_smoke (compliant → pass / non-compliant → blocked)
# ===========================================================================
async def _script_ok(script, env):
    return 0, "ASSERTIONS_RUN=3"


async def test_integration_compliant_app_passes(tmp_path, monkeypatch) -> None:
    """A compliant app (FE tab wired + endpoint serves the version) → boot PASS, the gate does not block, and
    acceptance runs."""
    monkeypatch.setattr(orchestrator.claude_agent, "PROJECTS_ROOT", tmp_path)
    _make_project(tmp_path, "green")
    monkeypatch.setattr(
        orchestrator, "_compose_smoke_step", _Recorder(release_notes=(0, _rn_probe_out(200, ["v1.0.0"])))
    )
    monkeypatch.setattr(orchestrator, "_run_acceptance_script", _script_ok)

    (boot_ok, boot_detail), acceptance = await orchestrator._run_release_smoke("green", "v1.0.0")

    assert boot_ok is True and boot_detail == "app booted + responds"
    assert acceptance is not None and acceptance[0] is True  # acceptance ran → the gate let it through


async def test_integration_missing_fe_tab_is_blocked(tmp_path, monkeypatch) -> None:
    """2b: the FE tab dropped (no UpdatesPage) → boot FAIL "Aktualizácie chýba vo frontende", acceptance NOT
    run (mirrors a boot FAIL → runtime floor)."""
    monkeypatch.setattr(orchestrator.claude_agent, "PROJECTS_ROOT", tmp_path)
    _make_project(tmp_path, "nofe", page=False)
    rec = _Recorder(release_notes=(0, _rn_probe_out(200, ["v1.0.0"])))
    monkeypatch.setattr(orchestrator, "_compose_smoke_step", rec)

    called = {"acc": False}

    async def _acc(script, env):
        called["acc"] = True
        return 0, "ASSERTIONS_RUN=3"

    monkeypatch.setattr(orchestrator, "_run_acceptance_script", _acc)

    (boot_ok, boot_detail), acceptance = await orchestrator._run_release_smoke("nofe", "v1.0.0")

    assert boot_ok is False and "Aktualizácie chýba vo frontende" in boot_detail and "UpdatesPage.tsx" in boot_detail
    assert acceptance is None
    assert called["acc"] is False, "the gate blocks before acceptance"
    assert not rec.ran("release-notes"), "2b (static) blocks before the 2a probe even runs"


async def test_integration_endpoint_missing_version_is_blocked(tmp_path, monkeypatch) -> None:
    """2a: FE tab present but the endpoint serves a list WITHOUT the completing version → boot FAIL naming the
    endpoint, acceptance NOT run."""
    monkeypatch.setattr(orchestrator.claude_agent, "PROJECTS_ROOT", tmp_path)
    _make_project(tmp_path, "noserve")
    # the endpoint serves only a PRIOR version — the completing v1.0.0 is missing (the second-version shape)
    monkeypatch.setattr(
        orchestrator, "_compose_smoke_step", _Recorder(release_notes=(0, _rn_probe_out(200, ["v0.9.0"])))
    )

    called = {"acc": False}

    async def _acc(script, env):
        called["acc"] = True
        return 0, "ASSERTIONS_RUN=3"

    monkeypatch.setattr(orchestrator, "_run_acceptance_script", _acc)

    (boot_ok, boot_detail), acceptance = await orchestrator._run_release_smoke("noserve", "v1.0.0")

    assert boot_ok is False and "/api/v1/release-notes" in boot_detail and "neobsahuje verziu v1.0.0" in boot_detail
    assert acceptance is None
    assert called["acc"] is False


async def test_integration_endpoint_404_is_blocked(tmp_path, monkeypatch) -> None:
    """2a: FE tab present but the BE endpoint is missing entirely (404) → boot FAIL (the nex-payables BE drop)."""
    monkeypatch.setattr(orchestrator.claude_agent, "PROJECTS_ROOT", tmp_path)
    _make_project(tmp_path, "no404")
    monkeypatch.setattr(
        orchestrator,
        "_compose_smoke_step",
        _Recorder(release_notes=(0, "RELEASE_NOTES_STATUS 404\nRELEASE_NOTES_BODY Not Found")),
    )

    (boot_ok, boot_detail), acceptance = await orchestrator._run_release_smoke("no404", "v1.0.0")

    assert boot_ok is False and "HTTP 404" in boot_detail
    assert acceptance is None


# ===========================================================================
# Regression — a SECOND version no longer deadlocks (the pre-smoke on-disk note)
# ===========================================================================
async def test_second_version_gate_resolved_by_pre_smoke_note(tmp_path, monkeypatch) -> None:
    """THE deadlock the gate would otherwise create: for a 2nd version the served list carries prior releases
    but NOT the completing one (its note is committed only at PASS — after the smoke), so 2a can never pass →
    the PASS-time commit never happens → deadlock.

    This asserts the mechanism that breaks it: writing the completing version's note to DISK BEFORE the smoke
    (what :func:`orchestrator._write_release_note_to_disk` does in the round) makes the baked image serve it.
    Here the release-notes probe is wired to READ the on-disk ``docs/specs/versions/v*/RELEASE_NOTES.md``
    (simulating the baked-image endpoint) so we can prove: WITHOUT the completing note on disk → blocked; WITH
    it (the pre-smoke write) → pass."""
    proj = _make_project(tmp_path, "second")
    versions_dir = proj / "docs" / "specs" / "versions"
    # a PRIOR released version already on disk — the "second version" premise
    (versions_dir / "v0.9.0").mkdir(parents=True)
    (versions_dir / "v0.9.0" / "RELEASE_NOTES.md").write_text("## v0.9.0\n\n- Prvé vydanie.\n", encoding="utf-8")
    monkeypatch.setattr(orchestrator.claude_agent, "PROJECTS_ROOT", tmp_path)

    def _served_from_disk() -> str:
        vs = sorted(d.name for d in versions_dir.glob("v*") if (d / "RELEASE_NOTES.md").is_file())
        return _rn_probe_out(200, vs)

    class _DiskProbeRecorder(_Recorder):
        async def __call__(self, cmd, timeout):
            if "release-notes" in " ".join(cmd):
                self.calls.append(cmd)
                return (0, _served_from_disk())  # the endpoint serves whatever notes are baked on disk
            return await super().__call__(cmd, timeout)

    monkeypatch.setattr(orchestrator, "_compose_smoke_step", _DiskProbeRecorder())
    monkeypatch.setattr(orchestrator, "_run_acceptance_script", _script_ok)

    # WITHOUT the completing version's note on disk → the served list lacks v1.0.0 → 2a BLOCKS (the deadlock).
    (boot_ok, boot_detail), _acc = await orchestrator._run_release_smoke("second", "v1.0.0")
    assert boot_ok is False and "neobsahuje verziu v1.0.0" in boot_detail

    # The pre-smoke write puts the completing version's note on disk (the fix) → the baked endpoint now serves
    # it → 2a PASSES. (In the live round this write runs before _run_release_smoke; here we invoke it directly.)
    (versions_dir / "v1.0.0").mkdir()
    (versions_dir / "v1.0.0" / "RELEASE_NOTES.md").write_text("## v1.0.0\n\n- Druhé vydanie.\n", encoding="utf-8")
    (boot_ok, boot_detail), acceptance = await orchestrator._run_release_smoke("second", "v1.0.0")
    assert boot_ok is True and boot_detail == "app booted + responds"
    assert acceptance is not None and acceptance[0] is True
