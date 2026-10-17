"""DEV-116 — a PROD deploy backs the installation's database up before it replaces anything; no backup, no deploy.

14.10.2026 NEX Inbox 1.8.0 went to MÁGERSTAV's PROD (MAGER) with migration 026 and nothing to go back to: the cockpit
had no backup step at all — the backup before 1.7.1 was Máger Dedo's own (SERVER-105). Director: „Súhlasím“.
"""

from __future__ import annotations

import asyncio

import pytest

from backend.config.settings import settings
from backend.services import deploy_progress, orchestrator
from backend.services import predeploy_backup as B
from backend.tests import _fake_backup_docker as fake_backup
from backend.tests.test_a_deploy_elsewhere_leaves_the_target_consistent import _asked_urls, _instance, _Proc

NOT_STARTED = "Nasadenie sa nezačalo, bežiaca verzia ostáva."


def _docker(monkeypatch, *, fail: str | None = None, fail_token: str | None = None, **replies):
    """Every command of the deploy with the step it ran in. ``fail`` names the backup script that exits 1,
    ``fail_token`` an argument of any other command that makes it exit 1."""
    events: list[tuple[list[str], str | None]] = []
    step = {"now": None}
    real_krok = deploy_progress.krok

    def _krok(instance_dir, krok):
        step["now"] = krok
        real_krok(instance_dir, krok)

    async def _spawn(*cmd, stdin=None, stdout=None, stderr=None, env=None):
        cmd = list(cmd)
        events.append((cmd, step["now"]))
        if fail is not None and cmd[-1] == fail:
            return _Proc(1, f"{fail} failed")
        if fail_token is not None and fail_token in cmd:
            return _Proc(1, f"{fail_token} failed")
        if cmd[-1].startswith('cat "/target/') and cmd[-1].endswith('.dump"'):
            # Streaming a stored dump OUT of the target dies the way it did on MAGER 17.10.2026 (DEV-140): the reader,
            # ``pg_restore -l``, stops after the archive's table of contents and the sender gets a broken pipe.
            return _Proc(1, "write /dev/stdout: broken pipe")
        answer = fake_backup.reply(cmd, **replies)
        if answer is not None:
            return _Proc(0, answer)
        if cmd[-1].endswith("/target/docker-compose.yml"):  # the compose read back from the target
            return _Proc(0, compose_text["text"])
        return _Proc(0, "port 8000: 200")

    compose_text = {"text": ""}
    monkeypatch.setattr(orchestrator.deploy_progress, "krok", _krok)
    monkeypatch.setattr(orchestrator.asyncio, "create_subprocess_exec", _spawn)
    return events, compose_text


def _deploy(monkeypatch, tmp_path, *, environment="prod", **docker):
    compose = _instance(tmp_path, monkeypatch)
    # A secret of the installation — no argument of any command may ever carry it.
    (compose.parent / ".env").write_text("POSTGRES_PASSWORD=s3cr3t-of-the-customer\n", encoding="utf-8")
    events, compose_text = _docker(monkeypatch, **docker)
    compose_text["text"] = compose.read_text(encoding="utf-8")
    _asked_urls(monkeypatch)
    if environment == "prod":
        result = asyncio.run(
            orchestrator._run_prod_deploy(
                "nex-inbox", "mager", "inbox", "nex-inbox", version_number="1.8.1", deploy_host="mager"
            )
        )
    else:
        result = asyncio.run(
            orchestrator._run_uat_deploy(
                "nex-inbox",
                "mager-inbox",
                environment="uat",
                customer_slug="mager",
                app="inbox",
                full_project_slug="nex-inbox",
                version_number="1.8.1",
            )
        )
    return result, events, compose


def _index(events, predicate) -> int:
    return next(i for i, (cmd, _step) in enumerate(events) if predicate(cmd))


def _is_compose(sub: str):
    return lambda cmd: cmd[:2] == ["docker", "compose"] and cmd[4] == sub


def _is_read_back(cmd: list[str]) -> bool:
    return cmd[:3] == ["docker", "run", "--rm"] and "pg_restore" in cmd


def test_the_database_is_backed_up_after_the_build_and_before_anything_is_replaced(tmp_path, monkeypatch):
    result, events, compose = _deploy(monkeypatch, tmp_path)

    build = _index(events, _is_compose("build"))
    dump = _index(events, lambda cmd: cmd[-1] == B._DUMP_SH)
    read_back = _index(events, _is_read_back)
    up = _index(events, _is_compose("up"))
    assert build < dump < read_back < up, "the backup must lie between the build and the replacement of containers"
    assert events[dump][1] == deploy_progress.KROK_ZALOHA
    dump_cmd = events[dump][0]
    assert dump_cmd[:7] == ["docker", "compose", "-f", str(compose), "exec", "-T", "postgres"]

    assert result[0] is True
    assert f"Záloha databázy pred nasadením: {compose.parent}/{B.BACKUP_DIR}/nex-inbox-1.8.1-" in result[1]
    assert "2 položiek" in result[1], "the entries counted from pg_restore -l, comments not included"


def test_no_argument_of_the_deploy_carries_the_installations_secret(tmp_path, monkeypatch):
    _result, events, _compose = _deploy(monkeypatch, tmp_path)

    leaked = [" ".join(cmd) for cmd, _step in events if any("s3cr3t" in part for part in cmd)]
    assert leaked == []
    assert B._DUMP_SH == 'exec pg_dump -U "$POSTGRES_USER" -d "${POSTGRES_DB:-$POSTGRES_USER}" -Fc'


@pytest.mark.parametrize(
    "case, docker, removed",
    [
        ("the database does not run", {"running": ""}, None),
        ("its size is unknown", {"fail": B._SIZE_SH}, None),
        ("the disk has no room", {"free_kb": 1024}, None),
        ("pg_dump fails", {"fail": B._DUMP_SH}, ".part"),
        ("the dump cannot be read back", {"toc": ";\n; Archive\n;\n"}, ".dump"),
    ],
)
def test_without_a_backup_nothing_is_replaced(tmp_path, monkeypatch, case, docker, removed):
    result, events, _compose = _deploy(monkeypatch, tmp_path, **docker)

    assert result[0] is False, case
    assert result.new_version_started is False, "the running version was not touched"
    assert result[1].endswith(NOT_STARTED), result[1]
    assert not any(_is_compose("up")(cmd) for cmd, _step in events), f"{case}: up ran without a backup"
    if removed:
        assert any(cmd[-1].startswith("rm -f ") and cmd[-1].endswith(f'{removed}"') for cmd, _step in events), (
            f"{case}: the unusable file stayed on the target"
        )


@pytest.mark.parametrize("step, token", [("up", "--force-recreate"), ("migration", "alembic")])
def test_a_failure_after_the_start_says_where_the_backup_lies(tmp_path, monkeypatch, step, token):
    """From ``up`` on, the old containers are gone and a migration may have run — this is when the backup is needed."""
    monkeypatch.setattr(orchestrator, "MIGRATE_ATTEMPTS", 1)

    result, _events, compose = _deploy(monkeypatch, tmp_path, fail_token=token)

    assert result[0] is False and result.new_version_started is True, step
    assert f"Záloha databázy pred nasadením: {compose.parent}/{B.BACKUP_DIR}/nex-inbox-1.8.1-" in result[1], result[1]


def test_a_failure_of_the_space_check_says_the_numbers(tmp_path, monkeypatch):
    result, _events, _compose = _deploy(monkeypatch, tmp_path, free_kb=1024)

    assert result[1].startswith(
        "Na zálohu nie je miesto: voľných 0.0 GB, databáza má 5.0 MB a rezerva disku je 1.0 GB."
    )


def test_a_first_deploy_has_nothing_to_back_up_and_goes_on(tmp_path, monkeypatch):
    result, events, _compose = _deploy(monkeypatch, tmp_path, containers="")

    assert not any(cmd[-1] == B._DUMP_SH for cmd, _step in events)
    assert any(_is_compose("up")(cmd) for cmd, _step in events)
    assert result[0] is True
    assert "Inštalácia ešte nemá kontajnery — databázu nie je čo zálohovať." in result[1]


def test_a_uat_deploy_does_not_back_up(tmp_path, monkeypatch):
    _result, events, _compose = _deploy(monkeypatch, tmp_path, environment="uat")

    assert not any(step == deploy_progress.KROK_ZALOHA for _cmd, step in events)
    assert not any(cmd[-1] == B._DUMP_SH for cmd, _step in events)


def test_old_backups_are_pruned_to_the_setting(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "predeploy_backup_keep", 2)

    result, events, _compose = _deploy(monkeypatch, tmp_path)

    prune = [cmd[-1] for cmd, _step in events if "ls -1t" in cmd[-1]]
    assert prune == [
        f'cd "/target/{B.BACKUP_DIR}" && ls -1t -- *.dump | tail -n +3 | while read f; do rm -f -- "$f"; done'
    ]
    assert "Ponechaných je posledných 2 záloh." in result[1]


def test_the_backup_is_read_back_on_the_target_from_the_stored_file(tmp_path, monkeypatch):
    """DEV-140 — 17.10.2026 NEX Inbox 1.9.0 stopped twice at „Záloha sa nedá prečítať“ on MÁGERSTAV's PROD.

    The read-back streamed the stored dump target → cockpit → target into ``pg_restore -l``, which stops reading after
    the archive's table of contents; the sender then died of a broken pipe (measured against MAGER: sender exit 1
    „write /dev/stdout: broken pipe“, pg_restore exit 0 with 113 entries) and the cockpit took a good 15 MB backup for
    an unreadable one. Only a dump that fits a pipe's buffer passed. The file is now read where it lies — a throwaway
    container of the database's own image, the backup folder mounted read-only, nothing travelling back.
    """
    result, events, compose = _deploy(monkeypatch, tmp_path)

    assert result[0] is True, result[1]
    reads = [cmd for cmd, _step in events if _is_read_back(cmd)]
    assert len(reads) == 1, reads
    cmd = reads[0]
    assert cmd[:-1] == [
        "docker",
        "run",
        "--rm",
        "--network",
        "none",
        "--mount",
        f"type=bind,source={compose.parent},target=/target,readonly",
        "--entrypoint",
        "pg_restore",
        fake_backup.DB_IMAGE,
        "-l",
    ]
    assert cmd[-1].startswith(f"/target/{B.BACKUP_DIR}/nex-inbox-1.8.1-") and cmd[-1].endswith(".dump"), cmd[-1]
    image = _index(events, _is_compose("images"))
    assert events[image][0][-2:] == ["-q", "postgres"], "the image is the one the database container runs from"
    assert not any(c[-1].startswith('cat "/target/') for c, _step in events), "nothing of the dump streams back"


def test_an_unreadable_backup_says_why(tmp_path, monkeypatch):
    reason = "pg_restore: error: did not find magic string in file header"

    result, _events, _compose = _deploy(monkeypatch, tmp_path, toc=reason)

    assert result[0] is False
    assert result[1] == f"Záloha sa nedá prečítať ({reason}), nasadenie by nemalo cestu späť. {NOT_STARTED}"


def test_the_pipe_streams_between_two_real_processes():
    """The plumbing itself, without Docker: the sink sees the source's bytes and both exit codes come back."""
    assert asyncio.run(B._pipe(["printf", "%s", "abc"], ["wc", "-c"], None, 10)) == (0, 0, "3")
    source_rc, sink_rc, _out = asyncio.run(B._pipe(["false"], ["wc", "-c"], None, 10))
    assert (source_rc, sink_rc) == (1, 0)


def test_the_deploy_screen_looks_for_the_same_sentence():
    """The row shows where the backup lies by finding this sentence in the report — the two must not drift apart."""
    from pathlib import Path

    page = Path(__file__).resolve().parents[2] / "frontend/src/components/deploy/DeployMatrixPage.tsx"
    assert f'const BACKUP_SENTENCE = "{B.BACKUP_SENTENCE}";' in page.read_text(encoding="utf-8")
