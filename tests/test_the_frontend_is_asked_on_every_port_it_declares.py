"""DEV-60 — the cockpit asks the web frontend on every port it declares, not only on the first one.

Career Asistent 0.1.0, Verifikácia 11.10.2026: the frontend publishes 10250 → 443 (HTTPS) and 10251 → 80 (HTTP).
The release test stack has no certificate, so nginx serves plain HTTP on 80 — as the app's design says. The cockpit
asked only the first declared port (443), got "Connection refused" and reported „Webové rozhranie sa nespustilo“;
the build stood until the Director wrote a technical instruction by hand. The same first-port guess decided the
check after a deploy to UAT and PROD. These tests run the real probe the cockpit sends into the container against a
frontend that listens only where the test says.
"""

from __future__ import annotations

import contextlib
import io
import types
import urllib.error
import urllib.request
from unittest import mock
from urllib.parse import urlsplit

import pytest
import yaml

from backend.services import orchestrator, uat_provisioner

CAREER_ASISTENT_COMPOSE = """\
services:
  backend:
    build: .
  frontend:
    build: ./frontend
    ports:
      - "10250:443"
      - "10251:80"
"""

REFUSED = "err <urlopen error [Errno 111] Connection refused>"


class _Containers:
    """``docker compose`` as the probes see it: a backend that answers on ``localhost`` and a frontend that
    listens only on ``frontend_ports``. Runs the probe's real source in-process and records every URL it asked."""

    def __init__(self, *frontend_ports: int) -> None:
        self.frontend_ports = set(frontend_ports)
        self.asked: list[str] = []

    def _urlopen(self, url, timeout=None):  # noqa: ARG002 — the probe's own signature
        if isinstance(url, urllib.request.Request):  # the public-route probe through Traefik — not DEV-60's concern
            return types.SimpleNamespace(status=200)
        self.asked.append(url)
        parts = urlsplit(url)
        if parts.hostname == "localhost" or parts.port in self.frontend_ports:
            return types.SimpleNamespace(status=200)
        raise urllib.error.URLError(ConnectionRefusedError(111, "Connection refused"))

    async def __call__(self, cmd, timeout, env=None):  # noqa: ARG002 — _compose_smoke_step's signature
        if "python" not in cmd:  # the container logs a failed probe brings along (DEV-61) — empty here
            return 0, ""
        source = cmd[cmd.index("-c") + 1]
        out, code = io.StringIO(), 0
        with mock.patch.object(urllib.request, "urlopen", self._urlopen), contextlib.redirect_stdout(out):
            try:
                exec(compile(source, "<probe>", "exec"), {})  # noqa: S102 — the cockpit's own probe
            except SystemExit as exc:
                code = exc.code if isinstance(exc.code, int) else 1
        return code, out.getvalue()


@pytest.fixture
def no_waiting(monkeypatch):
    async def _no_sleep(*_a, **_k):
        return None

    monkeypatch.setattr(orchestrator.asyncio, "sleep", _no_sleep)


def _smoke_stack(tmp_path, compose_yml: str = CAREER_ASISTENT_COMPOSE) -> orchestrator._SmokeStack:
    compose = tmp_path / "docker-compose.yml"
    compose.write_text(compose_yml)
    roles = uat_provisioner.identify_service_roles(yaml.safe_load(compose_yml)["services"])
    base = ["docker", "compose", "-p", "cas-smoke", "-f", str(compose)]
    return orchestrator._SmokeStack(
        base=base, compose=compose, override=tmp_path / "o.yml", project="cas-smoke", roles=roles, up_rc=0, up_detail=""
    )


def _deployed(monkeypatch, tmp_path, compose_yml: str = CAREER_ASISTENT_COMPOSE) -> None:
    (tmp_path / "uat" / "cas").mkdir(parents=True)
    (tmp_path / "uat" / "cas" / "docker-compose.yml").write_text("services: {}\n")
    (tmp_path / "projects" / "career-asistent").mkdir(parents=True)
    (tmp_path / "projects" / "career-asistent" / "docker-compose.yml").write_text(compose_yml)
    monkeypatch.setattr(orchestrator, "UAT_ROOT", tmp_path / "uat")
    monkeypatch.setattr(orchestrator.claude_agent, "PROJECTS_ROOT", tmp_path / "projects")


# --- the release check (Verifikácia) ---------------------------------------------------------------------------


async def test_the_release_check_finds_a_frontend_serving_on_its_second_port(monkeypatch, tmp_path, no_waiting):
    containers = _Containers(80)
    monkeypatch.setattr(orchestrator, "_compose_smoke_step", containers)

    ok, detail = await orchestrator._run_app_starts_smoke(_smoke_stack(tmp_path))

    assert (ok, detail) == (True, orchestrator._APP_RESPONDS)
    assert "http://frontend:80/" in containers.asked


async def test_the_release_check_names_every_port_it_tried_when_none_answers(monkeypatch, tmp_path, no_waiting):
    monkeypatch.setattr(orchestrator, "_compose_smoke_step", _Containers())

    ok, detail = await orchestrator._run_app_starts_smoke(_smoke_stack(tmp_path))

    assert ok is False
    assert detail == (
        f"frontend 'frontend' not serving within {orchestrator.ACCEPTANCE_SMOKE_READY_TIMEOUT}s: "
        f"port 443: {REFUSED}; port 80: {REFUSED}"
    )


async def test_the_release_check_asks_the_frontend_by_its_role_not_by_the_name_frontend(
    monkeypatch, tmp_path, no_waiting
):
    containers = _Containers(3000)
    monkeypatch.setattr(orchestrator, "_compose_smoke_step", containers)
    compose = "services:\n  backend:\n    build: .\n  web:\n    build: ./frontend\n    ports:\n      - '13000:3000'\n"
    stack = _smoke_stack(tmp_path, compose)
    assert stack.roles["frontend"] == "web", "setup: the web service must be the frontend"

    ok, _detail = await orchestrator._run_app_starts_smoke(stack)

    assert ok is True and "http://web:3000/" in containers.asked


# --- the check after a deploy (UAT and PROD) -------------------------------------------------------------------


async def test_the_deploy_check_finds_a_frontend_serving_on_its_second_port(monkeypatch, tmp_path, no_waiting):
    _deployed(monkeypatch, tmp_path)
    containers = _Containers(80)
    monkeypatch.setattr(orchestrator, "_compose_smoke_step", containers)

    ok, detail = await orchestrator._verify_uat_serves("career-asistent", "cas")

    assert (ok, detail) == (True, "OK")
    assert "http://uat-cas-frontend:80/" in containers.asked


async def test_the_deploy_check_names_every_port_it_tried_when_none_answers(monkeypatch, tmp_path, no_waiting):
    _deployed(monkeypatch, tmp_path)
    monkeypatch.setattr(orchestrator, "_compose_smoke_step", _Containers())

    ok, detail = await orchestrator._verify_uat_serves("career-asistent", "cas")

    assert ok is False
    assert detail == (
        f"frontend 'frontend' not serving within {orchestrator.ACCEPTANCE_SMOKE_READY_TIMEOUT}s: "
        f"port 443: {REFUSED}; port 80: {REFUSED}"
    )


async def test_a_frontend_that_declares_no_port_is_asked_on_the_nginx_port(monkeypatch, tmp_path, no_waiting):
    _deployed(monkeypatch, tmp_path, "services:\n  backend:\n    build: .\n  frontend:\n    build: ./frontend\n")
    containers = _Containers(80)
    monkeypatch.setattr(orchestrator, "_compose_smoke_step", containers)

    ok, _detail = await orchestrator._verify_uat_serves("career-asistent", "cas")

    assert ok is True and [u for u in containers.asked if "frontend" in u] == ["http://uat-cas-frontend:80/"]


# --- which ports a service declares ----------------------------------------------------------------------------


def test_every_port_a_service_declares_in_the_order_it_declares_them():
    service = {
        "ports": [
            "10250:443",
            "127.0.0.1:10251:80/tcp",
            {"target": 8443, "published": 18443},
            "10252:443",
            "8000-8001",
        ],
        "expose": [9000, "9100/tcp", "80"],
    }

    assert uat_provisioner.container_ports(service) == [443, 80, 8443, 9000, 9100]
    assert uat_provisioner.container_ports({}) == []


def test_where_traefik_routes_stays_the_first_declared_port():
    """Routing is not part of DEV-60: the generated compose still sends Traefik to the first declared port."""
    assert uat_provisioner.detect_internal_port({"ports": ["10250:443", "10251:80"]}, 80) == 443
    assert uat_provisioner.detect_internal_port({"expose": ["3000"]}, 80) == 3000
    assert uat_provisioner.detect_internal_port({}, 80) == 80
