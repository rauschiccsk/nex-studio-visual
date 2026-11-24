"""DEV-506 — every service that reads NEX Knowledge (or calls NEX Manager inside) is on the machine's network, and the
read after the deploy is tried from each of them.

24.11.2026 NEX HelpDesk 1.0.0 in PROD at ICC: „Stav spojení“ said „NEX Knowledge unreachable: ConnectError“ although
the deploy had said the app reads it. The cockpit put only the frontend and the backend on the machine's network
(``nex-proxy-net``) and tried the read from the backend; the HelpDesk reads its knowledge in its worker, which was
left on the installation's own network (measured on UAT: backend reaches it, worker „DOWN URLError“). Director:
„Súhlasím“.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

from backend.services import knowledge_link
from backend.services import uat_provisioner as P

NET = P.PROXY_NETWORK

SOURCE = """services:
  db:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: app
      POSTGRES_PASSWORD: x
      POSTGRES_DB: app
  migrate:
    build: ./backend
    command: ["alembic", "upgrade", "head"]
  backend:
    build: ./backend
    environment:
      NEX_KNOWLEDGE_BASE_URL: ${NEX_KNOWLEDGE_BASE_URL:-}
  worker:
    build: ./backend
    command: ["python", "-m", "app.worker"]
    environment:
      NEX_KNOWLEDGE_BASE_URL: ${NEX_KNOWLEDGE_BASE_URL:-}
      QUEUE: calls
  mailer:
    build: ./backend
    environment:
      - NEX_MANAGER_BASE_URL=${NEX_MANAGER_BASE_URL:-}
  frontend:
    build: ./frontend
"""


@pytest.fixture
def project(tmp_path) -> Path:
    here = tmp_path / "projects" / "nex-helpdesk"
    here.mkdir(parents=True)
    (here / "docker-compose.yml").write_text(SOURCE, encoding="utf-8")
    return here


def _render(project: Path, existing: str | None = None) -> dict:
    return P.build_compose_for_instance(
        project_path=project,
        slug="icc-uat",
        project="nex-helpdesk",
        environment="uat",
        customer_slug="icc",
        app="helpdesk",
        existing_compose_text=existing,
    )


def _networks(service: dict) -> list[str]:
    nets = service.get("networks") or []
    return sorted(nets if isinstance(nets, list) else nets.keys())


# ── 1. who goes onto the machine's network ─────────────────────────────────────────────────────────────────────────


def test_a_worker_that_reads_the_knowledge_is_on_the_machine_network_beside_its_own(project) -> None:
    services = _render(project)["services"]

    assert _networks(services["worker"]) == ["default", NET]
    assert _networks(services["backend"]) == ["default", NET]


def test_a_service_that_calls_the_manager_inside_is_there_too_in_list_form(project) -> None:
    services = _render(project)["services"]

    assert _networks(services["mailer"]) == ["default", NET]


def test_a_service_that_reads_neither_stays_off_it(project) -> None:
    services = _render(project)["services"]

    assert NET not in _networks(services["migrate"])
    assert NET not in _networks(services["db"])


def test_on_the_machine_network_the_worker_gets_no_address_of_its_own(project) -> None:
    worker = _render(project)["services"]["worker"]

    assert not [label for label in worker.get("labels") or [] if str(label).startswith("traefik.")]


OWN_ROUTING = """name: icc-helpdesk
services:
  db:
    image: postgres:16-alpine
    networks: [hd-net]
  backend:
    image: icc-helpdesk-backend:1.0.0
    networks: [hd-net, nex-ts-net]
    labels:
      - traefik.enable=true
      - traefik.http.routers.icc-helpdesk-api.rule=Host(`helpdesk.example.sk`)
  worker:
    image: icc-helpdesk-backend:1.0.0
    networks: [hd-net]
    environment:
      NEX_KNOWLEDGE_BASE_URL: ${NEX_KNOWLEDGE_BASE_URL:-}
  frontend:
    image: icc-helpdesk-frontend:1.0.0
    networks: [hd-net, nex-ts-net]
    labels:
      - traefik.enable=true
networks:
  hd-net:
    driver: bridge
  nex-ts-net:
    external: true
"""


def test_with_its_own_routing_a_reader_joins_its_backend_s_networks_and_no_other(project) -> None:
    """A hand-written installation (ICCINT-151) keeps its networks; the reader gets those its backend is on — never
    ``nex-proxy-net``, which that machine may not have."""
    rendered = _render(project, OWN_ROUTING)

    assert _networks(rendered["services"]["worker"]) == ["hd-net", "nex-ts-net"]
    assert NET not in yaml.safe_dump(rendered)


def test_a_redeploy_puts_the_reader_on_the_network_its_backend_has(project) -> None:
    """24.11.2026: a redeploy takes the networks of the installation as they are — the worker that was left off must
    get them all the same."""
    before = P.render_uat_compose(_render(project))
    left_off = yaml.safe_load(before)
    for name in ("worker", "mailer"):
        left_off["services"][name].pop("networks", None)

    services = _render(project, yaml.safe_dump(left_off))["services"]

    assert NET in _networks(services["backend"])
    assert NET in _networks(services["worker"]) and NET in _networks(services["mailer"])
    assert NET not in _networks(services["migrate"])


def _git(project: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(project), *args], capture_output=True, check=True)


@pytest.fixture
def released(tmp_path, monkeypatch) -> Path:
    """The project as a deploy takes it: committed and tagged with the version it deploys."""
    monkeypatch.setattr(P, "_docker_image_exists", lambda image: True)
    here = tmp_path / "projects" / "nex-helpdesk"
    here.mkdir(parents=True)
    (here / "docker-compose.yml").write_text(SOURCE, encoding="utf-8")
    (here / ".env.example").write_text("SECRET_KEY=change-me\nNEX_KNOWLEDGE_BASE_URL=\n", encoding="utf-8")
    _git(here.parent, "init", "-q", "-b", "main", str(here))
    _git(here, "add", "-A")
    _git(here, "-c", "user.email=t@example.com", "-c", "user.name=t", "commit", "-q", "-m", "release 1.0.0")
    _git(here, "tag", "v1.0.0")
    return here


def _deploy(tmp_path: Path):
    return P.provision_uat(
        "nex-helpdesk",
        "icc",
        projects_root=tmp_path / "projects",
        uat_root=tmp_path / "uat",
        customer_slug="icc",
        app="helpdesk",
        full_project_slug="nex-helpdesk",
        version="1.0.0",
    )


def test_a_redeploy_of_an_installation_written_before_puts_its_reader_on_the_network(tmp_path, released) -> None:
    """24.11.2026, NEX HelpDesk UAT ICC on cockpit 4.172.2: „Nasadiť znova“ kept the worker off the machine's network —
    a redeploy bumps the running installation's compose and does not render the services anew. The deploy itself, the
    way the deploy screen runs it, on an installation as cockpit 4.172.1 wrote it."""
    compose_path = _deploy(tmp_path).compose_path
    before = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    for name in ("worker", "mailer"):
        before["services"][name].pop("networks", None)
    compose_path.write_text(P.render_uat_compose(before), encoding="utf-8")

    result = _deploy(tmp_path)

    services = yaml.safe_load(result.compose_path.read_text(encoding="utf-8"))["services"]
    assert _networks(services["worker"]) == ["default", NET] and _networks(services["mailer"]) == ["default", NET]
    assert NET not in _networks(services["db"])
    assert "backend" in (services["worker"].get("links") or [])
    assert P.READERS_JOINED.format(services="mailer, worker", networks=f"default, {NET}") in result.warnings


def test_a_redeploy_that_changes_nothing_says_nothing_of_it(tmp_path, released) -> None:
    _deploy(tmp_path)

    result = _deploy(tmp_path)

    assert not [w for w in result.warnings if "NEX Knowledge alebo NEX Manager" in w]


# ── 2. on the machine's network a reader reaches its own installation by its links (DEV-306) ───────────────────────


def test_the_worker_reaches_its_own_backend_by_a_link(project) -> None:
    """On the machine's network „backend“ answers from every installation there; the link scopes it to its own. The
    database is not on that network, so its name cannot be taken there (as for the backend itself)."""
    rendered = yaml.safe_load(P.render_uat_compose(_render(project)))
    worker = rendered["services"]["worker"]

    assert "backend" in (worker.get("links") or [])


# ── 3. the read after the deploy is tried from every reader ───────────────────────────────────────────────────────

INSTALLED = """services:
  backend:
    container_name: uat-icc-helpdesk-backend
    environment:
      NEX_KNOWLEDGE_BASE_URL: http://uat-icc-knowledge-backend:8000
  worker:
    container_name: uat-icc-helpdesk-worker
    environment:
      - NEX_KNOWLEDGE_BASE_URL=http://uat-icc-knowledge-backend:8000
  migrate:
    container_name: uat-icc-helpdesk-migrate
    restart: "no"
    environment:
      NEX_KNOWLEDGE_BASE_URL: http://uat-icc-knowledge-backend:8000
"""


def _installed(tmp_path: Path) -> Path:
    compose = tmp_path / "docker-compose.yml"
    compose.write_text(INSTALLED, encoding="utf-8")
    return compose


def test_the_read_is_tried_from_every_service_that_has_the_address(tmp_path, monkeypatch) -> None:
    asked: list[str] = []
    monkeypatch.setattr(knowledge_link, "_read", lambda container, host: asked.append(container) or "OK 1")

    assert knowledge_link.check(_installed(tmp_path), None) == (knowledge_link.READS.format(areas="1"), [])
    assert asked == ["uat-icc-helpdesk-backend", "uat-icc-helpdesk-worker"]


@pytest.mark.parametrize("restart", ['"no"', "no", "'no'"])
def test_a_step_that_runs_once_is_not_asked_it_has_ended(tmp_path, monkeypatch, restart) -> None:
    """24.11.2026: the read after the deploy asked the migration, which had already ended — „Docker v kontajneri
    uat-icc-helpdesk-migrate neodpovedal“ hid what the backend and the worker said."""
    compose = _installed(tmp_path)
    compose.write_text(compose.read_text(encoding="utf-8").replace('restart: "no"', f"restart: {restart}"))
    asked: list[str] = []
    monkeypatch.setattr(knowledge_link, "_read", lambda container, host: asked.append(container) or "OK 1")

    knowledge_link.check(compose, None)

    assert asked == ["uat-icc-helpdesk-backend", "uat-icc-helpdesk-worker"]


def test_a_reader_that_cannot_reach_it_is_named(tmp_path, monkeypatch) -> None:
    outcomes = {"uat-icc-helpdesk-backend": "OK 1", "uat-icc-helpdesk-worker": "DOWN URLError"}
    monkeypatch.setattr(knowledge_link, "_read", lambda container, host: outcomes[container])

    said = knowledge_link.check(_installed(tmp_path), None)

    assert said == (
        None,
        [
            "Prepojenie s NEX Knowledge: zo služby uat-icc-helpdesk-worker sa na NEX Knowledge nedá dostať (URLError) "
            "— obe inštalácie musia bežať na tom istom stroji a služba v sieti nex-proxy-net."
        ],
    )
