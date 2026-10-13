"""DEV-101 — an installation gets the files its project mounts, and a mount path is never the backend's inner path.

13.10.2026, Career Asistent on UAT for ICC: the frontend restarted in a loop — nginx „cannot load certificate“. Its
compose mounts ``${TLS_CERT_PATH:-./ops/tls/none}`` and ``${TLS_KEY_PATH:-./ops/tls/none}`` into nginx; the project
ships ``ops/tls/none`` as an EMPTY file, which keeps the frontend on plain HTTP behind Traefik. Two things broke it:

* the cockpit never put ``ops/tls/none`` into the installation's folder, so Docker created a directory in its place;
* the installation's ``.env`` took ``TLS_CERT_PATH=/run/tls/cert.pem`` from the backend's ``environment`` — the path
  INSIDE the backend container — while compose reads the same variable as a path ON THE HOST; Docker made that a
  directory too, and the frontend's start script took two non-empty "files" for a certificate and a key.

A relative DIRECTORY mount is the installation's own data (NEX Inbox's invoice originals) and is never touched.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import yaml

from backend.services import uat_provisioner as P

COMPOSE = textwrap.dedent(
    """
    services:
      db:
        image: postgres:16-alpine
        environment:
          POSTGRES_USER: career
          POSTGRES_DB: career
          POSTGRES_PASSWORD: ${DB_PASSWORD}
      backend:
        build:
          context: .
          dockerfile: backend/Dockerfile
        environment:
          TLS_CERT_PATH: /run/tls/cert.pem
          APP_MODE: uat
        volumes:
          - ${TLS_CERT_PATH:-./ops/tls/none}:/run/tls/cert.pem:ro
          - ./originals:/var/lib/career/originals
        depends_on:
          db:
            condition: service_healthy
      frontend:
        build:
          context: ./frontend
        volumes:
          - ${TLS_CERT_PATH:-./ops/tls/none}:/etc/nginx/tls/cert.pem:ro
          - ${TLS_KEY_PATH:-./ops/tls/none}:/etc/nginx/tls/key.pem:ro
        depends_on:
          backend:
            condition: service_healthy
    """
)
ENV_EXAMPLE = "TLS_CERT_PATH=\nTLS_KEY_PATH=\nAPP_MODE=dev\n"


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "projects" / "career-asistent"
    (project / "ops" / "tls").mkdir(parents=True)
    (project / "ops" / "tls" / "none").write_bytes(b"")
    (project / "originals").mkdir()
    (project / "originals" / "sample.pdf").write_bytes(b"%PDF dev sample")
    (project / "docker-compose.yml").write_text(COMPOSE, encoding="utf-8")
    (project / ".env.example").write_text(ENV_EXAMPLE, encoding="utf-8")
    return project


def _deploy(tmp_path: Path):
    return P.provision_uat(
        "career-asistent", "icc-career-asistent", projects_root=tmp_path / "projects", uat_root=tmp_path / "uat"
    )


def _env(path: Path) -> dict[str, str]:
    return dict(line.split("=", 1) for line in path.read_text(encoding="utf-8").splitlines() if "=" in line)


def _set_env(path: Path, key: str, value: str) -> None:
    """Set one line of the installation's ``.env`` the way a past deploy left it."""
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if not line.startswith(f"{key}=")]
    path.write_text("\n".join([*lines, f"{key}={value}"]) + "\n", encoding="utf-8")


def test_a_mount_path_is_not_filled_with_the_backends_inner_path(tmp_path):
    _project(tmp_path)

    res = _deploy(tmp_path)

    env = _env(res.env_path)
    # An empty value is not written at all, so compose falls back to ``./ops/tls/none``.
    assert env.get("TLS_CERT_PATH", "") == "" and env.get("TLS_KEY_PATH", "") == ""
    assert env["APP_MODE"] == "uat", "the backend's own settings still win over the example"


def test_the_installation_gets_the_file_its_project_mounts(tmp_path):
    _project(tmp_path)

    res = _deploy(tmp_path)

    shipped = res.uat_dir / "ops" / "tls" / "none"
    assert shipped.is_file() and shipped.read_bytes() == b""
    rendered = yaml.safe_load(res.compose_path.read_text(encoding="utf-8"))
    assert "${TLS_KEY_PATH:-./ops/tls/none}:/etc/nginx/tls/key.pem:ro" in rendered["services"]["frontend"]["volumes"]


def test_a_data_directory_stays_the_installations_own(tmp_path):
    _project(tmp_path)

    res = _deploy(tmp_path)

    assert not (res.uat_dir / "originals").exists(), "the project's sample data must never land in a customer's data"


def test_a_redeploy_heals_what_the_bug_left_behind(tmp_path):
    """The installation as Career Asistent's UAT stood on 13.10.2026: Docker's empty directory where the file belongs,
    and the backend's inner path in ``.env``. A redeploy changes the version only (ICCINT-133) — so it must undo the
    cockpit's own wrong write, and say so."""
    _project(tmp_path)
    first = _deploy(tmp_path)
    (first.uat_dir / "ops" / "tls" / "none").unlink()
    (first.uat_dir / "ops" / "tls" / "none").mkdir()
    _set_env(first.env_path, "TLS_CERT_PATH", "/run/tls/cert.pem")

    again = _deploy(tmp_path)

    assert (again.uat_dir / "ops" / "tls" / "none").is_file()
    assert _env(again.env_path).get("TLS_CERT_PATH", "") == ""
    said = " ".join(again.warnings)
    assert "TLS_CERT_PATH" in said and "ops/tls/none" in said


def test_what_the_installation_holds_itself_is_left_alone(tmp_path):
    """A file the installation already has (its own certificate) is not overwritten, a directory with content is not
    removed, and a mount path that points at a real file on this machine is the installation's choice."""
    _project(tmp_path)
    first = _deploy(tmp_path)
    own = first.uat_dir / "ops" / "tls" / "none"
    own.write_bytes(b"-----BEGIN CERTIFICATE-----\n")
    real_cert = tmp_path / "certs" / "cert.pem"
    real_cert.parent.mkdir()
    real_cert.write_text("-----BEGIN CERTIFICATE-----\n", encoding="utf-8")
    _set_env(first.env_path, "TLS_CERT_PATH", str(real_cert))

    again = _deploy(tmp_path)

    assert own.read_bytes() == b"-----BEGIN CERTIFICATE-----\n"
    assert _env(again.env_path)["TLS_CERT_PATH"] == str(real_cert)


def test_an_inner_path_that_is_a_real_file_here_is_not_undone(tmp_path):
    """Only the bug's own write is undone: when the backend's inner path is also a real file on this machine, the
    installation may mean it — the value stays."""
    project = _project(tmp_path)
    real = tmp_path / "host" / "cert.pem"
    real.parent.mkdir()
    real.write_text("-----BEGIN CERTIFICATE-----\n", encoding="utf-8")
    (project / "docker-compose.yml").write_text(
        COMPOSE.replace("/run/tls/cert.pem\n", f"{real}\n", 1), encoding="utf-8"
    )
    first = _deploy(tmp_path)
    _set_env(first.env_path, "TLS_CERT_PATH", str(real))

    again = _deploy(tmp_path)

    assert _env(again.env_path)["TLS_CERT_PATH"] == str(real)
