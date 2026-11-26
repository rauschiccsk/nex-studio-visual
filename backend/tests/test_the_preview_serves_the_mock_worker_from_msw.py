"""DEV-521 — the Vizuál preview gets its mock server's worker (``/mockServiceWorker.js``) from the installed ``msw``
library, never from a file in the project an agent may delete.

26.11.2026, orthodox-register: the Director — „Vizuál … hotový ale nedá sa otvoriť … prázdna čierna obrazovka“. The
Staviteľ had deleted ``frontend/public/mockServiceWorker.js`` twice that day (with the template upgrade and when
tidying up after the Vizuál); Vite then answered its address with index.html, MSW refused it („unsupported MIME type
('text/html')“) and the app never rendered. Served from the library the preview runs. And a running preview started
with an older preview config of the cockpit is built anew — the way one on other libraries is (DEV-241) — so the fix
reaches a preview that already runs. Director: „Súhlasím“.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

from backend.services import vizual_sandbox

CONFIG_LABEL = "nex-studio.vizual.config"
WORKER = "/* the msw worker 2.15.0 */"

DRIVER = """
const plugin = mswWorker();
let route, handle;
plugin.configureServer({ middlewares: { use(r, fn) { route = r; handle = fn; } } });
const headers = {};
handle(
  { url: "/mockServiceWorker.js" },
  {
    setHeader(k, v) { headers[k] = v; },
    end(body) { console.log(JSON.stringify({ route, headers, body: String(body) })); },
  },
  () => console.log(JSON.stringify({ route, next: true })),
);
"""


def _app(tmp_path: Path, *, exports: dict | None = None, msw: bool = True) -> Path:
    """A frontend with ``msw`` installed, no ``public/mockServiceWorker.js`` (as orthodox-register was left)."""
    app = tmp_path / "app"
    (app / "public").mkdir(parents=True)
    if msw:
        lib = app / "node_modules" / "msw" / "lib"
        lib.mkdir(parents=True)
        package = {"name": "msw", "version": "2.15.0"}
        if exports is not None:
            package["exports"] = exports
        (lib.parent / "package.json").write_text(json.dumps(package))
        (lib / "mockServiceWorker.js").write_text(WORKER)
    return app


def _serve(app: Path) -> dict:
    """Run the preview's own plugin in Node and ask it for the worker the way the browser does."""
    node = shutil.which("node")
    assert node, "Node runs the preview's plugin — the cockpit's CI sets it up"
    (app / "check.mjs").write_text(vizual_sandbox.msw_worker_plugin_source() + DRIVER)
    run = subprocess.run([node, "check.mjs"], cwd=app, capture_output=True, text=True, timeout=60)
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout.strip().splitlines()[-1])


def test_the_worker_comes_from_the_installed_msw_with_no_file_in_the_project(tmp_path) -> None:
    app = _app(
        tmp_path, exports={"./mockServiceWorker.js": "./lib/mockServiceWorker.js", "./package.json": "./package.json"}
    )

    assert _serve(app) == {
        "route": "/mockServiceWorker.js",
        "headers": {"Content-Type": "text/javascript; charset=utf-8", "Cache-Control": "no-cache"},
        "body": WORKER,
    }
    assert not (app / "public" / "mockServiceWorker.js").exists()


def test_an_msw_that_does_not_export_the_worker_still_gives_it(tmp_path) -> None:
    assert _serve(_app(tmp_path, exports=None))["body"] == WORKER


def test_without_msw_the_request_goes_on_to_vite(tmp_path) -> None:
    assert _serve(_app(tmp_path, msw=False)) == {"route": "/mockServiceWorker.js", "next": True}


def test_the_preview_config_uses_the_plugin() -> None:
    config = vizual_sandbox._render_override_config()

    assert vizual_sandbox.msw_worker_plugin_source() in config
    assert "plugins: [mswWorker()]" in config


# ── a running preview on an older config of the cockpit is built anew ─────────────────────────


def test_the_preview_is_started_with_the_version_of_the_cockpits_config(tmp_path) -> None:
    (tmp_path / "package-lock.json").write_bytes(b'{"lock": 2}')

    argv = vizual_sandbox.build_run_argv(slug="orthodox-register", frontend_host_path=tmp_path)

    labels = [argv[i + 1] for i, a in enumerate(argv) if a == "--label"]
    version = hashlib.sha256(vizual_sandbox._render_override_config().encode()).hexdigest()[:12]
    assert vizual_sandbox.PREVIEW_CONFIG_VERSION == version
    assert f"{CONFIG_LABEL}={version}" in labels


def _sandbox(monkeypatch, tmp_path, *, config: str | None) -> list:
    """A running preview on the current libraries, started with the cockpit's config ``config``; records rm / run."""
    (tmp_path / "package-lock.json").write_bytes(b'{"lock": "new"}')
    calls: list = []
    monkeypatch.setattr(vizual_sandbox, "_resolve_frontend_path", lambda slug, fp, directory="frontend": tmp_path)
    monkeypatch.setattr(vizual_sandbox, "_inspect_state", lambda name: {"exists": True, "running": True})
    monkeypatch.setattr(
        vizual_sandbox, "_running_public_host", lambda name: vizual_sandbox.public_host("orthodox-register")
    )
    monkeypatch.setattr(
        vizual_sandbox, "_running_dependencies", lambda name: vizual_sandbox.dependencies_fingerprint(tmp_path)
    )
    monkeypatch.setattr(vizual_sandbox, "_running_config", lambda name: config)
    monkeypatch.setattr(vizual_sandbox, "_force_remove", lambda name: calls.append("rm"))
    monkeypatch.setattr(vizual_sandbox, "_ensure_node_modules", lambda path, slug: None)
    monkeypatch.setattr(vizual_sandbox, "_write_override_config", lambda path: path)
    monkeypatch.setattr(
        vizual_sandbox.subprocess,
        "run",
        lambda argv, **_: calls.append("run") or subprocess.CompletedProcess(argv, 0, "", ""),
    )
    return calls


def test_a_preview_on_the_current_config_is_reused(monkeypatch, tmp_path) -> None:
    calls = _sandbox(monkeypatch, tmp_path, config=vizual_sandbox.PREVIEW_CONFIG_VERSION)

    vizual_sandbox.spin_up("orthodox-register")

    assert calls == []


def test_a_preview_on_an_older_config_or_none_is_built_anew(monkeypatch, tmp_path) -> None:
    for config in ("0123456789ab", ""):  # an older cockpit's config; a preview started before the label existed
        calls = _sandbox(monkeypatch, tmp_path, config=config)

        vizual_sandbox.spin_up("orthodox-register")

        assert calls == ["rm", "run"], config


def test_a_preview_on_an_older_config_is_not_current_one_docker_cannot_tell_is(monkeypatch, tmp_path) -> None:
    (tmp_path / "package-lock.json").write_bytes(b'{"lock": "new"}')
    monkeypatch.setattr(vizual_sandbox, "_resolve_frontend_path", lambda slug, fp, directory="frontend": tmp_path)
    monkeypatch.setattr(
        vizual_sandbox, "_running_dependencies", lambda name: vizual_sandbox.dependencies_fingerprint(tmp_path)
    )
    seen = {}
    monkeypatch.setattr(vizual_sandbox, "_running_config", lambda name: seen["config"])

    for config, current in (
        (vizual_sandbox.PREVIEW_CONFIG_VERSION, True),
        ("0123456789ab", False),
        ("", False),
        (None, True),
    ):
        seen["config"] = config
        assert vizual_sandbox.preview_is_current("orthodox-register") is current, config
