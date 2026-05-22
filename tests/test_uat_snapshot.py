"""Tests for scripts/uat-snapshot.py.

Per F-003 §4.5 (uat-snapshot — ad-hoc DB dump pred risk-bound operáciami).
Tests derived from spec per Implementer charter §13.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "uat-snapshot.py"


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _import_module(monkeypatch):
    import importlib.util

    spec = importlib.util.spec_from_file_location("uat_snapshot", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    monkeypatch.setattr("sys.path", [str(SCRIPT.parent), *sys.path])
    spec.loader.exec_module(mod)
    return mod


def test_help_shows_usage():
    r = _run(["--help"])
    assert r.returncode == 0
    assert "slug" in r.stdout.lower()


def test_invalid_slug_fails_fast():
    r = _run(["BAD/slug"])
    assert r.returncode == 1
    assert "slug" in r.stderr.lower()


def test_snapshot_fails_when_uat_not_deployed(monkeypatch, tmp_path):
    mod = _import_module(monkeypatch)
    monkeypatch.setattr(mod, "UAT_ROOT", tmp_path / "uat")
    (tmp_path / "uat").mkdir()

    rc = mod.snapshot("dev", reason=None, version="v0.0.0")
    assert rc == 1


def test_snapshot_writes_to_snapshots_dir(monkeypatch, tmp_path):
    mod = _import_module(monkeypatch)
    fake_uat = tmp_path / "uat" / "dev"
    fake_uat.mkdir(parents=True)
    (fake_uat / "docker-compose.yml").write_text("# stub")
    snapshots_dir = fake_uat / "snapshots"
    snapshots_dir.mkdir()
    monkeypatch.setattr(mod, "UAT_ROOT", tmp_path / "uat")

    def fake_exec(container, command, **kwargs):
        result = MagicMock()
        result.stdout = b"-- pg_dump output\n"
        return result

    monkeypatch.setattr(mod._uat_lib, "docker_exec", fake_exec)

    rc = mod.snapshot("dev", reason=None, version="v0.2.0")
    assert rc == 0
    snapshots = list(snapshots_dir.glob("*.sql.gz"))
    assert len(snapshots) == 1
    assert "v0.2.0" in snapshots[0].name


def test_snapshot_includes_reason_in_filename(monkeypatch, tmp_path):
    mod = _import_module(monkeypatch)
    fake_uat = tmp_path / "uat" / "dev"
    fake_uat.mkdir(parents=True)
    (fake_uat / "docker-compose.yml").write_text("# stub")
    (fake_uat / "snapshots").mkdir()
    monkeypatch.setattr(mod, "UAT_ROOT", tmp_path / "uat")

    monkeypatch.setattr(
        mod._uat_lib,
        "docker_exec",
        lambda c, cmd, **kw: MagicMock(stdout=b"data"),
    )

    mod.snapshot("dev", reason="before-experimental", version="v0.2.0")
    snapshots = list((fake_uat / "snapshots").glob("*.sql.gz"))
    assert any("before-experimental" in s.name for s in snapshots)


def test_snapshot_sets_0600_permissions(monkeypatch, tmp_path):
    mod = _import_module(monkeypatch)
    fake_uat = tmp_path / "uat" / "dev"
    fake_uat.mkdir(parents=True)
    (fake_uat / "docker-compose.yml").write_text("# stub")
    (fake_uat / "snapshots").mkdir()
    monkeypatch.setattr(mod, "UAT_ROOT", tmp_path / "uat")

    monkeypatch.setattr(
        mod._uat_lib,
        "docker_exec",
        lambda c, cmd, **kw: MagicMock(stdout=b"data"),
    )

    mod.snapshot("dev", reason=None, version="v0.2.0")
    snapshots = list((fake_uat / "snapshots").glob("*.sql.gz"))
    # Check that file is owner-only readable
    mode = snapshots[0].stat().st_mode & 0o777
    assert mode == 0o600


def test_snapshot_uses_default_reason_label(monkeypatch, tmp_path):
    """Without --reason flag, filename uses 'ad-hoc' label per F-003 §8."""
    mod = _import_module(monkeypatch)
    fake_uat = tmp_path / "uat" / "dev"
    fake_uat.mkdir(parents=True)
    (fake_uat / "docker-compose.yml").write_text("# stub")
    (fake_uat / "snapshots").mkdir()
    monkeypatch.setattr(mod, "UAT_ROOT", tmp_path / "uat")

    monkeypatch.setattr(
        mod._uat_lib,
        "docker_exec",
        lambda c, cmd, **kw: MagicMock(stdout=b"data"),
    )

    mod.snapshot("dev", reason=None, version="v0.2.0")
    snapshots = list((fake_uat / "snapshots").glob("*.sql.gz"))
    assert any("ad-hoc" in s.name for s in snapshots)
