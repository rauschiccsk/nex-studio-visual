"""DEV-68 — a deploy to another machine talks only to that machine and never leaves its files behind its containers.

11.10.2026, NEX Inbox 1.7.1 on the customer's PROD (MAGER, reached over ``DOCKER_HOST=ssh://mager``): the new
containers ran and the database was migrated at 21:53, when the last check — the public route — failed with „service
"backend" is not running“. That check ran ``docker compose exec`` on THIS machine (no target), for a name the cockpit
composed itself (``mager-inbox.isnex.eu``) through this machine's Traefik. The deploy was then recorded as failed and
the compose from before the deploy was put back on MAGER: containers 1.7.1 under files 1.5.6. The build itself ran
only after the running backend had been stopped (one ``up --build`` step), so the customer was without the
application for 20 minutes.
"""

from __future__ import annotations

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
import yaml

from backend.services import deploy as deploy_service
from backend.services import deploy_progress, orchestrator, public_route, remote_instance
from backend.services import uat_provisioner as P
from backend.tests import _fake_backup_docker as fake_backup

#: The routers of the live installation on MAGER, as its compose has them (11.10.2026).
MAGER_FRONTEND_LABELS = [
    "traefik.enable=true",
    "traefik.docker.network=nex-ts-net",
    "traefik.http.services.mager-inbox.loadbalancer.server.port=80",
    "traefik.http.routers.mager-inbox-ts.rule=Host(`mager.tail5c98e2.ts.net`)",
    "traefik.http.routers.mager-inbox-ts.tls.certresolver=tailscale",
    "traefik.http.routers.mager-inbox-ts.entrypoints=mager-inbox",
    "traefik.http.routers.mager-inbox-sec.rule=Host(`magerstav-inbox.icc.sk`)",
    "traefik.http.routers.mager-inbox-sec.entrypoints=websecure",
    "traefik.http.routers.mager-inbox-sec.tls.certresolver=cf",
]
MAGER_BACKEND_LABELS = [
    "traefik.enable=true",
    "traefik.http.routers.mager-inbox-sec-api.rule=Host(`magerstav-inbox.icc.sk`) && PathPrefix(`/api`)",
    "traefik.http.routers.mager-inbox-sec-api.entrypoints=websecure",
]


# --- which addresses an installation publishes -------------------------------------------------------------------


def test_the_addresses_are_the_installations_own_routers():
    compose = {
        "services": {
            "backend": {"labels": MAGER_BACKEND_LABELS},
            "frontend": {"labels": MAGER_FRONTEND_LABELS},
        }
    }

    assert public_route.route_urls(compose) == ["https://magerstav-inbox.icc.sk/"]


def test_the_cockpits_own_routers_are_asked_over_http_and_the_other_forms_are_read_too():
    rendered = {
        "services": {
            "frontend": {"labels": P.frontend_traefik_labels("andros-shop", 80, "andros-shop.isnex.eu")},
            # an API on an address of its own is not the page a person opens
            "backend": {"labels": P.backend_traefik_labels("andros-shop", 8000, "api.andros-shop.isnex.eu")},
        }
    }
    as_dict = {
        "services": {
            "web": {
                "labels": {
                    "traefik.http.routers.a.rule": "Host(`a.example.com`)",
                    "traefik.http.routers.a.tls": "true",
                    "traefik.http.routers.b.rule": "Host(`b.example.com`)",
                }
            }
        }
    }

    assert public_route.route_urls(rendered) == ["http://andros-shop.isnex.eu/"]
    assert public_route.route_urls(as_dict) == ["http://b.example.com/", "https://a.example.com/"]


class _Status(BaseHTTPRequestHandler):
    codes: dict = {}

    def do_GET(self):  # noqa: N802 — the name http.server calls
        self.send_response(self.codes.get(self.path, 200))
        self.end_headers()
        self.wfile.write(b"x")

    def log_message(self, *_args):
        pass


@pytest.fixture
def server():
    handler = type("_S", (_Status,), {"codes": {"/missing": 404, "/broken": 502, "/login": 401}})
    httpd = HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def test_an_answer_of_the_app_is_a_route_and_traefiks_404_is_none(server):
    assert public_route.probe(f"{server}/")[0] == public_route.STATE_OK
    assert public_route.probe(f"{server}/login")[0] == public_route.STATE_OK, "the app answering 401 is a route"
    assert public_route.probe(f"{server}/missing")[0] == public_route.STATE_DOWN
    assert public_route.probe(f"{server}/broken")[0] == public_route.STATE_DOWN
    assert public_route.probe("http://127.0.0.1:9/")[0] == public_route.STATE_SKIP, "no answer says nothing"


def test_one_broken_address_breaks_the_route_and_none_is_no_claim(server):
    assert public_route.check([f"{server}/", f"{server}/missing"])[0] == public_route.STATE_DOWN
    assert public_route.check([f"{server}/", f"{server}/login"])[0] == public_route.STATE_OK
    assert public_route.check([f"{server}/", "http://127.0.0.1:9/"])[0] == public_route.STATE_SKIP
    assert public_route.check([])[0] == public_route.STATE_SKIP


# --- a deploy to another machine ----------------------------------------------------------------------------------

SOURCE = {
    "services": {
        "db": {"image": "postgres:16-alpine"},
        "backend": {"build": {"context": "./backend"}, "image": "nex-inbox-backend:v1.7.1", "ports": ["8000"]},
        "frontend": {"build": {"context": "./frontend"}, "image": "nex-inbox-frontend:v1.7.1", "ports": ["80"]},
    }
}


def _instance(tmp_path, monkeypatch) -> Path:
    projects = tmp_path / "projects"
    (projects / "nex-inbox" / "backend").mkdir(parents=True)
    (projects / "nex-inbox" / "docker-compose.yml").write_text(yaml.safe_dump(SOURCE), encoding="utf-8")
    # An app that migrates after ``up`` (no migrate service of its own) — the migration step runs too.
    (projects / "nex-inbox" / "backend" / "alembic.ini").write_text("[alembic]\n", encoding="utf-8")
    monkeypatch.setattr(orchestrator.claude_agent, "PROJECTS_ROOT", projects)

    instance = tmp_path / "customers" / "mager" / "nex-inbox"
    instance.mkdir(parents=True)
    compose = instance / "docker-compose.yml"
    compose.write_text(
        yaml.safe_dump(
            {
                "name": "mager-inbox",
                "services": {
                    "postgres": {"image": "postgres:16-alpine"},
                    "backend": {"image": "nex-inbox-backend:v1.7.1", "labels": MAGER_BACKEND_LABELS},
                    "frontend": {"image": "nex-inbox-frontend:v1.7.1", "labels": MAGER_FRONTEND_LABELS},
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(orchestrator, "_uat_compose_path", lambda *a, **k: compose)
    monkeypatch.setattr(orchestrator, "ACCEPTANCE_SMOKE_READY_INTERVAL", 1)
    monkeypatch.setattr(orchestrator, "ACCEPTANCE_SMOKE_READY_TIMEOUT", 2)
    return compose


class _Proc:
    def __init__(self, rc: int, out: str = ""):
        self.returncode, self._out = rc, out.encode()

    async def communicate(self):
        return self._out, b""

    def kill(self):
        pass


def _docker(monkeypatch, compose: Path, fail_on: str | None = None):
    """Every command a deploy spawns, with its environment and the deploy step it ran in."""
    events: list[tuple] = []
    step = {"now": None}
    real_krok = deploy_progress.krok

    def _krok(instance_dir, krok):
        step["now"] = krok
        real_krok(instance_dir, krok)

    async def _spawn(*cmd, stdin=None, stdout=None, stderr=None, env=None):
        cmd = list(cmd)
        events.append((cmd, env, step["now"]))
        if fail_on and fail_on in cmd:
            return _Proc(1, f"{fail_on} failed")
        backup = fake_backup.reply(cmd)  # DEV-116: the database backup between the build and ``up``
        if backup is not None:
            return _Proc(0, backup)
        if cmd[-1] == "/target/docker-compose.yml":  # the compose read back from the target
            return _Proc(0, compose.read_text(encoding="utf-8"))
        return _Proc(0, "port 8000: 200")

    monkeypatch.setattr(orchestrator.deploy_progress, "krok", _krok)
    monkeypatch.setattr(orchestrator.asyncio, "create_subprocess_exec", _spawn)
    return events


def _asked_urls(monkeypatch) -> list[str]:
    asked: list[str] = []

    def _probe(url):
        asked.append(url)
        return public_route.STATE_OK, f"{url} 200"

    monkeypatch.setattr(public_route, "probe", _probe)
    return asked


def _deploy_to_mager():
    return asyncio.run(
        orchestrator._run_prod_deploy(
            "nex-inbox", "mager", "inbox", "nex-inbox", version_number="1.7.1", deploy_host="mager"
        )
    )


def test_every_docker_command_of_a_deploy_elsewhere_goes_to_that_machine(tmp_path, monkeypatch):
    """The rule, not a list: whatever step spawns Docker during a deploy to MAGER, it must carry MAGER."""
    compose = _instance(tmp_path, monkeypatch)
    events = _docker(monkeypatch, compose)
    asked = _asked_urls(monkeypatch)

    result = _deploy_to_mager()

    docker = [(cmd, env) for cmd, env, _step in events if cmd[0] == "docker"]
    elsewhere = [" ".join(cmd[:8]) for cmd, env in docker if (env or {}).get("DOCKER_HOST") != "ssh://mager"]
    assert elsewhere == [], "these Docker commands went to this machine instead of MAGER"
    kinds = {cmd[4] if cmd[1] == "compose" else cmd[1] for cmd, _env in docker}
    assert {"run", "build", "up", "exec", "builder", "image"} <= kinds, f"the deploy did not run every step: {kinds}"
    assert any("alembic" in cmd for cmd, _env in docker), "the migration after up did not run"
    assert asked == ["https://magerstav-inbox.icc.sk/"], "the public route asked was not the installation's own"
    assert tuple(result) == (True, result[1]) and result.new_version_started is True


def test_the_build_runs_before_anything_is_replaced(tmp_path, monkeypatch):
    compose = _instance(tmp_path, monkeypatch)
    events = _docker(monkeypatch, compose)
    _asked_urls(monkeypatch)

    _deploy_to_mager()

    compose_steps = [(cmd[4], step) for cmd, _env, step in events if cmd[:2] == ["docker", "compose"]]
    up_at = [name for name, _step in compose_steps].index("up")
    assert compose_steps[0] == ("build", deploy_progress.KROK_STAVBA)
    assert compose_steps[up_at] == ("up", deploy_progress.KROK_SPUSTENIE)
    # DEV-116: between them only the database backup, while the running version still serves.
    between = compose_steps[1:up_at]
    assert between and {step for _name, step in between} == {deploy_progress.KROK_ZALOHA}
    up = next(cmd for cmd, _env, _step in events if cmd[:2] == ["docker", "compose"] and cmd[4] == "up")
    assert "--build" not in up, "up must not build: the build already ran while the installation was untouched"


@pytest.mark.parametrize(
    "fail_on, started",
    [("build", False), ("up", True), ("alembic", True)],
)
def test_the_deploy_says_whether_the_new_version_was_started(tmp_path, monkeypatch, fail_on, started):
    compose = _instance(tmp_path, monkeypatch)
    _docker(monkeypatch, compose, fail_on=fail_on)
    _asked_urls(monkeypatch)
    monkeypatch.setattr(orchestrator, "MIGRATE_ATTEMPTS", 1)

    result = _deploy_to_mager()

    assert result[0] is False
    assert result.new_version_started is started


def test_a_route_that_fails_after_the_start_is_a_failure_of_a_started_version(tmp_path, monkeypatch):
    compose = _instance(tmp_path, monkeypatch)
    _docker(monkeypatch, compose)
    monkeypatch.setattr(public_route, "probe", lambda url: (public_route.STATE_DOWN, f"{url} 404"))

    result = _deploy_to_mager()

    assert result[0] is False
    route, _, backup = result[1].partition(" Záloha databázy pred nasadením: ")
    assert route == (
        "appka beží, ale verejná adresa https://magerstav-inbox.icc.sk/ nie je dostupná — smerovanie zlyhalo: "
        "https://magerstav-inbox.icc.sk/ 404"
    )
    # DEV-116: a failure after the start is exactly when the backup is needed — the report says where it lies.
    assert backup.startswith(f"{compose.parent}/pre-deploy-backups/nex-inbox-1.7.1-"), result[1]
    assert result.new_version_started is True


# --- the files on the target after a failure ---------------------------------------------------------------------


class _Provisioned:
    warnings: list[str] = []
    fe_service = "frontend"
    uat_dir = Path("/opt/customers/mager/nex-inbox")
    compose_path = Path("/nonexistent/docker-compose.yml")
    previous_remote_files = {"docker-compose.yml": ("image: nex-inbox-backend:v1.5.6\n", 0o644)}


def _runner(monkeypatch, outcome):
    written: list = []

    async def _prod(*_a, **_k):
        return outcome

    monkeypatch.setattr(P, "provision_uat", lambda *a, **k: _Provisioned())
    monkeypatch.setattr(orchestrator, "_run_prod_deploy", _prod)
    monkeypatch.setattr(remote_instance, "write_files", lambda d, files, deploy_host: written.append(files) or None)
    result = asyncio.run(
        deploy_service._default_deploy_runner(
            project_slug="nex-inbox",
            uat_slug="mager-prod",
            version_number="1.7.1",
            force_fresh=False,
            deploy_host="mager",
        )
    )
    return result, written


def test_files_stay_new_when_the_new_version_already_runs(monkeypatch):
    outcome = orchestrator.InstanceDeployResult(False, "smerovanie zlyhalo", new_version_started=True)

    result, written = _runner(monkeypatch, outcome)
    ok, detail, _url = result

    assert ok is False
    assert result.new_version_started is True, "the deploy record must learn that the new version runs"
    assert written == [], "the old compose was put back under containers of the new version"
    assert detail == (
        "smerovanie zlyhalo Nová verzia na cieli už beží, preto predpis ostal na nej — návrat na predchádzajúci by "
        "inštaláciu rozladil (kontajnery novej verzie, predpis starej). Skontroluj aplikáciu skôr, než nasadíš znovu."
    )


def test_files_go_back_when_the_new_version_never_started(monkeypatch):
    outcome = orchestrator.InstanceDeployResult(False, "exit 1: build failed")

    result, written = _runner(monkeypatch, outcome)
    ok, detail, _url = result

    assert ok is False
    assert result.new_version_started is False
    assert written == [_Provisioned.previous_remote_files]
    assert detail == "exit 1: build failed Predpis na cieli vrátený do stavu pred nasadením."


def test_the_result_still_unpacks_as_a_pair():
    ok, detail = orchestrator.InstanceDeployResult(True, "OK", new_version_started=True)
    assert (ok, detail) == (True, "OK")
    assert json.dumps(list(orchestrator.InstanceDeployResult(False, "x"))) == '[false, "x"]'
