"""DEV-420 — the installation is rendered from the export of the deployed version's commit, and its images are built
from that commit, while its compose keeps pointing at the project's folder.

17.11.2026: a deploy read the project's working folder — its compose, ``.env.example``, mounted files — and built
``HEAD``; NEX Manager 1.6.0 could not be deployed while 1.7.0 was being built. Director: „Súhlasím“.
"""

from __future__ import annotations

import subprocess
import textwrap
from pathlib import Path

import yaml

from backend.services import deploy_source, uat_provisioner, verified_build

COMPOSE = textwrap.dedent(
    """
    services:
      db:
        image: postgres:16-alpine
        environment:
          POSTGRES_USER: app
          POSTGRES_DB: app
          POSTGRES_PASSWORD: ${DB_PASSWORD}
      backend:
        build:
          context: ./backend
          additional_contexts:
            docs: ./docs
        volumes:
          - ${TLS_CERT_PATH:-./ops/tls/none}:/run/tls/cert.pem:ro
        depends_on:
          db:
            condition: service_healthy
    """
)
WORKER = "  worker:\n    build:\n      context: ./backend\n"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout.strip()


def _repo(tmp_path: Path) -> tuple[Path, str]:
    """The project: the version 1.6.0 committed, then the 1.7.0 in progress in the working folder (a worker service,
    another mounted file, another app)."""
    repo = tmp_path / "projects" / "nex-manager"
    (repo / "backend").mkdir(parents=True)
    (repo / "docs").mkdir()
    (repo / "ops" / "tls").mkdir(parents=True)
    (repo / "backend" / "Dockerfile").write_text("FROM python:3.12-slim\n", encoding="utf-8")
    (repo / "backend" / "app.py").write_text("v = '1.6.0'\n", encoding="utf-8")
    (repo / "docs" / "AKO_ZACAT.md").write_text("# 1.6.0\n", encoding="utf-8")
    (repo / "ops" / "tls" / "none").write_text("1.6.0\n", encoding="utf-8")
    (repo / "docker-compose.yml").write_text(COMPOSE, encoding="utf-8")
    (repo / ".env.example").write_text("TLS_CERT_PATH=\n", encoding="utf-8")
    _git(tmp_path, "init", "-q", "-b", "main", str(repo))
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "1.6.0")
    commit = _git(repo, "rev-parse", "HEAD")
    (repo / "backend" / "app.py").write_text("v = '1.7.0 in progress'\n", encoding="utf-8")
    (repo / "ops" / "tls" / "none").write_text("1.7.0\n", encoding="utf-8")
    (repo / "docker-compose.yml").write_text(COMPOSE + WORKER, encoding="utf-8")
    _git(repo, "commit", "-qam", "1.7.0 work")
    return repo, commit


def test_the_installation_is_rendered_from_the_versions_commit_and_builds_point_at_the_project(tmp_path) -> None:
    repo, commit = _repo(tmp_path)
    source = deploy_source.export(repo, commit)
    try:
        result = uat_provisioner.provision_uat(
            "nex-manager",
            "icc-nex-manager",
            projects_root=tmp_path / "projects",
            uat_root=tmp_path / "uat",
            source_path=source.path,
        )
        rendered = yaml.safe_load(result.compose_path.read_text(encoding="utf-8"))
    finally:
        deploy_source.remove(source)

    services = rendered["services"]
    assert not any(name.endswith("worker") for name in services), "the service of 1.7.0 in progress was installed"
    backend = next(svc for name, svc in services.items() if name.endswith("backend"))
    assert backend["build"]["context"] == str(repo / "backend")
    assert backend["build"]["additional_contexts"] == {"docs": str(repo / "docs")}
    assert (result.compose_path.parent / "ops" / "tls" / "none").read_text(encoding="utf-8") == "1.6.0\n"
    assert not source.path.exists(), "the export outlived the deploy"


def test_the_images_are_built_from_the_versions_commit(tmp_path) -> None:
    repo, commit = _repo(tmp_path)
    compose = tmp_path / "installation.yml"
    compose.write_text(
        yaml.safe_dump({"services": {"backend": {"image": "nm-backend", "build": {"context": str(repo / "backend")}}}}),
        encoding="utf-8",
    )

    named = verified_build.prepare(compose, commit)
    head = verified_build.prepare(compose)
    try:
        assert named.build is not None and named.build.commit == commit
        assert (named.build.root / "src" / "backend" / "app.py").read_text(encoding="utf-8") == "v = '1.6.0'\n"
        assert head.build is not None and head.build.commit == _git(repo, "rev-parse", "HEAD")
    finally:
        verified_build.cleanup(named.build)
        verified_build.cleanup(head.build)


def test_builds_point_from_the_export_back_at_the_project() -> None:
    export, project = Path("/tmp/nsv-deploy-source-x/nm"), Path("/opt/projects/nm")
    compose = {
        "services": {
            "backend": {"build": {"context": str(export / "backend"), "additional_contexts": [f"docs={export}/docs"]}},
            "frontend": {"build": str(export / "frontend")},
            "web": {"build": {"context": "https://github.com/x/y.git"}},
            "db": {"image": "postgres:16-alpine"},
        }
    }

    moved = uat_provisioner.builds_from(compose, export, project)["services"]

    assert moved["backend"]["build"] == {
        "context": str(project / "backend"),
        "additional_contexts": [f"docs={project}/docs"],
    }
    assert moved["frontend"]["build"] == str(project / "frontend")
    assert moved["web"]["build"] == {"context": "https://github.com/x/y.git"}
    assert moved["db"] == {"image": "postgres:16-alpine"}
