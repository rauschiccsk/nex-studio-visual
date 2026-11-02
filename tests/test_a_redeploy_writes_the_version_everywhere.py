"""DEV-288 — a redeploy writes the deployed version into every variable of the installation that carries it.

Only ``PROJECT_VERSION`` was rewritten. 02.11.2026 Máger Dedo measured NEX Manager of MÁGERSTAV on MAGER: after 1.4.2
was deployed its ``.env`` still held an old ``VITE_APP_VERSION``; ``docker compose config --hash backend`` gave the
running container's hash only with ``VITE_APP_VERSION=1.4.2`` — the version reached the containers from the
environment the cockpit hands ``up``, not from the installation's own files. A hand ``up`` on the machine would have
brought the old version back.
"""

from __future__ import annotations

from pathlib import Path

from backend.services import uat_provisioner as P

ENV = (
    "POSTGRES_DB=nex_manager_mager\nPOSTGRES_PASSWORD=the-password-FAKE\nAPP_VERSION=1.0.0\n"
    "VITE_APP_VERSION=1.3.0\nPROJECT_VERSION=1.4.0\nVITE_LAUNCH_LIVE=true\n"
)


def _installation(tmp_path: Path, env: str = ENV) -> Path:
    folder = tmp_path / "mager" / "nex-manager"
    folder.mkdir(parents=True)
    (folder / ".env").write_text(env, encoding="utf-8")
    return folder


def test_every_version_the_installation_carries_becomes_the_deployed_one(tmp_path):
    env = P.env_version_bump(_installation(tmp_path), version="1.4.2")

    assert {k: env[k] for k in P.APP_VERSION_ENV_KEYS} == {
        "APP_VERSION": "1.4.2",
        "VITE_APP_VERSION": "1.4.2",
        "PROJECT_VERSION": "1.4.2",
    }
    assert (env["POSTGRES_DB"], env["POSTGRES_PASSWORD"], env["VITE_LAUNCH_LIVE"]) == (
        "nex_manager_mager",
        "the-password-FAKE",
        "true",
    ), "nothing but the version changes"


def test_a_version_the_installation_does_not_have_is_not_invented(tmp_path):
    env = P.env_version_bump(_installation(tmp_path, "POSTGRES_DB=x\nPROJECT_VERSION=1.0.0\n"), version="v1.4.3")

    assert "APP_VERSION" not in env and "VITE_APP_VERSION" not in env
    assert env["PROJECT_VERSION"] == "1.4.3"


def test_a_version_variable_the_new_version_brings_gets_the_deployed_version(tmp_path):
    env = P.env_version_bump(
        _installation(tmp_path, "POSTGRES_DB=x\n"), version="1.5.0", example={"VITE_APP_VERSION": "1.0.0", "NEW": "a"}
    )

    assert (env["VITE_APP_VERSION"], env["NEW"]) == ("1.5.0", "a")


SOURCE = """
services:
  db:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: app
      POSTGRES_PASSWORD: x
      POSTGRES_DB: app
  backend:
    build:
      context: .
    environment:
      APP_VERSION: ${VITE_APP_VERSION}
  frontend:
    build:
      context: ./frontend
"""


def test_the_written_settings_of_a_redeployed_installation_carry_the_new_version(tmp_path, monkeypatch):
    monkeypatch.setattr(P, "_docker_image_exists", lambda image: True)
    project = tmp_path / "projects" / "nex-manager"
    project.mkdir(parents=True)
    (project / "docker-compose.yml").write_text(SOURCE, encoding="utf-8")
    (project / ".env.example").write_text("VITE_APP_VERSION=1.0.0\n", encoding="utf-8")

    def deploy(version):
        return P.provision_uat(
            "nex-manager",
            "mager-prod",
            projects_root=tmp_path / "projects",
            uat_root=tmp_path / "uat",
            prod_root=tmp_path / "customers",
            environment="prod",
            customer_slug="mager",
            app="manager",
            full_project_slug="nex-manager",
            version=version,
        )

    first = deploy("1.4.1")
    first.env_path.write_text(first.env_path.read_text().replace("VITE_APP_VERSION=1.4.1", "VITE_APP_VERSION=0.9.0"))

    again = deploy("1.4.2")

    written = P._parse_env_file(again.env_path)
    assert (written["VITE_APP_VERSION"], written["PROJECT_VERSION"]) == ("1.4.2", "1.4.2")
