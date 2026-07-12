"""Tests for scripts/uat-status.py.

Per F-003 §4.3 (uat-status — containers + URL + snapshots + disk usage).
Tests derived from spec per Implementer charter §13.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "uat-status.py"


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _import_module(monkeypatch):
    import importlib.util

    spec = importlib.util.spec_from_file_location("uat_status", SCRIPT)
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


def test_not_deployed_when_compose_missing(monkeypatch, tmp_path, capsys):
    mod = _import_module(monkeypatch)
    monkeypatch.setattr(mod, "UAT_ROOT", tmp_path / "uat")

    rc = mod.status("dev")
    assert rc == 0
    captured = capsys.readouterr()
    assert "NOT DEPLOYED" in captured.out


def test_running_status_when_compose_and_containers_up(monkeypatch, tmp_path, capsys):
    mod = _import_module(monkeypatch)

    fake_uat = tmp_path / "uat" / "dev"
    fake_uat.mkdir(parents=True)
    (fake_uat / "docker-compose.yml").write_text("# stub")
    (fake_uat / "snapshots").mkdir()
    (fake_uat / ".env").write_text("UAT_SLUG=dev\nPROJECT_VERSION=v0.2.0\n")
    monkeypatch.setattr(mod, "UAT_ROOT", tmp_path / "uat")
    monkeypatch.setattr(mod._uat_lib, "PORT_STATE_FILE", tmp_path / ".uat-ports.json")

    # Pre-allocate port (state file populated)
    mod._uat_lib.allocate_port("dev")

    # Mock docker ps: 3 containers running
    def fake_run(cmd, **kwargs):
        # docker ps --format JSON output
        result = MagicMock()
        result.stdout = (
            '{"Names":"uat-dev-backend","Status":"Up 2 minutes (healthy)"}\n'
            '{"Names":"uat-dev-frontend","Status":"Up 2 minutes"}\n'
            '{"Names":"uat-dev-postgres","Status":"Up 2 minutes (healthy)"}\n'
        )
        result.returncode = 0
        return result

    monkeypatch.setattr(mod.subprocess, "run", fake_run)

    rc = mod.status("dev")
    assert rc == 0
    captured = capsys.readouterr()
    assert "RUNNING" in captured.out
    assert "uat-dev.isnex.eu" in captured.out


def test_status_lists_snapshots_count(monkeypatch, tmp_path, capsys):
    mod = _import_module(monkeypatch)
    fake_uat = tmp_path / "uat" / "dev"
    snapshots_dir = fake_uat / "snapshots"
    snapshots_dir.mkdir(parents=True)
    (snapshots_dir / "v0.1.0-2026-05-01.sql.gz").write_bytes(b"a" * 1024)
    (snapshots_dir / "v0.2.0-2026-05-15.sql.gz").write_bytes(b"b" * 2048)
    (fake_uat / "docker-compose.yml").write_text("# stub")
    monkeypatch.setattr(mod, "UAT_ROOT", tmp_path / "uat")
    monkeypatch.setattr(mod._uat_lib, "PORT_STATE_FILE", tmp_path / ".uat-ports.json")

    with patch.object(mod, "_get_container_statuses", return_value=[]):
        mod.status("dev")
    captured = capsys.readouterr()
    assert "2" in captured.out  # 2 snapshots
    assert "v0.2.0-2026-05-15.sql.gz" in captured.out  # latest


def test_status_reads_version_from_env(monkeypatch, tmp_path, capsys):
    mod = _import_module(monkeypatch)
    fake_uat = tmp_path / "uat" / "dev"
    fake_uat.mkdir(parents=True)
    (fake_uat / "docker-compose.yml").write_text("# stub")
    (fake_uat / "snapshots").mkdir()
    (fake_uat / ".env").write_text("PROJECT_VERSION=v0.9.42\nUAT_SLUG=dev\n")
    monkeypatch.setattr(mod, "UAT_ROOT", tmp_path / "uat")
    monkeypatch.setattr(mod._uat_lib, "PORT_STATE_FILE", tmp_path / ".uat-ports.json")

    with patch.object(mod, "_get_container_statuses", return_value=[]):
        mod.status("dev")
    captured = capsys.readouterr()
    # Rich styles the console output (ANSI colour codes + number highlighting split the version string), so
    # strip the escape sequences before the substring match — the version read from PROJECT_VERSION IS shown.
    import re

    plain = re.sub(r"\x1b\[[0-9;]*m", "", captured.out)
    assert "0.9.42" in plain


def test_read_env_value_parses_simple_kv(monkeypatch, tmp_path):
    mod = _import_module(monkeypatch)
    env = tmp_path / ".env"
    env.write_text("FOO=bar\nKEY=value123\n# comment\nEMPTY=\n")
    assert mod._read_env_value(env, "FOO") == "bar"
    assert mod._read_env_value(env, "KEY") == "value123"
    assert mod._read_env_value(env, "MISSING") is None
