"""DEV-275 — a new installation on another machine gets that machine's name and routing.

31.10.2026 the Director moved ICC's production to MAGER („Áno, ICC má bežať na MAGERi“). The cockpit could write a new
installation only the way ANDROS routes one: ``icc-manager.isnex.eu`` (a name in ANDROS's zone), the network
``nex-proxy-net`` and the plain entrypoint ``web``. On MAGER that name leads to ANDROS, nothing of MAGER's Traefik
listens on ``nex-proxy-net`` and ``web`` is bound to 127.0.0.1 only. Máger Dedo described MAGER's way the same
day: ``<customer>-<app>.icc.sk`` over Tailscale, the network ``nex-ts-net``, the entrypoint ``websecure``, certificates
of the resolver ``cf``, routers ``<base>-sec`` and ``<base>-sec-api`` naming their services ``<base>`` and
``<base>-api``, no ports on the machine — as MÁGERSTAV's NEX Manager runs there.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from backend.services import deploy as deploy_service
from backend.services import instance_adoption
from backend.services import uat_provisioner as P

SOURCE = """
services:
  db:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: demo
      POSTGRES_PASSWORD: x
      POSTGRES_DB: demo
  backend:
    build:
      context: .
    env_file: [.env]
  frontend:
    build:
      context: ./frontend
"""
EXAMPLE = "# @public-url\nPUBLIC_URL=\n"

#: MÁGERSTAV's NEX Manager on MAGER (Máger Dedo, 31.10.2026, from the running containers) — the names made ICC's.
MAGER_FRONTEND = [
    "traefik.enable=true",
    "traefik.docker.network=nex-ts-net",
    "traefik.http.routers.icc-manager-sec.entrypoints=websecure",
    "traefik.http.routers.icc-manager-sec.rule=Host(`icc-manager.icc.sk`)",
    "traefik.http.routers.icc-manager-sec.priority=10",
    "traefik.http.routers.icc-manager-sec.service=icc-manager",
    "traefik.http.routers.icc-manager-sec.tls.certresolver=cf",
    "traefik.http.services.icc-manager.loadbalancer.server.port=80",
]
MAGER_BACKEND = [
    "traefik.enable=true",
    "traefik.docker.network=nex-ts-net",
    "traefik.http.routers.icc-manager-sec-api.entrypoints=websecure",
    "traefik.http.routers.icc-manager-sec-api.rule=Host(`icc-manager.icc.sk`) && PathPrefix(`/api`)",
    "traefik.http.routers.icc-manager-sec-api.priority=20",
    "traefik.http.routers.icc-manager-sec-api.service=icc-manager-api",
    "traefik.http.routers.icc-manager-sec-api.tls.certresolver=cf",
    "traefik.http.services.icc-manager-api.loadbalancer.server.port=8000",
]
#: What an installation on this machine has carried since the Phase-1 ledger migration — unchanged.
HERE_FRONTEND = [
    "traefik.enable=true",
    "traefik.docker.network=nex-proxy-net",
    "traefik.http.routers.icc-manager.rule=Host(`icc-manager.isnex.eu`)",
    "traefik.http.routers.icc-manager.entrypoints=web",
    "traefik.http.routers.icc-manager.priority=10",
    "traefik.http.services.icc-manager.loadbalancer.server.port=80",
]
#: The second router MÁGERSTAV carries on MAGER — its own, through an entrypoint of its own (ICCINT-151).
MAGERSTAV_TAILSCALE_ROUTER = [
    "traefik.http.routers.mager-manager-ts.rule=Host(`mager.tail5c98e2.ts.net`)",
    "traefik.http.routers.mager-manager-ts.entrypoints=mager-manager",
    "traefik.http.routers.mager-manager-ts.tls.certresolver=tailscale",
]


class _Target:
    """The other machine: what is on it, and what the cockpit wrote there."""

    def __init__(self, files: dict[str, str] | None = None):
        self.files = dict(files or {})
        self.written: dict[str, str] = {}

    def read_text(self, instance_dir, filename, *, deploy_host, timeout=120):
        assert deploy_host == "mager"
        return self.files.get(filename), None

    def write_files(self, instance_dir, files, *, deploy_host, timeout=180):
        assert deploy_host == "mager"
        self.written.update({name: text for name, (text, _mode) in files.items()})
        return None


def _provision(tmp_path: Path, *, deploy_host=None, private=False, project="nex-manager", app="manager", **kw):
    path = tmp_path / "projects" / project
    if not path.is_dir():
        path.mkdir(parents=True)
        (path / "docker-compose.yml").write_text(SOURCE, encoding="utf-8")
        (path / ".env.example").write_text(EXAMPLE, encoding="utf-8")
    return P.provision_uat(
        project,
        "icc-prod",
        projects_root=tmp_path / "projects",
        uat_root=tmp_path / "uat",
        prod_root=tmp_path / "customers",
        environment="prod",
        customer_slug="icc",
        app=app,
        full_project_slug=project,
        deploy_host=deploy_host,
        private=private,
        **kw,
    )


def _labels(compose: dict, service: str) -> list[str]:
    return sorted(str(label) for label in compose["services"][service].get("labels") or [])


def _env(text: str) -> dict[str, str]:
    return dict(line.split("=", 1) for line in text.splitlines() if "=" in line and not line.startswith("#"))


def test_a_new_installation_on_mager_is_named_and_routed_like_magerstav(tmp_path, monkeypatch):
    target = _Target()
    monkeypatch.setattr(P, "remote_instance", target)

    _provision(tmp_path, deploy_host="mager")

    compose = yaml.safe_load(target.written["docker-compose.yml"])
    assert _labels(compose, "frontend") == sorted(MAGER_FRONTEND)
    assert _labels(compose, "backend") == sorted(MAGER_BACKEND)
    assert compose["networks"]["nex-ts-net"] == {"external": True}
    assert "nex-proxy-net" not in compose["networks"]
    assert {s: "nex-ts-net" in svc.get("networks", []) for s, svc in compose["services"].items()} == {
        "db": False,
        "backend": True,
        "frontend": True,
    }
    assert [s for s, svc in compose["services"].items() if svc.get("ports")] == [], "MAGER routes, no port is taken"
    assert _env(target.written[".env"])["PUBLIC_URL"] == "https://icc-manager.icc.sk"
    header = target.written["docker-compose.yml"].split("\nname:", 1)[0]
    assert "join the\n# external `nex-ts-net`" in header and "carries its OWN" not in header


def test_the_same_installation_on_this_machine_is_routed_as_before(tmp_path):
    result = _provision(tmp_path)

    compose = yaml.safe_load(result.compose_path.read_text(encoding="utf-8"))
    assert _labels(compose, "frontend") == sorted(HERE_FRONTEND)
    assert compose["networks"]["nex-proxy-net"] == {"external": True} and "nex-ts-net" not in compose["networks"]
    assert _env(result.env_path.read_text(encoding="utf-8"))["PUBLIC_URL"] == "https://icc-manager.isnex.eu"
    header = result.compose_path.read_text(encoding="utf-8").split("\nname:", 1)[0]
    assert "join the\n# external `nex-proxy-net`" in header


def test_a_private_project_on_mager_is_named_in_mager_s_zone(tmp_path, monkeypatch):
    target = _Target()
    monkeypatch.setattr(P, "remote_instance", target)

    _provision(tmp_path, deploy_host="mager", private=True, project="career-asistent", app="career-asistent")

    assert _env(target.written[".env"])["PUBLIC_URL"] == "https://icc-career-asistent.icc.sk"
    assert P._instance_naming("prod", "x", "icc", "career-asistent", private=True)[1] == (
        "icc-career-asistent.int.isnex.eu"
    ), "here a private project keeps the private zone"


def test_a_redeploy_on_mager_takes_the_installation_for_the_cockpits_and_writes_its_address(tmp_path, monkeypatch):
    first = _Target()
    monkeypatch.setattr(P, "remote_instance", first)
    _provision(tmp_path, deploy_host="mager")
    on_mager = dict(first.written)
    on_mager[".env"] = on_mager[".env"].replace("PUBLIC_URL=https://icc-manager.icc.sk", "PUBLIC_URL=")
    target = _Target(on_mager)
    monkeypatch.setattr(P, "remote_instance", target)
    monkeypatch.setattr(P, "_docker_image_exists", lambda image: True)

    result = _provision(tmp_path, deploy_host="mager", version="1.4.3")

    assert _env(target.written[".env"])["PUBLIC_URL"] == "https://icc-manager.icc.sk"
    assert not [w for w in result.warnings if "vlastné smerovanie" in w]
    assert _labels(yaml.safe_load(target.written["docker-compose.yml"]), "frontend") == sorted(MAGER_FRONTEND)


def test_the_header_tells_the_cockpits_routing_on_mager_from_magerstav_s_own():
    routing = P.routing_for("mager")
    cockpits = {"name": "icc-manager", "services": {"frontend": {"labels": MAGER_FRONTEND}}}
    magerstav = {
        "name": "mager-manager",
        "services": {"frontend": {"labels": MAGER_FRONTEND + MAGERSTAV_TAILSCALE_ROUTER}},
    }
    here_on_mager = {"name": "icc-manager", "services": {"frontend": {"labels": HERE_FRONTEND}}}

    assert P._predpis_nesie_vlastne_smerovanie(cockpits, routing) is False
    assert P._predpis_nesie_vlastne_smerovanie(magerstav, routing) is True
    assert P._predpis_nesie_vlastne_smerovanie(here_on_mager, routing) is True, "ANDROS's routing is not MAGER's"
    assert P._predpis_nesie_vlastne_smerovanie(here_on_mager, P.routing_for()) is False
    for what, changed in (
        ("nex-proxy-net on MAGER is not the cockpit's", ("nex-ts-net", "nex-proxy-net")),
        ("the plain entrypoint on MAGER is not the cockpit's", ("entrypoints=websecure", "entrypoints=web")),
        ("a certificate of Tailscale is not the cockpit's", ("certresolver=cf", "certresolver=tailscale")),
        ("a middleware is not the cockpit's", ("priority=10", "middlewares=auth")),
    ):
        labels = [label.replace(*changed) for label in MAGER_FRONTEND]
        compose = {"name": "icc-manager", "services": {"frontend": {"labels": labels}}}
        assert P._predpis_nesie_vlastne_smerovanie(compose, routing), what


def test_the_adoption_preview_of_an_empty_place_on_mager_shows_mager_s_routing(tmp_path):
    path = tmp_path / "nex-manager"
    path.mkdir()
    (path / "docker-compose.yml").write_text(SOURCE, encoding="utf-8")
    render = instance_adoption.RenderRequest(
        project_path=path,
        slug="icc-prod",
        project="nex-manager",
        environment="prod",
        customer_slug="icc",
        app="manager",
    )

    text, error = instance_adoption._vykresli(render, None, "mager")

    assert error is None
    assert _labels(yaml.safe_load(text), "frontend") == sorted(MAGER_FRONTEND)


def test_the_prod_page_links_the_installation_by_the_name_it_has_there():
    class _Customer:
        subdomain = "icc"
        slug = "icc"
        prod_host = "mager"

    class _Project:
        slug = "nex-manager"
        private_network = False

    assert deploy_service._instance_url(_Customer(), "prod", _Project()) == "https://icc-manager.icc.sk"
    assert deploy_service._instance_url(_Customer(), "uat", _Project()) == "https://uat-icc-manager.isnex.eu"
    _Customer.prod_host = None
    assert deploy_service._instance_url(_Customer(), "prod", _Project()) == "https://icc-manager.isnex.eu"
