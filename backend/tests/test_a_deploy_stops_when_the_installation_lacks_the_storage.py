"""DEV-67 — a deploy never leaves the application writing where the installation keeps no storage, and a deploy
after which the application reports a problem it did not have before says so.

11.10.2026, NEX Inbox 1.7.1 on the customer's UAT (MAGERSTAV): version 1.7.0 moved the Genesis share under a parent
folder — the project mounts storage at ``/var/lib/inbox/genesis/inbox`` and points ``SMB_MOUNT_DIR`` there. The
installation still mounted the share at ``/var/lib/inbox/genesis-out``. The redeploy added the variable (ICCINT-104),
reported „Nasadené“, and the application wrote its invoices into its own container for hours while its health check
said the share was not working. Nothing in the cockpit compared the new version's storage with the installation's,
and nothing looked at the health the application reported after the start.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
import yaml

from backend.services import deploy as deploy_service
from backend.services import orchestrator
from backend.services import uat_provisioner as P

#: The real function — the suite's ``_isolate_instance_health`` replaces the module attribute during every test.
REAL_INSTANCE_HEALTH = orchestrator.instance_health

SHARE = "/var/lib/inbox/genesis/inbox"
OLD_SHARE_MOUNT = "/mnt/mager-edocs-inbox-uat:/var/lib/inbox/genesis-out"
PARENT_MOUNT = "/mnt/mager-uat-genesis:/var/lib/inbox/genesis:rslave"

#: The project as NEX Inbox 1.7.1 declares it (shape of ``/opt/projects/nex-inbox/docker-compose.yml``).
SOURCE = {
    "services": {
        "db": {"image": "postgres:16-alpine", "volumes": ["inbox_dev_pg_data:/var/lib/postgresql/data"]},
        "backend": {
            "build": {"context": "./backend"},
            "image": "nex-inbox-backend:v1.7.1",
            "environment": {
                "SMB_MOUNT_DIR": SHARE,
                "SMB_ARCHIVE_DIR": "/var/lib/inbox/genesis/inbox/${TENANT_SLUG}",
                "CLAUDE_CONFIG_DIR": "/home/inbox/.claude",
                "ORIGINALS_DIR": "/var/lib/inbox/originals",
                "LOG_DIR": "/tmp/inbox-logs",
            },
            "volumes": [
                f"inbox_dev_genesis:{SHARE}",
                "./claude-config:/home/inbox/.claude:ro",
                "inbox_dev_originals:/var/lib/inbox/originals",
            ],
        },
        "frontend": {"build": {"context": "./frontend"}, "image": "nex-inbox-frontend:v1.7.1"},
    },
    "volumes": {"inbox_dev_pg_data": None, "inbox_dev_genesis": None, "inbox_dev_originals": None},
}


def _installation(genesis_mount, environment=None) -> dict:
    backend = {"image": "nex-inbox-backend:v1.6.0", "volumes": ["./originals:/var/lib/inbox/originals", genesis_mount]}
    if environment is not None:
        backend["environment"] = environment
    return {
        "name": "uat-mager-inbox",
        "services": {
            "postgres": {"image": "postgres:16-alpine", "volumes": ["postgres-data:/var/lib/postgresql/data"]},
            "backend": backend,
            "frontend": {"image": "nex-inbox-frontend:v1.6.0"},
        },
    }


# --- which variable points where the installation keeps nothing ----------------------------------------------------


def test_the_share_the_version_moved_is_found_where_the_installation_did_not_follow():
    found = P.paths_without_storage(_installation(OLD_SHARE_MOUNT), SOURCE, {})

    assert found == [
        P.PathWithoutStorage(
            "backend", "SMB_MOUNT_DIR", SHARE, ("/var/lib/inbox/originals", "/var/lib/inbox/genesis-out")
        )
    ]


def test_the_parent_folder_of_1_7_is_storage_for_the_share():
    assert P.paths_without_storage(_installation(PARENT_MOUNT), SOURCE, {}) == []


def test_a_read_only_mount_of_the_project_is_configuration_not_storage():
    """Measured on all seven installations the cockpit deploys: the project mounts its Claude settings read-only and
    the installations pass a token instead. Counting that mount as storage would stop every NEX Inbox deploy."""
    source = {
        "services": {
            "backend": {
                "image": "nex-inbox-backend:v1.7.1",
                "environment": {"CLAUDE_CONFIG_DIR": "/home/inbox/.claude", "PROMPTS_DIR": "/srv/prompts"},
                "volumes": [
                    "./claude-config:/home/inbox/.claude:ro",
                    {"type": "bind", "source": "./prompts", "target": "/srv/prompts", "read_only": True},
                ],
            }
        }
    }
    installation = {"services": {"backend": {"image": "nex-inbox-backend:v1.6.0"}}}

    assert P.paths_without_storage(installation, source, {}) == []


def test_the_value_the_installation_runs_with_is_judged():
    # The installation keeps its own value — the redeploy never overwrites it (ICCINT-104), so it is what runs.
    own_value = _installation(OLD_SHARE_MOUNT, environment={"SMB_MOUNT_DIR": "/var/lib/inbox/genesis-out"})
    assert P.paths_without_storage(own_value, SOURCE, {}) == []

    # A value from the installation's .env reaches the service through env_file — it is the one that runs, not the
    # project's.
    env_file = {"SMB_MOUNT_DIR": "/var/lib/inbox/genesis-out"}
    assert P.paths_without_storage(_installation(OLD_SHARE_MOUNT), SOURCE, env_file) == []
    env_file = {"SMB_MOUNT_DIR": "/var/lib/inbox/genesis/inbox/mager"}
    from_env_file = P.paths_without_storage(_installation(OLD_SHARE_MOUNT), SOURCE, env_file)
    assert [(f.key, f.value) for f in from_env_file] == [("SMB_MOUNT_DIR", "/var/lib/inbox/genesis/inbox/mager")]

    # The compose environment wins over the .env, as in Docker Compose.
    both = _installation(OLD_SHARE_MOUNT, environment=["SMB_MOUNT_DIR=/var/lib/inbox/genesis-out"])
    assert P.paths_without_storage(both, SOURCE, {"SMB_MOUNT_DIR": SHARE}) == []


def test_a_long_form_volume_of_the_installation_counts():
    long_form = {"type": "bind", "source": "/mnt/mager-uat-genesis", "target": "/var/lib/inbox/genesis/"}

    assert P.paths_without_storage(_installation(long_form), SOURCE, {}) == []


# --- the deploy stops before it writes ----------------------------------------------------------------------------


def _project_and_installation(tmp_path: Path, genesis_mount) -> tuple[Path, Path, Path]:
    projects = tmp_path / "projects"
    (projects / "nex-inbox").mkdir(parents=True)
    (projects / "nex-inbox" / "docker-compose.yml").write_text(yaml.safe_dump(SOURCE), encoding="utf-8")
    uat_root = tmp_path / "uat"
    installation = uat_root / "skuska" / "nex-inbox"
    installation.mkdir(parents=True)
    compose = f"# {P.GENERATED_BY_MARKER}\n" + yaml.safe_dump(_installation(genesis_mount), sort_keys=False)
    (installation / "docker-compose.yml").write_text(compose, encoding="utf-8")
    (installation / ".env").write_text("POSTGRES_DB=x\nPOSTGRES_PASSWORD=y\nPROJECT_VERSION=1.6.0\n", encoding="utf-8")
    return projects, uat_root, installation


def _redeploy(projects: Path, uat_root: Path):
    return P.provision_uat(
        "nex-inbox",
        "skuska-uat",
        version="1.7.1",
        projects_root=projects,
        uat_root=uat_root,
        customer_slug="skuska",
        app="nex-inbox",
        full_project_slug="nex-inbox",
    )


def test_a_redeploy_onto_an_installation_without_the_storage_stops_and_writes_nothing(tmp_path, monkeypatch):
    projects, uat_root, installation = _project_and_installation(tmp_path, OLD_SHARE_MOUNT)
    compose_before = (installation / "docker-compose.yml").read_text(encoding="utf-8")
    env_before = (installation / ".env").read_text(encoding="utf-8")
    monkeypatch.setattr(P, "_docker_image_exists", lambda image: True)

    with pytest.raises(P.StorageNotMountedError) as refusal:
        _redeploy(projects, uat_root)

    assert str(refusal.value) == (
        "Nasadenie zastavené — inštalácia nie je pripravená na novú verziu: služba backend má podľa novej verzie "
        "ukladať do /var/lib/inbox/genesis/inbox (premenná SMB_MOUNT_DIR), no inštalácia tam priečinok pripojený "
        "nemá (pripojené má: /var/lib/inbox/originals, /var/lib/inbox/genesis-out). Aplikácia by zapisovala mimo "
        "pripojených priečinkov a dáta by nikto nenašiel (napríklad faktúry by sa nedoručovali). Inštaláciu treba "
        "najprv prestaviť podľa postupu novej verzie — to robí technický tím; pošli mu túto vetu. Inštalácia ostala "
        "nedotknutá."
    )
    assert (installation / "docker-compose.yml").read_text(encoding="utf-8") == compose_before, "compose changed"
    assert (installation / ".env").read_text(encoding="utf-8") == env_before, ".env changed"


def test_an_installation_that_followed_the_version_is_deployed(tmp_path, monkeypatch):
    projects, uat_root, installation = _project_and_installation(tmp_path, PARENT_MOUNT)
    monkeypatch.setattr(P, "_docker_image_exists", lambda image: True)

    _redeploy(projects, uat_root)

    written = yaml.safe_load((installation / "docker-compose.yml").read_text(encoding="utf-8"))
    assert written["services"]["backend"]["image"] == "nex-inbox-backend:v1.7.1"
    assert PARENT_MOUNT in written["services"]["backend"]["volumes"]


def test_the_refusal_is_what_the_deploy_screen_shows(monkeypatch):
    def _refuse(*_a, **_k):
        raise P.StorageNotMountedError("Nasadenie zastavené — inštalácia nie je pripravená na novú verziu: …")

    async def _never_up(*_a, **_k):
        raise AssertionError("a refused installation must not be started")

    monkeypatch.setattr(P, "provision_uat", _refuse)
    monkeypatch.setattr(orchestrator, "_run_uat_deploy", _never_up)

    ok, detail, url = asyncio.run(
        deploy_service._default_deploy_runner(
            project_slug="nex-inbox", uat_slug="mager-uat", version_number="1.7.1", force_fresh=False
        )
    )

    assert (ok, detail, url) == (
        False,
        "provision failed: Nasadenie zastavené — inštalácia nie je pripravená na novú verziu: …",
        None,
    )


# --- the health the application reports, before and after -----------------------------------------------------------


class _Health(BaseHTTPRequestHandler):
    body: dict = {}

    def do_GET(self):  # noqa: N802 — the name http.server calls
        data = json.dumps(self.body).encode()
        self.send_response(200 if self.path == "/api/v1/health" else 404)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *_args):
        pass


def _probe(body: dict) -> dict:
    handler = type("_H", (_Health,), {"body": body})
    server = HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        src = orchestrator._health_probe_src(server.server_address[1], "/api/v1/health")
        done = subprocess.run([sys.executable, "-c", src], capture_output=True, text=True, timeout=30, check=True)
    finally:
        server.shutdown()
    return json.loads(done.stdout)


def test_the_probe_reads_the_checks_the_application_reports():
    """The program that runs inside the backend container, run here against an endpoint shaped like NEX Inbox's."""
    reported = _probe(
        {
            "status": "degraded",
            "checks": {
                "database": {"status": "ok", "message": None, "last_check_at": "2026-10-11T17:00:00Z"},
                "smb": {"status": "degraded", "message": "Zdieľanie Genesis nie je pripojené"},
            },
        }
    )

    assert reported == {"database": ["ok", ""], "smb": ["degraded", "Zdieľanie Genesis nie je pripojené"]}
    assert _probe({"status": "ok"}) == {}, "an application without checks has nothing to compare"


def _compose(tmp_path: Path) -> Path:
    path = tmp_path / "docker-compose.yml"
    path.write_text(yaml.safe_dump(_installation(PARENT_MOUNT)), encoding="utf-8")
    return path


def test_the_health_is_asked_from_the_installations_backend_on_its_machine(tmp_path, monkeypatch):
    calls = []

    async def _step(cmd, timeout, env=None):
        calls.append((cmd, env))
        return 0, 'starting\n{"smb": ["ok", ""], "database": ["ok", ""]}\n'

    monkeypatch.setattr(orchestrator, "_compose_smoke_step", _step)
    compose = _compose(tmp_path)

    health = asyncio.run(REAL_INSTANCE_HEALTH(compose, "nex-inbox", "mager"))

    assert health == {"smb": ("ok", ""), "database": ("ok", "")}
    cmd, env = calls[0]
    assert cmd[:9] == ["docker", "compose", "-f", str(compose), "exec", "-T", "backend", "python", "-c"]
    assert env == orchestrator._verify_env_for_target("mager")
    assert env["DOCKER_HOST"] == "ssh://mager", "the health of a PROD on another machine must be asked there"

    asyncio.run(REAL_INSTANCE_HEALTH(compose, "nex-inbox"))
    assert calls[1][1] is None, "an installation here is asked here"


@pytest.mark.parametrize("answer", [(1, "service backend is not running"), (0, "{}"), (0, "Traceback …")])
def test_a_health_that_cannot_be_read_is_none(tmp_path, monkeypatch, answer):
    async def _step(cmd, timeout, env=None):
        return answer

    monkeypatch.setattr(orchestrator, "_compose_smoke_step", _step)

    assert asyncio.run(REAL_INSTANCE_HEALTH(_compose(tmp_path), "nex-inbox")) is None


def _answers(monkeypatch, *answers):
    asked = []

    async def _health(compose, project_slug, deploy_host=None):
        asked.append(deploy_host)
        return answers[min(len(asked), len(answers)) - 1]

    monkeypatch.setattr(orchestrator, "instance_health", _health)
    monkeypatch.setattr(orchestrator, "HEALTH_SETTLE_INTERVAL", 0)
    return asked


BEFORE = {"database": ("ok", ""), "smb": ("ok", ""), "imap": ("degraded", "no successful poll yet")}


def test_a_check_the_deploy_broke_is_reported_once_it_stays_broken(monkeypatch):
    after = {
        "database": ("ok", ""),
        "smb": ("degraded", "Zdieľanie Genesis nie je pripojené"),
        "imap": ("degraded", "no successful poll yet"),
        "ollama": ("down", "nepoužíva sa"),
    }
    asked = _answers(monkeypatch, after)

    worse = asyncio.run(orchestrator.health_regressions(Path("c.yml"), "nex-inbox", BEFORE, "mager"))

    assert worse == ["smb — Zdieľanie Genesis nie je pripojené (pred nasadením ok, teraz degraded)"]
    assert asked == ["mager"] * orchestrator.HEALTH_SETTLE_ATTEMPTS


def test_a_check_still_starting_is_asked_again_not_reported(monkeypatch):
    starting = {**BEFORE, "smb": ("degraded", "ešte neprebehla prvá skúška zdieľania")}
    asked = _answers(monkeypatch, starting, starting, dict(BEFORE))

    assert asyncio.run(orchestrator.health_regressions(Path("c.yml"), "nex-inbox", BEFORE)) == []
    assert len(asked) == 3


def test_nothing_to_compare_is_no_claim(monkeypatch):
    asked = _answers(monkeypatch, {"smb": ("down", "")})
    assert asyncio.run(orchestrator.health_regressions(Path("c.yml"), "nex-inbox", None)) == []
    assert asked == [], "with no health before the deploy there is nothing to ask after it"

    _answers(monkeypatch, None)
    assert asyncio.run(orchestrator.health_regressions(Path("c.yml"), "nex-inbox", BEFORE)) == []


# --- the deploy screen ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "uat_slug, deploy_host, asked_host", [("mager-uat", "mager", None), ("mager-prod", "mager", "mager")]
)
def test_the_deploy_says_the_application_reports_a_new_problem(
    monkeypatch, tmp_path, uat_slug, deploy_host, asked_host
):
    events = []

    class _Result:
        warnings: list[str] = []
        fe_service = "frontend"
        compose_path = tmp_path / "docker-compose.yml"

    async def _health(compose, project_slug, deploy_host=None):
        events.append(("health", compose, deploy_host))
        if len([e for e in events if e[0] == "health"]) == 1:
            return {"database": ("ok", ""), "smb": ("ok", "")}
        return {"database": ("ok", ""), "smb": ("degraded", "Zdieľanie Genesis nie je pripojené")}

    async def _up(*_a, **_k):
        events.append(("up",))
        return True, "OK"

    monkeypatch.setattr(P, "provision_uat", lambda *a, **k: _Result())
    monkeypatch.setattr(orchestrator, "_run_uat_deploy", _up)
    monkeypatch.setattr(orchestrator, "_run_prod_deploy", _up)
    monkeypatch.setattr(orchestrator, "instance_health", _health)
    monkeypatch.setattr(orchestrator, "HEALTH_SETTLE_INTERVAL", 0)

    result = asyncio.run(
        deploy_service._default_deploy_runner(
            project_slug="nex-inbox",
            uat_slug=uat_slug,
            version_number="1.7.1",
            force_fresh=False,
            deploy_host=deploy_host,
        )
    )

    ok, _detail, _url = result
    assert ok is True, "the version runs — the problem is said out loud, it does not undo the deploy"
    assert result.warnings == [
        "⚠️ Po nasadení aplikácia hlási problém, ktorý predtým nemala: smb — Zdieľanie Genesis nie je pripojené "
        "(pred nasadením ok, teraz degraded). Skontroluj to skôr, než verziu akceptuješ alebo nasadíš ďalej."
    ]
    assert events[0] == ("health", _Result.compose_path, asked_host), "the health before is asked before the start"
    assert events[1] == ("up",)
    assert {e[2] for e in events if e[0] == "health"} == {asked_host}


def test_a_deploy_that_did_not_start_asks_nothing_after(monkeypatch, tmp_path):
    asked = []

    class _Result:
        warnings: list[str] = []
        fe_service = "frontend"
        compose_path = tmp_path / "docker-compose.yml"

    async def _health(*_a, **_k):
        asked.append(1)
        return {"smb": ("ok", "")}

    async def _failed_up(*_a, **_k):
        return False, "build failed"

    monkeypatch.setattr(P, "provision_uat", lambda *a, **k: _Result())
    monkeypatch.setattr(orchestrator, "_run_uat_deploy", _failed_up)
    monkeypatch.setattr(orchestrator, "instance_health", _health)

    result = asyncio.run(
        deploy_service._default_deploy_runner(
            project_slug="nex-inbox", uat_slug="mager-uat", version_number="1.7.1", force_fresh=False
        )
    )

    ok, _detail, _url = result
    assert ok is False
    assert result.warnings == []
    assert asked == [1], "only the health before the deploy was asked"


def test_the_suite_never_asks_a_live_installation():
    """The test job runs on ANDROS next to live installations; ``_isolate_instance_health`` keeps tests off them."""
    assert orchestrator.instance_health is not REAL_INSTANCE_HEALTH
    assert asyncio.run(orchestrator.instance_health(Path("/opt/uat/mager/nex-inbox/docker-compose.yml"), "x")) is None
