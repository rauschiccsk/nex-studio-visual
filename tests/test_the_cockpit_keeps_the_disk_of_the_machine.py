"""DEV-305 — the cockpit checks the room on the machine's disk before a deploy and cleans up after one.

04.11.2026 the first deploy of NEX Knowledge to the PROD of ICC filled MAGER's system disk while it built a 4.4 GB image
(„no space left on device“), and the Postgres of MÁGERSTAV's NEX Inbox on the same disk restarted with a recovery.
Docker there held 22.4 GB of images — every deployed version — and 21.6 GB of build cache; the cockpit never cleaned up
and never asked whether there was room. Now a deploy without room does not start and says why, a deploy that came up
removes the installation's images of older versions (the running one and the previous stay) and keeps the build cache
under its cap, and a deploy a full disk failed says so in a sentence. Director 04.11.2026: „Súhlasím“.
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
    """A Docker machine: its free space, its images, what was removed and pruned."""

    def __init__(self, monkeypatch, *, free, images=(), here=(), free_after=None, removable=lambda ref: True):
        self.free = [free, free_after if free_after is not None else free]
        self.images, self.here = list(images), list(here)
        self.removed: list[str] = []
        self.pruned: list[int] = []
        self.asked: list[object] = []
        self._removable = removable
        monkeypatch.setattr(M, "_free_bytes", self._free_bytes)
        monkeypatch.setattr(M, "_images", lambda host: self.images if host else (self.here or self.images))
        monkeypatch.setattr(M, "_remove", self._remove)
        monkeypatch.setattr(M, "_prune_build_cache", lambda cap, host: self.pruned.append(cap) or True)

    def _free_bytes(self, host):
        self.asked.append(host)
        return self.free.pop(0) if len(self.free) > 1 else self.free[0]

    def _remove(self, reference, host):
        if self._removable(reference):
            self.removed.append(reference)
            return True
        return False


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


# ---------- cleaning after the deploy ----------


def test_after_a_deploy_the_older_versions_go_and_the_previous_stays(tmp_path, monkeypatch):
    images = [
        _image(f"{APP}-backend", "v1.2.0", "4"),
        _image(f"{APP}-backend", "v1.1.0", "3"),
        _image(f"{APP}-backend", "1.0.0", "2"),
        _image(f"{APP}-backend", "v1.0.0", "1"),
        _image(f"{APP}-worker", "v1.2.0", "4"),
        _image(f"{APP}-worker", "v1.1.0", "3"),
        _image("mager-inbox-backend", "1.5.0", "1"),
        _image("pgvector/pgvector", "pg15", "1"),
    ]
    machine = _Machine(monkeypatch, free=12 * GB, free_after=20 * GB, images=images)

    said = M.clean_after_deploy(_compose(tmp_path / "docker-compose.yml"), "mager")

    assert machine.removed == [f"{APP}-backend:1.0.0", f"{APP}-backend:v1.0.0"]
    assert machine.pruned == [settings.deploy_build_cache_cap_gb * GB]
    assert said == [
        f"Kokpit po nasadení na stroji MAGER zmazal obrazy starších verzií (2): {APP}-backend:1.0.0, "
        f"{APP}-backend:v1.0.0; voľné miesto na disku Dockera 12,0 GB → 20,0 GB."
    ]


def test_an_image_a_container_still_uses_stays_and_is_said(tmp_path, monkeypatch):
    images = [_image(f"{APP}-backend", t, d) for t, d in (("v1.2.0", "4"), ("v1.1.0", "3"), ("v1.0.0", "2"))]
    _Machine(monkeypatch, free=12 * GB, images=images, removable=lambda ref: False)

    said = M.clean_after_deploy(_compose(tmp_path / "docker-compose.yml"), None)

    assert said == [
        f"Tieto staré obrazy na stroji {settings.this_machine_label} ostali, používa ich kontajner: "
        f"{APP}-backend:v1.0.0."
    ]


def test_a_build_cache_that_did_not_shrink_is_said(tmp_path, monkeypatch):
    machine = _Machine(monkeypatch, free=12 * GB)
    monkeypatch.setattr(M, "_prune_build_cache", lambda cap, host: False)

    said = M.clean_after_deploy(_compose(tmp_path / "docker-compose.yml"), "mager")

    assert said == ["Medzipamäť stavieb na stroji MAGER sa nepodarilo zmenšiť."]
    assert machine.removed == []


def test_images_nobody_could_read_are_said_and_nothing_goes(tmp_path, monkeypatch):
    machine = _Machine(monkeypatch, free=12 * GB)
    monkeypatch.setattr(M, "_images", lambda host: None)

    said = M.clean_after_deploy(_compose(tmp_path / "docker-compose.yml"), "mager")

    assert said == ["Kokpit po nasadení nevedel prečítať obrazy na stroji MAGER, staré verzie preto neodpratal."]
    assert machine.removed == [] and machine.pruned == []


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
async def test_a_deploy_that_came_up_cleans_up_and_says_it(db_session, monkeypatch, installation):
    images = [_image(f"{APP}-backend", t, d) for t, d in (("v1.2.0", "4"), ("v1.1.0", "3"), ("v1.0.0", "2"))]
    machine = _Machine(monkeypatch, free=40 * GB, images=images)
    user = _make_user(db_session)
    project = _make_project(db_session, user=user)
    _make_version(db_session, project, "0.1.0")
    customer = _make_customer(db_session, project)

    outcome = await deploy_service.deploy(
        db_session,
        customer.id,
        version_number="0.1.0",
        environment="uat",
        actor_id=user.id,
        deploy_runner=_FakeRunner(),
    )

    assert machine.removed == [f"{APP}-backend:v1.0.0"]
    assert any(f"zmazal obrazy starších verzií (1): {APP}-backend:v1.0.0" in w for w in outcome.warnings), outcome
    assert outcome[0].status == "ok"


@pytest.mark.asyncio
async def test_a_deploy_a_full_disk_failed_says_it_plainly(db_session, monkeypatch, installation):
    machine = _Machine(monkeypatch, free=40 * GB, images=[_image(f"{APP}-backend", "v1.0.0", "1")])
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
    assert machine.removed == [] and machine.pruned == [], "a failed deploy cleans nothing"
