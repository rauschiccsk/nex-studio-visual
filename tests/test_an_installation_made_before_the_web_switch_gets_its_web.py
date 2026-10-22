"""DEV-164 — an installation made before its project became a Web project gets the web on its next deploy.

22.10.2026 Career Asistent 1.1.0 was deployed to the UAT of ICC made on 13.10. for 1.0.0, when the project had one
frontend. A redeploy only raises the version of what an installation has (ICCINT-133), so ``web`` never came: the
deploy said „Projekt má služby, ktoré inštalácia nemá: web … doplň ich do inštalácie“, the administration stayed on
the main address, the check of the web failed on a name nobody served, and the Manažér had nothing to press. Director
22.10.2026: „Súhlasím“. A routing the cockpit wrote itself is replaced by the Web one; an installation's own routing
(adopted, hand-written) stays and is said.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from backend.services import project_surfaces
from backend.services import uat_provisioner as P

#: Career Asistent's compose as it was for 1.0.0 (one frontend), shortened to what the installation is made of.
STANDARD = {
    "services": {
        "db": {"image": "postgres:16", "environment": {"POSTGRES_USER": "career", "POSTGRES_DB": "career"}},
        "backend": {"build": {"context": "./backend"}, "depends_on": ["db"]},
        "frontend": {"build": {"context": "./frontend"}, "ports": ["10251:443"], "depends_on": ["backend"]},
    }
}
WEB_SERVICE = {"build": {"context": "./web"}, "ports": ["10255:443"], "depends_on": ["backend"]}

HOST = "uat-icc-career-asistent.int.isnex.eu"
ADMIN_HOST = "uat-icc-career-asistent-admin.int.isnex.eu"


@pytest.fixture
def career(tmp_path, monkeypatch):
    projects, uat = tmp_path / "projects", tmp_path / "uat"
    project = projects / "career-asistent"
    for d in ("backend", "frontend", "web"):
        (project / d).mkdir(parents=True)
    (project / "docker-compose.yml").write_text(yaml.safe_dump(STANDARD), encoding="utf-8")
    (project / ".env.example").write_text("SESSION_SECRET=\n", encoding="utf-8")
    monkeypatch.setattr(P, "_docker_image_exists", lambda image: True)
    surfaces = {"type": project_surfaces.STANDARD}
    monkeypatch.setattr(
        project_surfaces, "surfaces_of_slug", lambda slug: project_surfaces.surfaces_of_type(surfaces["type"])
    )
    return project, projects, uat, surfaces


def _deploy(projects: Path, uat: Path, version: str) -> P.ProvisionResult:
    return P.provision_uat(
        "career-asistent",
        "icc",
        version=version,
        projects_root=projects,
        uat_root=uat,
        customer_slug="icc",
        app="career-asistent",
        full_project_slug="career-asistent",
        private=True,
    )


def _switch_to_web(project: Path, surfaces: dict) -> None:
    compose = yaml.safe_load((project / "docker-compose.yml").read_text(encoding="utf-8"))
    compose["services"]["web"] = WEB_SERVICE
    (project / "docker-compose.yml").write_text(yaml.safe_dump(compose), encoding="utf-8")
    surfaces["type"] = project_surfaces.WEB


def _installation(uat: Path) -> dict:
    return yaml.safe_load((uat / "icc" / "career-asistent" / "docker-compose.yml").read_text(encoding="utf-8"))


def _rules(svc: dict) -> list[str]:
    return sorted(label.split("=", 1)[1] for label in P._labels_as_list(svc.get("labels")) if ".rule=" in label)


def test_the_redeploy_gives_the_installation_its_web_and_moves_the_administration(career):
    project, projects, uat, surfaces = career
    _deploy(projects, uat, "1.0.0")
    secrets_before = (uat / "icc" / "career-asistent" / ".env").read_text(encoding="utf-8")
    _switch_to_web(project, surfaces)

    result = _deploy(projects, uat, "1.1.0")

    services = _installation(uat)["services"]
    assert sorted(services) == ["backend", "db", "frontend", "web"]
    assert _rules(services["web"]) == [f"Host(`{HOST}`)"]
    assert _rules(services["frontend"]) == [f"Host(`{ADMIN_HOST}`)"]
    assert all(HOST in rule and ADMIN_HOST in rule for rule in _rules(services["backend"]))
    assert services["web"]["image"] == "uat-icc-career-asistent-web:v1.1.0"
    assert services["frontend"]["image"] == "uat-icc-career-asistent-frontend:v1.1.0"
    assert (
        "Webový projekt: do inštalácie pribudlo web na adrese "
        + HOST
        + "; frontend je odteraz na adrese "
        + (ADMIN_HOST + ".")
        in result.warnings
    )
    assert not [w for w in result.warnings if w.startswith("Projekt má služby, ktoré inštalácia nemá")]
    secrets_after = (uat / "icc" / "career-asistent" / ".env").read_text(encoding="utf-8")
    assert [r for r in secrets_after.splitlines() if r.startswith("POSTGRES_PASSWORD=")] == [
        r for r in secrets_before.splitlines() if r.startswith("POSTGRES_PASSWORD=")
    ], "the database password of the installation changed"


def test_an_installation_that_has_its_web_is_left_as_it_is(career):
    project, projects, uat, surfaces = career
    _deploy(projects, uat, "1.0.0")
    _switch_to_web(project, surfaces)
    _deploy(projects, uat, "1.1.0")
    before = _installation(uat)

    result = _deploy(projects, uat, "1.1.1")

    after = _installation(uat)
    assert {n: _rules(s) for n, s in after["services"].items()} == {n: _rules(s) for n, s in before["services"].items()}
    assert not [w for w in result.warnings if w.startswith("Webový projekt")]


def test_an_installation_with_its_own_routing_keeps_it_and_is_told(career):
    project, projects, uat, surfaces = career
    _deploy(projects, uat, "1.0.0")
    compose_path = uat / "icc" / "career-asistent" / "docker-compose.yml"
    own = compose_path.read_text(encoding="utf-8").replace(HOST, "kariera.icc.sk")  # its own address, by hand
    compose_path.write_text(own, encoding="utf-8")
    _switch_to_web(project, surfaces)

    result = _deploy(projects, uat, "1.1.0")

    services = _installation(uat)["services"]
    assert "web" not in services
    assert _rules(services["frontend"]) == ["Host(`kariera.icc.sk`)"]
    assert (
        "Webový projekt: inštalácia má vlastné smerovanie, preto do nej web nepribudol — doplň ho do inštalácie ručne."
        in result.warnings
    )
