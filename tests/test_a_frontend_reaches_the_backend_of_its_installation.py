"""DEV-306 — a frontend in the machine's shared Traefik network reaches the backend of its own installation only.

04.11.2026 (Máger Dedo, MSG-227; measured on ANDROS): in ``nex-proxy-net`` ten containers answered to „backend“ —
compose gives a service its name on every network it is on — and Docker answers a name from the network first in
alphabetical order. The frontend of the UAT of ICC's NEX Manager (``uat-icc-manager_…`` sorts after ``nex-proxy-net``)
got all ten, and of ten requests through its nginx (``proxy_pass http://backend:8000``) its own backend answered one —
others came from NEX Inbox of MÁGERSTAV and other apps. A link is scoped to the container that has it: with
``links: [backend]`` the name led 6 of 6 times to the own backend (scratch network). The cockpit now writes the link
into every compose it writes, says it on the redeploy that adds it, and after every deploy checks where the name leads.
Director 04.11.2026: „súhlasím“.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from backend.services import backend_reach
from backend.services import uat_provisioner as P

NET = "nex-proxy-net"


def _installation(**services) -> dict:
    """An installation as the cockpit writes one: frontend and backend on the project and the shared network."""
    return {
        "name": "uat-icc-manager",
        "services": {
            "db": {"image": "postgres:16-alpine"},
            "backend": {"image": "uat-icc-manager-backend:v1.5.1", "networks": ["default", NET]},
            "frontend": {"image": "uat-icc-manager-frontend:v1.5.1", "networks": ["default", NET]},
            "worker": {"image": "uat-icc-manager-backend:v1.5.1", "command": ["python", "-m", "app.worker"]},
            **services,
        },
        "networks": {NET: {"external": True}},
    }


# ---------- the link ----------


def test_a_frontend_on_the_shared_network_is_linked_to_its_backend():
    compose, linked = P.links_to_own_backend(_installation(), NET)

    assert compose["services"]["frontend"]["links"] == ["backend"]
    assert linked == ["frontend"]
    assert "links" not in compose["services"]["backend"], "the backend calls nobody by the shared name"
    assert "links" not in compose["services"]["worker"], "off the shared network a name cannot lead elsewhere"
    assert "links" not in compose["services"]["db"]


def test_the_given_compose_is_not_changed():
    given = _installation()

    P.links_to_own_backend(given, NET)

    assert "links" not in given["services"]["frontend"]


def test_both_frontends_of_a_web_project_are_linked():
    web = {"image": "uat-icc-career-web:v1.1.0", "networks": ["default", NET]}

    compose, linked = P.links_to_own_backend(_installation(web=web), NET)

    assert linked == ["frontend", "web"]
    assert compose["services"]["web"]["links"] == ["backend"]


def test_links_a_service_had_stay_and_the_backends_aliases_come():
    installation = _installation()
    installation["services"]["frontend"]["links"] = ["cache"]
    installation["services"]["backend"]["networks"] = {"default": {"aliases": ["api"]}, NET: None}
    installation["services"]["frontend"]["networks"] = {"default": None, NET: None}

    compose, _ = P.links_to_own_backend(installation, NET)

    assert compose["services"]["frontend"]["links"] == ["cache", "backend", "backend:api"]


def test_a_service_that_shares_no_network_with_the_backend_gets_no_link():
    """A link needs a network both are on — compose would refuse the installation otherwise."""
    installation = _installation()
    installation["services"]["backend"]["networks"] = ["default"]
    installation["services"]["frontend"]["networks"] = [NET]

    compose, linked = P.links_to_own_backend(installation, NET)

    assert linked == [] and "links" not in compose["services"]["frontend"]


def test_the_shared_network_is_known_by_its_name_too():
    """MAGER's installations key it otherwise: ``proxy: {external: true, name: nex-ts-net}``."""
    installation = _installation()
    installation["networks"] = {"proxy": {"external": True, "name": "nex-ts-net"}}
    for name in ("backend", "frontend"):
        installation["services"][name]["networks"] = ["default", "proxy"]

    _, linked = P.links_to_own_backend(installation, "nex-ts-net")

    assert linked == ["frontend"]


def test_a_second_pass_adds_nothing():
    once, _ = P.links_to_own_backend(_installation(), NET)

    twice, linked = P.links_to_own_backend(once, NET)

    assert linked == [] and twice == once


# ---------- the compose the deploy writes ----------

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
  frontend:
    build:
      context: ./frontend
    depends_on: [backend]
"""


def _project(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setattr(P, "_docker_image_exists", lambda image: True)
    project = tmp_path / "projects" / "nex-manager"
    project.mkdir(parents=True)
    (project / "docker-compose.yml").write_text(SOURCE, encoding="utf-8")
    (project / ".env.example").write_text("SECRET_KEY=change-me\n", encoding="utf-8")
    return project


def _deploy(tmp_path: Path, version: str):
    return P.provision_uat(
        "nex-manager",
        "icc",
        projects_root=tmp_path / "projects",
        uat_root=tmp_path / "uat",
        customer_slug="icc",
        app="manager",
        full_project_slug="nex-manager",
        version=version,
    )


def _services(result) -> dict:
    return yaml.safe_load(result.compose_path.read_text(encoding="utf-8"))["services"]


def test_a_new_installation_links_its_frontend(tmp_path, monkeypatch):
    _project(tmp_path, monkeypatch)

    first = _deploy(tmp_path, "1.5.0")

    assert _services(first)["frontend"]["links"] == ["backend"]
    assert not any("naviazal" in w for w in first.warnings), "a new installation is not changed, it is written"


def test_an_installation_made_before_gets_the_link_on_its_next_deploy_and_is_told(tmp_path, monkeypatch):
    _project(tmp_path, monkeypatch)
    first = _deploy(tmp_path, "1.5.0")
    old = yaml.safe_load(first.compose_path.read_text(encoding="utf-8"))
    del old["services"]["frontend"]["links"]
    header = "".join(
        line for line in first.compose_path.read_text(encoding="utf-8").splitlines(True) if line[:1] == "#"
    )
    first.compose_path.write_text(header + yaml.safe_dump(old, sort_keys=False), encoding="utf-8")

    again = _deploy(tmp_path, "1.5.1")

    assert _services(again)["frontend"]["links"] == ["backend"]
    assert (
        "Kokpit naviazal frontend na backend tejto inštalácie — meno backendu z nich už nevedie do iných inštalácií "
        "v spoločnej sieti." in again.warnings
    )
    third = _deploy(tmp_path, "1.5.2")
    assert not any("naviazal" in w for w in third.warnings), "said once, on the deploy that changed it"


# ---------- where the name leads on the machine ----------


@pytest.fixture
def written(tmp_path) -> Path:
    compose, _ = P.links_to_own_backend(_installation(), NET)
    for name, svc in compose["services"].items():
        svc["container_name"] = f"uat-icc-manager-{name}"
    path = tmp_path / "docker-compose.yml"
    path.write_text(yaml.safe_dump(compose), encoding="utf-8")
    return path


def test_a_frontend_that_reaches_its_own_backend_says_nothing(written, monkeypatch):
    monkeypatch.setattr(backend_reach, "_addresses", lambda c, h: {"172.26.0.5", "192.168.64.3"})
    monkeypatch.setattr(backend_reach, "_resolved", lambda c, n, h: {"172.26.0.5"})

    assert backend_reach.check(written, None) == []


def test_a_frontend_that_reaches_another_backend_is_said(written, monkeypatch):
    asked = []
    monkeypatch.setattr(backend_reach, "_addresses", lambda c, h: asked.append(("own", c)) or {"172.26.0.5"})
    monkeypatch.setattr(
        backend_reach, "_resolved", lambda c, n, h: asked.append((c, n, h)) or {"172.26.0.5", "172.26.0.18"}
    )

    said = backend_reach.check(written, "mager")

    assert said == [
        "⚠️ Z frontend vedie meno „backend“ aj mimo backendu tejto inštalácie (172.26.0.18) — požiadavky cez jej nginx "
        "môžu dostať iné aplikácie. Skontroluj to skôr, než verziu akceptuješ alebo nasadíš ďalej."
    ]
    assert asked == [("own", "uat-icc-manager-backend"), ("uat-icc-manager-frontend", "backend", "mager")]


def test_a_name_nobody_could_ask_is_said(written, monkeypatch):
    monkeypatch.setattr(backend_reach, "_resolved", lambda c, n, h: None)

    assert backend_reach.check(written, None) == [
        "Kokpit nevedel overiť, kam z frontend vedie meno „backend“ — skontroluj to pred akceptáciou."
    ]


@pytest.mark.asyncio
async def test_every_deploy_looks_where_the_name_leads(db_session, monkeypatch, written):
    from backend.services import deploy as deploy_service
    from tests.test_deploy_service import _FakeRunner, _make_customer, _make_project, _make_user, _make_version

    monkeypatch.setattr(P, "instance_dir_for", lambda **kw: written.parent)
    monkeypatch.setattr(backend_reach, "_addresses", lambda c, h: {"172.26.0.5"})
    monkeypatch.setattr(backend_reach, "_resolved", lambda c, n, h: {"172.26.0.18"})
    user = _make_user(db_session)
    project = _make_project(db_session, user=user)
    _make_version(db_session, project, "0.1.0")
    customer = _make_customer(db_session, project)

    outcome = await deploy_service.deploy(
        db_session,
        customer.id,
        version_number="0.1.0",
        environment="uat",
        actor_id=user.id,
        deploy_runner=_FakeRunner(),
    )

    assert any(w.startswith("⚠️ Z frontend vedie meno „backend“ aj mimo backendu") for w in outcome.warnings)


#: The real primitive, taken before the suite's default stub replaces it for every test.
REAL_RESOLVED = backend_reach._resolved


def test_every_address_the_name_has_is_asked_not_only_the_first(monkeypatch):
    """nginx uses every address a name resolves to; ``getent hosts`` shows the first only, ``ahosts`` all of them."""
    import subprocess

    out = (
        "172.26.0.5      STREAM backend\n172.26.0.5      DGRAM  backend\n"
        "172.26.0.18     STREAM backend\n172.26.0.18     DGRAM  backend\n"
    )
    asked = []

    def docker(args, deploy_host):
        asked.append(args)
        return subprocess.CompletedProcess(args, 0, out, "")

    monkeypatch.setattr(backend_reach, "_docker", docker)

    assert REAL_RESOLVED("uat-icc-manager-frontend", "backend", None) == {"172.26.0.5", "172.26.0.18"}
    assert asked == [["exec", "uat-icc-manager-frontend", "getent", "ahosts", "backend"]]
