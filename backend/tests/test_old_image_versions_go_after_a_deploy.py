"""DEV-124 — after a successful deploy only the running version of the installation's images and one to go back to
stay on the machine; older versions nobody uses go.

14.10.2026: on MAGER the NEX Inbox images 1.5.6 (backend ~3 GB) stayed beside 1.8.0 and 1.7.1; on ANDROS
``nex-inbox-backend`` had nine versions of ~2.1 GB each. The cleanup after a deploy (ICCINT-154) removed the build
cache and unnamed layers only — named older versions stayed forever, and every deploy added one.

What the guards hold: only after a SUCCESSFUL deploy; only the repositories the installation's compose names; the
version deployed, every newer one and the newest older one (the way back) stay; an image any container uses — stopped,
another installation's, another customer's — stays; tags that are not versions stay; a failure never fails the deploy.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import yaml

from backend.services import orchestrator as O

GB = 1024**3
#: The images on the machine: ``repo:tag`` → (image id, size). v1.4.0 … v1.8.0 of one app, plus another app.
IMAGES = {
    "nex-inbox-backend:v1.4.0": ("sha256:a140", 2.1),
    "nex-inbox-backend:v1.5.0": ("sha256:a150", 2.1),
    "nex-inbox-backend:v1.5.6": ("sha256:a156", 2.1),
    "nex-inbox-backend:v1.6.0": ("sha256:a160", 2.1),
    "nex-inbox-backend:v1.7.1": ("sha256:a171", 2.1),
    "nex-inbox-backend:v1.8.0": ("sha256:a180", 2.1),
    "nex-inbox-backend:latest": ("sha256:a999", 2.1),
    "nex-inbox-frontend:v1.6.0": ("sha256:f160", 0.05),
    "nex-inbox-frontend:v1.7.1": ("sha256:f171", 0.05),
    "nex-inbox-frontend:v1.8.0": ("sha256:f180", 0.05),
    "nex-manager-backend:v1.0.0": ("sha256:m100", 1.0),
}


class _Proc:
    def __init__(self, out: str = "", returncode: int = 0):
        self._out, self.returncode = out.encode(), returncode

    async def communicate(self):
        return self._out, b""


@pytest.fixture
def compose(tmp_path) -> Path:
    path = tmp_path / "docker-compose.yml"
    services = {
        "backend": {"image": "nex-inbox-backend:v1.8.0"},
        "alembic-init": {"image": "nex-inbox-backend:v1.8.0"},
        "frontend": {"image": "nex-inbox-frontend:v1.8.0"},
        "postgres": {"image": "postgres:16-alpine"},
    }
    path.write_text(yaml.safe_dump({"services": services}), encoding="utf-8")
    return path


def _docker(monkeypatch, *, used: tuple[str, ...] = (), images=None, rm_fails: tuple[str, ...] = ()):
    """Docker on the target, in memory: lists images and the images containers use, removes tags."""
    images = dict(IMAGES if images is None else images)
    calls: list[dict] = []

    async def _exec(*cmd, **kw):
        cmd = list(cmd)
        calls.append({"cmd": cmd, "env": kw.get("env")})
        if cmd[:3] == ["docker", "image", "ls"]:
            rows = [f"{ref.rsplit(':', 1)[0]}\t{ref.rsplit(':', 1)[1]}\t{i}\t{s}GB" for ref, (i, s) in images.items()]
            return _Proc("\n".join(rows) + "\n")
        if cmd[:3] == ["docker", "ps", "-a"]:
            return _Proc("".join(f"c{n}\n" for n, _ in enumerate(used)))
        if cmd[:2] == ["docker", "inspect"]:
            return _Proc("".join(f"{images[ref][0]}\n" for ref in used))
        if cmd[:3] == ["docker", "image", "rm"]:
            if cmd[3] in rm_fails:
                return _Proc("Error response from daemon: conflict", 1)
            images.pop(cmd[3])
            return _Proc(f"Untagged: {cmd[3]}\n")
        raise AssertionError(f"unexpected docker call {cmd}")

    monkeypatch.setattr(O.asyncio, "create_subprocess_exec", _exec)
    return calls, images


def _removed(calls) -> list[str]:
    return [c["cmd"][3] for c in calls if c["cmd"][:3] == ["docker", "image", "rm"]]


def test_the_running_version_and_the_way_back_stay_older_ones_go(monkeypatch, compose) -> None:
    calls, left = _docker(monkeypatch)
    detail = asyncio.run(O._uprac_stare_verzie(True, "OK", "mager", compose))
    assert _removed(calls) == [
        "nex-inbox-backend:v1.6.0",
        "nex-inbox-backend:v1.5.6",
        "nex-inbox-backend:v1.5.0",
        "nex-inbox-backend:v1.4.0",
        "nex-inbox-frontend:v1.6.0",
    ]
    assert {"nex-inbox-backend:v1.8.0", "nex-inbox-backend:v1.7.1", "nex-inbox-frontend:v1.7.1"} <= set(left)
    assert "nex-inbox-backend:latest" in left and "nex-manager-backend:v1.0.0" in left
    assert detail == (
        "OK Staršie verzie obrazov zmazané: nex-inbox-backend v1.6.0, v1.5.6, v1.5.0, v1.4.0; "
        "nex-inbox-frontend v1.6.0 (spolu približne 8,4 GB)."
    )


def test_an_older_version_a_container_uses_stays(monkeypatch, compose) -> None:
    """UAT and PROD of different customers may run different versions of the same images on one machine."""
    calls, left = _docker(monkeypatch, used=("nex-inbox-backend:v1.5.0",))
    asyncio.run(O._uprac_stare_verzie(True, "OK", "mager", compose))
    assert "nex-inbox-backend:v1.5.0" in left
    assert "nex-inbox-backend:v1.5.0" not in _removed(calls)
    assert "nex-inbox-backend:v1.4.0" not in left


def test_a_newer_version_than_the_deployed_one_stays(monkeypatch, compose) -> None:
    """Going back to 1.7.1 after a failed 1.8.0 must not throw 1.8.0 away."""
    compose.write_text(
        yaml.safe_dump({"services": {"backend": {"image": "nex-inbox-backend:v1.7.1"}}}), encoding="utf-8"
    )
    calls, left = _docker(monkeypatch)
    asyncio.run(O._uprac_stare_verzie(True, "OK", "mager", compose))
    assert {"nex-inbox-backend:v1.8.0", "nex-inbox-backend:v1.7.1", "nex-inbox-backend:v1.6.0"} <= set(left)
    assert _removed(calls) == ["nex-inbox-backend:v1.5.6", "nex-inbox-backend:v1.5.0", "nex-inbox-backend:v1.4.0"]


def test_a_failed_deploy_removes_nothing(monkeypatch, compose) -> None:
    calls, _ = _docker(monkeypatch)
    assert asyncio.run(O._uprac_stare_verzie(False, "deploy zlyhal", "mager", compose)) == "deploy zlyhal"
    assert calls == []


@pytest.mark.parametrize("target, docker_host", [("mager", "ssh://mager"), (None, None)])
def test_the_machine_the_installation_runs_on_is_cleaned(monkeypatch, compose, target, docker_host) -> None:
    """On the customer's machine through its Docker, and on this one too — nine versions of 2 GB stood on ANDROS."""
    calls, _ = _docker(monkeypatch)
    asyncio.run(O._uprac_stare_verzie(True, "OK", target, compose))
    assert {(c["env"] or {}).get("DOCKER_HOST") for c in calls} == {docker_host}
    assert _removed(calls)


def test_nothing_to_remove_says_nothing(monkeypatch, compose) -> None:
    images = {k: v for k, v in IMAGES.items() if k.endswith(("v1.7.1", "v1.8.0"))}
    calls, _ = _docker(monkeypatch, images=images)
    assert asyncio.run(O._uprac_stare_verzie(True, "OK", "mager", compose)) == "OK"
    assert _removed(calls) == []


def test_a_failed_removal_is_said_and_never_fails_the_deploy(monkeypatch, compose) -> None:
    calls, left = _docker(monkeypatch, rm_fails=("nex-inbox-backend:v1.5.6",))
    detail = asyncio.run(O._uprac_stare_verzie(True, "OK", "mager", compose))
    assert "nex-inbox-backend:v1.4.0" not in left
    assert detail.endswith("⚠️ Niektoré staršie verzie obrazov sa zmazať nepodarilo: nex-inbox-backend:v1.5.6.")


def test_a_stuck_docker_is_not_waited_out_forever(monkeypatch, compose) -> None:
    class _Stuck:
        returncode = None

        async def communicate(self):
            await asyncio.sleep(10)

    async def _exec(*cmd, **kw):
        return _Stuck()

    monkeypatch.setattr(O.asyncio, "create_subprocess_exec", _exec)
    monkeypatch.setattr(O, "CLEANUP_TIMEOUT", 0.05)
    detail = asyncio.run(O._uprac_stare_verzie(True, "OK", "mager", compose))
    assert detail.startswith("OK ⚠️ Staršie verzie obrazov sa nepodarilo upratať")


# ── wired into the deploy itself ──────────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("environment", ["prod", "uat"])
def test_a_successful_deploy_looks_for_old_versions_of_its_images(tmp_path, monkeypatch, environment) -> None:
    from backend.tests.test_a_prod_deploy_backs_the_database_up_first import _deploy

    result, events, _ = _deploy(monkeypatch, tmp_path, environment=environment)
    assert result[0] is True, result[1]
    listed = [cmd for cmd, _ in events if cmd[:3] == ["docker", "image", "ls"]]
    assert listed, "the deploy never looked for older versions of its images"
