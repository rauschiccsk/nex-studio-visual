"""DEV-278 — an app on another machine pairs with the customer's NEX Manager on that same machine.

An app opened from the customer's NEX Manager (NEX Websites, NEX Knowledge) gets the Manager's launch key, the name of
its installation, its address and a key of its own the Manager hands out when it registers the app. The cockpit read
all of it from the Manager's files on ANDROS also for an installation on MAGER, and sent the registration to the
Manager's container name — which from ANDROS does not lead to MAGER. On 31.10.2026, before ICC's production moved to
MAGER, those files on ANDROS were the ones of ICC's old hand-authored NEX Manager still running there: NEX Websites on
MAGER would have taken its keys, and no registration could succeed.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from backend.services import uat_provisioner as P

COMPOSE = """
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
  frontend:
    build:
      context: ./frontend
"""
ENV_EXAMPLE = (
    "MANAGER_LAUNCH_SIGNING_KEY=\nMANAGER_DEPLOY_SLUG=\nMANAGER_MODULE_SLUG=nex-websites\n"
    "NEX_MANAGER_BASE_URL=\nNEX_MANAGER_API_KEY=\n"
)
#: ICC's NEX Manager as the cockpit made it on MAGER (DEV-275).
MANAGER_ON_MAGER = """
name: icc-manager
services:
  backend:
    container_name: icc-manager-backend
    labels:
      - traefik.http.routers.icc-manager-sec-api.rule=Host(`icc-manager.icc.sk`) && PathPrefix(`/api`)
      - traefik.http.routers.icc-manager-sec-api.entrypoints=websecure
      - traefik.http.services.icc-manager-api.loadbalancer.server.port=8000
  frontend:
    container_name: icc-manager-frontend
    labels:
      - traefik.http.routers.icc-manager-sec.rule=Host(`icc-manager.icc.sk`)
      - traefik.http.routers.icc-manager-sec.entrypoints=websecure
      - traefik.http.routers.icc-manager-sec.tls.certresolver=cf
      - traefik.http.services.icc-manager.loadbalancer.server.port=80
"""
MAGER_ENV = "LAUNCH_SIGNING_KEY=key-on-mager-FAKE\nDEPLOY_SLUG=icc-prod\nPROVISIONING_TOKEN=token-on-mager-FAKE\n"
#: What is left of ICC's old NEX Manager on ANDROS.
ANDROS_ENV = "LAUNCH_SIGNING_KEY=key-on-andros-FAKE\nDEPLOY_SLUG=icc\nPROVISIONING_TOKEN=token-on-andros-FAKE\n"
ANDROS_COMPOSE = "services:\n  backend:\n    container_name: icc-manager-backend\n"


class _Mager:
    """MAGER: the files of each installation folder, and what the cockpit wrote there."""

    def __init__(self, files: dict[tuple[str, str], str]):
        self.files = dict(files)
        self.written: dict[tuple[str, str], str] = {}

    def read_text(self, instance_dir, filename, *, deploy_host, timeout=120):
        assert deploy_host == "mager"
        return self.files.get((Path(instance_dir).name, filename)), None

    def write_files(self, instance_dir, files, *, deploy_host, timeout=180):
        for name, (text, _mode) in files.items():
            self.written[(Path(instance_dir).name, name)] = text
            self.files[(Path(instance_dir).name, name)] = text
        return None


class _ManagerApi:
    """The NEX Manager's module registry as the cockpit sees it over the network."""

    def __init__(self, modules=None):
        self.modules = list(modules or [])
        self.requests: list[tuple[str, str, str]] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append((request.method, str(request.url), request.headers.get("X-Provisioning-Token", "")))
        if request.method == "GET":
            return httpx.Response(200, json=self.modules)
        if request.method == "POST":
            self.modules.append({"id": 1, **json.loads(request.content)})
            return httpx.Response(201, json={"identity_key": "minted-on-mager-FAKE"})
        return httpx.Response(200, json={})


@pytest.fixture
def world(tmp_path, monkeypatch):
    project = tmp_path / "projects" / "nex-websites"
    project.mkdir(parents=True)
    (project / "docker-compose.yml").write_text(COMPOSE, encoding="utf-8")
    (project / ".env.example").write_text(ENV_EXAMPLE, encoding="utf-8")
    andros = tmp_path / "customers" / "icc" / "nex-manager"
    andros.mkdir(parents=True)
    (andros / ".env").write_text(ANDROS_ENV, encoding="utf-8")
    (andros / "docker-compose.yml").write_text(ANDROS_COMPOSE, encoding="utf-8")
    mager = _Mager({("nex-manager", ".env"): MAGER_ENV, ("nex-manager", "docker-compose.yml"): MANAGER_ON_MAGER})
    api = _ManagerApi()
    real_client = httpx.Client
    monkeypatch.setattr(P, "remote_instance", mager)
    monkeypatch.setattr(P.httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(api.handle), **kw))
    monkeypatch.setattr(P, "_docker_image_exists", lambda image: True)
    return tmp_path, mager, api


def _deploy(tmp_path: Path, version: str = "1.0.0"):
    return P.provision_uat(
        "nex-websites",
        "icc-prod",
        projects_root=tmp_path / "projects",
        uat_root=tmp_path / "uat",
        prod_root=tmp_path / "customers",
        environment="prod",
        customer_slug="icc",
        app="websites",
        full_project_slug="nex-websites",
        version=version,
        deploy_host="mager",
    )


def _written_env(mager: _Mager) -> dict[str, str]:
    return P._parse_env_text(mager.written[("nex-websites", ".env")])


def test_a_new_app_on_mager_takes_the_keys_of_the_manager_on_mager_and_registers_there(world):
    tmp_path, mager, api = world

    _deploy(tmp_path)

    env = _written_env(mager)
    assert env["MANAGER_LAUNCH_SIGNING_KEY"] == "key-on-mager-FAKE"
    assert env["MANAGER_DEPLOY_SLUG"] == "icc-prod"
    assert env["NEX_MANAGER_BASE_URL"] == "http://icc-manager-backend:8000", "the app asks it on MAGER's network"
    assert env["NEX_MANAGER_API_KEY"] == "minted-on-mager-FAKE"
    assert api.requests == [
        ("GET", "https://icc-manager.icc.sk/api/v1/modules", "token-on-mager-FAKE"),
        ("POST", "https://icc-manager.icc.sk/api/v1/modules", "token-on-mager-FAKE"),
    ]
    assert api.modules[0]["base_url"] == "https://icc-websites.icc.sk"


def test_a_redeploy_on_mager_pairs_the_app_with_the_manager_there_again(world):
    tmp_path, mager, api = world
    _deploy(tmp_path)
    stale = mager.files[("nex-websites", ".env")].replace("key-on-mager-FAKE", "key-of-before-FAKE")
    mager.files[("nex-websites", ".env")] = stale

    result = _deploy(tmp_path, version="1.0.1")

    assert _written_env(mager)["MANAGER_LAUNCH_SIGNING_KEY"] == "key-on-mager-FAKE"
    assert P.PAIRING_RENEWED.format(what="kľúč spustenia") in result.warnings


def test_an_installation_with_its_own_address_keeps_it_in_the_manager(world):
    """MÁGERSTAV's NEX Inbox on MAGER is opened at magerstav-inbox.icc.sk; the cockpit would name it mager-inbox.icc.sk.
    Registered at the cockpit's name, the tile in the Manager would lead nowhere."""
    tmp_path, mager, api = world
    _deploy(tmp_path)
    own = mager.files[("nex-websites", "docker-compose.yml")].replace("icc-websites.icc.sk", "icc-web.icc.sk")
    mager.files[("nex-websites", "docker-compose.yml")] = own
    api.modules[0]["base_url"] = "https://icc-web.icc.sk"
    api.requests.clear()

    _deploy(tmp_path, version="1.0.1")

    assert api.requests == [("GET", "https://icc-manager.icc.sk/api/v1/modules", "token-on-mager-FAKE")]
    assert api.modules[0]["base_url"] == "https://icc-web.icc.sk"


def test_a_manager_on_mager_without_an_address_is_said_and_not_guessed(world, monkeypatch):
    tmp_path, mager, api = world
    mager.files[("nex-manager", "docker-compose.yml")] = ANDROS_COMPOSE  # no router of its own

    result = _deploy(tmp_path)

    assert api.requests == [], "the container name does not lead from ANDROS to MAGER"
    assert any("kokpit nenašiel adresu, na ktorej Manager odpovedá" in w for w in result.warnings)
    assert _written_env(mager)["MANAGER_LAUNCH_SIGNING_KEY"] == "key-on-mager-FAKE"


@pytest.mark.parametrize("unreadable", [".env", "docker-compose.yml"])
def test_a_manager_that_cannot_be_read_on_mager_stops_the_deploy(world, monkeypatch, unreadable):
    tmp_path, mager, _api = world
    read = mager.read_text

    def read_text(instance_dir, filename, *, deploy_host, timeout=120):
        if Path(instance_dir).name == "nex-manager" and filename == unreadable:
            return None, "MAGER neodpovedá"
        return read(instance_dir, filename, deploy_host=deploy_host)

    monkeypatch.setattr(mager, "read_text", read_text)

    with pytest.raises(ValueError, match="MAGER neodpovedá"):
        _deploy(tmp_path)


def test_on_this_machine_the_manager_here_is_asked_at_its_container():
    manager = P.PairedManager.here(Path("/nonexistent"))
    assert manager == P.PairedManager({}, None, None)
