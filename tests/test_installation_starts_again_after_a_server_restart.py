"""DEV-11 — an installation the cockpit renders comes back on its own after the server restarts, in UAT exactly as
in PROD; only what runs once and exits stays down.

06.10.2026: after ANDROS moved, the UAT installations of NEX Manager for MAGERSTAV and of the ICC websites stayed off
and had to be started by hand, because the generator gave every UAT service ``restart: "no"``. That came from the
24.05.2026 design (UAT lives one test cycle, stale stacks would fight over ports) — neither holds: UAT installations
serve customers for acceptance for months and route through Traefik by name, holding no host port. On 15.07.2026,
after UAT stacks fell over on a Docker restart, the Director had the generator fixed at once — but the fix landed in
the old nex-studio repository and never reached the cockpit.

What runs once is told by the compose itself, not by a list of names: the source says ``restart: "no"``, another
service waits for it to finish (``service_completed_successfully`` — restarting it after exit 0 would hang every
``up``), or it is the test runner parked behind a profile.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
import yaml

from backend.services import uat_provisioner as P

COMPOSE = textwrap.dedent(
    """
    services:
      db:
        image: postgres:16-alpine
        environment:
          POSTGRES_USER: manager
          POSTGRES_DB: manager
          POSTGRES_PASSWORD: ${DB_PASSWORD}
      cache:
        image: redis:7-alpine
      migrate:
        build:
          context: .
          dockerfile: backend/Dockerfile
        command: ["alembic", "upgrade", "head"]
        depends_on:
          db:
            condition: service_healthy
      seed:
        build:
          context: .
          dockerfile: backend/Dockerfile
        command: ["python", "-m", "app.seed"]
        restart: "no"
      test:
        build:
          context: .
          dockerfile: backend/Dockerfile
        command: ["pytest"]
        environment:
          TEST_DATABASE_URL: postgresql://manager@db/manager_test
      backend:
        build:
          context: .
          dockerfile: backend/Dockerfile
        ports: ["8000:8000"]
        depends_on:
          db:
            condition: service_healthy
          migrate:
            condition: service_completed_successfully
          cache:
            condition: service_started
      frontend:
        build:
          context: ./frontend
        ports: ["3000:80"]
        depends_on: [backend]
    """
)

EXPECTED = {
    "db": "unless-stopped",
    "cache": "unless-stopped",
    "backend": "unless-stopped",
    "frontend": "unless-stopped",
    "migrate": "no",  # the backend waits for it to finish
    "seed": "no",  # the source says so
    "test": "no",  # the test runner, parked behind a profile
}


def _render(tmp_path: Path, environment: str) -> dict[str, str]:
    project = tmp_path / environment / "projects" / "nex-manager"
    project.mkdir(parents=True)
    (project / "docker-compose.yml").write_text(COMPOSE)
    extra = (
        {"customer_slug": "mager", "app": "manager", "full_project_slug": "nex-manager"}
        if environment == "prod"
        else {}
    )
    res = P.provision_uat(
        "nex-manager",
        "mager-manager",
        projects_root=tmp_path / environment / "projects",
        uat_root=tmp_path / environment / "uat",
        prod_root=tmp_path / environment / "customers",
        environment=environment,
        **extra,
    )
    data = yaml.safe_load(res.compose_path.read_text())
    return {name: svc.get("restart") for name, svc in data["services"].items()}


@pytest.mark.parametrize("environment", ["uat", "prod"])
def test_long_running_services_start_again_and_one_shots_stay_down(tmp_path, environment):
    assert _render(tmp_path, environment) == EXPECTED
