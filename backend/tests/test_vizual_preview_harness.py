"""Vizuál preview harness (v4.0.22): the live-FE sandbox runs in PREVIEW mode.

The faithful live-FE Vizuál (what replaces the decoupled mockup) needs the real app to
render in the sandbox WITHOUT a backend or login. The FE preview entry (MSW mock /session
+ fixtures) activates on ``import.meta.env.VITE_PREVIEW`` — Vite auto-exposes ``VITE_``-prefixed
vars — so the sandbox ``docker run`` must set it.
"""

from __future__ import annotations

from pathlib import Path

from backend.services.vizual_sandbox import build_run_argv


def test_sandbox_run_argv_enables_preview_mode() -> None:
    argv = build_run_argv(slug="demo", frontend_host_path=Path("/tmp/demo/frontend"))
    # Vite exposes VITE_-prefixed env to import.meta.env → the FE preview entry activates.
    assert "VITE_PREVIEW=1" in argv
    # Passed as a real ``-e`` docker env option (immediately preceded by ``-e``).
    assert argv[argv.index("VITE_PREVIEW=1") - 1] == "-e"
