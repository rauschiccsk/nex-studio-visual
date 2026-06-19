"""Tests for scripts/_uat_lib.py shared helper module.

Per F-003 §3-§4 (paths, ports, snapshots, subprocess wrappers) +
Sub-round 4 §3.1 (O-DS-2 Python + rich) + §3.4 (O-003-2 Forever snapshot retention).

Tests derived from spec, not from implementation (Implementer charter §13).

v0.9.0 Phase 2 (CR-2): the per-project provisioning helpers (detect_*, env synthesis, redeploy
preservation, health-path resolution, compose rendering) moved to
:mod:`backend.services.uat_provisioner` — their tests now live in ``tests/test_uat_provisioner.py``.
This file keeps only the shared **ops** helpers retained in ``_uat_lib``.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add scripts/ to sys.path so we can import _uat_lib (no package install).
SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import _uat_lib  # noqa: E402

# ---------- Slug validation ----------


def test_validate_slug_accepts_simple():
    _uat_lib.validate_slug("mager")
    _uat_lib.validate_slug("dev")
    _uat_lib.validate_slug("test-customer-1")


def test_validate_slug_rejects_empty():
    with pytest.raises(ValueError, match="empty"):
        _uat_lib.validate_slug("")


def test_validate_slug_rejects_uppercase():
    with pytest.raises(ValueError, match="lowercase"):
        _uat_lib.validate_slug("MAGER")


def test_validate_slug_rejects_slash():
    with pytest.raises(ValueError, match="slash|invalid char"):
        _uat_lib.validate_slug("mager/sub")


def test_validate_slug_rejects_dot():
    with pytest.raises(ValueError, match="invalid char|dot"):
        _uat_lib.validate_slug("mager.evil")


def test_validate_slug_rejects_leading_dash():
    with pytest.raises(ValueError, match="dash|invalid"):
        _uat_lib.validate_slug("-mager")


def test_validate_slug_rejects_space():
    with pytest.raises(ValueError, match="invalid char|space"):
        _uat_lib.validate_slug("my customer")


# ---------- Path utilities ----------


def test_uat_dir_returns_expected_path():
    assert _uat_lib.uat_dir("mager") == Path("/opt/uat/mager")


def test_snapshots_dir_returns_expected_path():
    assert _uat_lib.snapshots_dir("mager") == Path("/opt/uat/mager/snapshots")


def test_project_dir_returns_expected_path():
    assert _uat_lib.project_dir("nex-inbox") == Path("/opt/projects/nex-inbox")


def test_uat_compose_path_returns_expected():
    assert _uat_lib.uat_compose_path("mager") == Path("/opt/uat/mager/docker-compose.yml")


def test_nginx_config_path_returns_expected():
    assert _uat_lib.nginx_config_path("mager") == Path("/etc/nginx/sites-available/uat-mager.conf")


def test_path_helpers_validate_slug():
    """All path helpers must reject invalid slugs (defence in depth)."""
    with pytest.raises(ValueError):
        _uat_lib.uat_dir("BAD/slug")


# ---------- CR-025: read_uat_env ----------


def test_uat_env_path_returns_expected():
    assert _uat_lib.uat_env_path("mager") == Path("/opt/uat/mager/.env")


def test_read_uat_env_parses_basic(tmp_path, monkeypatch):
    """CR-025: simple KEY=VALUE pairs parsed into dict."""
    env_file = tmp_path / "mager" / ".env"
    env_file.parent.mkdir()
    env_file.write_text("POSTGRES_USER=appuser\nPOSTGRES_DB=appdb\n")
    monkeypatch.setattr(_uat_lib, "uat_dir", lambda slug: tmp_path / slug)
    monkeypatch.setattr(_uat_lib, "uat_env_path", lambda slug: tmp_path / slug / ".env")

    env = _uat_lib.read_uat_env("mager")
    assert env["POSTGRES_USER"] == "appuser"
    assert env["POSTGRES_DB"] == "appdb"


def test_read_uat_env_ignores_comments_and_blanks(tmp_path, monkeypatch):
    """CR-025: # comments + blank lines skipped without breaking parse."""
    env_file = tmp_path / "dev" / ".env"
    env_file.parent.mkdir()
    env_file.write_text("# header comment\n\nPOSTGRES_USER=appuser\n  # indented comment\nPOSTGRES_DB=appdb\n\n")
    monkeypatch.setattr(_uat_lib, "uat_env_path", lambda slug: tmp_path / slug / ".env")

    env = _uat_lib.read_uat_env("dev")
    assert env == {"POSTGRES_USER": "appuser", "POSTGRES_DB": "appdb"}


def test_read_uat_env_missing_file_returns_empty(tmp_path, monkeypatch):
    """CR-025: graceful degradation when /opt/uat/<slug>/.env doesn't exist."""
    monkeypatch.setattr(_uat_lib, "uat_env_path", lambda slug: tmp_path / "absent" / ".env")
    assert _uat_lib.read_uat_env("absent") == {}


def test_read_uat_env_preserves_values_with_equals(tmp_path, monkeypatch):
    """CR-025: only first '=' splits, values like 'postgresql://...' stay intact."""
    env_file = tmp_path / "dev" / ".env"
    env_file.parent.mkdir()
    env_file.write_text("DATABASE_URL=postgresql://u:p@host:5432/db\n")
    monkeypatch.setattr(_uat_lib, "uat_env_path", lambda slug: tmp_path / slug / ".env")

    env = _uat_lib.read_uat_env("dev")
    assert env["DATABASE_URL"] == "postgresql://u:p@host:5432/db"


# ---------- Port allocation ----------


@pytest.fixture
def temp_port_state(tmp_path, monkeypatch):
    """Point _uat_lib at a temp port-allocations file."""
    state_file = tmp_path / ".uat-ports.json"
    monkeypatch.setattr(_uat_lib, "PORT_STATE_FILE", state_file)
    return state_file


def test_allocate_port_first_slug_returns_range_start(temp_port_state):
    port = _uat_lib.allocate_port("mager")
    assert port == 19500


def test_allocate_port_second_slug_returns_next(temp_port_state):
    _uat_lib.allocate_port("mager")
    port = _uat_lib.allocate_port("dev")
    assert port == 19501


def test_allocate_port_existing_slug_returns_same_port(temp_port_state):
    p1 = _uat_lib.allocate_port("mager")
    p2 = _uat_lib.allocate_port("mager")
    assert p1 == p2 == 19500


def test_allocate_port_persists_to_state_file(temp_port_state):
    _uat_lib.allocate_port("mager")
    _uat_lib.allocate_port("dev")
    data = json.loads(temp_port_state.read_text())
    assert data == {"mager": 19500, "dev": 19501}


def test_allocate_port_reuses_released_port(temp_port_state):
    _uat_lib.allocate_port("mager")  # 19500
    _uat_lib.allocate_port("dev")  # 19501
    _uat_lib.release_port("mager")
    port = _uat_lib.allocate_port("test-new")
    assert port == 19500  # reuses released


def test_allocate_port_range_exhausted_raises(temp_port_state):
    for i in range(100):
        _uat_lib.allocate_port(f"slug-{i}")
    with pytest.raises(RuntimeError, match="exhausted|no free port"):
        _uat_lib.allocate_port("slug-overflow")


def test_release_port_unknown_slug_is_noop(temp_port_state):
    _uat_lib.release_port("never-allocated")  # must not raise


def test_get_allocated_port_returns_none_for_unknown(temp_port_state):
    assert _uat_lib.get_allocated_port("mager") is None


def test_get_allocated_port_returns_port_for_known(temp_port_state):
    _uat_lib.allocate_port("mager")
    assert _uat_lib.get_allocated_port("mager") == 19500


def test_allocate_port_custom_range(temp_port_state):
    port = _uat_lib.allocate_port("mager", range_start=20000, range_end=20009)
    assert 20000 <= port <= 20009


# ---------- Snapshot filename ----------


def test_snapshot_filename_basic():
    name = _uat_lib.snapshot_filename("v0.1.0")
    assert name.startswith("v0.1.0-")
    assert name.endswith(".sql.gz")


def test_snapshot_filename_with_reason():
    name = _uat_lib.snapshot_filename("v0.1.0", reason="before-experimental")
    assert "before-experimental" in name
    assert name.endswith(".sql.gz")


def test_snapshot_filename_teardown_marker():
    name = _uat_lib.snapshot_filename("v0.1.0", teardown=True)
    assert "teardown" in name
    assert name.endswith(".sql.gz")


def test_snapshot_filename_contains_iso_date():
    import re

    name = _uat_lib.snapshot_filename("v0.1.0")
    assert re.search(r"\d{4}-\d{2}-\d{2}", name)


def test_snapshot_filename_teardown_and_reason_both():
    name = _uat_lib.snapshot_filename("v0.1.0", reason="ad-hoc", teardown=True)
    assert "ad-hoc" in name or "teardown" in name  # either annotation present


# ---------- Subprocess wrappers ----------


def test_docker_compose_invokes_subprocess(tmp_path):
    with patch("_uat_lib.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=b"", stderr=b"")
        _uat_lib.docker_compose(["up", "-d"], cwd=tmp_path)
        args, kwargs = mock_run.call_args
        cmd = args[0]
        assert cmd[0] == "docker"
        assert "compose" in cmd
        assert "up" in cmd and "-d" in cmd
        assert kwargs["cwd"] == tmp_path


def test_docker_compose_raises_on_nonzero(tmp_path):
    with patch("_uat_lib.subprocess.run") as mock_run:
        mock_run.side_effect = subprocess.CalledProcessError(1, ["docker", "compose"])
        with pytest.raises(subprocess.CalledProcessError):
            _uat_lib.docker_compose(["up"], cwd=tmp_path)


def test_docker_exec_invokes_subprocess():
    with patch("_uat_lib.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        _uat_lib.docker_exec("uat-mager-postgres", ["pg_dump", "-U", "postgres"])
        args, _ = mock_run.call_args
        cmd = args[0]
        assert cmd[:3] == ["docker", "exec", "uat-mager-postgres"]
        assert "pg_dump" in cmd


def test_wait_healthy_returns_true_when_endpoint_ok():
    with patch("_uat_lib.urllib.request.urlopen") as mock_open:
        resp = MagicMock()
        resp.status = 200
        resp.__enter__ = lambda self: self
        resp.__exit__ = lambda self, *a: None
        mock_open.return_value = resp
        assert _uat_lib.wait_healthy("http://localhost:19500/health", timeout=5) is True


def test_wait_healthy_returns_false_on_timeout():
    with patch("_uat_lib.urllib.request.urlopen") as mock_open, patch("_uat_lib.time.sleep"):
        mock_open.side_effect = Exception("connection refused")
        assert _uat_lib.wait_healthy("http://localhost:19500/health", timeout=1, interval=0.1) is False


# ---------- Template rendering (generic helper) ----------


def test_render_template_substitutes_jinja(tmp_path, monkeypatch):
    template_dir = tmp_path / "templates" / "uat"
    template_dir.mkdir(parents=True)
    (template_dir / "test.conf").write_text("port = {{ UAT_PORT }};\nslug = {{ SLUG }};")
    monkeypatch.setattr(_uat_lib, "TEMPLATES_DIR", tmp_path / "templates")

    rendered = _uat_lib.render_template("uat/test.conf", {"UAT_PORT": "19500", "SLUG": "mager"})
    assert "port = 19500;" in rendered
    assert "slug = mager;" in rendered


def test_render_template_missing_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(_uat_lib, "TEMPLATES_DIR", tmp_path / "templates")
    with pytest.raises(Exception):  # jinja2.TemplateNotFound or similar
        _uat_lib.render_template("uat/nonexistent.conf", {})


# ---------- Rich UI helpers ----------


def test_console_is_rich_console():
    from rich.console import Console

    assert isinstance(_uat_lib.console, Console)


def test_status_table_returns_rich_table():
    from rich.table import Table

    table = _uat_lib.status_table({"Slug": "mager", "Port": "19500"})
    assert isinstance(table, Table)


def test_confirm_returns_default_when_non_interactive(monkeypatch):
    # When stdin is not a TTY, confirm returns the default value.
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert _uat_lib.confirm("Continue?", default=False) is False
    assert _uat_lib.confirm("Continue?", default=True) is True


def test_print_url_outputs_url(capsys):
    _uat_lib.print_url("https://uat-mager.isnex.eu")
    captured = capsys.readouterr()
    assert "uat-mager.isnex.eu" in captured.out
