"""DEV-164 — a Web project's installation: every frontend its own address, /api from both, the checks ask both.

An installation had one address: Traefik routed one frontend and /api only from that address, every ``@public-url``
got the same address (links meant for the public would lead into the administration), the check after a deploy asked
one frontend and one address and the deploy matrix showed one link. The web was built and started, but nobody could
open it. Director 20.10.2026: „Ideme kokpit naučiť stavať projekt s dvoma frontendmi.“ The web takes the main
address, the administration ``…-admin``; a Štandardný installation is exactly as before.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.services import deploy, env_contract, orchestrator, project_surfaces, uat_provisioner
from backend.services.uat_provisioner import build_uat_compose

WEB = project_surfaces.surfaces_of_type(project_surfaces.WEB)
SOURCE = {
    "services": {
        "backend": {"build": ".", "ports": ["14930:8000"]},
        "frontend": {"build": "./frontend", "ports": ["14931:80"]},
        "web": {"build": "./web", "ports": ["14935:80"]},
        "db": {"image": "postgres:16"},
    }
}
ROLES = {"backend": "backend", "frontend": "frontend", "db": "db"}


def _compose(surfaces=(), *, environment="uat", private=False) -> dict:
    return build_uat_compose(
        slug="icc-uat",
        project="career-asistent",
        project_path=Path("/opt/projects/career-asistent"),
        source=SOURCE,
        roles=ROLES,
        db_user="app",
        db_name="app",
        environment=environment,
        customer_slug="icc",
        app="career-asistent",
        private=private,
        surfaces=surfaces,
    )


def _rule(compose: dict, service: str) -> str:
    return next(label for label in compose["services"][service]["labels"] if ".rule=" in label)


# --- the routing ------------------------------------------------------------------------------------------------------


def test_the_web_takes_the_main_address_and_the_administration_its_own():
    compose = _compose(WEB)

    assert (
        _rule(compose, "web")
        == "traefik.http.routers.uat-icc-career-asistent.rule=Host(`uat-icc-career-asistent.isnex.eu`)"
    )
    assert _rule(compose, "frontend") == (
        "traefik.http.routers.uat-icc-career-asistent-admin.rule=Host(`uat-icc-career-asistent-admin.isnex.eu`)"
    )
    assert _rule(compose, "backend") == (
        "traefik.http.routers.uat-icc-career-asistent-api.rule=(Host(`uat-icc-career-asistent-admin.isnex.eu`) || "
        "Host(`uat-icc-career-asistent.isnex.eu`)) && PathPrefix(`/api`)"
    )


def test_a_private_web_projects_prod_addresses_are_in_the_private_zone():
    compose = _compose(WEB, environment="prod", private=True)

    assert "Host(`icc-career-asistent-admin.int.isnex.eu`)" in _rule(compose, "frontend")
    assert "Host(`icc-career-asistent.int.isnex.eu`)" in _rule(compose, "web")


def test_a_standard_installation_is_routed_as_before():
    compose = _compose()

    assert _rule(compose, "frontend") == (
        "traefik.http.routers.uat-icc-career-asistent.rule=Host(`uat-icc-career-asistent.isnex.eu`)"
    )
    assert _rule(compose, "backend") == (
        "traefik.http.routers.uat-icc-career-asistent-api.rule=Host(`uat-icc-career-asistent.isnex.eu`) && "
        "PathPrefix(`/api`)"
    )
    assert "labels" not in compose["services"]["web"], "a Štandardný installation routes no second frontend"


def test_the_installation_reads_the_frontends_of_its_own_project(monkeypatch, tmp_path):
    project = tmp_path / "career-asistent"
    project.mkdir()
    (project / "docker-compose.yml").write_text(
        "services:\n  backend: {build: .}\n  frontend: {build: ./frontend}\n  web: {build: ./web}\n"
        "  db: {image: postgres:16}\n"
    )
    asked: list[str] = []
    monkeypatch.setattr(project_surfaces, "surfaces_of_slug", lambda slug: asked.append(slug) or WEB)

    compose = uat_provisioner.build_compose_for_instance(
        project_path=project,
        slug="icc-uat",
        project="career-asistent",
        environment="uat",
        customer_slug="icc",
        app="career-asistent",
    )

    assert asked == ["career-asistent"]
    assert "Host(`uat-icc-career-asistent-admin.isnex.eu`)" in _rule(compose, "frontend")


# --- the addresses the app is told ----------------------------------------------------------------------------------


def test_the_administration_address_has_a_marker_of_its_own():
    contract = env_contract.parse("# @public-url\nWEB_URL=\n# @public-url-admin\nADMIN_URL=\n")

    assert (contract.public_url_keys, contract.public_url_admin_keys, contract.problems) == (
        {"WEB_URL"},
        {"ADMIN_URL"},
        (),
    )


@pytest.mark.parametrize(
    ("surfaces", "told"), [(WEB, {"ADMIN_URL": "https://uat-icc-career-asistent-admin.isnex.eu"}), ((), {})]
)
def test_only_a_web_project_tells_the_app_the_administrations_address(monkeypatch, surfaces, told):
    monkeypatch.setattr(
        project_surfaces, "surfaces_of_slug", lambda slug: surfaces or project_surfaces.surfaces_of_type("standard")
    )
    contract = env_contract.parse("# @public-url\nWEB_URL=\n# @public-url-admin\nADMIN_URL=\n")

    found = uat_provisioner._further_public_urls(
        contract, "uat", "icc-uat", "icc", "career-asistent", False, "career-asistent"
    )

    assert found == told


def test_public_links_go_to_the_web_and_staff_links_to_the_administration():
    out = uat_provisioner.generate_uat_env(
        slug="uat-icc-career-asistent",
        project="career-asistent",
        version="0.1.0",
        services={},
        be_service=None,
        db_service=None,
        source_env_example={"WEB_URL": "", "ADMIN_URL": ""},
        db_user="u",
        db_name="d",
        shared_db_password="p",
        public_url_keys=frozenset({"WEB_URL"}),
        public_url="https://uat-icc-career-asistent.isnex.eu",
        public_urls={"ADMIN_URL": "https://uat-icc-career-asistent-admin.isnex.eu"},
    )
    values = dict(line.split("=", 1) for line in out.splitlines() if "=" in line and not line.startswith("#"))

    assert (values["WEB_URL"], values["ADMIN_URL"]) == (
        "https://uat-icc-career-asistent.isnex.eu",
        "https://uat-icc-career-asistent-admin.isnex.eu",
    )


# --- the check after a deploy asks every frontend and every address --------------------------------------------------


@pytest.mark.parametrize(
    ("surfaces", "containers", "addresses"),
    [
        (
            WEB,
            ["uat-icc-career-asistent-frontend", "uat-icc-career-asistent-web"],
            ["uat-icc-career-asistent-admin.isnex.eu", "uat-icc-career-asistent.isnex.eu"],
        ),
        ((), ["uat-icc-career-asistent-frontend"], ["uat-icc-career-asistent.isnex.eu"]),
    ],
)
async def test_the_deploy_check_asks_every_frontend_and_every_address(
    monkeypatch, tmp_path, surfaces, containers, addresses
):
    import yaml

    (tmp_path / "uat" / "icc" / "career-asistent").mkdir(parents=True)
    (tmp_path / "uat" / "icc" / "career-asistent" / "docker-compose.yml").write_text("services: {}\n")
    (tmp_path / "projects" / "career-asistent").mkdir(parents=True)
    (tmp_path / "projects" / "career-asistent" / "docker-compose.yml").write_text(yaml.safe_dump(SOURCE))
    monkeypatch.setattr(orchestrator, "UAT_ROOT", tmp_path / "uat")
    monkeypatch.setattr(orchestrator.claude_agent, "PROJECTS_ROOT", tmp_path / "projects")
    monkeypatch.setattr(
        project_surfaces, "surfaces_of_slug", lambda slug: surfaces or project_surfaces.surfaces_of_type("standard")
    )
    asked_containers: list[str] = []
    asked_addresses: list[str] = []

    async def _ready(base, be_role, ports, *, host, path, env=None):
        if host != "localhost":
            asked_containers.append(host)
        return True, "ok"

    async def _route(base, be_role, public_host):
        asked_addresses.append(public_host)
        return "up", "200"

    monkeypatch.setattr(orchestrator, "_await_http_ready", _ready)
    monkeypatch.setattr(orchestrator, "_verify_public_route", _route)

    ok, detail = await orchestrator._verify_uat_serves(
        "career-asistent",
        "icc-uat",
        environment="uat",
        customer_slug="icc",
        app="career-asistent",
        full_project_slug="career-asistent",
    )

    assert (ok, detail) == (True, "OK")
    assert asked_containers == containers
    assert asked_addresses == addresses


# --- the links the Manažér opens -------------------------------------------------------------------------------------


def test_the_deploy_matrix_links_every_frontend_of_a_web_project(db_session):
    from tests.test_web_project_surfaces import _project, _user

    owner = _user(db_session)
    web = _project(db_session, owner, project_type="web", web_port=14905)
    standard = _project(db_session, owner, base=14910)

    assert deploy._frontend_urls("https://uat-icc-cas.isnex.eu", web) == [
        {"label": "Administrácia", "url": "https://uat-icc-cas-admin.isnex.eu"},
        {"label": "Web", "url": "https://uat-icc-cas.isnex.eu"},
    ]
    assert deploy._frontend_urls("https://uat-icc-cas.isnex.eu", standard) == []


def test_web_is_open_to_the_manazer():
    assert project_surfaces.WEB_PROJECTS_AVAILABLE is True
