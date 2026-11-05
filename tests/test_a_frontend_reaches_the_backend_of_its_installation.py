"""DEV-306 — a frontend in the machine's shared Traefik network reaches the backend of its own installation only.

04.11.2026 (Máger Dedo, MSG-227; measured on ANDROS): in ``nex-proxy-net`` ten containers answered to „backend“ —
compose gives a service its name on every network it is on — and Docker answers a name from the network first in
alphabetical order. The frontend of the UAT of ICC's NEX Manager (``uat-icc-manager_…`` sorts after ``nex-proxy-net``)
got all ten, and of ten requests through its nginx (``proxy_pass http://backend:8000``) its own backend answered one —
others came from NEX Inbox of MÁGERSTAV and other apps. A link is scoped to the container that has it: with
``links: [backend]`` the name led 6 of 6 times to the own backend (scratch network). The cockpit now writes the link
into every compose it writes, says it on the redeploy that adds it, and after every deploy checks where the name leads.
Director 04.11.2026: „súhlasím“.

05.11.2026 (Máger Dedo, MSG-227): with that, the backends still saw „frontend“ on every installation of the shared
network (five on MAGER). A link back by ``links`` is a cycle for compose („dependency cycle detected: backend ->
frontend -> backend“, measured); a link by the container's name (``external_links``) is no dependency, and with it the
backend got its own frontend 6 of 6 times, and again after the frontend was recreated. Every service on the shared
network now reaches every other service of its installation there by a link — the backend by ``links`` as before
(it keeps the start order), the others by their container. Director 05.11.2026: „Súhlasím“.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

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


def _named(compose: dict) -> dict:
    """Every container named as ``build_uat_compose`` names it."""
    for name, svc in compose["services"].items():
        svc["container_name"] = f"uat-icc-manager-{name}"
    return compose


def _cycle(services: dict) -> Optional[list[str]]:
    """A dependency cycle as ``docker compose up`` sees one — ``depends_on`` and ``links``; ``external_links`` are
    none."""

    def needs(name: str) -> list[str]:
        svc = services.get(name) or {}
        return [*(svc.get("depends_on") or []), *(str(link).split(":")[0] for link in svc.get("links") or [])]

    done: set[str] = set()

    def visit(name: str, path: list[str]) -> Optional[list[str]]:
        if name in path:
            return path + [name]
        if name in done:
            return None
        for dep in needs(name):
            found = visit(dep, path + [name])
            if found:
                return found
        done.add(name)
        return None

    return next((found for found in (visit(name, []) for name in services) if found), None)


# ---------- the links ----------


def test_a_frontend_on_the_shared_network_is_linked_to_its_backend():
    compose, linked = P.links_within_installation(_installation(), NET)

    assert compose["services"]["frontend"]["links"] == ["backend"]
    assert "external_links" not in compose["services"]["frontend"], "nothing else of it is on the shared network"
    assert "links" not in compose["services"]["backend"], "a link back by links is a cycle for compose"
    assert linked == ["backend", "frontend"]
    for off in ("worker", "db"):
        assert not {"links", "external_links"} & set(compose["services"][off]), "off the shared network, no stranger"


def test_the_backend_reaches_the_frontend_of_its_installation_by_its_container():
    """MSG-227: in ``nex-ts-net`` on MAGER the backends of five installations saw five „frontend“s."""
    compose, _ = P.links_within_installation(_named(_installation()), NET)

    assert compose["services"]["backend"]["external_links"] == ["uat-icc-manager-frontend:frontend"]


def test_no_link_the_cockpit_writes_is_a_cycle_for_compose():
    web = {"image": "uat-icc-career-web:v1.1.0", "networks": ["default", NET], "depends_on": ["backend"]}
    installation = _named(_installation(web=web))
    installation["services"]["frontend"]["depends_on"] = ["backend"]

    compose, _ = P.links_within_installation(installation, NET)

    assert _cycle(compose["services"]) is None


def test_the_cycle_check_sees_one():
    assert _cycle({"backend": {"links": ["frontend"]}, "frontend": {"depends_on": ["backend"]}}) == [
        "backend",
        "frontend",
        "backend",
    ]


def test_a_link_to_the_backend_that_would_close_a_cycle_goes_by_its_container():
    installation = _named(_installation())
    installation["services"]["backend"]["depends_on"] = ["frontend"]

    compose, _ = P.links_within_installation(installation, NET)

    assert "links" not in compose["services"]["frontend"]
    assert compose["services"]["frontend"]["external_links"] == ["uat-icc-manager-backend:backend"]
    assert _cycle(compose["services"]) is None


def test_without_a_container_name_it_is_the_one_compose_gives():
    compose, _ = P.links_within_installation(_installation(), NET)

    assert compose["services"]["backend"]["external_links"] == ["uat-icc-manager-frontend-1:frontend"]


def test_a_service_whose_container_has_no_one_name_is_not_linked_to():
    """Without a project name, or with more replicas than one, no container name is the service's."""
    nameless = _installation()
    del nameless["name"]
    replicated = _installation()
    replicated["services"]["frontend"]["deploy"] = {"replicas": 2}

    for installation in (nameless, replicated):
        compose, linked = P.links_within_installation(installation, NET)

        assert "external_links" not in compose["services"]["backend"]
        assert compose["services"]["frontend"]["links"] == ["backend"], "a link by the service's name needs none"
        assert linked == ["frontend"]


def test_every_service_of_a_web_project_reaches_every_other_one():
    web = {"image": "uat-icc-career-web:v1.1.0", "networks": ["default", NET]}

    compose, linked = P.links_within_installation(_named(_installation(web=web)), NET)

    services = compose["services"]
    assert linked == ["backend", "frontend", "web"]
    assert services["frontend"]["links"] == ["backend"] and services["web"]["links"] == ["backend"]
    assert services["frontend"]["external_links"] == ["uat-icc-manager-web:web"]
    assert services["web"]["external_links"] == ["uat-icc-manager-frontend:frontend"]
    assert services["backend"]["external_links"] == [
        "uat-icc-manager-frontend:frontend",
        "uat-icc-manager-web:web",
    ]


def test_links_a_service_had_stay_and_the_backends_aliases_come():
    installation = _installation()
    installation["services"]["frontend"]["links"] = ["cache"]
    installation["services"]["backend"]["networks"] = {"default": {"aliases": ["api"]}, NET: None}
    installation["services"]["frontend"]["networks"] = {"default": None, NET: None}

    compose, _ = P.links_within_installation(installation, NET)

    assert compose["services"]["frontend"]["links"] == ["cache", "backend", "backend:api"]


def test_the_frontends_aliases_on_the_shared_network_come_too():
    installation = _named(_installation())
    installation["services"]["frontend"]["networks"] = {"default": None, NET: {"aliases": ["app"]}}

    compose, _ = P.links_within_installation(installation, NET)

    assert compose["services"]["backend"]["external_links"] == [
        "uat-icc-manager-frontend:frontend",
        "uat-icc-manager-frontend:app",
    ]


def test_an_external_link_a_service_had_stays_and_is_not_doubled():
    installation = _named(_installation())
    installation["services"]["backend"]["external_links"] = ["redis-1:cache", "uat-icc-manager-frontend:frontend"]

    compose, linked = P.links_within_installation(installation, NET)

    assert compose["services"]["backend"]["external_links"] == ["redis-1:cache", "uat-icc-manager-frontend:frontend"]
    assert linked == ["frontend"]


def test_the_given_compose_is_not_changed():
    given = _installation()

    P.links_within_installation(given, NET)

    assert not {"links", "external_links"} & (set(given["services"]["frontend"]) | set(given["services"]["backend"]))


def test_a_service_that_shares_no_network_with_the_backend_gets_no_link():
    """A link needs a network both are on — compose would refuse the installation otherwise."""
    installation = _installation()
    installation["services"]["backend"]["networks"] = ["default"]
    installation["services"]["frontend"]["networks"] = [NET]

    compose, linked = P.links_within_installation(installation, NET)

    assert linked == [] and "links" not in compose["services"]["frontend"]


def test_the_shared_network_is_known_by_its_name_too():
    """MAGER's installations key it otherwise: ``proxy: {external: true, name: nex-ts-net}``."""
    installation = _installation()
    installation["networks"] = {"proxy": {"external": True, "name": "nex-ts-net"}}
    for name in ("backend", "frontend"):
        installation["services"][name]["networks"] = ["default", "proxy"]

    _, linked = P.links_within_installation(installation, "nex-ts-net")

    assert linked == ["backend", "frontend"]


def test_a_second_pass_adds_nothing():
    once, _ = P.links_within_installation(_installation(), NET)

    twice, linked = P.links_within_installation(once, NET)

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


def _rewritten_without(result, *keys: tuple[str, str]) -> None:
    """The installation as an older cockpit wrote it — these ``(service, key)`` not there."""
    old = yaml.safe_load(result.compose_path.read_text(encoding="utf-8"))
    for service, key in keys:
        del old["services"][service][key]
    header = "".join(
        line for line in result.compose_path.read_text(encoding="utf-8").splitlines(True) if line[:1] == "#"
    )
    result.compose_path.write_text(header + yaml.safe_dump(old, sort_keys=False), encoding="utf-8")


def test_a_new_installation_links_its_services(tmp_path, monkeypatch):
    _project(tmp_path, monkeypatch)

    first = _deploy(tmp_path, "1.5.0")

    services = _services(first)
    assert services["frontend"]["links"] == ["backend"]
    assert services["backend"]["external_links"] == [f"{services['frontend']['container_name']}:frontend"]
    assert not any("naviazal" in w for w in first.warnings), "a new installation is not changed, it is written"


def test_an_installation_made_before_gets_the_links_on_its_next_deploy_and_is_told(tmp_path, monkeypatch):
    _project(tmp_path, monkeypatch)
    first = _deploy(tmp_path, "1.5.0")
    _rewritten_without(first, ("frontend", "links"), ("backend", "external_links"))

    again = _deploy(tmp_path, "1.5.1")

    assert _services(again)["frontend"]["links"] == ["backend"]
    assert (
        "Kokpit naviazal backend, frontend na ostatné služby tejto inštalácie — mená týchto služieb z nich už nevedú "
        "do iných inštalácií v spoločnej sieti." in again.warnings
    )
    third = _deploy(tmp_path, "1.5.2")
    assert not any("naviazal" in w for w in third.warnings), "said once, on the deploy that changed it"


def test_an_installation_the_cockpit_linked_to_its_backend_gets_the_link_back(tmp_path, monkeypatch):
    """What 4.112.1–4.113.2 wrote: the frontend linked to the backend, nothing back."""
    _project(tmp_path, monkeypatch)
    first = _deploy(tmp_path, "1.5.0")
    _rewritten_without(first, ("backend", "external_links"))

    again = _deploy(tmp_path, "1.5.1")

    assert _services(again)["backend"]["external_links"] == [
        f"{_services(again)['frontend']['container_name']}:frontend"
    ]
    assert (
        "Kokpit naviazal backend na ostatné služby tejto inštalácie — mená týchto služieb z nich už nevedú do iných "
        "inštalácií v spoločnej sieti." in again.warnings
    )


# ---------- where the names lead on the machine ----------

ADDRESSES = {
    "uat-icc-manager-backend": {"172.26.0.5", "192.168.64.3"},
    "uat-icc-manager-frontend": {"172.26.0.7", "192.168.64.4"},
}
OWN = {
    ("uat-icc-manager-frontend", "backend"): {"172.26.0.5"},
    ("uat-icc-manager-backend", "frontend"): {"172.26.0.7"},
}


@pytest.fixture
def written(tmp_path) -> Path:
    compose, _ = P.links_within_installation(_named(_installation()), NET)
    path = tmp_path / "docker-compose.yml"
    path.write_text(yaml.safe_dump(compose), encoding="utf-8")
    return path


def _machine(monkeypatch, resolved: dict, asked: Optional[list] = None) -> None:
    def addresses(container, deploy_host):
        if asked is not None:
            asked.append(("own", container))
        return ADDRESSES.get(container)

    def resolve(container, name, deploy_host):
        if asked is not None:
            asked.append((container, name, deploy_host))
        return resolved.get((container, name))

    monkeypatch.setattr(backend_reach, "_addresses", addresses)
    monkeypatch.setattr(backend_reach, "_resolved", resolve)


def test_services_that_reach_their_own_say_nothing(written, monkeypatch):
    _machine(monkeypatch, OWN)

    assert backend_reach.check(written, None) == []


def test_every_name_the_cockpit_linked_is_asked_where_it_leads(written, monkeypatch):
    asked = []
    _machine(monkeypatch, OWN, asked)

    backend_reach.check(written, "mager")

    assert sorted(a for a in asked if a[0] != "own") == [
        ("uat-icc-manager-backend", "frontend", "mager"),
        ("uat-icc-manager-frontend", "backend", "mager"),
    ]


def test_an_alias_is_asked_as_its_name_is(tmp_path, monkeypatch):
    installation = _named(_installation())
    installation["services"]["frontend"]["networks"] = {"default": None, NET: {"aliases": ["app"]}}
    compose, _ = P.links_within_installation(installation, NET)
    path = tmp_path / "docker-compose.yml"
    path.write_text(yaml.safe_dump(compose), encoding="utf-8")
    _machine(monkeypatch, OWN | {("uat-icc-manager-backend", "app"): {"172.26.0.19"}})

    assert backend_reach.check(path, None) == [
        "⚠️ Z backend vedie meno „app“ aj mimo služby frontend tejto inštalácie (172.26.0.19) — volanie tým menom "
        "môže dostať iná aplikácia. Skontroluj to skôr, než verziu akceptuješ alebo nasadíš ďalej."
    ]


def test_a_frontend_that_reaches_another_backend_is_said(written, monkeypatch):
    _machine(monkeypatch, OWN | {("uat-icc-manager-frontend", "backend"): {"172.26.0.5", "172.26.0.18"}})

    assert backend_reach.check(written, "mager") == [
        "⚠️ Z frontend vedie meno „backend“ aj mimo backendu tejto inštalácie (172.26.0.18) — požiadavky cez jej nginx "
        "môžu dostať iné aplikácie. Skontroluj to skôr, než verziu akceptuješ alebo nasadíš ďalej."
    ]


def test_a_backend_that_reaches_another_frontend_is_said(written, monkeypatch):
    _machine(monkeypatch, OWN | {("uat-icc-manager-backend", "frontend"): {"172.26.0.7", "172.26.0.19"}})

    assert backend_reach.check(written, None) == [
        "⚠️ Z backend vedie meno „frontend“ aj mimo služby frontend tejto inštalácie (172.26.0.19) — volanie tým "
        "menom môže dostať iná aplikácia. Skontroluj to skôr, než verziu akceptuješ alebo nasadíš ďalej."
    ]


def test_a_name_nobody_could_ask_is_said(written, monkeypatch):
    _machine(monkeypatch, {})

    assert backend_reach.check(written, None) == [
        "Kokpit nevedel overiť, kam z backend vedie meno „frontend“ — skontroluj to pred akceptáciou.",
        "Kokpit nevedel overiť, kam z frontend vedie meno „backend“ — skontroluj to pred akceptáciou.",
    ]


@pytest.mark.asyncio
async def test_every_deploy_looks_where_the_names_lead(db_session, monkeypatch, written):
    from backend.services import deploy as deploy_service
    from tests.test_deploy_service import _FakeRunner, _make_customer, _make_project, _make_user, _make_version

    monkeypatch.setattr(P, "instance_dir_for", lambda **kw: written.parent)
    _machine(monkeypatch, OWN | {("uat-icc-manager-frontend", "backend"): {"172.26.0.18"}})
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
