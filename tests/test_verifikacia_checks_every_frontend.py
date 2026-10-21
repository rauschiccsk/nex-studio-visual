"""DEV-163 — Verifikácia boots and asks every frontend of a Web project, and names the one that fails.

The boot check knew one frontend: in a Web project the public web (service ``web``) was built and started, but never
asked whether it serves — it was reported as „V tom čase ešte bežali: web — ich výsledok táto kontrola neposudzuje“.
A Web project whose compose had no ``web`` service at all would have passed. The release acceptance script was told
only one frontend. Director 20.10.2026: „Ideme kokpit naučiť stavať projekt s dvoma frontendmi.“
"""

from __future__ import annotations

import pytest
import yaml

from backend.services import claude_agent, orchestrator, uat_provisioner
from tests.test_the_frontend_is_asked_on_every_port_it_declares import _Containers, no_waiting  # noqa: F401

WEB_COMPOSE = """\
services:
  backend:
    build: .
  frontend:
    build: ./frontend
    ports:
      - "14931:80"
  web:
    build: ./web
    ports:
      - "14935:80"
  db:
    image: postgres:16
"""


def _project(monkeypatch, tmp_path, compose_yml: str, project_type: str):
    (tmp_path / "kariera").mkdir()
    (tmp_path / "kariera" / "docker-compose.yml").write_text(compose_yml)
    monkeypatch.setattr(claude_agent, "PROJECTS_ROOT", tmp_path)
    monkeypatch.setattr(claude_agent, "_project_type", lambda slug: project_type if slug == "kariera" else None)


def _stack(tmp_path, roles) -> orchestrator._SmokeStack:
    compose = tmp_path / "kariera" / "docker-compose.yml"
    base = ["docker", "compose", "-p", "kariera-smoke", "-f", str(compose)]
    return orchestrator._SmokeStack(
        base=base,
        compose=compose,
        override=tmp_path / "o.yml",
        project="kariera-smoke",
        roles=roles,
        up_rc=0,
        up_detail="",
    )


def test_a_web_projects_web_is_a_role_of_its_own(monkeypatch, tmp_path):
    _project(monkeypatch, tmp_path, WEB_COMPOSE, "web")

    _compose, roles, verdict = orchestrator._smoke_compose_target("kariera")

    assert verdict is None
    assert roles == {"backend": "backend", "frontend": "frontend", "db": "db", "web": "web"}


def test_a_standard_projects_roles_are_as_they_were(monkeypatch, tmp_path):
    _project(monkeypatch, tmp_path, WEB_COMPOSE, "standard")

    _compose, roles, verdict = orchestrator._smoke_compose_target("kariera")

    assert verdict is None
    assert roles == uat_provisioner.identify_service_roles(yaml.safe_load(WEB_COMPOSE)["services"])


@pytest.mark.parametrize(
    ("drop", "said"),
    [
        ("web", "docker-compose.yml nemá službu `web` — Web (`web/`) sa nedá spustiť ani overiť"),
        (
            "frontend",
            "docker-compose.yml nemá službu `frontend` — Administrácia (`frontend/`) sa nedá spustiť ani overiť",
        ),
    ],
)
def test_a_web_project_missing_a_frontends_service_fails_before_anything_is_built(monkeypatch, tmp_path, drop, said):
    services = yaml.safe_load(WEB_COMPOSE)["services"]
    del services[drop]
    _project(monkeypatch, tmp_path, yaml.safe_dump({"services": services}), "web")

    _compose, _roles, verdict = orchestrator._smoke_compose_target("kariera")

    assert verdict == (False, said)


async def test_the_boot_check_asks_the_web_and_says_when_it_does_not_serve(monkeypatch, tmp_path, no_waiting):  # noqa: F811
    _project(monkeypatch, tmp_path, WEB_COMPOSE, "web")
    _compose, roles, _verdict = orchestrator._smoke_compose_target("kariera")
    containers = _Containers(80)
    monkeypatch.setattr(orchestrator, "_compose_smoke_step", containers)
    monkeypatch.setattr(orchestrator, "_compose_backend_port", lambda compose: None)

    ok, _detail = await orchestrator._run_app_starts_smoke(_stack(tmp_path, roles))
    assert ok is True
    assert {"http://frontend:80/", "http://web:80/"} <= set(containers.asked)

    silent_web = _Containers()
    silent_web.frontend_ports = {80}

    def _only_frontend(url, timeout=None):  # the web listens nowhere
        if "//web:" in str(url):
            raise __import__("urllib.error").error.URLError(ConnectionRefusedError(111, "Connection refused"))
        return _Containers._urlopen(silent_web, url, timeout)

    silent_web._urlopen = _only_frontend
    monkeypatch.setattr(orchestrator, "_compose_smoke_step", silent_web)
    ok, detail = await orchestrator._run_app_starts_smoke(_stack(tmp_path, roles))
    assert ok is False
    assert detail.startswith(f"frontend 'web' not serving within {orchestrator.ACCEPTANCE_SMOKE_READY_TIMEOUT}s")


def test_a_passing_check_names_the_web_among_those_that_answered(tmp_path):
    roles = {"backend": "backend", "frontend": "frontend", "db": "db", "web": "web"}
    states = {"backend": ("running", 0), "frontend": ("running", 0), "web": ("running", 0), "migrate": ("exited", 0)}

    said = orchestrator._boot_pass_detail(_stack(tmp_path, roles), states)

    assert said == "Aplikácia nabehla a odpovedá. Odpovedali: backend, frontend, web. Dobehli čisto: migrate."


@pytest.mark.parametrize(
    ("roles", "told"),
    [
        ({"backend": "backend", "frontend": "frontend", "db": "db", "web": "web"}, "frontend web"),
        ({"backend": "backend", "frontend": "frontend", "db": "db"}, "frontend"),
    ],
)
async def test_the_acceptance_script_is_told_every_frontend(monkeypatch, tmp_path, roles, told):
    (tmp_path / "kariera").mkdir()
    (tmp_path / "kariera" / "docker-compose.yml").write_text(WEB_COMPOSE)
    (tmp_path / "kariera" / "release_smoke_test.sh").write_text("#!/bin/sh\n")
    monkeypatch.setattr(claude_agent, "PROJECTS_ROOT", tmp_path)
    seen: dict[str, str] = {}

    async def _script(script, env):
        seen.update(env)
        return 0, "ASSERTIONS_RUN=1\nFEATURE_ASSERTIONS_RUN=1\n"

    monkeypatch.setattr(orchestrator, "_run_acceptance_script", _script)

    await orchestrator._run_release_acceptance(_stack(tmp_path, roles), "kariera", (1, 0))

    assert (seen["SMOKE_FRONTEND"], seen["SMOKE_FRONTENDS"]) == ("frontend", told)
