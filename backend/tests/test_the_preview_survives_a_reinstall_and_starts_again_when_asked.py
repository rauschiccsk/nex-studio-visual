"""DEV-419 — the live preview keeps the libraries it pre-bundled outside the project, and „Spustiť náhľad znova“ builds
it anew even when it runs.

17.11.2026 NEX Manager 1.7.0: the preview started at 15:09:30; at 15:10 the AI Agent ran ``npm ci`` (the Vizuál's
first step), which deleted ``node_modules`` with the tmpfs Vite kept its pre-bundled react on. Every
``/node_modules/.vite/deps/…`` answered 504, the page stayed blank and no checklist link opened. The button left a
running preview alone and was not shown for it. Director: „Založ tiket a zaraď do balíku. Celý balík budeme
implementovať.“
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from backend.services import vizual_sandbox

LABEL = "nex-studio.vizual.dependencies"


def _tmpfs(argv: list[str]) -> list[str]:
    return [argv[i + 1] for i, a in enumerate(argv) if a == "--tmpfs"]


def test_vite_keeps_its_pre_bundled_libraries_outside_the_project(tmp_path) -> None:
    argv = vizual_sandbox.build_run_argv(slug="nex-manager", frontend_host_path=tmp_path, dependencies="x")
    config = vizual_sandbox._render_override_config()

    assert f'cacheDir: "{vizual_sandbox.VITE_CACHE_DIR}",' in config
    assert f"{vizual_sandbox.VITE_CACHE_DIR}:rw,mode=1777" in _tmpfs(argv)
    # Outside the bind-mounted project (/app): an npm ci in the project cannot take it away.
    assert not vizual_sandbox.VITE_CACHE_DIR.startswith("/app")


def _running_preview(monkeypatch, tmp_path) -> list:
    """A preview that runs under its right name on the libraries its frontend has now; records rm / run."""
    (tmp_path / "package-lock.json").write_bytes(b'{"lock": "same"}')
    calls: list = []
    monkeypatch.setattr(vizual_sandbox, "_resolve_frontend_path", lambda slug, fp, directory="frontend": tmp_path)
    monkeypatch.setattr(vizual_sandbox, "_inspect_state", lambda name: {"exists": True, "running": True})
    monkeypatch.setattr(vizual_sandbox, "_running_public_host", lambda name: vizual_sandbox.public_host("nex-manager"))
    monkeypatch.setattr(
        vizual_sandbox, "_running_dependencies", lambda name: vizual_sandbox.dependencies_fingerprint(tmp_path)
    )
    monkeypatch.setattr(
        vizual_sandbox, "_running_config", lambda name: vizual_sandbox.PREVIEW_CONFIG_VERSION
    )  # DEV-521
    monkeypatch.setattr(vizual_sandbox, "_force_remove", lambda name: calls.append(("rm", name)))
    monkeypatch.setattr(vizual_sandbox, "_ensure_node_modules", lambda path, slug: None)
    monkeypatch.setattr(vizual_sandbox, "_write_override_config", lambda path: path)

    def _run(argv, **_):
        calls.append(("run", [a for a in argv if a.startswith(f"{LABEL}=")]))
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(vizual_sandbox.subprocess, "run", _run)
    return calls


def test_a_running_preview_is_reused_when_nobody_asked_for_it_anew(monkeypatch, tmp_path: Path) -> None:
    calls = _running_preview(monkeypatch, tmp_path)

    vizual_sandbox.spin_up("nex-manager")

    assert calls == []


def test_asked_anew_a_running_preview_on_current_libraries_is_replaced(monkeypatch, tmp_path: Path) -> None:
    calls = _running_preview(monkeypatch, tmp_path)

    vizual_sandbox.spin_up("nex-manager", fresh=True)

    assert [c[0] for c in calls] == ["rm", "run"]
    assert calls[0] == ("rm", "vizual-nex-manager")
