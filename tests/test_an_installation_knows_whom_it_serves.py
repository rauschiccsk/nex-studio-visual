"""DEV-377 — an installation knows whom it serves and whether it is the test or the live one.

13.11.2026 the Director ran NEX Manager 1.5.4 and could not tell for whom: the window, its header and the icon on the
task bar all read „NEX Manager“ — ICC s.r.o. and MÁGERSTAV s.r.o. each have one on UAT and on PROD, and with 50
customers there would be a hundred alike windows. The cockpit knew every installation and told none of them. On the
proposal (the app shows its customer and environment; an „Inštalácie“ page in the cockpit): „Súhlasím, vyskúšam a
uvidíme.“

The cockpit now writes the identity into the backend service of every installation it renders — the customer's name
and ``uat``/``prod`` — and the app template shows it.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

from backend.services import uat_provisioner as P

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
      context: ./backend
    environment:
      LOG_LEVEL: info
  frontend:
    build:
      context: ./frontend
"""


def test_the_backend_is_told_whom_it_serves_in_a_mapping():
    compose = yaml.safe_load(SOURCE)
    told = P.with_installation_identity(compose, "backend", customer_name="  MÁGERSTAV   s.r.o. ", environment="uat")

    assert told["services"]["backend"]["environment"] == {
        "LOG_LEVEL": "info",
        "NEX_ENVIRONMENT": "uat",
        "NEX_CUSTOMER_NAME": "MÁGERSTAV s.r.o.",
    }
    assert compose["services"]["backend"]["environment"] == {"LOG_LEVEL": "info"}  # the input stays as it was
    assert "NEX_CUSTOMER_NAME" not in str(told["services"]["frontend"])


def test_a_list_keeps_its_form_and_an_old_identity_is_replaced():
    compose = {"services": {"api": {"environment": ["LOG_LEVEL=info", "NEX_CUSTOMER_NAME=Stará s.r.o."]}}}

    told = P.with_installation_identity(compose, "api", customer_name="Nová s.r.o.", environment="prod")

    assert told["services"]["api"]["environment"] == [
        "LOG_LEVEL=info",
        "NEX_ENVIRONMENT=prod",
        "NEX_CUSTOMER_NAME=Nová s.r.o.",
    ]


def test_without_a_customer_only_the_environment_is_told_and_without_a_backend_nothing():
    compose = yaml.safe_load(SOURCE)

    assert P.with_installation_identity(compose, "backend", customer_name=None, environment="uat")["services"][
        "backend"
    ]["environment"] == {"LOG_LEVEL": "info", "NEX_ENVIRONMENT": "uat"}
    assert P.with_installation_identity(compose, None, customer_name="ICC s.r.o.", environment="uat") is compose


def _git(project: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(project), *args], capture_output=True, check=True)


@pytest.fixture
def project(tmp_path, monkeypatch) -> Path:
    monkeypatch.setattr(P, "_docker_image_exists", lambda image: True)
    project = tmp_path / "projects" / "nex-manager"
    project.mkdir(parents=True)
    (project / ".env.example").write_text("SECRET_KEY=change-me\n", encoding="utf-8")
    (project / "docker-compose.yml").write_text(SOURCE, encoding="utf-8")
    _git(project.parent, "init", "-q", "-b", "main", str(project))
    _git(project, "add", "-A")
    _git(project, "-c", "user.email=t@example.com", "-c", "user.name=t", "commit", "-q", "-m", "release 1.5.4")
    _git(project, "tag", "v1.5.4")
    return project


def _deploy(tmp_path: Path, customer_name, environment="uat"):
    roots = {"uat_root": tmp_path / "uat"} if environment == "uat" else {"prod_root": tmp_path / "prod"}
    return P.provision_uat(
        "nex-manager",
        "mager" if environment == "uat" else "mager-prod",
        projects_root=tmp_path / "projects",
        customer_slug="mager",
        app="manager",
        full_project_slug="nex-manager",
        version="1.5.4",
        environment=environment,
        customer_name=customer_name,
        **roots,
    )


def _backend_env(result) -> dict:
    return yaml.safe_load(result.compose_path.read_text(encoding="utf-8"))["services"]["backend"]["environment"]


@pytest.mark.parametrize("environment", ["uat", "prod"])
def test_a_new_installation_tells_its_backend(tmp_path, project, environment):
    env = _backend_env(_deploy(tmp_path, "MÁGERSTAV s.r.o.", environment))

    assert env["NEX_CUSTOMER_NAME"] == "MÁGERSTAV s.r.o." and env["NEX_ENVIRONMENT"] == environment


def test_a_redeploy_tells_it_again_with_the_customers_new_name(tmp_path, project):
    _deploy(tmp_path, "MÁGERSTAV s.r.o.")

    env = _backend_env(_deploy(tmp_path, "MÁGERSTAV a.s."))

    assert env["NEX_CUSTOMER_NAME"] == "MÁGERSTAV a.s." and env["NEX_ENVIRONMENT"] == "uat"


async def test_the_deploy_hands_the_runner_the_customers_name(db_session, monkeypatch, tmp_path):
    """The deploy screen's „Nasadiť“ reaches the renderer with the customer's name — the seam the identity goes by."""
    from backend.services import ci_status
    from backend.services import deploy as deploy_service
    from tests import test_red_ci_after_the_gate_stops_the_deploy as red_ci

    told: dict = {}

    async def _runner(**kwargs):
        told.update(kwargs)
        return True, "OK", "https://uat-mager-manager.isnex.eu"

    async def _green(project_root, sha=None, *, cerstvy=False):
        return red_ci._ci("green", detail="CI zelené")

    project, _version = red_ci._finished_version(db_session)
    customer = red_ci.customer_service.create(
        db_session, project.id, red_ci.CustomerCreate(name="MÁGERSTAV s.r.o.", slug="mager", subdomain="mager")
    )
    monkeypatch.setattr(red_ci.orchestrator.claude_agent, "PROJECTS_ROOT", tmp_path)
    monkeypatch.setattr(red_ci.orchestrator, "_project_has_ci", lambda root: False)
    monkeypatch.setattr(ci_status, "stav_commitu", _green)

    await deploy_service.deploy(
        db_session,
        customer.id,
        version_number="1.7.0",
        environment="uat",
        actor_id=project.created_by,
        deploy_runner=_runner,
    )

    assert told["customer_name"] == "MÁGERSTAV s.r.o."
