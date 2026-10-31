"""DEV-277 — a deploy to another machine stops while an installation of this machine stands in its working folder.

The cockpit keeps the files of an installation on another machine here too, at the path they have there — docker compose
resolves the mounted folders against it. On 31.10.2026, when ICC's production was to move to MAGER, the old PROD
installations of ICC ran on ANDROS in exactly those folders: NEX Websites and Career Asistent (made by the cockpit,
running since 15.10.) and a hand-authored NEX Manager (since 06.10.). The first deploy to MAGER would have written
MAGER's compose and settings — new passwords too — over the two running here, and refused the third as hand-authored,
which says nothing of what is really in the way.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from backend.services import instance_adoption as ia
from backend.services import uat_provisioner as P

#: The real question to this machine's Docker — the suite answers it with „nothing here“ (root conftest).
REAL_CONTAINERS_HERE_OF = P._containers_here_of

SOURCE = """
services:
  db:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: demo
      POSTGRES_PASSWORD: x
      POSTGRES_DB: demo
  backend:
    build:
      context: .
  frontend:
    build:
      context: ./frontend
"""
#: What stands in the folder on ANDROS: the compose of the old installation and its settings.
LIVE_COMPOSE = f"# {P.GENERATED_BY_MARKER}\nname: icc-websites\nservices: {{}}\n"
LIVE_ENV = "POSTGRES_PASSWORD=the-password-of-the-database-here\n"
HAND_AUTHORED = "name: icc-manager\nservices: {}\n"


class _Target:
    def __init__(self):
        self.touched: list[str] = []

    def read_text(self, instance_dir, filename, *, deploy_host, timeout=120):
        self.touched.append(f"read {filename}")
        return None, None

    def write_files(self, instance_dir, files, *, deploy_host, timeout=180):
        self.touched.append("write " + ", ".join(sorted(files)))
        return None


def _world(tmp_path: Path, compose: str = LIVE_COMPOSE) -> Path:
    project = tmp_path / "projects" / "nex-websites"
    project.mkdir(parents=True)
    (project / "docker-compose.yml").write_text(SOURCE, encoding="utf-8")
    here = tmp_path / "customers" / "icc" / "nex-websites"
    here.mkdir(parents=True)
    (here / "docker-compose.yml").write_text(compose, encoding="utf-8")
    (here / ".env").write_text(LIVE_ENV, encoding="utf-8")
    return here


def _deploy(tmp_path: Path, deploy_host="mager"):
    return P.provision_uat(
        "nex-websites",
        "icc-prod",
        projects_root=tmp_path / "projects",
        uat_root=tmp_path / "uat",
        prod_root=tmp_path / "customers",
        environment="prod",
        customer_slug="icc",
        app="websites",
        full_project_slug="nex-websites",
        deploy_host=deploy_host,
    )


def _asked(answer, asked: list[Path]):
    def containers_here_of(instance_dir):
        asked.append(instance_dir)
        return answer

    return containers_here_of


def test_a_running_installation_here_stops_the_deploy_and_nothing_is_written(tmp_path, monkeypatch):
    here = _world(tmp_path)
    target, asked = _Target(), []
    monkeypatch.setattr(P, "remote_instance", target)
    monkeypatch.setattr(P, "_containers_here_of", _asked((["icc-websites-backend", "icc-websites-db"], None), asked))

    with pytest.raises(P.LocalInstallationInTheWayError) as refused:
        _deploy(tmp_path)

    assert str(refused.value) == (
        f"Na stroji ANDROS stojí v priečinku {here} inštalácia (kontajnery icc-websites-backend, icc-websites-db). "
        "Ten istý priečinok kokpit potrebuje ako pracovnú kópiu inštalácie na stroji MAGER — nasadenie by tunajšej "
        "inštalácii prepísalo predpis aj nastavenia vrátane hesiel. Najprv ju na stroji ANDROS zastav a odlož; "
        "nič sa nezapísalo."
    )
    assert asked == [here]
    assert target.touched == [], "nothing is read from or written to the other machine"
    assert (here / "docker-compose.yml").read_text(encoding="utf-8") == LIVE_COMPOSE
    assert (here / ".env").read_text(encoding="utf-8") == LIVE_ENV


def test_a_hand_authored_installation_here_is_named_for_what_it_is(tmp_path, monkeypatch):
    here = _world(tmp_path, compose=HAND_AUTHORED)
    monkeypatch.setattr(P, "remote_instance", _Target())
    monkeypatch.setattr(P, "_containers_here_of", _asked((["icc-manager-backend"], None), []))

    with pytest.raises(P.LocalInstallationInTheWayError, match="kontajnery icc-manager-backend"):
        _deploy(tmp_path)

    assert (here / "docker-compose.yml").read_text(encoding="utf-8") == HAND_AUTHORED


def test_a_docker_that_cannot_be_asked_stops_the_deploy_too(tmp_path, monkeypatch):
    here = _world(tmp_path)
    target = _Target()
    monkeypatch.setattr(P, "remote_instance", target)
    monkeypatch.setattr(P, "_containers_here_of", _asked(([], "Cannot connect to the Docker daemon"), []))

    with pytest.raises(P.LocalInstallationInTheWayError) as refused:
        _deploy(tmp_path)

    assert str(refused.value) == (
        f"Kokpit nevie zistiť, či na stroji ANDROS v priečinku {here} nestojí inštalácia "
        "(Cannot connect to the Docker daemon). Nasadenie na stroj MAGER preto nič nezapísalo — mohlo by prepísať "
        "tunajšiu inštaláciu."
    )
    assert target.touched == [] and (here / ".env").read_text(encoding="utf-8") == LIVE_ENV


def test_a_free_folder_lets_the_deploy_through(tmp_path, monkeypatch):
    project = tmp_path / "projects" / "nex-websites"
    project.mkdir(parents=True)
    (project / "docker-compose.yml").write_text(SOURCE, encoding="utf-8")
    target, asked = _Target(), []
    monkeypatch.setattr(P, "remote_instance", target)
    monkeypatch.setattr(P, "_containers_here_of", _asked(([], None), asked))

    _deploy(tmp_path)

    assert asked == [tmp_path / "customers" / "icc" / "nex-websites"]
    assert "write .env, docker-compose.yml" in target.touched


def test_a_deploy_on_this_machine_does_not_ask(tmp_path, monkeypatch):
    project = tmp_path / "projects" / "nex-websites"
    project.mkdir(parents=True)
    (project / "docker-compose.yml").write_text(SOURCE, encoding="utf-8")
    asked: list[Path] = []
    monkeypatch.setattr(P, "_containers_here_of", _asked((["icc-websites-backend"], None), asked))

    _deploy(tmp_path, deploy_host=None)

    assert asked == [], "on this machine the folder IS the installation"


def test_the_question_names_the_folder_and_counts_stopped_containers_too(monkeypatch):
    calls = []

    def run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="icc-websites-migrate\nicc-websites-backend\n", stderr="")

    monkeypatch.setattr(P.subprocess, "run", run)

    assert REAL_CONTAINERS_HERE_OF(Path("/opt/customers/icc/nex-websites")) == (
        ["icc-websites-backend", "icc-websites-migrate"],
        None,
    )
    assert calls == [
        [
            "docker",
            "ps",
            "--all",
            "--filter",
            "label=com.docker.compose.project.working_dir=/opt/customers/icc/nex-websites",
            "--format",
            "{{.Names}}",
        ]
    ]


def test_a_failed_question_is_an_error_not_an_empty_folder(monkeypatch):
    monkeypatch.setattr(
        P.subprocess,
        "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, stdout="", stderr="permission denied\n"),
    )
    assert REAL_CONTAINERS_HERE_OF(Path("/x")) == ([], "permission denied")

    def missing(cmd, **kw):
        raise FileNotFoundError("docker")

    monkeypatch.setattr(P.subprocess, "run", missing)
    assert REAL_CONTAINERS_HERE_OF(Path("/x")) == ([], "FileNotFoundError: docker")


@pytest.mark.asyncio
async def test_an_adoption_elsewhere_stops_before_it_sets_anything_aside(tmp_path, monkeypatch):
    here = _world(tmp_path)
    set_aside = []
    monkeypatch.setattr(ia, "instance_dir_for", lambda **_: here)
    monkeypatch.setattr(ia, "set_aside_hand_authored", lambda *a, **k: set_aside.append(a) or [])
    monkeypatch.setattr(P, "_containers_here_of", _asked((["icc-websites-backend"], None), []))

    ok, detail, url = await ia.adopting_deploy_runner(
        project_slug="nex-websites", uat_slug="icc-prod", version_number="1.0.0", force_fresh=False, deploy_host="mager"
    )

    assert (ok, url) == (False, None)
    assert detail.startswith(f"provision failed: Na stroji ANDROS stojí v priečinku {here} inštalácia")
    assert set_aside == [], "nothing on the other machine is set aside"
