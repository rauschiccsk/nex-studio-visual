"""DEV-280 — at a deploy, an application's data folders and volumes belong to the user its new version runs as.

NEX Knowledge and Author Asistent ran as uid 10001; the template of applications and MAGER's backup (it runs as icc,
uid 1000) want 1000 (DEV-281, DEV-282). Measured 02.11.2026 on the UAT on ANDROS: NEX Knowledge's ``/data`` held three
folders of nk (10001), Author Asistent's ``/var/lib/author-asistent/claude`` one of app (10001). A version that runs as
1000 could not have written into them. The cockpit made new folders for 1000 (DEV-197) and left everything else as
the first start made it.

The script the cockpit runs on the target is run here for real, by ``sh`` on a scratch folder, with ``chown`` replaced
by a stub that writes down what it was asked to do.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path, PurePosixPath

import pytest

from backend.services import instance_data_dirs as D
from backend.services import orchestrator, remote_instance

INSTANCE = PurePosixPath("/opt/uat/icc/nex-knowledge")
#: NEX Knowledge's UAT as the cockpit renders it, trimmed to what decides.
COMPOSE = """
name: uat-icc-knowledge
services:
  db:
    image: pgvector/pgvector:pg16
    volumes:
      - db-data:/var/lib/postgresql/data
  backend:
    build:
      context: /opt/projects/nex-knowledge
    image: uat-icc-knowledge-backend:v0.2.0
    volumes:
      - knowledge-data:/data
      - ./exports:/exports
      - ./ops/tls/none:/run/tls/cert.pem:ro
      - /opt/shared:/shared
  worker:
    build:
      context: /opt/projects/nex-knowledge
    image: uat-icc-knowledge-worker:v0.2.0
    volumes:
      - type: volume
        source: knowledge-data
        target: /data
  migrate:
    build:
      context: /opt/projects/nex-knowledge
    image: uat-icc-knowledge-migrate:v0.2.0
    volumes:
      - db-data:/var/lib/postgresql/data
  redis:
    image: redis:7-alpine
    volumes:
      - redis-data:/data
  test:
    build:
      context: /opt/projects/nex-knowledge
    image: uat-icc-knowledge-test:v0.2.0
    profiles: [ci]
    volumes:
      - ./test-results:/results
volumes:
  db-data:
  knowledge-data:
  redis-data:
"""
#: The real call — the suite answers Docker for the step itself (root conftest).
REAL_DOCKER = D._docker
ID_10001 = "uid=10001(nk) gid=10001(nk) groups=10001(nk)\n"
ID_1000 = "uid=1000(nk) gid=1000(nk) groups=1000(nk)\n"
ID_ROOT = "uid=0(root) gid=0(root) groups=0(root)\n"


def test_the_data_is_what_the_installation_s_own_services_mount_outside_a_database():
    assert D.data_mounts(COMPOSE, INSTANCE) == [
        D.DataMount("backend", "uat-icc-knowledge-backend:v0.2.0", "volume", "uat-icc-knowledge_knowledge-data"),
        D.DataMount("backend", "uat-icc-knowledge-backend:v0.2.0", "folder", "exports"),
        D.DataMount("backend", "uat-icc-knowledge-backend:v0.2.0", "folder", "ops/tls/none"),
        D.DataMount("worker", "uat-icc-knowledge-worker:v0.2.0", "volume", "uat-icc-knowledge_knowledge-data"),
    ]


class _Docker:
    """Docker of the target: who each image runs as, which volumes exist, and every command it was given."""

    def __init__(self, ids: dict[str, str], volumes=("uat-icc-knowledge_knowledge-data",), changed="", failed=""):
        self.ids, self.volumes, self.changed, self.failed = ids, list(volumes), changed, failed
        self.calls: list[tuple[list[str], object]] = []

    def __call__(self, cmd, deploy_host, timeout):
        self.calls.append((cmd, deploy_host))
        if "--entrypoint" in cmd:
            image = cmd[-1]
            if image not in self.ids:
                return subprocess.CompletedProcess(cmd, 125, "", f"Unable to find image '{image}'")
            return subprocess.CompletedProcess(cmd, 0, self.ids[image], "")
        if cmd[:3] == ["docker", "volume", "ls"]:
            return subprocess.CompletedProcess(cmd, 0, "\n".join(self.volumes) + "\n", "")
        return subprocess.CompletedProcess(cmd, 1 if self.failed else 0, self.changed, self.failed)

    def helper(self) -> list[str]:
        runs = [cmd for cmd, _host in self.calls if remote_instance.IMAGE in cmd]
        assert len(runs) <= 1
        return runs[0] if runs else []


@pytest.fixture
def compose(tmp_path) -> Path:
    path = tmp_path / "docker-compose.yml"
    path.write_text(COMPOSE.replace("/opt/uat/icc/nex-knowledge", str(tmp_path)), encoding="utf-8")
    return path


def test_the_owner_of_the_new_version_gets_its_folders_and_volumes(compose, monkeypatch):
    docker = _Docker(
        {"uat-icc-knowledge-backend:v0.2.0": ID_1000, "uat-icc-knowledge-worker:v0.2.0": ID_1000},
        changed="changed:/v/2\nchanged:/target/exports\n",
    )
    monkeypatch.setattr(D, "_docker", docker)

    changed, error = D.align_owners(compose, deploy_host="mager")

    assert (changed, error) == (["uat-icc-knowledge_knowledge-data", "exports"], None)
    helper = docker.helper()
    assert [helper[i + 1] for i, a in enumerate(helper) if a == "-v"] == [
        f"{compose.parent}:/target",
        "uat-icc-knowledge_knowledge-data:/v/2",
    ]
    assert "--network" in helper and helper[helper.index("--network") + 1] == "none"
    assert helper[helper.index("-c") + 2 :] == [
        "sh",
        "/target/exports",
        "1000:1000",
        "/target/ops/tls/none",
        "1000:1000",
        "/v/2",
        "1000:1000",
    ]
    assert {host for _cmd, host in docker.calls} == {"mager"}, "asked on the machine the installation runs on"


def test_an_application_that_runs_as_root_changes_nothing(compose, monkeypatch):
    docker = _Docker({"uat-icc-knowledge-backend:v0.2.0": ID_ROOT, "uat-icc-knowledge-worker:v0.2.0": ID_ROOT})
    monkeypatch.setattr(D, "_docker", docker)

    assert D.align_owners(compose, deploy_host=None) == ([], None)
    assert docker.helper() == []


def test_a_volume_not_made_yet_is_left_to_docker(compose, monkeypatch):
    docker = _Docker(
        {"uat-icc-knowledge-backend:v0.2.0": ID_1000, "uat-icc-knowledge-worker:v0.2.0": ID_1000}, volumes=()
    )
    monkeypatch.setattr(D, "_docker", docker)

    D.align_owners(compose, deploy_host=None)

    helper = docker.helper()
    assert [helper[i + 1] for i, a in enumerate(helper) if a == "-v"] == [f"{compose.parent}:/target"]
    assert "/v/2" not in helper


def test_two_users_on_one_place_are_not_the_cockpit_s_to_decide(compose, monkeypatch):
    docker = _Docker({"uat-icc-knowledge-backend:v0.2.0": ID_1000, "uat-icc-knowledge-worker:v0.2.0": ID_10001})
    monkeypatch.setattr(D, "_docker", docker)

    D.align_owners(compose, deploy_host=None)

    assert "/v/2" not in docker.helper(), "the volume backend and worker share is left as it is"
    assert "/target/exports" in docker.helper(), "the backend's own folder is still its user's"


def test_an_image_whose_user_cannot_be_told_stops_the_deploy(compose, monkeypatch):
    docker = _Docker({"uat-icc-knowledge-backend:v0.2.0": ID_1000})  # the worker's image is not there
    monkeypatch.setattr(D, "_docker", docker)

    changed, error = D.align_owners(compose, deploy_host=None)

    assert changed == [] and docker.helper() == []
    assert error == (
        "dáta aplikácie sa nedali dať jej používateľovi: pod akým používateľom beží obraz "
        "uat-icc-knowledge-worker:v0.2.0, sa nedalo zistiť: Unable to find image 'uat-icc-knowledge-worker:v0.2.0'"
    )


def test_a_change_that_fails_on_the_machine_stops_the_deploy(compose, monkeypatch):
    docker = _Docker(
        {"uat-icc-knowledge-backend:v0.2.0": ID_1000, "uat-icc-knowledge-worker:v0.2.0": ID_1000},
        failed="chown: /v/2: Operation not permitted",
    )
    monkeypatch.setattr(D, "_docker", docker)

    assert D.align_owners(compose, deploy_host=None) == (
        [],
        "dáta aplikácie sa nedali dať jej používateľovi: chown: /v/2: Operation not permitted",
    )


def test_docker_is_asked_on_the_machine_the_installation_runs_on(monkeypatch):
    seen = []
    monkeypatch.setattr(
        D.subprocess, "run", lambda cmd, **kw: seen.append(kw["env"]) or subprocess.CompletedProcess(cmd, 0, "", "")
    )

    REAL_DOCKER(["docker", "volume", "ls"], "mager", 5)
    REAL_DOCKER(["docker", "volume", "ls"], None, 5)

    assert seen[0]["DOCKER_HOST"] == "ssh://mager"
    assert seen[1] is None, "this machine: the cockpit's own Docker"


def test_a_compose_without_data_of_its_own_asks_nothing(tmp_path, monkeypatch):
    path = tmp_path / "docker-compose.yml"
    path.write_text("services:\n  web:\n    image: nginx\n    volumes: [./html:/usr/share/nginx/html]\n")

    def never(*_a, **_k):
        raise AssertionError("Docker was asked")

    monkeypatch.setattr(D, "_docker", never)

    assert D.align_owners(path, deploy_host=None) == ([], None)


def test_the_user_is_read_from_id_in_the_image():
    def answer(stdout, rc=0):
        return lambda cmd, host, timeout: subprocess.CompletedProcess(cmd, rc, stdout, "boom" if rc else "")

    with pytest.MonkeyPatch.context() as m:
        m.setattr(D, "_docker", answer(ID_10001))
        assert D.image_owner("x", deploy_host=None) == ("10001:10001", None)
        m.setattr(D, "_docker", answer(ID_ROOT))
        assert D.image_owner("x", deploy_host=None) == (None, None)
        m.setattr(D, "_docker", answer("", rc=1))
        assert D.image_owner("x", deploy_host=None) == (
            None,
            "pod akým používateľom beží obraz x, sa nedalo zistiť: boom",
        )


@pytest.fixture
def run_align(tmp_path):
    """Runs the script of :func:`align_owners` on real paths with a ``chown`` that only writes down."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "chown.log"
    (bin_dir / "chown").write_text(f'#!/bin/sh\necho "$@" >> {log}\n')
    (bin_dir / "chown").chmod(0o755)

    def _run(*pairs: str) -> tuple[subprocess.CompletedProcess, list[str]]:
        done = subprocess.run(
            ["sh", "-c", D._ALIGN_SCRIPT, "sh", *pairs],
            capture_output=True,
            text=True,
            env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"},
            check=False,
        )
        return done, log.read_text().splitlines() if log.is_file() else []

    return _run


def test_the_script_changes_only_what_has_another_owner_and_skips_what_is_not_there(tmp_path, run_align):
    mine = tmp_path / "mine"
    other = tmp_path / "other"
    mine.mkdir()
    other.mkdir()
    me = f"{os.getuid()}:{os.getgid()}"

    done, chowned = run_align(str(mine), me, str(other), "10001:10001", str(tmp_path / "missing"), "1000:1000")

    assert done.returncode == 0, done.stderr
    assert chowned == [f"-R 10001:10001 {other}"]
    assert done.stdout.splitlines() == [f"changed:{other}"]


def test_a_failed_change_fails_the_script(tmp_path, run_align):
    (tmp_path / "bin" / "chown").write_text("#!/bin/sh\nexit 1\n")
    target = tmp_path / "data"
    target.mkdir()

    done, _chowned = run_align(str(target), "10001:10001")

    assert done.returncode != 0


async def test_a_deploy_gives_the_data_to_the_app_after_the_folders_and_before_the_start(monkeypatch, tmp_path):
    compose = tmp_path / "docker-compose.yml"
    compose.write_text(COMPOSE)
    steps: list[str] = []

    async def _step(cmd, *_a, **_k):
        steps.append("up" if "up" in cmd else "build")
        return True, ""

    async def _stop(*_a, **_k):
        return False, "stop here"

    def _owners(path, *, deploy_host):
        steps.append(f"owners:{deploy_host}")
        return [], None

    _stub_the_rest(monkeypatch, compose, _step, _stop)
    monkeypatch.setattr(D, "prepare", lambda *_a, **_k: steps.append("folders") or ([], None))
    monkeypatch.setattr(D, "align_owners", _owners)

    await orchestrator._run_prod_deploy("nex-knowledge", "icc", "knowledge", "nex-knowledge", deploy_host=None)

    assert steps == ["build", "folders", "owners:None", "up"]


async def test_data_that_cannot_be_given_to_the_app_stops_the_deploy_before_the_start(monkeypatch, tmp_path):
    compose = tmp_path / "docker-compose.yml"
    compose.write_text(COMPOSE)
    steps: list[str] = []

    async def _step(cmd, *_a, **_k):
        steps.append("up" if "up" in cmd else "build")
        return True, ""

    async def _never(*_a, **_k):
        raise AssertionError("the deploy went on")

    _stub_the_rest(monkeypatch, compose, _step, _never)
    monkeypatch.setattr(D, "prepare", lambda *_a, **_k: ([], None))
    monkeypatch.setattr(D, "align_owners", lambda *_a, **_k: ([], "dáta aplikácie sa nedali dať jej používateľovi: x"))

    ok, detail = await orchestrator._run_prod_deploy("nex-knowledge", "icc", "knowledge", "nex-knowledge")

    assert (ok, steps) == (False, ["build"])
    assert detail.startswith("dáta aplikácie sa nedali dať jej používateľovi: x")


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
