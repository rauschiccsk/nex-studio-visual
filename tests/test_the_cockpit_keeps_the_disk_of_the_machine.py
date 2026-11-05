"""DEV-305 — the cockpit checks the room on the machine's disk before a deploy and says a full disk plainly.

04.11.2026 the first deploy of NEX Knowledge to the PROD of ICC filled MAGER's system disk while it built a 4.4 GB image
(„no space left on device“), and the Postgres of MÁGERSTAV's NEX Inbox on the same disk restarted with a recovery.
Docker there held 22.4 GB of images and 21.6 GB of build cache, and the cockpit never asked whether there was room.
Now a deploy without room does not start and says why, and a deploy a full disk failed says so in a sentence. Cleaning
up after a deploy stays where it was (DEV-124, ICCINT-154 / DEV-166 — ``tests/test_the_cockpit_cleans_up_after_a_deploy
_in_one_place.py``). Director 04.11.2026: „Súhlasím“.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from backend.config.settings import settings
from backend.services import deploy as deploy_service
from backend.services import machine_disk as M
from backend.services import uat_provisioner
from tests.test_deploy_service import _FakeRunner, _make_customer, _make_project, _make_user, _make_version

GB = M.GB
APP = "icc-knowledge"


def _compose(path: Path, tag: str = "v1.2.0") -> Path:
    """An installation as the cockpit writes it: its images named after their containers, a foreign database."""
    path.parent.mkdir(parents=True, exist_ok=True)
    services = {
        "db": {"image": "pgvector/pgvector:pg16", "container_name": f"{APP}-db"},
        "backend": {"image": f"{APP}-backend:{tag}", "container_name": f"{APP}-backend"},
        "worker": {"image": f"{APP}-worker:{tag}", "container_name": f"{APP}-worker"},
        "frontend": {"image": "nginx:1.27", "container_name": f"{APP}-frontend"},
    }
    path.write_text(yaml.safe_dump({"services": services}), encoding="utf-8")
    return path


class _Machine:
    """A Docker machine: its free space and its images."""

    def __init__(self, monkeypatch, *, free, images=(), here=()):
        self.free = free
        self.images, self.here = list(images), list(here)
        self.asked: list[object] = []
        monkeypatch.setattr(M, "_free_bytes", self._free_bytes)
        monkeypatch.setattr(M, "_images", lambda host: self.images if host else (self.here or self.images))

    def _free_bytes(self, host):
        self.asked.append(host)
        return self.free


def _image(repository: str, tag: str, created: str, size: float = 0.3) -> M.Image:
    return M.Image(repository, tag, f"2026-11-0{created} 10:00:00 +0000 UTC", int(size * 1000**3))


# ---------- what Docker says, read ----------


def test_the_free_room_is_the_least_of_dockers_folders():
    df = (
        "Filesystem     1024-blocks      Used Available Capacity Mounted on\n"
        "/dev/sda2        102687672  66539732  31038176  69% /m0\n"
        "/dev/sda2        102687672  66539732  12000000  69% /m1\n"
    )
    assert M.free_from_df(df) == 12000000 * 1024
    assert M.free_from_df("df: /m0: No such file or directory\n") is None


@pytest.mark.parametrize("text, size", [("4.42GB", 4_420_000_000), ("49.6MB", 49_600_000), ("0B", 0), ("", 0)])
def test_dockers_sizes_are_read_in_bytes(text, size):
    assert M.size_from_docker(text) == size


def test_the_images_are_read_without_the_untagged():
    text = (
        "icc-knowledge-backend\tv1.0.0\t2026-11-04 20:29:40 +0000 UTC\t4.42GB\n"
        "<none>\t<none>\t2026-11-04 19:40:00 +0000 UTC\t2.04GB\n"
    )
    assert M.images_from(text) == [M.Image("icc-knowledge-backend", "v1.0.0", "2026-11-04 20:29:40 +0000 UTC", 4.42e9)]


def test_only_the_images_the_cockpit_named_for_the_installation_are_its(tmp_path):
    compose = _compose(tmp_path / "docker-compose.yml")
    assert M.installation_images(compose) == {f"{APP}-backend": "v1.2.0", f"{APP}-worker": "v1.2.0"}
    assert M.installation_images(tmp_path / "none.yml") == {}


# ---------- room before the build ----------


def test_a_machine_with_room_lets_the_deploy_start(tmp_path, monkeypatch):
    _Machine(monkeypatch, free=40 * GB, images=[_image(f"{APP}-backend", "v1.2.0", "4", 4.42)])
    compose = _compose(tmp_path / "prod" / "docker-compose.yml")

    assert M.room_refusal("mager", installation=compose, built_here=tmp_path / "uat.yml") is None


def test_a_first_prod_knows_the_size_from_the_uat_here_and_stops_without_room(tmp_path, monkeypatch):
    """04.11.2026: MAGER had ~12 GB free, NEX Knowledge's image 4.42 GB — the build filled the disk."""
    uat = "uat-icc-knowledge"
    machine = _Machine(monkeypatch, free=12 * GB, here=[_image(f"{uat}-backend", "v0.2.1", "4", 4.42)])
    built_here = tmp_path / "uat" / "docker-compose.yml"
    built_here.parent.mkdir(parents=True)
    built_here.write_text(
        yaml.safe_dump(
            {"services": {"backend": {"image": f"{uat}-backend:v0.2.1", "container_name": f"{uat}-backend"}}}
        ),
        encoding="utf-8",
    )

    said = M.room_refusal("mager", installation=tmp_path / "prod" / "docker-compose.yml", built_here=built_here)

    assert said == (
        "Nasadenie sa nezačalo: na stroji MAGER je na disku Dockera voľných 12,0 GB, stavba potrebuje aspoň 20,3 GB — "
        "obraz aplikácie má 4,1 GB a stavba zaberie asi 2,5-násobok, plus 10 GB rezerva pre databázy na tom istom "
        "disku. Požiadaj správcu stroja MAGER, aby uvoľnil miesto, a nasaď znova."
    )
    assert machine.asked == ["mager"], "the room is measured on the machine that builds"


def test_images_of_other_installations_do_not_count(tmp_path, monkeypatch):
    _Machine(monkeypatch, free=12 * GB, images=[_image("mager-inbox-backend", "1.10.2", "3", 9.0)])
    compose = _compose(tmp_path / "docker-compose.yml")

    assert M.room_refusal("mager", installation=compose, built_here=tmp_path / "uat.yml") is None


def test_a_room_nobody_could_measure_stops_the_deploy(tmp_path, monkeypatch):
    _Machine(monkeypatch, free=None)

    said = M.room_refusal(None, installation=tmp_path / "a.yml", built_here=tmp_path / "b.yml")

    assert said and said.startswith("Nasadenie sa nezačalo: kokpit nevedel zmerať voľné miesto")
    assert settings.this_machine_label in said


def test_a_full_disk_that_failed_a_deploy_is_said_in_a_sentence():
    docker = "exit 1: failed to extract layer …: write /var/lib/containerd/…: no space left on device"

    assert M.said_plainly(docker, "mager") == (
        "Na stroji MAGER sa počas nasadenia minulo miesto na disku — stavba nedobehla. Požiadaj správcu stroja MAGER, "
        f"aby uvoľnil miesto, a nasaď znova. Hláška Dockeru: {docker}"
    )
    assert M.said_plainly("exit 1: migrate failed", "mager") == "exit 1: migrate failed"


# ---------- the deploy itself ----------


@pytest.fixture
def installation(tmp_path, monkeypatch):
    """Where the deploy finds the installation it deploys to — a compose of the cockpit's naming."""
    path = tmp_path / "instalacia"
    monkeypatch.setattr(uat_provisioner, "instance_dir_for", lambda **kw: path / kw["environment"])
    _compose(path / "uat" / "docker-compose.yml")
    return path / "uat"


@pytest.mark.asyncio
async def test_a_deploy_without_room_does_not_start(db_session, monkeypatch, installation):
    _Machine(monkeypatch, free=2 * GB)
    user = _make_user(db_session)
    project = _make_project(db_session, user=user)
    _make_version(db_session, project, "0.1.0")
    customer = _make_customer(db_session, project)
    runner = _FakeRunner()

    with pytest.raises(ValueError, match="^Nasadenie sa nezačalo: na stroji ANDROS je na disku Dockera voľných 2,0 GB"):
        await deploy_service.deploy(
            db_session, customer.id, version_number="0.1.0", environment="uat", actor_id=user.id, deploy_runner=runner
        )

    assert runner.calls == [], "nothing was written or built"


@pytest.mark.asyncio
async def test_a_deploy_a_full_disk_failed_says_it_plainly(db_session, monkeypatch, installation):
    _Machine(monkeypatch, free=40 * GB, images=[_image(f"{APP}-backend", "v1.0.0", "1")])
    user = _make_user(db_session)
    project = _make_project(db_session, user=user)
    _make_version(db_session, project, "0.1.0")
    customer = _make_customer(db_session, project)
    runner = _FakeRunner(ok=False, detail="exit 1: write /var/lib/docker/x: no space left on device")

    outcome = await deploy_service.deploy(
        db_session, customer.id, version_number="0.1.0", environment="uat", actor_id=user.id, deploy_runner=runner
    )

    assert outcome[0].status == "failed"
    assert outcome[0].detail.startswith("Na stroji ANDROS sa počas nasadenia minulo miesto na disku")


# ---------- which folders are measured ----------

#: The real primitive, taken before the suite's default stub replaces it for every test.
REAL_FREE_BYTES = M._free_bytes


class _Docker:
    """Docker as ``_free_bytes`` meets it: ``info``, the read of containerd's config, the ``df`` container."""

    def __init__(self, driver_status: str, config: str | None = None):
        self.driver_status, self.config = driver_status, config
        self.calls: list[list[str]] = []

    def __call__(self, args, deploy_host, *, timeout=120):
        import subprocess

        self.calls.append(args)
        if args[0] == "info":
            return subprocess.CompletedProcess(args, 0, f"/data/docker|{self.driver_status}\n", "")
        if args[-2:] == ["cat", "/c/config.toml"]:
            found = self.config is not None
            return subprocess.CompletedProcess(
                args, 0 if found else 1, self.config or "", "" if found else "no such file"
            )
        targets = [a for a in args if a.startswith("/m")]
        lines = "".join(f"/dev/sda3 857000000 140000000 600000000 18% {t}\n" for t in targets)
        return subprocess.CompletedProcess(
            args, 0, "Filesystem 1024-blocks Used Available Capacity Mounted on\n" + lines, ""
        )

    def measured(self) -> list[str]:
        df = next(c for c in self.calls if "df" in c)
        return [m.split(",")[1].removeprefix("source=") for m in df if m.startswith("type=bind")]


SNAPSHOTTER = '[["driver-type","io.containerd.snapshotter.v1"]]'


def test_containerd_is_measured_where_the_machine_keeps_it(monkeypatch):
    """MAGER since 04.11.2026 (SERVER-148): ``root = "/data/containerd"`` in /etc/containerd/config.toml; the old
    /var/lib/containerd still exists with the data kept for a way back — measuring it would read the wrong disk."""
    docker = _Docker(SNAPSHOTTER, config='version = 2\nroot = "/data/containerd"\n\n[grpc]\n  address = "/run/x"\n')
    monkeypatch.setattr(M, "_docker", docker)

    assert REAL_FREE_BYTES("mager") == 600000000 * 1024
    assert docker.measured() == ["/data/docker", "/data/containerd"]


def test_containerd_without_a_root_of_its_own_is_measured_at_its_default(monkeypatch):
    docker = _Docker(SNAPSHOTTER, config=None)
    monkeypatch.setattr(M, "_docker", docker)

    assert REAL_FREE_BYTES("mager") == 600000000 * 1024
    assert docker.measured() == ["/data/docker", "/var/lib/containerd"]


def test_docker_without_containerd_measures_its_own_folder_only(monkeypatch):
    docker = _Docker('[["Backing Filesystem","extfs"]]')
    monkeypatch.setattr(M, "_docker", docker)

    assert REAL_FREE_BYTES(None) == 600000000 * 1024
    assert docker.measured() == ["/data/docker"]
    assert not any(c[-2:] == ["cat", "/c/config.toml"] for c in docker.calls), "no containerd, no config to read"


def test_a_containerd_config_that_does_not_read_is_no_measurement(monkeypatch):
    monkeypatch.setattr(M, "_docker", _Docker(SNAPSHOTTER, config='root = "/data/containerd'))

    assert REAL_FREE_BYTES("mager") is None
