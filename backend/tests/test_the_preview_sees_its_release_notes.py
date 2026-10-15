"""DEV-126 — the live preview sees the project's release notes, read-only, and nothing else of the project.

Director 15.10.2026: „URL na vizual nefunguje. Vypisuje "404 page not found"“. The preview of NEX Manager 1.3.0
stopped 7 minutes after „Vizuál je pripravený“ (28.09.2026): its „Aktualizácie" read ``../docs/specs/versions``
from ``/app`` — the cockpit's own standard for every app — and the sandbox had only ``frontend/``.
"""

from __future__ import annotations

from pathlib import Path

from backend.services.vizual_sandbox import build_run_argv


def _project(tmp_path: Path, *, notes: bool = True) -> Path:
    frontend = tmp_path / "nex-manager" / "frontend"
    frontend.mkdir(parents=True)
    if notes:
        (tmp_path / "nex-manager" / "docs" / "specs" / "versions" / "v1.3.0").mkdir(parents=True)
    return frontend


def _mounts(argv: list[str]) -> list[str]:
    return [argv[i + 1] for i, a in enumerate(argv) if a == "--mount"]


def test_the_release_notes_reach_the_preview_read_only_where_the_app_looks_for_them(tmp_path) -> None:
    frontend = _project(tmp_path)
    argv = build_run_argv(slug="nex-manager", frontend_host_path=frontend)
    notes = (tmp_path / "nex-manager" / "docs" / "specs" / "versions").resolve()
    # ``../docs/specs/versions`` seen from the app's ``/app`` is ``/docs/specs/versions``.
    assert _mounts(argv) == [f"type=bind,source={notes},target=/docs/specs/versions,readonly"]
    assert argv[argv.index("-v") + 1] == f"{frontend}:/app"


def test_a_project_without_release_notes_gets_no_extra_mount(tmp_path) -> None:
    argv = build_run_argv(slug="nex-manager", frontend_host_path=_project(tmp_path, notes=False))
    assert _mounts(argv) == []
    assert not any("/docs" in a for a in argv)


def test_a_link_out_of_the_project_never_reaches_the_preview(tmp_path) -> None:
    frontend = _project(tmp_path, notes=False)
    secret = tmp_path / "elsewhere"
    secret.mkdir()
    (tmp_path / "nex-manager" / "docs" / "specs").mkdir(parents=True)
    (tmp_path / "nex-manager" / "docs" / "specs" / "versions").symlink_to(secret)
    argv = build_run_argv(slug="nex-manager", frontend_host_path=frontend)
    assert _mounts(argv) == []
    assert not any(str(secret) in a for a in argv)


def test_a_linked_docs_folder_out_of_the_project_never_reaches_the_preview(tmp_path) -> None:
    frontend = _project(tmp_path, notes=False)
    outside = tmp_path / "outside"
    (outside / "specs" / "versions").mkdir(parents=True)
    (tmp_path / "nex-manager" / "docs").symlink_to(outside)
    argv = build_run_argv(slug="nex-manager", frontend_host_path=frontend)
    assert _mounts(argv) == []
