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


# ── an installation made before: its next deploy ─────────────────────────────────────────────────────────────────
# 19.10.2026 the live check failed: the switch-off was only in the render of a NEW installation, and a deploy of an
# existing one only raises the version (``version_bump_from_text`` — „Nič iné sa nemení“). Seven one-shot containers on
# ANDROS and two on MAGER stayed „unhealthy“. Now the deploy switches the inherited check off there too and says so.

SAID = (
    "Jednorazové kroky alembic-init, migrate už po skončení nebudú hlásiť „unhealthy“ — kokpit im vypol kontrolu "
    "zdravia zdedenú z obrazu."
)


def _deploy(tmp_path, version=None):
    kwargs = {"version": version} if version else {}
    return P.provision_uat(
        "nex-inbox", "mager", projects_root=tmp_path / "projects", uat_root=tmp_path / "uat", **kwargs
    )


def _as_made_before(compose_path) -> None:
    """The installation as a cockpit before 4.51.0 wrote it — its one-shot steps without a check of their own."""
    text = compose_path.read_text()
    header = "".join(line for line in text.splitlines(keepends=True) if line.startswith("#"))
    data = yaml.safe_load(text)
    for name in ("alembic-init", "migrate"):
        data["services"][name].pop("healthcheck")
    compose_path.write_text(header + yaml.safe_dump(data, sort_keys=False))  # the cockpit's own header stays


def test_an_installation_made_before_gets_the_check_off_at_its_next_deploy_and_is_told(tmp_path, monkeypatch):
    monkeypatch.setattr(P, "_docker_image_exists", lambda image: True)
    _make_project(tmp_path, "nex-inbox", COMPOSE)
    first = _deploy(tmp_path)
    _as_made_before(first.compose_path)

    result = _deploy(tmp_path, version="1.1.0")

    services = yaml.safe_load(result.compose_path.read_text())["services"]
    assert {n: services[n].get("healthcheck") for n in ("alembic-init", "migrate")} == {
        "alembic-init": {"disable": True},
        "migrate": {"disable": True},
    }
    source = yaml.safe_load(COMPOSE)["services"]
    assert services["seed"]["healthcheck"] == source["seed"]["healthcheck"], "a step's own check was replaced"
    assert services["backend"]["healthcheck"] == source["backend"]["healthcheck"]
    assert "healthcheck" not in services["frontend"]
    assert SAID in result.warnings

    again = _deploy(tmp_path, version="1.1.1")

    assert not [w for w in again.warnings if w.startswith("Jednorazové kroky")], "said again with nothing changed"


def test_the_adoption_preview_foresees_the_same_switch_off():
    """The preview of an adoption and the write go through one function (ICCINT-151) — it shows what will be written."""
    # A hand-written compose says ``restart: no`` bare — YAML reads it as ``False``; the cockpit writes ``'no'``.
    before = textwrap.dedent(
        """
        services:
          migrate:
            image: nex-inbox-backend:v1.0.0
            restart: no
          alembic-init:
            image: nex-inbox-backend:v1.0.0
            restart: 'no'
          backend:
            image: nex-inbox-backend:v1.0.0
            restart: unless-stopped
        """
    )

    bumped = P.version_bump_from_text(before, version="1.1.0", project_slug="nex-inbox")["services"]

    assert {name: svc.get("healthcheck") for name, svc in bumped.items()} == {
        "migrate": {"disable": True},
        "alembic-init": {"disable": True},
        "backend": None,
    }
