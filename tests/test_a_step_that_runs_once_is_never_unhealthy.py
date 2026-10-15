"""DEV-123 — a step that runs once (migrate, alembic-init) never ends „unhealthy“ in a compose the cockpit writes.

14.10.2026, NEX Inbox 1.8.0 on MAGER: ``mager-inbox-alembic-init`` and ``mager-manager-migrate`` exited 0, yet
``docker ps -a --filter health=unhealthy`` listed them — they run from the backend's image, inherit its HEALTHCHECK
(curl on /api/v1/health) and never serve. A guard built on that filter would cry wolf; on ANDROS seven such containers
were „sick“. The cockpit switches the inherited check off for every service that runs once, unless the source compose
gave that service a check of its own; a service that keeps running keeps exactly what the source said.
"""

from __future__ import annotations

import textwrap

import pytest
import yaml

from backend.services import uat_provisioner as P
from tests.test_provisioner_prod import _make_project

COMPOSE = textwrap.dedent(
    """
    services:
      db:
        image: postgres:16-alpine
        environment:
          POSTGRES_USER: inbox
          POSTGRES_DB: inbox
          POSTGRES_PASSWORD: ${DB_PASSWORD}
        healthcheck:
          test: ["CMD-SHELL", "pg_isready -U inbox"]
      alembic-init:
        build:
          context: .
          dockerfile: backend/Dockerfile
        command: ["alembic", "upgrade", "head"]
        depends_on:
          db:
            condition: service_healthy
      migrate:
        build:
          context: .
          dockerfile: backend/Dockerfile
        command: ["alembic", "upgrade", "head"]
        restart: "no"
      seed:
        build:
          context: .
          dockerfile: backend/Dockerfile
        command: ["python", "-m", "seed"]
        restart: "no"
        healthcheck:
          test: ["CMD", "test", "-f", "/tmp/seeded"]
      backend:
        build:
          context: .
          dockerfile: backend/Dockerfile
        healthcheck:
          test: ["CMD", "curl", "-fsS", "http://localhost:8000/api/v1/health"]
        depends_on:
          alembic-init:
            condition: service_completed_successfully
      frontend:
        build:
          context: ./frontend
        depends_on:
          backend:
            condition: service_healthy
    """
)


def _render(tmp_path, environment: str) -> dict:
    _make_project(tmp_path, "nex-inbox", COMPOSE)
    customer = (
        {
            "environment": "prod",
            "prod_root": tmp_path / "customers",
            "customer_slug": "mager",
            "app": "inbox",
            "full_project_slug": "nex-inbox",
        }
        if environment == "prod"
        else {}
    )
    res = P.provision_uat(
        "nex-inbox", "mager", projects_root=tmp_path / "projects", uat_root=tmp_path / "uat", **customer
    )
    return yaml.safe_load(res.compose_path.read_text())["services"]


@pytest.mark.parametrize("environment", ["uat", "prod"])
def test_every_step_that_runs_once_has_the_inherited_check_switched_off(tmp_path, environment):
    services = _render(tmp_path, environment)
    once = {name for name, svc in services.items() if svc["restart"] == "no"}
    assert once == {"alembic-init", "migrate", "seed"}, "the steps that run once were not recognised"
    assert {name: services[name].get("healthcheck") for name in once - {"seed"}} == {
        "alembic-init": {"disable": True},
        "migrate": {"disable": True},
    }


@pytest.mark.parametrize("environment", ["uat", "prod"])
def test_a_check_the_source_gave_is_kept(tmp_path, environment):
    """The source compose decides when it says something — a one-shot's own check and every long-lived service's."""
    services = _render(tmp_path, environment)
    source = yaml.safe_load(COMPOSE)["services"]
    assert services["seed"]["healthcheck"] == source["seed"]["healthcheck"]
    for name in ("db", "backend"):
        assert services[name]["healthcheck"] == source[name]["healthcheck"]
    assert "healthcheck" not in services["frontend"], "a long-lived service without a check got one invented"
