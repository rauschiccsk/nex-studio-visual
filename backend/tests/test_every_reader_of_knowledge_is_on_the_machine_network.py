"""DEV-506 — every service that reads NEX Knowledge (or calls NEX Manager inside) is on the machine's network, and the
read after the deploy is tried from each of them.

24.11.2026 NEX HelpDesk 1.0.0 in PROD at ICC: „Stav spojení“ said „NEX Knowledge unreachable: ConnectError“ although
the deploy had said the app reads it. The cockpit put only the frontend and the backend on the machine's network
(``nex-proxy-net``) and tried the read from the backend; the HelpDesk reads its knowledge in its worker, which was
left on the installation's own network (measured on UAT: backend reaches it, worker „DOWN URLError“). Director:
„Súhlasím“.
"""

from __future__ import annotations

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
