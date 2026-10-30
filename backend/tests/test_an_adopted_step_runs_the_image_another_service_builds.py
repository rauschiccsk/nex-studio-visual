"""DEV-267 — a step of a hand-written installation that runs another service's image is built the same way.

30.10.2026 the Director adopted the hand-written NEX Manager of ICC on UAT. Its ``migrate`` runs the backend's image
(``nexmanager-backend:1.0.0``) — exactly what the project's own compose does (both build ``backend/Dockerfile``).
The version bump gave the backend and the frontend their builds by role, but ``migrate`` has no role the cockpit knows,
so it kept a bare ``nexmanager-backend:v1.4.2`` nothing would build, and the deploy stopped: „Nasadenie by zapísalo
obrazy, ktoré sa nemajú odkiaľ vziať … migrate=nexmanager-backend:v1.4.2“. The preview had said the installation could
be adopted — it never asked the question the deploy asks.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from backend.services import instance_adoption as ia
from backend.services import uat_provisioner as P

#: The hand-written NEX Manager of ICC on UAT, in its shape (no secrets).
ICC = """
name: uat-icc-manager
services:
  db:
    image: postgres:16-alpine
    volumes:
      - db_data:/var/lib/postgresql/data
  migrate:
    image: nexmanager-backend:1.0.0
    command: ["alembic", "upgrade", "head"]
    restart: "no"
    container_name: uat-icc-manager-migrate
  backend:
    image: nexmanager-backend:1.0.0
    container_name: uat-icc-manager-backend
  frontend:
    image: nexmanager-frontend:1.0.0
    container_name: uat-icc-manager-frontend
volumes:
  db_data:
"""

#: The project's own compose: migrate and backend build the same Dockerfile.
SOURCE = {
    "services": {
        "db": {"image": "postgres:16-alpine"},
        "migrate": {
            "build": {"context": ".", "dockerfile": "backend/Dockerfile"},
            "command": ["alembic", "upgrade", "head"],
        },
        "backend": {"build": {"context": ".", "dockerfile": "backend/Dockerfile"}},
        "frontend": {"build": {"context": "./frontend"}},
    }
}


def _bumped(text: str, tmp_path: Path) -> dict:
    return P.version_bump_from_text(
        text, version="1.4.2", project_slug="nex-manager", source=SOURCE, project_path=tmp_path
    )


def test_the_step_on_the_backend_s_image_is_built_as_the_backend(tmp_path):
    after = _bumped(ICC, tmp_path)
    services = after["services"]

    assert services["migrate"]["build"] == services["backend"]["build"]
    assert services["migrate"]["image"] == services["backend"]["image"] == "nexmanager-backend:v1.4.2"
    assert P.unbuildable_images(yaml.safe_load(ICC), after, image_exists=lambda _image: False) == []
    # the installation keeps what is its own
    assert after["name"] == "uat-icc-manager" and "db_data" in after["volumes"]
    assert services["db"]["image"] == "postgres:16-alpine" and "build" not in services["db"]
    assert services["migrate"]["container_name"] == "uat-icc-manager-migrate"


def test_an_image_no_service_of_the_installation_builds_still_stops_the_deploy(tmp_path):
    worker = ICC.replace(
        "volumes:\n  db_data:\n", "  worker:\n    image: nexmanager-worker:1.0.0\nvolumes:\n  db_data:\n", 1
    )
    after = _bumped(worker, tmp_path)

    assert "build" not in after["services"]["worker"]
    assert P.unbuildable_images(yaml.safe_load(worker), after, image_exists=lambda _image: False) == [
        "worker=nexmanager-worker:v1.4.2"
    ]


def _instance(tmp_path: Path, text: str) -> Path:
    inst = tmp_path / "uat" / "icc" / "nex-manager"
    inst.mkdir(parents=True)
    (inst / "docker-compose.yml").write_text(text, encoding="utf-8")
    (inst / ".env").write_text("POSTGRES_USER=icc\n", encoding="utf-8")
    project = tmp_path / "projects" / "nex-manager"
    project.mkdir(parents=True)
    (project / "docker-compose.yml").write_text(yaml.safe_dump(SOURCE), encoding="utf-8")
    return inst


def _render(tmp_path: Path) -> ia.RenderRequest:
    return ia.RenderRequest(
        project_path=tmp_path / "projects" / "nex-manager",
        slug="icc-uat",
        project="nex-manager",
        environment="uat",
        customer_slug="icc",
        app="manager",
        version="1.4.2",
    )


def test_the_preview_asks_what_the_deploy_asks(tmp_path, monkeypatch):
    monkeypatch.setattr(P, "_docker_image_exists", lambda _image: False)
    worker = ICC.replace(
        "volumes:\n  db_data:\n", "  worker:\n    image: nexmanager-worker:1.0.0\nvolumes:\n  db_data:\n", 1
    )

    refused = ia.preview(_instance(tmp_path, worker), render=_render(tmp_path))

    assert refused.can_adopt is False
    assert any("worker=nexmanager-worker:v1.4.2" in reason for reason in refused.blocking)


def test_the_preview_lets_the_icc_installation_be_adopted(tmp_path, monkeypatch):
    monkeypatch.setattr(P, "_docker_image_exists", lambda _image: False)

    shown = ia.preview(_instance(tmp_path, ICC), render=_render(tmp_path))

    assert shown.can_adopt is True and shown.blocking == []
