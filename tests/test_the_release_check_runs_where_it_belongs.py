"""DEV-58 — a project's release check (``release_smoke_test.sh``) runs where it belongs, and cannot fill the server.

The previerka of Dedo Home on 04.10.2026 (the cockpit's real engine over a copy of the project) found three faults
in the environment the check ran in: C1 it ran in the cockpit's ``/app``, not in the project; C2 its ``mktemp -d``
files lay where the Docker daemon cannot see them; C4 nothing bounded what it wrote — the project's tests filled
ANDROS's root disk. These tests run a real script through the real runner.
"""

from __future__ import annotations

import asyncio
import os
import time
from collections import namedtuple
from pathlib import Path

import pytest

from backend.config.settings import settings
from backend.services import orchestrator, smoke_scratch

Usage = namedtuple("Usage", "total used free")
GB = 1024**3


def _script(root: Path, body: str) -> Path:
    project = root / "projekt"
    project.mkdir(parents=True, exist_ok=True)
    script = project / "release_smoke_test.sh"
    script.write_text("set -e\n" + body)
    return script


def _run(script: Path) -> tuple[int, str]:
    return asyncio.run(orchestrator._run_acceptance_script(script, {"SMOKE_PROJECT": "x"}))


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # A zombie still answers kill(0); it is gone in every way that matters.
    try:
        return Path(f"/proc/{pid}/stat").read_text().split()[2] != "Z"
    except OSError:
        return False


def test_the_check_runs_in_the_project_with_a_scratch_folder_docker_sees(tmp_path):
    script = _script(
        tmp_path,
        'echo "PWD=$(pwd)"\necho "TMP=$TMPDIR"\ntest -d "$TMPDIR"\nmktemp -d > /dev/null\necho "ASSERTIONS_RUN=1"\n',
    )

    rc, out = _run(script)

    assert rc == 0, out
    lines = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
    assert lines["PWD"] == str(script.parent), "C1: skript beží mimo priečinka projektu"
    scratch = Path(lines["TMP"])
    assert scratch.parent == smoke_scratch.scratch_root(), "C2: dočasné súbory mimo priečinka, ktorý vidí Docker"
    assert not scratch.exists(), "dočasný priečinok po behu ostal"


def test_without_the_folder_docker_sees_the_check_does_not_run_blind(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "release_smoke_tmp_root", str(tmp_path / "nepripojene"))
    marker = tmp_path / "bezal"
    script = _script(tmp_path, f'touch "{marker}"\n')

    rc, out = _run(script)

    assert rc == 126 and "v PROD compose kokpitu chýba jeho pripojenie" in out
    assert not marker.exists(), "skript bežal bez priečinka, ktorý vidí Docker"


def _disk(monkeypatch, free_values: list[int]) -> None:
    """The free space the guard sees: the first value at the start, then one per look (the last one repeats)."""
    values = iter(free_values)
    last = [free_values[-1]]

    def _usage(_path):
        last[0] = next(values, last[0])
        return Usage(total=900 * GB, used=0, free=last[0])

    monkeypatch.setattr(smoke_scratch.shutil, "disk_usage", _usage)
    monkeypatch.setattr(smoke_scratch, "DISK_GUARD_INTERVAL", 0.2)


def _stoppable(tmp_path) -> tuple[Path, Path]:
    pid_file = tmp_path / "dieta.pid"
    script = _script(tmp_path, f'sleep 60 &\necho $! > "{pid_file}"\nsleep 60\n')
    return script, pid_file


def test_a_check_that_takes_more_than_the_budget_is_stopped_with_its_children(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "release_smoke_disk_budget_bytes", 30 * GB)
    monkeypatch.setattr(settings, "release_smoke_min_free_bytes", 5 * GB)
    _disk(monkeypatch, [100 * GB, 90 * GB, 60 * GB])
    script, pid_file = _stoppable(tmp_path)

    started = time.monotonic()
    rc, out = _run(script)

    assert time.monotonic() - started < 20, "strážca disku nezastavil previerku"
    assert rc == 125
    assert out.splitlines()[-1] == (
        "Previerka zastavená strážcom disku: od začiatku zabrala na disku servera 40,0 GB, viac než dovolených "
        "30,0 GB (voľné miesto kleslo z 100,0 na 60,0 GB). Skúšky projektu nesmú zaplniť server — over, čo v nich "
        "toľko zapisuje."
    )
    assert not _alive(int(pid_file.read_text())), "proces, ktorý previerka spustila, beží ďalej"


def test_a_server_below_the_floor_stops_the_check_even_within_the_budget(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "release_smoke_disk_budget_bytes", 30 * GB)
    monkeypatch.setattr(settings, "release_smoke_min_free_bytes", 20 * GB)
    _disk(monkeypatch, [25 * GB, 15 * GB])
    script, _pid = _stoppable(tmp_path)

    rc, out = _run(script)

    assert rc == 125 and out.splitlines()[-1].startswith(
        "Previerka zastavená strážcom disku: na disku servera ostáva len 15,0 GB voľných, menej než hranica 20,0 GB."
    )


def test_a_check_within_the_limits_runs_to_its_end(tmp_path, monkeypatch):
    _disk(monkeypatch, [100 * GB, 99 * GB])
    script = _script(tmp_path, "sleep 1\necho hotovo\n")

    rc, out = _run(script)

    assert (rc, out.strip()) == (0, "hotovo")


def test_a_check_past_its_time_is_stopped_with_its_children_too(tmp_path, monkeypatch):
    monkeypatch.setattr(orchestrator, "RELEASE_ACCEPTANCE_TIMEOUT", 1)
    script, pid_file = _stoppable(tmp_path)

    rc, out = _run(script)

    assert (rc, out) == (124, "timeout (1s)")
    assert not _alive(int(pid_file.read_text()))


def test_the_backend_start_removes_what_interrupted_runs_left(monkeypatch, tmp_path):
    left = smoke_scratch.new_scratch()
    (left / "subor").write_text("x")

    assert smoke_scratch.sweep() == 1 and not left.exists()

    monkeypatch.setattr(settings, "release_smoke_tmp_root", str(tmp_path / "nie-je"))
    assert smoke_scratch.sweep() == 0


@pytest.mark.parametrize("folder_mode", [0o1777])
def test_the_scratch_folder_is_writable_by_a_container_running_as_someone_else(folder_mode):
    folder = smoke_scratch.new_scratch()
    assert folder.stat().st_mode & 0o7777 == folder_mode


def test_the_backend_start_calls_the_sweep():
    """The start of the backend is too heavy for a test to run; the wiring is checked in its code instead —
    the call is in the lifespan, not just a function nobody calls."""
    import ast

    import backend.main as main

    tree = ast.parse(Path(main.__file__).read_text(encoding="utf-8"))
    lifespan = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "lifespan")
    called = {n.func.id for n in ast.walk(lifespan) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert "_sweep_smoke_scratch" in called
