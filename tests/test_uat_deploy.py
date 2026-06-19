"""Tests for scripts/uat-deploy.py (v0.9.0 Phase 2 thin CLI wrapper).

The provisioning logic (detection, render, .env synthesis) moved to
:mod:`backend.services.uat_provisioner` (tested in ``tests/test_uat_provisioner.py``). These tests
cover the CLI's own surface + orchestration: arg parsing, slug validation, UAT-root guard, dry-run
(no docker), port allocation, delegation to the provisioner, and port-release on a post-allocation
failure.

All side effects (subprocess, network) are mocked — no real docker call.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
NEX_STUDIO_ROOT = SCRIPTS_DIR.parent
for _p in (str(SCRIPTS_DIR), str(NEX_STUDIO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

SCRIPT = SCRIPTS_DIR / "uat-deploy.py"

# A minimal but realistic 3-service source compose (db + backend + frontend).
THREE_SERVICE_COMPOSE = """
services:
  db:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: appuser
      POSTGRES_DB: appdb
      POSTGRES_PASSWORD: ${DB_PASSWORD}
  backend:
    build:
      context: .
      dockerfile: backend/Dockerfile
    ports: ["8000:8000"]
    env_file: [.env]
    environment:
      SECRET_KEY: change-me
      CLAUDE_CODE_OAUTH_TOKEN: ${CLAUDE_CODE_OAUTH_TOKEN}
    depends_on:
      db:
        condition: service_healthy
  frontend:
    build:
      context: ./frontend
    ports: ["3000:80"]
    depends_on:
      backend:
        condition: service_healthy
"""


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _import_deploy_module(monkeypatch):
    spec = importlib.util.spec_from_file_location("uat_deploy", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    monkeypatch.setattr("sys.path", [str(SCRIPT.parent), str(NEX_STUDIO_ROOT), *sys.path])
    spec.loader.exec_module(mod)
    return mod


def _setup(monkeypatch, tmp_path, *, source_compose: str, project_name: str = "dev"):
    """Set up a fake UAT root + project (with source compose); return (mod, uat_root, project_path)."""
    mod = _import_deploy_module(monkeypatch)
    uat_root = tmp_path / "uat"
    uat_root.mkdir()
    project_path = tmp_path / "projects" / project_name
    project_path.mkdir(parents=True)
    (project_path / "docker-compose.yml").write_text(source_compose)
    monkeypatch.setattr(mod, "UAT_ROOT", uat_root)
    monkeypatch.setattr(mod, "PROJECTS_ROOT", tmp_path / "projects")
    monkeypatch.setattr(mod._uat_lib, "PORT_STATE_FILE", tmp_path / ".uat-ports.json")
    return mod, uat_root, project_path


# ---------- CLI surface ----------


def test_help_shows_usage():
    r = _run(["--help"])
    assert r.returncode == 0
    assert "slug" in r.stdout.lower()


def test_missing_slug_argument():
    r = _run([])
    assert r.returncode != 0
    assert "slug" in (r.stderr + r.stdout).lower()


def test_invalid_slug_fails_fast():
    r = _run(["BAD/slug"])
    assert r.returncode == 1
    assert "slug" in r.stderr.lower()


def test_empty_slug_after_strip():
    r = _run(["--dry-run", ""])
    assert r.returncode != 0


# ---------- UAT root guard ----------


def test_deploy_fails_when_uat_root_missing(monkeypatch, tmp_path):
    """When /opt/uat/ is unavailable, deploy must exit with clear error."""
    mod = _import_deploy_module(monkeypatch)
    monkeypatch.setattr(mod, "UAT_ROOT", tmp_path / "no-such-uat-root")
    assert mod.check_uat_root_exists() is False


# ---------- Project resolution ----------


def test_default_project_equals_slug(monkeypatch):
    mod = _import_deploy_module(monkeypatch)
    assert mod.resolve_project(slug="dev", project=None) == "dev"


def test_explicit_project_overrides_slug(monkeypatch):
    mod = _import_deploy_module(monkeypatch)
    assert mod.resolve_project(slug="mager", project="nex-inbox") == "nex-inbox"


# ---------- Dry-run flow ----------


def test_dry_run_does_not_invoke_docker(monkeypatch, tmp_path):
    """--dry-run must produce a plan, NOT call docker or write files."""
    mod, uat_root, _ = _setup(monkeypatch, tmp_path, source_compose=THREE_SERVICE_COMPOSE)
    with (
        patch.object(mod._uat_lib, "docker_compose") as mock_dc,
        patch.object(mod._uat_lib, "wait_healthy") as mock_wh,
    ):
        rc = mod.deploy("dev", project=None, dry_run=True)
    assert rc == 0
    assert not mock_dc.called
    assert not mock_wh.called
    assert not (uat_root / "dev" / "docker-compose.yml").exists()  # no write on dry-run


def test_dry_run_prints_summary(monkeypatch, tmp_path, capsys):
    """--dry-run prints summary including expected URL."""
    mod, _, _ = _setup(monkeypatch, tmp_path, source_compose=THREE_SERVICE_COMPOSE)
    rc = mod.deploy("dev", project=None, dry_run=True)
    assert rc == 0
    out = capsys.readouterr().out
    assert "uat-dev.isnex.eu" in out


def test_dry_run_fails_when_no_source_compose(monkeypatch, tmp_path):
    """A project with no docker-compose.yml cannot be provisioned — fail fast, release the port."""
    mod = _import_deploy_module(monkeypatch)
    uat_root = tmp_path / "uat"
    uat_root.mkdir()
    (tmp_path / "projects" / "dev").mkdir(parents=True)  # no compose
    monkeypatch.setattr(mod, "UAT_ROOT", uat_root)
    monkeypatch.setattr(mod, "PROJECTS_ROOT", tmp_path / "projects")
    monkeypatch.setattr(mod._uat_lib, "PORT_STATE_FILE", tmp_path / ".uat-ports.json")
    rc = mod.deploy("dev", project=None, dry_run=True)
    assert rc == 1
    assert mod._uat_lib.get_allocated_port("dev") is None  # port released


# ---------- Port allocation ----------


def test_deploy_allocates_port(monkeypatch, tmp_path):
    mod = _import_deploy_module(monkeypatch)
    monkeypatch.setattr(mod._uat_lib, "PORT_STATE_FILE", tmp_path / ".uat-ports.json")
    assert mod._uat_lib.allocate_port("dev") == 19500


# ---------- Delegation to the provisioner ----------


def test_deploy_delegates_render_to_provisioner(monkeypatch, tmp_path):
    """A real (non-dry) deploy provisions via uat_provisioner → compose + .env written; build/up mocked."""
    mod, uat_root, _ = _setup(monkeypatch, tmp_path, source_compose=THREE_SERVICE_COMPOSE)
    with (
        patch.object(mod._uat_lib, "docker_compose") as mock_dc,
        patch.object(mod._uat_lib, "docker_exec"),
        patch.object(mod._uat_lib, "wait_healthy", return_value=True),
    ):
        rc = mod.deploy("dev", project=None, dry_run=False, version="v0.2.0")
    assert rc == 0
    compose = uat_root / "dev" / "docker-compose.yml"
    assert compose.exists()
    content = compose.read_text()
    assert "uat-dev-backend" in content
    assert "traefik.http.routers.uat-dev.rule=Host(`uat-dev.isnex.eu`)" in content
    assert mock_dc.called  # build + up invoked


def test_deploy_writes_synthetic_env_file(monkeypatch, tmp_path):
    """The generated .env carries detected DB creds + synthetic secrets (chmod 600)."""
    mod, uat_root, _ = _setup(monkeypatch, tmp_path, source_compose=THREE_SERVICE_COMPOSE)
    with (
        patch.object(mod._uat_lib, "docker_compose"),
        patch.object(mod._uat_lib, "docker_exec"),
        patch.object(mod._uat_lib, "wait_healthy", return_value=True),
    ):
        rc = mod.deploy("dev", project=None, dry_run=False, version="v0.2.0")
    assert rc == 0
    env_file = uat_root / "dev" / ".env"
    assert env_file.exists()
    content = env_file.read_text()
    assert "POSTGRES_USER=appuser" in content
    assert "POSTGRES_DB=appdb" in content
    assert "SECRET_KEY=change-me" not in content  # synthetic, not the source value
    assert "CLAUDE_CODE_OAUTH_TOKEN=__UAT_SYNTHETIC__" in content  # ${VAR} placeholder
    assert oct(env_file.stat().st_mode)[-3:] == "600"


def test_deploy_releases_port_on_post_allocation_failure(monkeypatch, tmp_path):
    """If provisioning fails AFTER port allocation, release_port must run (no port leak)."""
    mod, _, _ = _setup(monkeypatch, tmp_path, source_compose=THREE_SERVICE_COMPOSE)

    def boom(*a, **kw):
        raise RuntimeError("simulated provisioning failure")

    monkeypatch.setattr(mod.uat_provisioner, "provision_uat", boom)
    rc = mod.deploy("dev", project=None, dry_run=False, version="v0.0.0")
    assert rc == 1
    assert mod._uat_lib.get_allocated_port("dev") is None
