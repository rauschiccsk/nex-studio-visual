"""DEV-279 — on MAGER an app's data outside its database lives in the installation's folder, which the backup reads.

Career Asistent keeps its users' received e-mails in ``/data``, NEX Knowledge its uploaded files, Author Asistent its
Claude sign-in — each project in a Docker volume (``appdata``, ``knowledge-data``, ``claude_home``), and the cockpit
took the volumes over as the project has them. On ANDROS the backup runs as root and takes everything an installation
mounts. MAGER's backup runs as icc without root, reads only ``/opt/customers`` and dumps the databases (Máger Dedo,
31.10.2026): the files of those three apps would not have been in it. A database's own data stays in its volume — the
dump covers it, and the live files of a running database are no backup.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath

import yaml

from backend.services import instance_data_dirs
from backend.services import uat_provisioner as P

#: Career Asistent's shape (31.10.2026).
SOURCE = """
services:
  db:
    image: postgres:16
    environment:
      POSTGRES_USER: app
      POSTGRES_PASSWORD: x
      POSTGRES_DB: app
    volumes:
      - pgdata:/var/lib/postgresql/data
  backend:
    build:
      context: .
    volumes:
      - appdata:/data
  worker:
    build:
      context: .
    command: ["python", "-m", "app.worker"]
    volumes:
      - appdata:/data
  frontend:
    build:
      context: ./frontend
volumes:
  pgdata:
  appdata:
"""


class _Target:
    def __init__(self, files=None):
        self.files = dict(files or {})
        self.written: dict[str, str] = {}

    def read_text(self, instance_dir, filename, *, deploy_host, timeout=120):
        return self.files.get(filename), None

    def write_files(self, instance_dir, files, *, deploy_host, timeout=180):
        self.written.update({name: text for name, (text, _mode) in files.items()})
        return None


def _deploy(tmp_path: Path, deploy_host=None, version=None):
    path = tmp_path / "projects" / "career-asistent"
    if not path.is_dir():
        path.mkdir(parents=True)
        (path / "docker-compose.yml").write_text(SOURCE, encoding="utf-8")
    return P.provision_uat(
        "career-asistent",
        "icc-prod",
        projects_root=tmp_path / "projects",
        uat_root=tmp_path / "uat",
        prod_root=tmp_path / "customers",
        environment="prod",
        customer_slug="icc",
        app="career-asistent",
        full_project_slug="career-asistent",
        deploy_host=deploy_host,
        **({"version": version} if version else {}),
    )


def _mounts(compose: dict) -> dict:
    return {name: svc.get("volumes") for name, svc in compose["services"].items() if svc.get("volumes")}


def test_on_mager_the_files_go_into_the_installation_s_folder_and_the_database_stays_in_its_volume(
    tmp_path, monkeypatch
):
    target = _Target()
    monkeypatch.setattr(P, "remote_instance", target)

    _deploy(tmp_path, deploy_host="mager")

    text = target.written["docker-compose.yml"]
    compose = yaml.safe_load(text)
    assert _mounts(compose) == {
        "db": ["pgdata:/var/lib/postgresql/data"],
        "backend": ["./appdata:/data"],
        "worker": ["./appdata:/data"],
    }
    assert list(compose["volumes"]) == ["pgdata"]
    # the folder is made for the application's user before the first start (DEV-197)
    assert instance_data_dirs.data_dirs(text, PurePosixPath("/opt/customers/icc/career-asistent")) == ["appdata"]


def test_on_this_machine_the_volumes_stay_as_the_project_has_them(tmp_path):
    result = _deploy(tmp_path)

    compose = yaml.safe_load(result.compose_path.read_text(encoding="utf-8"))
    assert _mounts(compose)["backend"] == ["appdata:/data"]
    assert set(compose["volumes"]) == {"pgdata", "appdata"}


def test_an_installation_that_stands_keeps_its_volumes(tmp_path, monkeypatch):
    monkeypatch.setattr(P, "_docker_image_exists", lambda image: True)
    here = _deploy(tmp_path)  # the compose as an installation made with volumes has it
    target = _Target(
        {
            "docker-compose.yml": here.compose_path.read_text(encoding="utf-8"),
            ".env": here.env_path.read_text(encoding="utf-8"),
        }
    )
    monkeypatch.setattr(P, "remote_instance", target)

    _deploy(tmp_path, deploy_host="mager", version="1.1.0")

    compose = yaml.safe_load(target.written["docker-compose.yml"])
    assert _mounts(compose)["backend"] == ["appdata:/data"], "a redeploy changes the version, not where data lies"


def test_every_form_of_a_mount_is_moved_and_a_database_s_volume_never_is():
    services = {
        "db": {"volumes": ["shared:/var/lib/postgresql/data"]},
        "backup": {"volumes": ["shared:/dump:ro"]},
        "backend": {
            "volumes": [
                "files:/data:ro",
                {"type": "volume", "source": "cache", "target": "/cache", "volume": {"nocopy": True}},
                {"source": "logs", "target": "/logs"},
                "./config:/config:ro",
                "/srv/shared:/shared",
                "/tmp/anon",
            ]
        },
    }
    volumes = {"shared": None, "files": None, "cache": {}, "logs": None}

    moved = P.keep_data_in_folder(services, volumes)

    assert moved == ["cache", "files", "logs"]
    assert services["db"]["volumes"] == ["shared:/var/lib/postgresql/data"]
    assert services["backup"]["volumes"] == ["shared:/dump:ro"], "one volume is never split between two places"
    assert services["backend"]["volumes"] == [
        "./files:/data:ro",
        {"type": "bind", "source": "./cache", "target": "/cache"},
        {"source": "./logs", "target": "/logs", "type": "bind"},
        "./config:/config:ro",
        "/srv/shared:/shared",
        "/tmp/anon",
    ]
    assert volumes == {"shared": None}
