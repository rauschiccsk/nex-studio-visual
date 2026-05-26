"""Tests for scripts/_uat_lib.py shared helper module.

Per F-003 §3-§4 (paths, ports, snapshots, subprocess wrappers) +
Sub-round 4 §3.1 (O-DS-2 Python + rich) + §3.4 (O-003-2 Forever snapshot retention).

Tests derived from spec, not from implementation (Implementer charter §13).
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


# ---------- Template rendering ----------


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


# ---------- CR-021: detect_backend_config (real I/O cez tmp_path) ----------


def test_detect_backend_config_returns_default_when_no_compose(tmp_path):
    """No docker-compose.yml in source → returns safe defaults."""
    cfg = _uat_lib.detect_backend_config(tmp_path)
    assert cfg["backend_port"] == 8000
    assert cfg["healthcheck_test"] is None
    assert cfg["dockerfile"] == "Dockerfile"


def test_detect_backend_config_parses_nex_inbox_style(tmp_path):
    """Standard 'host:container' port mapping (e.g. '8000:8000')."""
    (tmp_path / "docker-compose.yml").write_text(
        "services:\n"
        "  backend:\n"
        "    build:\n"
        "      context: .\n"
        "      dockerfile: backend/Dockerfile\n"
        "    ports:\n"
        '      - "8000:8000"\n'
    )
    cfg = _uat_lib.detect_backend_config(tmp_path)
    assert cfg["backend_port"] == 8000
    assert cfg["dockerfile"] == "backend/Dockerfile"


def test_detect_backend_config_parses_nex_studio_style(tmp_path):
    """Non-default port mapping (e.g. '9176:9176')."""
    (tmp_path / "docker-compose.yml").write_text(
        "services:\n"
        "  backend:\n"
        "    build:\n"
        "      context: .\n"
        "      dockerfile: backend/Dockerfile\n"
        "    ports:\n"
        '      - "9176:9176"\n'
    )
    cfg = _uat_lib.detect_backend_config(tmp_path)
    assert cfg["backend_port"] == 9176


def test_detect_backend_config_parses_localhost_prefix(tmp_path):
    """Port mapping with '127.0.0.1:' prefix — container port is LAST segment."""
    (tmp_path / "docker-compose.yml").write_text('services:\n  backend:\n    ports:\n      - "127.0.0.1:9176:9176"\n')
    cfg = _uat_lib.detect_backend_config(tmp_path)
    assert cfg["backend_port"] == 9176


def test_detect_backend_config_parses_dict_mapping(tmp_path):
    """Long-form dict port mapping with published/target keys."""
    (tmp_path / "docker-compose.yml").write_text(
        "services:\n  backend:\n    ports:\n      - published: 9176\n        target: 9176\n"
    )
    cfg = _uat_lib.detect_backend_config(tmp_path)
    assert cfg["backend_port"] == 9176


def test_detect_backend_config_preserves_custom_healthcheck(tmp_path):
    """Source-defined healthcheck.test is re-used (not overridden)."""
    (tmp_path / "docker-compose.yml").write_text(
        "services:\n"
        "  backend:\n"
        "    ports:\n"
        '      - "8000:8000"\n'
        "    healthcheck:\n"
        '      test: ["CMD", "curl", "-f", "http://localhost:8000/api/v1/health"]\n'
    )
    cfg = _uat_lib.detect_backend_config(tmp_path)
    assert cfg["healthcheck_test"] == [
        "CMD",
        "curl",
        "-f",
        "http://localhost:8000/api/v1/health",
    ]


def test_detect_backend_config_dockerfile_falls_back_when_missing(tmp_path):
    """No dockerfile specified in build → 'Dockerfile' default."""
    (tmp_path / "docker-compose.yml").write_text('services:\n  backend:\n    ports:\n      - "8000:8000"\n')
    cfg = _uat_lib.detect_backend_config(tmp_path)
    assert cfg["dockerfile"] == "Dockerfile"


# ---------- CR-022: detect_db_credentials ----------


def test_detect_db_credentials_parses_nex_studio_style(tmp_path):
    """nex-studio uses 'nexstudio' as user + db."""
    (tmp_path / "docker-compose.yml").write_text(
        "services:\n"
        "  db:\n"
        "    environment:\n"
        "      POSTGRES_USER: nexstudio\n"
        "      POSTGRES_PASSWORD: nexstudio\n"
        "      POSTGRES_DB: nexstudio\n"
    )
    creds = _uat_lib.detect_db_credentials(tmp_path)
    assert creds["POSTGRES_USER"] == "nexstudio"
    assert creds["POSTGRES_DB"] == "nexstudio"


def test_detect_db_credentials_parses_nex_inbox_style(tmp_path):
    """nex-inbox uses 'nex_inbox' as user + 'nex_inbox_dev' as db."""
    (tmp_path / "docker-compose.yml").write_text(
        "services:\n"
        "  db:\n"
        "    environment:\n"
        "      POSTGRES_DB: nex_inbox_dev\n"
        "      POSTGRES_USER: nex_inbox\n"
        "      POSTGRES_PASSWORD: dev_password_change_me\n"
    )
    creds = _uat_lib.detect_db_credentials(tmp_path)
    assert creds["POSTGRES_USER"] == "nex_inbox"
    assert creds["POSTGRES_DB"] == "nex_inbox_dev"


def test_detect_db_credentials_defaults_when_no_compose(tmp_path):
    """No source compose → safe defaults (None for db name = caller derives)."""
    creds = _uat_lib.detect_db_credentials(tmp_path)
    assert creds["POSTGRES_USER"] == "postgres"
    assert creds["POSTGRES_DB"] is None
    assert creds["POSTGRES_PASSWORD"] is None


# ---------- CR-022: detect_backend_env_vars ----------


def test_detect_backend_env_vars_parses_nex_studio_complex(tmp_path):
    """nex-studio backend has 12 env vars including ${VAR} expansion + plain SECRET_KEY."""
    (tmp_path / "docker-compose.yml").write_text(
        "services:\n"
        "  backend:\n"
        "    environment:\n"
        "      DATABASE_URL: postgresql+pg8000://nexstudio:nexstudio@db:5432/nexstudio\n"
        "      SECRET_KEY: change-me-in-production\n"
        "      CLAUDE_CODE_OAUTH_TOKEN: ${CLAUDE_CODE_OAUTH_TOKEN}\n"
        "      GITHUB_TOKEN: ${GITHUB_TOKEN}\n"
        "      DISABLE_AUTOUPDATER: 1\n"
    )
    env = _uat_lib.detect_backend_env_vars(tmp_path)
    # ${VAR} expansion → placeholder
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "__UAT_SYNTHETIC__"
    assert env["GITHUB_TOKEN"] == "__UAT_SYNTHETIC__"
    # _KEY suffix → synthetic random
    assert env["SECRET_KEY"] != "change-me-in-production"
    assert len(env["SECRET_KEY"]) >= 32
    # Plain value → copy as-is (string-ified)
    assert str(env["DISABLE_AUTOUPDATER"]) == "1"


def test_detect_backend_env_vars_parses_nex_inbox_split(tmp_path):
    """nex-inbox split DB_HOST/PORT/NAME/USER/PASSWORD. DB connection vars rewritten."""
    (tmp_path / "docker-compose.yml").write_text(
        "services:\n"
        "  db:\n"
        "    environment:\n"
        "      POSTGRES_USER: nex_inbox\n"
        "      POSTGRES_DB: nex_inbox_dev\n"
        "  backend:\n"
        "    environment:\n"
        "      DB_HOST: db\n"
        '      DB_PORT: "5432"\n'
        "      DB_NAME: nex_inbox_dev\n"
        "      DB_USER: nex_inbox\n"
        "      DB_PASSWORD: dev_password_change_me\n"
        "      TENANT_SLUG: dev\n"
    )
    env = _uat_lib.detect_backend_env_vars(tmp_path)
    # DB connection vars rewritten to UAT db hostname
    assert env["DB_HOST"] == "postgres"
    assert env["DB_NAME"] == "nex_inbox_dev"
    assert env["DB_USER"] == "nex_inbox"
    # DB_PASSWORD matches _PASSWORD suffix → synthetic
    assert env["DB_PASSWORD"] != "dev_password_change_me"
    assert len(env["DB_PASSWORD"]) >= 32
    # Plain non-secret var → copy as-is
    assert env["TENANT_SLUG"] == "dev"


def test_detect_backend_env_vars_generates_synthetic_secrets(tmp_path):
    """All keys matching _PASSWORD/_SECRET/_KEY/_TOKEN suffix → random hex32."""
    (tmp_path / "docker-compose.yml").write_text(
        "services:\n"
        "  backend:\n"
        "    environment:\n"
        "      MY_PASSWORD: original_password\n"
        "      OAUTH_TOKEN: original_token\n"
        "      JWT_SECRET: original_secret\n"
        "      API_KEY: original_key\n"
    )
    env = _uat_lib.detect_backend_env_vars(tmp_path)
    for key in ("MY_PASSWORD", "OAUTH_TOKEN", "JWT_SECRET", "API_KEY"):
        assert len(env[key]) >= 32, f"{key} not synthetic"
        assert "original_" not in env[key], f"{key} retains original value"


def test_detect_env_example_parses_basic(tmp_path):
    """CR-026: <source>/.env.example parsed s same parser ako read_uat_env."""
    (tmp_path / ".env.example").write_text(
        "# Header\n\nLAUNCH_TOKEN=change-me\nJWT_SECRET_KEY=set-in-prod\nOPERATOR_EMAIL=ops@example.com\n"
    )
    env = _uat_lib.detect_env_example(tmp_path)
    assert env == {
        "LAUNCH_TOKEN": "change-me",
        "JWT_SECRET_KEY": "set-in-prod",
        "OPERATOR_EMAIL": "ops@example.com",
    }


def test_detect_env_example_missing_file_returns_empty(tmp_path):
    """CR-026: graceful fallback when .env.example absent."""
    assert _uat_lib.detect_env_example(tmp_path) == {}


def test_detect_backend_env_vars_unions_env_example_with_compose(tmp_path):
    """CR-026: keys from BOTH .env.example and compose.environment present."""
    (tmp_path / ".env.example").write_text("LAUNCH_TOKEN=change-me\nOPERATOR_EMAIL=ops@example.com\n")
    (tmp_path / "docker-compose.yml").write_text(
        "services:\n  backend:\n    environment:\n      TENANT_SLUG: dev\n      DB_HOST: db\n"
    )
    env = _uat_lib.detect_backend_env_vars(tmp_path)
    assert "LAUNCH_TOKEN" in env  # from .env.example, secret → synthetic
    assert "OPERATOR_EMAIL" in env  # from .env.example, plain → copy
    assert env["OPERATOR_EMAIL"] == "ops@example.com"
    assert env["TENANT_SLUG"] == "dev"  # from compose
    assert env["DB_HOST"] == "postgres"  # from compose, rewritten to UAT hostname


def test_detect_backend_env_vars_compose_overrides_env_example(tmp_path):
    """CR-026: same key in both → compose value wins (authoritative for runtime)."""
    (tmp_path / ".env.example").write_text("OPERATOR_EMAIL=placeholder@example.com\n")
    (tmp_path / "docker-compose.yml").write_text(
        "services:\n  backend:\n    environment:\n      OPERATOR_EMAIL: ops@nexinbox.test\n"
    )
    env = _uat_lib.detect_backend_env_vars(tmp_path)
    assert env["OPERATOR_EMAIL"] == "ops@nexinbox.test"


def test_detect_backend_env_vars_env_example_secret_gets_synthetic(tmp_path):
    """CR-026: .env.example-only secret (e.g. LAUNCH_TOKEN) hits synthetic gen, not passthrough."""
    (tmp_path / ".env.example").write_text(
        "LAUNCH_TOKEN=change-me-in-prod\n"
        "JWT_SECRET_KEY=please-rotate\n"
        "EMAIL_CREDS_ENCRYPTION_KEY=base64-encoded-32byte-key\n"
    )
    (tmp_path / "docker-compose.yml").write_text("services:\n  backend:\n    environment: {}\n")
    env = _uat_lib.detect_backend_env_vars(tmp_path)
    for key in ("LAUNCH_TOKEN", "JWT_SECRET_KEY", "EMAIL_CREDS_ENCRYPTION_KEY"):
        assert len(env[key]) >= 32, f"{key} should be synthetic random hex32"
        assert "change-me" not in env[key]
        assert "please-rotate" not in env[key]
        assert "base64" not in env[key]


def test_detect_backend_env_vars_marks_user_secret_as_placeholder(tmp_path):
    """${VAR} env-var expansion → __UAT_SYNTHETIC__ placeholder (cannot read host env)."""
    (tmp_path / "docker-compose.yml").write_text(
        "services:\n"
        "  backend:\n"
        "    environment:\n"
        "      EXTERNAL_TOKEN: ${EXTERNAL_TOKEN}\n"
        "      MY_PLAIN: plain_value\n"
    )
    env = _uat_lib.detect_backend_env_vars(tmp_path)
    assert env["EXTERNAL_TOKEN"] == "__UAT_SYNTHETIC__"
    assert env["MY_PLAIN"] == "plain_value"


def test_detect_backend_env_vars_shares_synthetic_db_password_within_call(tmp_path):
    """CR-023: POSTGRES_PASSWORD + DB_PASSWORD + DATABASE_URL embed share one synth value.

    Bug #5 (smoke 2026-05-26): postgres container init password vs backend
    connect password were two independent ``secrets.token_hex(32)`` calls →
    auth failed. Fix: a single shared synthetic password threaded through
    every DB credential consumer in one detect_backend_env_vars() call.
    """
    (tmp_path / "docker-compose.yml").write_text(
        "services:\n"
        "  db:\n"
        "    environment:\n"
        "      POSTGRES_USER: appuser\n"
        "      POSTGRES_DB: appdb\n"
        "  backend:\n"
        "    environment:\n"
        "      POSTGRES_PASSWORD: prod_password\n"
        "      DB_PASSWORD: prod_password\n"
        "      DATABASE_URL: postgresql+pg8000://appuser:prod_password@db:5432/appdb\n"
    )
    env = _uat_lib.detect_backend_env_vars(tmp_path)
    # Extract embedded password from DATABASE_URL (between first ':' after ://
    # username and the '@' host separator).
    url = env["DATABASE_URL"]
    userinfo = url.split("://", 1)[1].rsplit("@", 1)[0]
    embedded_password = userinfo.split(":", 1)[1]
    assert env["POSTGRES_PASSWORD"] == embedded_password
    assert env["DB_PASSWORD"] == embedded_password
    assert env["POSTGRES_PASSWORD"] != "prod_password"  # not passthrough


def test_detect_backend_env_vars_accepts_explicit_shared_password(tmp_path):
    """CR-023: ``synthetic_db_password`` kwarg threads the same value into all consumers.

    Caller (uat-deploy.generate_uat_env) generates the password once for the
    top-level POSTGRES_PASSWORD .env line and passes it here so the backend's
    DATABASE_URL embed agrees with the postgres container init password.
    """
    (tmp_path / "docker-compose.yml").write_text(
        "services:\n"
        "  db:\n"
        "    environment:\n"
        "      POSTGRES_USER: appuser\n"
        "      POSTGRES_DB: appdb\n"
        "  backend:\n"
        "    environment:\n"
        "      DATABASE_URL: postgresql://appuser:prod_pwd@db:5432/appdb\n"
    )
    shared = "deadbeef" * 8  # 64-char placeholder
    env = _uat_lib.detect_backend_env_vars(tmp_path, synthetic_db_password=shared)
    assert f":{shared}@" in env["DATABASE_URL"]


# ---------- CR-022: detect_frontend_config ----------


def test_detect_frontend_config_parses_nex_studio_subdir(tmp_path):
    """nex-studio frontend: context = './frontend', dockerfile = 'Dockerfile'."""
    (tmp_path / "docker-compose.yml").write_text(
        "services:\n  frontend:\n    build:\n      context: ./frontend\n      dockerfile: Dockerfile\n"
    )
    cfg = _uat_lib.detect_frontend_config(tmp_path)
    assert cfg is not None
    assert cfg["context"] == "./frontend"
    assert cfg["dockerfile"] == "Dockerfile"


def test_detect_frontend_config_parses_nex_inbox_repo_root(tmp_path):
    """nex-inbox frontend: context = '.', dockerfile = 'frontend/Dockerfile'."""
    (tmp_path / "docker-compose.yml").write_text(
        "services:\n"
        "  frontend:\n"
        "    build:\n"
        "      context: .\n"
        "      dockerfile: frontend/Dockerfile\n"
        "      args:\n"
        "        VITE_API_BASE_URL: /api/v1\n"
    )
    cfg = _uat_lib.detect_frontend_config(tmp_path)
    assert cfg is not None
    assert cfg["context"] == "."
    assert cfg["dockerfile"] == "frontend/Dockerfile"
    assert cfg["build_args"] == {"VITE_API_BASE_URL": "/api/v1"}


def test_detect_frontend_config_returns_none_when_no_frontend_service(tmp_path):
    """Backend-only projekt → no frontend config detected."""
    (tmp_path / "docker-compose.yml").write_text("services:\n  backend:\n    image: foo\n")
    cfg = _uat_lib.detect_frontend_config(tmp_path)
    assert cfg is None


def test_detect_frontend_config_extracts_container_port_short_form(tmp_path):
    """CR-024: nex-studio short port form '9177:9177' → container_port = 9177.

    Bug #7 root: UAT template hardcoded ':80' but nex-studio frontend nginx
    listens on 9177 — docker-proxy forward to wrong port caused HTTP 502.
    """
    (tmp_path / "docker-compose.yml").write_text(
        'services:\n  frontend:\n    build:\n      context: ./frontend\n    ports:\n      - "9177:9177"\n'
    )
    cfg = _uat_lib.detect_frontend_config(tmp_path)
    assert cfg is not None
    assert cfg["container_port"] == 9177


def test_detect_frontend_config_extracts_container_port_with_ip_prefix(tmp_path):
    """CR-024: nex-inbox extended form '127.0.0.1:5173:80' → container_port = 80."""
    (tmp_path / "docker-compose.yml").write_text(
        'services:\n  frontend:\n    build:\n      context: .\n    ports:\n      - "127.0.0.1:5173:80"\n'
    )
    cfg = _uat_lib.detect_frontend_config(tmp_path)
    assert cfg is not None
    assert cfg["container_port"] == 80


def test_detect_frontend_config_defaults_container_port_to_80(tmp_path):
    """CR-024: no ports entry → container_port falls back to 80 (Docker default)."""
    (tmp_path / "docker-compose.yml").write_text("services:\n  frontend:\n    build:\n      context: ./frontend\n")
    cfg = _uat_lib.detect_frontend_config(tmp_path)
    assert cfg is not None
    assert cfg["container_port"] == 80


def test_detect_frontend_config_supports_dict_target(tmp_path):
    """CR-024: long-form ports dict {target: 8080, published: 80} → 8080."""
    (tmp_path / "docker-compose.yml").write_text(
        "services:\n"
        "  frontend:\n"
        "    build:\n"
        "      context: ./frontend\n"
        "    ports:\n"
        "      - target: 8080\n"
        "        published: 80\n"
        "        protocol: tcp\n"
    )
    cfg = _uat_lib.detect_frontend_config(tmp_path)
    assert cfg is not None
    assert cfg["container_port"] == 8080


def test_detect_frontend_config_strips_protocol_suffix(tmp_path):
    """CR-024: '8080:80/tcp' protocol suffix stripped → container_port = 80."""
    (tmp_path / "docker-compose.yml").write_text(
        'services:\n  frontend:\n    build:\n      context: ./frontend\n    ports:\n      - "8080:80/tcp"\n'
    )
    cfg = _uat_lib.detect_frontend_config(tmp_path)
    assert cfg is not None
    assert cfg["container_port"] == 80


# ---------- CR-022: detect_alembic_strategy ----------


def test_detect_alembic_strategy_self_bootstrap_nex_studio_style(tmp_path):
    """backend/main.py contains 'command.upgrade' → self-bootstrap mode."""
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / "main.py").write_text(
        "from alembic import command\n"
        "from alembic.config import Config\n"
        "\n"
        "def _run_alembic_upgrade():\n"
        "    alembic_cfg = Config('alembic.ini')\n"
        '    command.upgrade(alembic_cfg, "head")\n'
    )
    (tmp_path / "backend" / "alembic").mkdir()
    assert _uat_lib.detect_alembic_strategy(tmp_path) == "self-bootstrap"


def test_detect_alembic_strategy_external_nex_inbox_style(tmp_path):
    """backend/alembic exists but main.py has no command.upgrade → external."""
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / "main.py").write_text("from fastapi import FastAPI\napp = FastAPI()\n")
    (tmp_path / "backend" / "alembic").mkdir()
    assert _uat_lib.detect_alembic_strategy(tmp_path) == "external"


def test_detect_alembic_strategy_skip_when_no_alembic_dir(tmp_path):
    """No backend/alembic/ dir → skip (graceful degradation pre frontend-only project)."""
    assert _uat_lib.detect_alembic_strategy(tmp_path) == "skip"
