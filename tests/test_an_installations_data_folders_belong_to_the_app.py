"""DEV-197 — the folders an installation keeps its data in exist before its first start, owned by the application.

A bind mount whose folder is missing is made by Docker, as root. NEX Inbox's backend runs as uid 1000 and keeps the
invoices it receives in ``./originals`` — on a new machine it could not write the first one. ICCINT-151 (24.09.2026)
wrote the helper but nothing called it, so DEV-155 removed it as dead code. Director 23.10.2026: „Áno, schvaľujem
všetkých 14 bodov“ (point 7).

The script the cockpit runs on the target is run here for real, by ``sh`` on a scratch folder, with ``chown`` replaced
by a stub that writes down what it was asked to do.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path, PurePosixPath

import pytest

from backend.services import instance_data_dirs, orchestrator, remote_instance

INSTANCE = PurePosixPath("/opt/customers/mager/nex-inbox")
COMPOSE = """
services:
  backend:
    volumes:
      - ./originals:/var/lib/inbox/originals
      - ./exports:/var/lib/inbox/exports:rw
      - type: bind
        source: ./ops/tls/none
        target: /tls
      - /opt/customers/mager/nex-inbox/logs:/logs
      - /mnt/mager-uat-genesis:/genesis
      - ${GENESIS_SHARE}:/share
      - ../neighbour:/neighbour
      - ./:/everything
      - pg-data:/var/lib/postgresql/data
      - /container-only
  db:
    image: postgres:16
volumes:
  pg-data:
"""


def test_the_folders_are_the_installations_own_bind_mounts_parents_first():
    assert instance_data_dirs.data_dirs(COMPOSE, INSTANCE) == [
        "exports",
        "logs",
        "ops",
        "originals",
        "ops/tls",
        "ops/tls/none",
    ]


def test_a_compose_without_data_folders_or_unreadable_has_none():
    assert instance_data_dirs.data_dirs("services:\n  web:\n    image: nginx\n", INSTANCE) == []
    assert instance_data_dirs.data_dirs("services: [unclosed", INSTANCE) == []


def test_only_the_installations_folder_is_mounted_and_the_owner_is_the_apps():
    cmd = instance_data_dirs._prepare_cmd(INSTANCE, ["originals"])

    assert cmd[:3] == ["docker", "run", "--rm"] and "--network" in cmd and remote_instance.IMAGE in cmd
    assert [cmd[i + 1] for i, a in enumerate(cmd) if a == "-v"] == [f"{INSTANCE}:/target"]
    assert f"chown {remote_instance.UID}:{remote_instance.GID}" in cmd[cmd.index("-c") + 1]


@pytest.fixture
def run_script(tmp_path):
    """Runs the script of :func:`instance_data_dirs._prepare_cmd` on *root* with a ``chown`` that only writes down."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "chown.log"
    (bin_dir / "chown").write_text(f'#!/bin/sh\necho "$@" >> {log}\n')
    (bin_dir / "chown").chmod(0o755)

    def _run(root: Path, dirs: list[str]) -> tuple[subprocess.CompletedProcess, list[str]]:
        cmd = instance_data_dirs._prepare_cmd(PurePosixPath("/target"), dirs)
        script, args = cmd[cmd.index("-c") + 1], cmd[cmd.index("-c") + 2 :]
        assert args[1] == "/target", "the script is told the mounted folder as its first argument"
        done = subprocess.run(
            ["sh", "-c", script, args[0], str(root), *args[2:]],
            capture_output=True,
            text=True,
            env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"},
            check=False,
        )
        return done, log.read_text().splitlines() if log.is_file() else []

    return _run


def test_missing_folders_are_made_and_given_to_the_app(tmp_path, run_script):
    root = tmp_path / "instance"
    root.mkdir()

    done, chowned = run_script(root, ["ops", "originals", "ops/tls"])

    assert done.returncode == 0, done.stderr
    assert (root / "originals").is_dir() and (root / "ops" / "tls").is_dir()
    assert chowned == [f"1000:1000 {root}/ops", f"1000:1000 {root}/originals", f"1000:1000 {root}/ops/tls"]
    assert instance_data_dirs.created(done.stdout) == ["ops", "originals", "ops/tls"]


def test_a_folder_that_is_there_is_left_as_it_is(tmp_path, run_script):
    root = tmp_path / "instance"
    (root / "originals").mkdir(parents=True)
    (root / "originals" / "INB-1.pdf").write_text("invoice")

    done, chowned = run_script(root, ["originals", "exports"])

    assert done.returncode == 0, done.stderr
    assert chowned == [f"1000:1000 {root}/exports"]
    assert (root / "originals" / "INB-1.pdf").read_text() == "invoice"


def test_a_folder_that_cannot_be_made_fails_the_script(tmp_path, run_script):
    root = tmp_path / "instance"
    root.mkdir()
    (root / "ops").write_text("a file where a folder should be")

    done, _chowned = run_script(root, ["ops", "ops/tls", "originals"])

    assert done.returncode != 0
    assert not (root / "originals").exists(), "the script must stop at the first folder it cannot make"


async def test_a_deploy_prepares_the_folders_after_the_build_and_before_the_start(monkeypatch, tmp_path):
    compose = tmp_path / "docker-compose.yml"
    compose.write_text(COMPOSE)
    steps: list[str] = []

    async def _step(cmd, *_a, **_k):
        steps.append("up" if "up" in cmd else "build")
        return True, ""

    def _prepare(path, *, deploy_host):
        steps.append(f"folders:{path}:{deploy_host}")
        return ["originals"], None

    async def _stop(*_a, **_k):
        return False, "stop here"

    _stub_the_rest(monkeypatch, compose, _step, _stop)
    monkeypatch.setattr(instance_data_dirs, "prepare", _prepare)

    await orchestrator._run_prod_deploy("nex-inbox", "mager", "inbox", "nex-inbox", deploy_host=None)

    assert steps == ["build", f"folders:{compose}:None", "up"]


async def test_folders_that_cannot_be_prepared_stop_the_deploy_before_the_start(monkeypatch, tmp_path):
    compose = tmp_path / "docker-compose.yml"
    compose.write_text(COMPOSE)
    steps: list[str] = []

    async def _step(cmd, *_a, **_k):
        steps.append("up" if "up" in cmd else "build")
        return True, ""

    async def _never(*_a, **_k):
        raise AssertionError("the deploy went on")

    _stub_the_rest(monkeypatch, compose, _step, _never)
    monkeypatch.setattr(
        instance_data_dirs, "prepare", lambda *_a, **_k: ([], "priečinky pre dáta sa nedali pripraviť: x")
    )

    ok, detail = await orchestrator._run_prod_deploy("nex-inbox", "mager", "inbox", "nex-inbox")

    assert (ok, steps) == (False, ["build"])
    assert detail.startswith("priečinky pre dáta sa nedali pripraviť: x")


def _stub_the_rest(monkeypatch, compose: Path, step, after_up) -> None:
    from backend.services.predeploy_backup import BackupOutcome

    async def _backed_up(*_a, **_k):
        return BackupOutcome(True, "")

    async def _no_provenance(*_a, **_k):
        return {}

    monkeypatch.setattr(orchestrator, "_deploy_compose_step", step)
    monkeypatch.setattr(orchestrator, "_run_post_up_migration", after_up)
    monkeypatch.setattr(orchestrator, "_build_provenance_env", _no_provenance)
    monkeypatch.setattr(orchestrator, "_uat_compose_path", lambda *a, **k: compose)
    monkeypatch.setattr(orchestrator.predeploy_backup, "back_up", _backed_up)
