"""DEV-238 — a UAT installation can be built again from scratch; its data go with it, and only on UAT.

26.10.2026 the first deploy of Author Asistent wrote its UAT installation broken (DEV-235), and a redeploy keeps an
installation's settings (ICCINT-133) — nothing on the screen could rebuild it. The API had ``force_fresh``, and it
did not do what it said („rotating secrets, wiping data“): the provisioner rendered NEW database secrets, the deploy
started the installation with ``up -d --force-recreate``, and the database volume kept the old data and the old
password — the application could not have reached its database. Director 27.10.2026: „Súhlasím“.

Pinned: „nanovo“ on UAT takes the installation down WITH its volumes before anything is rendered; a teardown that
fails stops the deploy; a plain deploy takes nothing down; on PROD „nanovo“ is refused; the teardown is
``docker compose -f <compose> down -v --remove-orphans``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from backend.services import deploy as deploy_service
from backend.services import orchestrator, uat_provisioner
from tests.test_deploy_service import _deploy, _FakeRunner, _make_customer, _make_project, _make_user, _make_version


class _World:
    """The provisioner, the deploy leg and the teardown — each records that it ran, in order."""

    def __init__(self, monkeypatch, *, teardown_ok=True):
        self.steps: list[str] = []
        self.teardown_path: Path | None = None

        class _Result:
            warnings: list[str] = []
            fe_service = "frontend"
            compose_path = Path("/nonexistent/docker-compose.yml")

        def _provision(project_slug, uat_slug, *, version, rotate_secrets, **kw):
            self.steps.append(f"provision(rotate={rotate_secrets})")
            return _Result()

        async def _up(project_slug, uat_slug, **kw):
            self.steps.append("up")
            return True, "OK"

        async def _health(*_a, **_kw):
            return None

        def _down(compose_path, **_kw):
            self.steps.append("down -v")
            self.teardown_path = compose_path
            return (True, "OK") if teardown_ok else (False, "exit 1: volume is in use")

        monkeypatch.setattr(uat_provisioner, "provision_uat", _provision)
        monkeypatch.setattr(uat_provisioner, "compose_down_with_volumes", _down)
        monkeypatch.setattr(orchestrator, "_run_uat_deploy", _up)
        monkeypatch.setattr(orchestrator, "instance_health", _health)


def _run(force_fresh: bool, uat_slug: str = "tata-uat"):
    return asyncio.run(
        deploy_service._default_deploy_runner(
            project_slug="author-asistent", uat_slug=uat_slug, version_number="0.1.0", force_fresh=force_fresh
        )
    )


def test_fresh_on_uat_takes_the_installation_down_with_its_volumes_first(monkeypatch):
    world = _World(monkeypatch)

    ok, _detail, _url = _run(force_fresh=True)

    assert ok is True
    assert world.steps == ["down -v", "provision(rotate=True)", "up"]
    assert world.teardown_path == uat_provisioner.UAT_ROOT / "tata" / "author-asistent" / "docker-compose.yml"


def test_a_failed_teardown_stops_before_anything_is_rendered(monkeypatch):
    world = _World(monkeypatch, teardown_ok=False)

    ok, detail, url = _run(force_fresh=True)

    assert (ok, url) == (False, None)
    assert world.steps == ["down -v"]
    assert "nepodarilo zrušiť" in detail and "volume is in use" in detail


def test_a_plain_deploy_takes_nothing_down(monkeypatch):
    world = _World(monkeypatch)

    _run(force_fresh=False)

    assert world.steps == ["provision(rotate=False)", "up"]


@pytest.mark.asyncio
async def test_fresh_on_prod_is_refused_before_anything_runs(db_session):
    user = _make_user(db_session)
    project = _make_project(db_session, user=user)
    _make_version(db_session, project, "0.1.0")
    customer = _make_customer(db_session, project)
    runner = _FakeRunner()

    with pytest.raises(ValueError, match="nanovo"):
        await _deploy(
            db_session,
            customer,
            version_number="0.1.0",
            environment="prod",
            actor=user,
            runner=runner,
            force_fresh=True,
        )

    assert runner.calls == []


def test_the_teardown_is_compose_down_with_volumes(monkeypatch, tmp_path):
    compose = tmp_path / "docker-compose.yml"
    compose.write_text("services: {}\n")
    seen: list[list[str]] = []

    class _Done:
        returncode = 0
        stdout = ""
        stderr = ""

    def _fake_run(cmd, **_kw):
        seen.append(cmd)
        return _Done()

    monkeypatch.setattr(uat_provisioner.subprocess, "run", _fake_run)

    assert uat_provisioner.compose_down_with_volumes(compose) == (True, "OK")
    assert seen == [["docker", "compose", "-f", str(compose), "down", "-v", "--remove-orphans"]]


def test_no_installation_is_nothing_to_take_down(monkeypatch, tmp_path):
    def _never(cmd, **_kw):
        raise AssertionError("docker was asked to take down an installation that is not there")

    monkeypatch.setattr(uat_provisioner.subprocess, "run", _never)

    ok, _detail = uat_provisioner.compose_down_with_volumes(tmp_path / "docker-compose.yml")

    assert ok is True
