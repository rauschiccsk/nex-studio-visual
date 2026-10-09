"""DEV-42 — a project can be reachable only from the private network: its installations and its Vizuál preview get
names in ``*.int.isnex.eu`` (ANDROS in Tailscale) and no public name, and every deploy checks nobody else gets in.

09.10.2026, specification of Career Asistent (personal data of candidates): „prístup len z našej privátnej siete".
Measured the same day: every installation and preview the cockpit brings up is published on the public
``*.isnex.eu`` — the NEX Inbox preview answered from Switzerland, Moldova and Vietnam (check-host.net). Director:
„Áno, súhlasím s plánom DEV-42".

Pinned here:
* one place decides the zone; installations (UAT and PROD), their links and the preview all take it from there;
* a private project's compose names only private hosts — never a public router;
* the deploy runner hands the option to the provisioner and checks the installation afterwards; problems reach
  the Manažér as deploy warnings; a public project is not checked and stays as before;
* the check itself: a public router, a name outside Tailscale and a server that lets strangers in are each said;
* a preview running under the other zone's name is recreated, not reused;
* the option is set at creation, changed in the settings and offered by the new-project form (off).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import yaml

from backend.services import deploy as deploy_service
from backend.services import orchestrator, private_access, uat_provisioner, vizual_sandbox
from backend.services.uat_provisioner import _instance_naming, build_uat_compose, domain_suffix

SOURCE = {
    "services": {
        "web": {"image": "app-web:latest", "ports": ["3000:80"]},
        "backend": {"image": "app-be:latest", "ports": ["8000:8000"], "environment": {"POSTGRES_PASSWORD": "x"}},
        "db": {"image": "postgres:16", "environment": {"POSTGRES_PASSWORD": "x"}},
    }
}


def _compose(environment: str, *, private: bool) -> dict:
    return build_uat_compose(
        slug="icc-uat" if environment == "uat" else "icc-prod",
        project="career-asistent",
        project_path=Path("/opt/projects/career-asistent"),
        source=SOURCE,
        roles={"frontend": "web", "backend": "backend", "db": "db"},
        db_user="app",
        db_name="app",
        environment=environment,
        customer_slug="icc",
        app="career-asistent",
        private=private,
    )


# ── one zone decision, three users of it ─────────────────────────────────────────────────────────────────────────


def test_one_place_decides_the_zone():
    assert domain_suffix(True) == "int.isnex.eu"
    assert domain_suffix(False) == "isnex.eu"


@pytest.mark.parametrize(
    ("environment", "private", "host"),
    [
        ("uat", True, "uat-icc-career-asistent.int.isnex.eu"),
        ("prod", True, "icc-career-asistent.int.isnex.eu"),
        ("uat", False, "uat-icc-career-asistent.isnex.eu"),
        ("prod", False, "icc-career-asistent.isnex.eu"),
    ],
)
def test_installations_are_named_in_the_projects_zone(environment, private, host):
    assert _instance_naming(environment, "x", "icc", "career-asistent", private=private)[1] == host


@pytest.mark.parametrize("environment", ["uat", "prod"])
def test_a_private_installation_has_no_public_router(environment):
    compose = _compose(environment, private=True)

    assert private_access.public_hosts(compose) == []
    assert private_access._router_hosts(compose)  # it does have routers — all of them private


def test_a_public_installation_keeps_its_public_routers():
    assert private_access.public_hosts(_compose("uat", private=False)) == ["uat-icc-career-asistent.isnex.eu"]


def test_the_links_on_the_deploy_screen_follow_the_project():
    class _Customer:
        subdomain = None
        slug = "icc"

    class _Project:
        slug = "career-asistent"
        private_network = True

    assert (
        deploy_service._instance_url(_Customer(), "uat", _Project()) == "https://uat-icc-career-asistent.int.isnex.eu"
    )
    assert deploy_service._instance_url(_Customer(), "prod", _Project()) == "https://icc-career-asistent.int.isnex.eu"
    _Project.private_network = False
    assert deploy_service._instance_url(_Customer(), "prod", _Project()) == "https://icc-career-asistent.isnex.eu"


# ── the deploy runner: hand the option over, check afterwards ────────────────────────────────────────────────────


def _runner(monkeypatch, *, private: bool, problems=None, uat_slug="icc-uat", deploy_host=None):
    captured: dict = {"check": []}

    class _Result:
        warnings: list[str] = []
        fe_service = "web"
        compose_path = Path("/opt/uat/icc/career-asistent/docker-compose.yml")

    def _fake_provision(project_slug, uat_slug, *, version, rotate_secrets, **kw):
        captured["provision"] = kw
        return _Result()

    async def _fake_run_uat(project_slug, uat_slug, **kw):
        return True, "OK"

    async def _fake_run_prod(
        project_slug, customer_slug, app, full_project_slug, version_number=None, deploy_host=None
    ):
        return True, "OK"

    def _fake_check(compose_path, host):
        captured["check"].append((compose_path, host))
        return list(problems or [])

    monkeypatch.setattr(uat_provisioner, "provision_uat", _fake_provision)
    monkeypatch.setattr(orchestrator, "_run_uat_deploy", _fake_run_uat)
    monkeypatch.setattr(orchestrator, "_run_prod_deploy", _fake_run_prod)
    monkeypatch.setattr(private_access, "check", _fake_check)
    outcome = asyncio.run(
        deploy_service._default_deploy_runner(
            project_slug="career-asistent",
            uat_slug=uat_slug,
            version_number="v0.1.0",
            force_fresh=False,
            deploy_host=deploy_host,
            private_network=private,
        )
    )
    return outcome, captured


def test_a_private_deploy_is_provisioned_private_and_checked(monkeypatch):
    outcome, captured = _runner(monkeypatch, private=True)

    ok, _detail, url = outcome
    assert captured["provision"]["private"] is True
    assert url == "https://uat-icc-career-asistent.int.isnex.eu"
    assert captured["check"] == [
        (Path("/opt/uat/icc/career-asistent/docker-compose.yml"), "uat-icc-career-asistent.int.isnex.eu")
    ]
    assert outcome.warnings == []


def test_what_the_check_finds_reaches_the_manager_as_a_deploy_warning(monkeypatch):
    problem = "Server pustil uat-icc-career-asistent.int.isnex.eu aj mimo Tailscale (odpoveď 200, mala byť 403)."
    outcome, _ = _runner(monkeypatch, private=True, problems=[problem])

    ok, _detail, _url = outcome
    assert ok is True  # the app runs; what is wrong is who can reach it — said, not hidden
    assert outcome.warnings == [problem]


def test_a_public_deploy_is_not_checked_and_stays_as_before(monkeypatch):
    outcome, captured = _runner(monkeypatch, private=False)

    assert captured["provision"]["private"] is False
    _ok, _detail, url = outcome
    assert captured["check"] == []
    assert url == "https://uat-icc-career-asistent.isnex.eu"


def test_a_prod_on_another_machine_is_routed_by_that_machine(monkeypatch):
    _, captured = _runner(monkeypatch, private=True, uat_slug="icc-prod", deploy_host="mager")

    assert captured["check"] == []


# (That the deploy hands the project's option to the runner: tests/test_deploy_service.py, DEV-42 section.)


# ── the check itself ─────────────────────────────────────────────────────────────────────────────────────────────


def _write(tmp_path: Path, compose: dict) -> Path:
    path = tmp_path / "docker-compose.yml"
    path.write_text(yaml.safe_dump(compose), encoding="utf-8")
    return path


def _net(monkeypatch, *, addresses=("100.107.134.104",), status=403):
    monkeypatch.setattr(
        private_access.socket, "getaddrinfo", lambda host, port, proto=0: [(0, 0, 0, "", (a, port)) for a in addresses]
    )
    calls = []

    def _status(host, via):
        calls.append((host, via))
        return status

    monkeypatch.setattr(private_access, "status_from_outside", _status)
    return calls


HOST = "uat-icc-career-asistent.int.isnex.eu"


def test_a_private_installation_passes(tmp_path, monkeypatch):
    calls = _net(monkeypatch)

    assert private_access.check(_write(tmp_path, _compose("uat", private=True)), HOST, via="192.168.32.1") == []
    assert calls == [(HOST, "192.168.32.1")]


def test_a_public_router_is_said(tmp_path, monkeypatch):
    _net(monkeypatch)
    compose = _compose("uat", private=True)
    compose["services"]["web"]["labels"].append("traefik.http.routers.leak.rule=Host(`career.isnex.eu`)")

    problems = private_access.check(_write(tmp_path, compose), HOST, via="192.168.32.1")

    assert problems == ["Inštalácia má aj verejné meno career.isnex.eu — mala byť len v súkromnej sieti."]


def test_a_name_pointing_outside_tailscale_is_said(tmp_path, monkeypatch):
    _net(monkeypatch, addresses=("100.107.134.104", "104.21.95.241"))

    problems = private_access.check(_write(tmp_path, _compose("uat", private=True)), HOST, via="192.168.32.1")

    assert problems == [f"Meno {HOST} ukazuje mimo Tailscale (104.21.95.241)."]


def test_a_server_that_lets_strangers_in_is_said(tmp_path, monkeypatch):
    _net(monkeypatch, status=200)

    problems = private_access.check(_write(tmp_path, _compose("uat", private=True)), HOST, via="192.168.32.1")

    assert problems == [f"Server pustil {HOST} aj mimo Tailscale (odpoveď 200, mala byť 403)."]


def test_a_check_that_cannot_ask_the_server_never_passes_silently(tmp_path, monkeypatch):
    _net(monkeypatch)

    def _boom(host, via):
        raise ConnectionRefusedError

    monkeypatch.setattr(private_access, "status_from_outside", _boom)

    problems = private_access.check(_write(tmp_path, _compose("uat", private=True)), HOST, via="192.168.32.1")

    assert problems == [f"Nepodarilo sa overiť, či server púšťa {HOST} len z Tailscale (ConnectionRefusedError)."]


def test_dict_shaped_labels_are_read_too():
    compose = {
        "services": {
            "web": {"labels": {"traefik.http.routers.web.rule": "Host(`a.isnex.eu`) || Host(`b.int.isnex.eu`)"}}
        }
    }

    assert private_access.public_hosts(compose) == ["a.isnex.eu"]


# ── the Vizuál preview ───────────────────────────────────────────────────────────────────────────────────────────


def test_the_preview_of_a_private_project_is_named_in_the_private_zone(tmp_path):
    labels = vizual_sandbox._traefik_labels("career-asistent", private=True)
    argv = vizual_sandbox.build_run_argv(slug="career-asistent", frontend_host_path=tmp_path, private=True)

    assert "traefik.http.routers.vizual-career-asistent.rule=Host(`vizual-career-asistent.int.isnex.eu`)" in labels
    assert "VIZUAL_PUBLIC_HOST=vizual-career-asistent.int.isnex.eu" in argv
    assert not [a for a in argv if "vizual-career-asistent.isnex.eu" in a]


def _preview(monkeypatch, tmp_path, *, running_host):
    calls: list = []
    state = {"running": running_host is not None}
    monkeypatch.setattr(vizual_sandbox, "_resolve_frontend_path", lambda slug, fp: tmp_path)
    monkeypatch.setattr(
        vizual_sandbox, "_inspect_state", lambda name: {"exists": state["running"] or bool(calls), "running": True}
    )
    monkeypatch.setattr(vizual_sandbox, "_running_public_host", lambda name: running_host)
    monkeypatch.setattr(vizual_sandbox, "_force_remove", lambda name: calls.append(("rm", name)))
    monkeypatch.setattr(vizual_sandbox, "_ensure_node_modules", lambda path, slug: None)
    monkeypatch.setattr(vizual_sandbox, "_write_override_config", lambda path: path)

    class _Proc:
        returncode = 0
        stderr = ""

    def _run(argv, **kw):
        calls.append(("run", [a for a in argv if a.startswith("VIZUAL_PUBLIC_HOST=")]))
        return _Proc()

    monkeypatch.setattr(vizual_sandbox.subprocess, "run", _run)
    url = vizual_sandbox.spin_up("career-asistent", private=True)
    return url, calls


def test_a_preview_running_under_its_public_name_is_recreated_private(monkeypatch, tmp_path):
    url, calls = _preview(monkeypatch, tmp_path, running_host="vizual-career-asistent.isnex.eu")

    assert url == "https://vizual-career-asistent.int.isnex.eu"
    assert calls == [
        ("rm", "vizual-career-asistent"),
        ("run", ["VIZUAL_PUBLIC_HOST=vizual-career-asistent.int.isnex.eu"]),
    ]


def test_a_preview_already_running_under_the_right_name_is_reused(monkeypatch, tmp_path):
    url, calls = _preview(monkeypatch, tmp_path, running_host="vizual-career-asistent.int.isnex.eu")

    assert url == "https://vizual-career-asistent.int.isnex.eu"
    assert calls == []


async def test_the_vizual_round_previews_a_private_project_in_the_private_zone(db_session, monkeypatch):
    from tests.test_orchestrator_v2_vizual import _make_version, _msgs, _patch_spin_up, _seed_drawn, _seed_state

    version, project = _make_version(db_session)
    project.private_network = True
    _seed_state(db_session, version.id, stage="vizual", actor="ai_agent")
    _seed_drawn(db_session, version.id)
    calls: dict = {}
    _patch_spin_up(monkeypatch, calls)

    await orchestrator.run_dispatch(db_session, version.id)

    assert calls == {"slug": project.slug, "private": True}
    [note] = [m for m in _msgs(db_session, version.id) if m.payload and m.payload.get("vizual_url")]
    assert note.payload["vizual_url"] == f"https://vizual-{project.slug}.int.isnex.eu"
