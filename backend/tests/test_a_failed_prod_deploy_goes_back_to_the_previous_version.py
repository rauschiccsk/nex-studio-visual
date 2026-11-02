"""DEV-291 — a PROD deploy that failed after its containers were replaced goes back to the previous version, when the
database kept the migration revision it had before.

Until now such a deploy reported the failure and left the installation standing: the old containers were gone, the new
ones did not serve, and the previous version's images lay unused on the machine until somebody deployed it again by
hand. NEX Manager 1.5.0 moves its people into the template's tables — a failed migration there would have stopped
opening every other application of the customer too. Director, 02.11.2026: „Súhlasím“.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from backend.services import deploy as deploy_service
from backend.services import deploy_progress, deploy_return, orchestrator, remote_instance
from backend.services import predeploy_backup as B
from backend.services import uat_provisioner as P
from backend.tests import _fake_backup_docker as fake_backup
from backend.tests.test_a_prod_deploy_backs_the_database_up_first import _deploy, _index, _is_compose

PREVIOUS_COMPOSE = (
    "services:\n"
    "  postgres:\n    image: postgres:16-alpine\n"
    "  backend:\n    image: icc-manager-backend:v1.4.2\n"
    "  frontend:\n    image: icc-manager-frontend:v1.4.2\n"
)
PREVIOUS_ENV = "POSTGRES_PASSWORD=previous-secret-FAKE\n"
NEW_COMPOSE = PREVIOUS_COMPOSE.replace("v1.4.2", "v1.5.0")


class _Provisioned:
    """What provisioning this deploy wrote and what it overwrote."""

    warnings: list[str] = []
    fe_service = "frontend"

    def __init__(self, tmp_path: Path, previous: dict | None = None):
        self.uat_dir = Path("/opt/customers/icc/nex-manager")
        self.compose_path = tmp_path / "docker-compose.yml"
        self.env_path = tmp_path / ".env"
        self.compose_path.write_text(NEW_COMPOSE, encoding="utf-8")
        self.env_path.write_text("POSTGRES_PASSWORD=previous-secret-FAKE\nNEW=1\n", encoding="utf-8")
        self.previous_files = (
            previous
            if previous is not None
            else {"docker-compose.yml": (PREVIOUS_COMPOSE, 0o664), ".env": (PREVIOUS_ENV, 0o600)}
        )


class _Machine:
    """The target and Docker as the return meets them."""

    def __init__(self, monkeypatch, *, revision_after="026_v190", up_ok=True, serves=True, write_error=None):
        self.written: list = []
        self.compose_cmds: list[tuple[list[str], dict]] = []
        self.steps: list[str] = []
        self.revision_asked = 0

        async def _revision(compose, *, deploy_host):
            self.revision_asked += 1
            return revision_after

        async def _step(cmd, env, deadline, limit):
            self.compose_cmds.append((cmd, env))
            return (True, "") if up_ok else (False, "exit 1: image icc-manager-backend:v1.4.2 not found")

        async def _serves(*_a, **_k):
            return (True, "OK") if serves else (False, "backend /api neodpovedá (502)")

        def _write(uat_dir, files, *, deploy_host):
            self.written.append((uat_dir, dict(files), deploy_host))
            return write_error

        monkeypatch.setattr(deploy_return, "database_revision", _revision)
        monkeypatch.setattr(orchestrator, "_deploy_compose_step", _step)
        monkeypatch.setattr(orchestrator, "_verify_uat_serves", _serves)
        monkeypatch.setattr(remote_instance, "write_files", _write)
        monkeypatch.setattr(deploy_progress, "krok", lambda _dir, krok: self.steps.append(krok))


def _return(provisioned, revision_before="026_v190", deploy_host="mager"):
    return asyncio.run(
        deploy_return.return_to_previous(
            provisioned,
            revision_before,
            project_slug="nex-manager",
            customer_slug="icc",
            app="manager",
            deploy_host=deploy_host,
            private=True,
        )
    )


# ── the database kept its revision: back to the previous version ──────────────────────────────────────────────────
def test_the_previous_version_is_started_again_from_its_own_images(tmp_path, monkeypatch):
    machine = _Machine(monkeypatch)
    provisioned = _Provisioned(tmp_path)

    returned, sentence = _return(provisioned)

    assert returned is True
    assert sentence == (
        "Kokpit vrátil inštaláciu na predošlú verziu 1.4.2: databáza ostala v pôvodnom stave (revízia migrácií "
        "026_v190), predošlá verzia znova beží a odpovedá. Dáta sú nedotknuté."
    )
    # the files go back on the target and into the copy docker compose reads here
    assert machine.written == [(provisioned.uat_dir, provisioned.previous_files, "mager")]
    assert provisioned.compose_path.read_text(encoding="utf-8") == PREVIOUS_COMPOSE
    assert provisioned.env_path.read_text(encoding="utf-8") == PREVIOUS_ENV
    assert provisioned.env_path.stat().st_mode & 0o777 == 0o600
    # started without a build, under the previous version's number
    ((cmd, env),) = machine.compose_cmds
    assert cmd == [
        "docker",
        "compose",
        "-f",
        str(provisioned.compose_path),
        "up",
        "-d",
        "--force-recreate",
        "--no-build",
    ]
    assert (env["APP_VERSION"], env["VITE_APP_VERSION"]) == ("1.4.2", "1.4.2")
    assert machine.steps == [deploy_progress.KROK_NAVRAT]


def test_an_installation_on_this_machine_goes_back_here_only(tmp_path, monkeypatch):
    machine = _Machine(monkeypatch)
    provisioned = _Provisioned(tmp_path)

    returned, _sentence = _return(provisioned, deploy_host=None)

    assert returned is True
    assert machine.written == [], "nothing is written to another machine"
    assert provisioned.compose_path.read_text(encoding="utf-8") == PREVIOUS_COMPOSE


def test_an_installation_without_a_database_goes_back(tmp_path, monkeypatch):
    _Machine(monkeypatch, revision_after=deploy_return.NO_DATABASE)

    returned, _sentence = _return(_Provisioned(tmp_path), revision_before=deploy_return.NO_DATABASE)

    assert returned is True


# ── the return itself fails: said, not hidden ─────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    ("machine_kwargs", "expected"),
    [
        (
            {"write_error": "MAGER neodpovedá"},
            "Návrat na predošlú verziu 1.4.2 zlyhal pri zápise na cieľ (MAGER neodpovedá) — inštalácia nebeží.",
        ),
        (
            {"up_ok": False},
            "Predpis predošlej verzie 1.4.2 je späť, ale nespustila sa: exit 1: image icc-manager-backend:v1.4.2 "
            "not found — inštalácia nebeží.",
        ),
        ({"serves": False}, "Predošlá verzia 1.4.2 sa znova spustila, ale neodpovedá: backend /api neodpovedá (502)"),
    ],
    ids=["files not written", "not started", "does not answer"],
)
def test_a_return_that_fails_says_so(tmp_path, monkeypatch, machine_kwargs, expected):
    machine = _Machine(monkeypatch, **machine_kwargs)

    returned, sentence = _return(_Provisioned(tmp_path))

    assert (returned, sentence) == (True, expected)
    if "write_error" in machine_kwargs:
        assert machine.compose_cmds == [], "nothing is started over files that did not reach the target"


# ── when it must not go back ──────────────────────────────────────────────────────────────────────────────────────
def test_a_database_the_new_version_changed_is_not_given_the_previous_code(tmp_path, monkeypatch):
    machine = _Machine(monkeypatch, revision_after="027_v150")
    provisioned = _Provisioned(tmp_path)

    returned, sentence = _return(provisioned)

    assert returned is False
    assert sentence == (
        "Databáza sa pri nasadení zmenila (revízia migrácií 026_v190 → 027_v150), preto kokpit predošlú verziu "
        "nespustil — bežala by nad schémou, ktorú nepozná. Návrat znamená obnovu zálohy."
    )
    assert (machine.written, machine.compose_cmds, machine.steps) == ([], [], [])
    assert provisioned.compose_path.read_text(encoding="utf-8") == NEW_COMPOSE


@pytest.mark.parametrize(("before", "after"), [(None, "026_v190"), ("026_v190", None)], ids=["before", "after"])
def test_a_state_that_could_not_be_read_is_not_guessed(tmp_path, monkeypatch, before, after):
    machine = _Machine(monkeypatch, revision_after=after)

    returned, sentence = _return(_Provisioned(tmp_path), revision_before=before)

    assert returned is False
    assert sentence.startswith("Stav databázy pred nasadením alebo po ňom sa nedal zmerať")
    assert (machine.written, machine.compose_cmds) == ([], [])


def test_a_first_deploy_has_nothing_to_go_back_to(tmp_path, monkeypatch):
    machine = _Machine(monkeypatch)

    returned, sentence = _return(_Provisioned(tmp_path, previous={}))

    assert (returned, sentence) == (
        False,
        "Predošlá verzia na cieli nie je (prvé nasadenie), kokpit sa nemá kam vrátiť.",
    )
    assert machine.revision_asked == 0


def test_no_sentence_carries_the_installations_secret(tmp_path, monkeypatch):
    sentences = []
    for kwargs in ({}, {"revision_after": "027"}, {"up_ok": False}, {"serves": False}, {"write_error": "x"}):
        with monkeypatch.context() as m:
            _Machine(m, **kwargs)
            sentences.append(_return(_Provisioned(tmp_path))[1])

    assert [s for s in sentences if "secret" in s] == []


# ── reading the revision ──────────────────────────────────────────────────────────────────────────────────────────
COMPOSE_WITH_DB = "services:\n  postgres:\n    image: postgres:16-alpine\n  backend:\n    image: app:v1.0.0\n"


def _read_revision(tmp_path, monkeypatch, replies, compose_text=COMPOSE_WITH_DB):
    compose = tmp_path / "docker-compose.yml"
    compose.write_text(compose_text, encoding="utf-8")
    asked: list[list[str]] = []
    queue = list(replies)

    async def _run(argv, env, timeout):
        asked.append(argv)
        return queue.pop(0)

    async def _no_wait(_seconds):
        return None

    monkeypatch.setattr(B, "_run", _run)
    monkeypatch.setattr(deploy_return.asyncio, "sleep", _no_wait)
    return asyncio.run(deploy_return.database_revision(compose, deploy_host="mager")), asked


def test_the_revision_is_read_in_the_installations_database(tmp_path, monkeypatch):
    revision, asked = _read_revision(tmp_path, monkeypatch, [(0, "026_v190")])

    assert revision == "026_v190"
    assert asked == [
        ["docker", "compose", "-f", str(tmp_path / "docker-compose.yml"), "exec", "-T", "postgres", "sh", "-c"]
        + [deploy_return.REVISION_SH]
    ]
    assert "alembic_version" in deploy_return.REVISION_SH and "$POSTGRES_USER" in deploy_return.REVISION_SH


def _run_revision_script(tmp_path, *, kit: str | None, app_fails: bool = False) -> tuple[int, str]:
    """The script as the database container runs it — with a ``psql`` that answers like that database would."""
    import os
    import stat
    import subprocess

    fake = tmp_path / "bin" / "psql"
    fake.parent.mkdir()
    fake.write_text(
        "#!/bin/sh\n"
        'for arg; do query="$arg"; done\n'  # the query is psql's last argument
        'case "$query" in\n'
        f'  *"FROM alembic_version"*) {"exit 1" if app_fails else "echo 026_v190"} ;;\n'
        f"  *to_regclass*) echo {'t' if kit is not None else 'f'} ;;\n"
        f'  *"FROM kit_alembic_version"*) echo {kit or "-"} ;;\n'
        '  *) echo "unexpected: $query" >&2; exit 2 ;;\n'
        "esac\n",
        encoding="utf-8",
    )
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    env = {"PATH": f"{fake.parent}:{os.environ['PATH']}", "POSTGRES_USER": "app", "POSTGRES_DB": "app"}
    done = subprocess.run(["sh", "-c", deploy_return.REVISION_SH], env=env, capture_output=True, text=True)
    return done.returncode, done.stdout.strip()


def test_the_script_reads_the_application_s_record(tmp_path):
    assert _run_revision_script(tmp_path, kit=None) == (0, "026_v190")


def test_on_the_app_template_it_reads_the_template_s_record_too(tmp_path):
    """A template migration that ran before the application's failed one changed the database as well (DEV-291)."""
    assert _run_revision_script(tmp_path, kit="kit_0001") == (0, "026_v190 kit:kit_0001")


def test_a_database_without_the_application_s_record_fails_the_script(tmp_path):
    code, _out = _run_revision_script(tmp_path, kit="kit_0001", app_fails=True)

    assert code != 0


def test_a_database_container_that_is_starting_is_asked_again(tmp_path, monkeypatch):
    revision, asked = _read_revision(
        tmp_path, monkeypatch, [(1, "service postgres is not running"), (1, "container is restarting"), (0, "026")]
    )

    assert (revision, len(asked)) == ("026", 3)


@pytest.mark.parametrize(
    "reply",
    [(1, 'ERROR:  relation "alembic_version" does not exist'), (0, ""), (0, "\n")],
    ids=["no alembic", "empty table", "blank line"],
)
def test_a_revision_that_is_not_there_is_unknown_at_once(tmp_path, monkeypatch, reply):
    revision, asked = _read_revision(tmp_path, monkeypatch, [reply])

    assert (revision, len(asked)) == (None, 1)


def test_an_installation_without_a_database_has_nothing_to_read(tmp_path, monkeypatch):
    revision, asked = _read_revision(tmp_path, monkeypatch, [], compose_text="services:\n  web:\n    image: app:v1\n")

    assert (revision, asked) == (deploy_return.NO_DATABASE, [])


# ── the deploy measures it before the containers are replaced ─────────────────────────────────────────────────────
def test_a_prod_deploy_reads_the_revision_after_the_backup_and_before_the_replacement(tmp_path, monkeypatch):
    result, events, _compose = _deploy(monkeypatch, tmp_path)

    read = _index(events, lambda cmd: cmd[-1] == deploy_return.REVISION_SH)
    assert _index(events, lambda cmd: cmd[-1] == B._DUMP_SH) < read < _index(events, _is_compose("up"))
    assert result.revision_before == fake_backup.REVISION


def test_a_failure_after_the_replacement_carries_the_revision(tmp_path, monkeypatch):
    result, _events, _compose = _deploy(monkeypatch, tmp_path, fail_token="--force-recreate")

    assert (result[0], result.new_version_started, result.revision_before) == (False, True, fake_backup.REVISION)


def test_a_uat_deploy_does_not_read_it(tmp_path, monkeypatch):
    result, events, _compose = _deploy(monkeypatch, tmp_path, environment="uat")

    assert [cmd for cmd, _step in events if cmd[-1] == deploy_return.REVISION_SH] == []
    assert result.revision_before is None


# ── the deploy runner goes back, and only when it should ──────────────────────────────────────────────────────────
def _runner(tmp_path, monkeypatch, outcome, **machine_kwargs):
    machine = _Machine(monkeypatch, **machine_kwargs)
    provisioned = _Provisioned(tmp_path)

    async def _prod(*_a, **_k):
        return outcome

    async def _health(*_a, **_k):
        return {}

    monkeypatch.setattr(P, "provision_uat", lambda *a, **k: provisioned)
    monkeypatch.setattr(orchestrator, "_run_prod_deploy", _prod)
    monkeypatch.setattr(orchestrator, "instance_health", _health)
    result = asyncio.run(
        deploy_service._default_deploy_runner(
            project_slug="nex-manager",
            uat_slug="icc-prod",
            version_number="1.5.0",
            force_fresh=False,
            deploy_host="mager",
        )
    )
    return result, machine, provisioned


def test_a_failed_migration_on_prod_ends_with_the_previous_version_running(tmp_path, monkeypatch):
    outcome = orchestrator.InstanceDeployResult(
        False, "migrácia zlyhala: počty nesedia", new_version_started=True, revision_before="026_v190"
    )

    result, machine, provisioned = _runner(tmp_path, monkeypatch, outcome)
    ok, detail, _url = result

    assert ok is False, "the new version was not deployed"
    assert detail == (
        "migrácia zlyhala: počty nesedia Kokpit vrátil inštaláciu na predošlú verziu 1.4.2: databáza ostala v "
        "pôvodnom stave (revízia migrácií 026_v190), predošlá verzia znova beží a odpovedá. Dáta sú nedotknuté."
    )
    assert machine.written == [(provisioned.uat_dir, provisioned.previous_files, "mager")], "written once, not twice"
    assert result.new_version_started is False, "the deploy screen would show the new version as the one that runs"


def test_a_changed_database_keeps_the_new_files_and_says_why(tmp_path, monkeypatch):
    outcome = orchestrator.InstanceDeployResult(
        False, "backend /api neodpovedá", new_version_started=True, revision_before="026_v190"
    )

    result, machine, _provisioned = _runner(tmp_path, monkeypatch, outcome, revision_after="027_v150")
    _ok, detail, _url = result

    assert machine.written == [] and machine.compose_cmds == []
    assert "revízia migrácií 026_v190 → 027_v150" in detail
    assert result.new_version_started is True, "the new version stays where it was started"
    assert detail.endswith("Skontroluj aplikáciu skôr, než nasadíš znovu.")


@pytest.mark.parametrize(
    "outcome",
    [
        orchestrator.InstanceDeployResult(True, "OK", new_version_started=True, revision_before="026_v190"),
        orchestrator.InstanceDeployResult(False, "exit 1: build failed", revision_before="026_v190"),
    ],
    ids=["deployed", "never started"],
)
def test_a_deploy_that_did_not_fail_after_the_start_never_goes_back(tmp_path, monkeypatch, outcome):
    result, machine, _provisioned = _runner(tmp_path, monkeypatch, outcome)

    assert machine.compose_cmds == [] and machine.revision_asked == 0
    assert "predošlú verziu" not in result[1]
