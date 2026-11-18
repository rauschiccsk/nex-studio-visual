"""DEV-425 — building and starting an app has one limit: a deploy on any machine and both steps of Verifikácia.

15 minutes came on 16.06.2026 as a backstop over builds of 1–2 minutes (CR-NS-098) and Verifikácia copied it; another
machine got 45 minutes on 25.09.2026 (ICCINT-151). Orthodox Hermenia's backend image of 6–8 GB would not fit 15 minutes
on ANDROS's line at its first build (DEV-424). Director 18.11.2026: „Súhlasím“.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from backend.config.settings import settings
from backend.services import deploy_rehearsal as R
from backend.services import orchestrator as O
from backend.services import verified_build

#: A limit no constant in the code has — whatever reaches the steps was read from the settings.
LIMIT = 1234


def test_the_limit_is_45_minutes() -> None:
    assert settings.deploy_build_timeout_seconds == 45 * 60


@pytest.fixture
def limit(monkeypatch):
    monkeypatch.setattr(settings, "deploy_build_timeout_seconds", LIMIT)


@pytest.mark.asyncio
@pytest.mark.parametrize("deploy_host", [None, "mager"])
async def test_a_deploy_on_any_machine_builds_within_the_limit(monkeypatch, tmp_path, limit, deploy_host) -> None:
    compose = tmp_path / "docker-compose.yml"
    compose.write_text("services:\n  backend:\n    build: ./backend\n", encoding="utf-8")
    limits: list[int] = []

    async def _step(cmd, env, deadline, step_limit):
        limits.append(step_limit)
        return False, "stop here"

    async def _no_provenance(*_a, **_k):
        return {}

    async def _target_compose(*_a, **_k):
        return 0, compose.read_text(encoding="utf-8")

    monkeypatch.setattr(O, "_deploy_compose_step", _step)
    monkeypatch.setattr(O, "_build_provenance_env", _no_provenance)
    monkeypatch.setattr(O, "_uat_compose_path", lambda *a, **k: compose)
    monkeypatch.setattr(O, "_verify_env_for_target", lambda host: {})
    monkeypatch.setattr(O, "_compose_smoke_step", _target_compose)
    monkeypatch.setattr(O, "_remote_compose_text", lambda rc, out: (None, out))
    monkeypatch.setattr(O, "_compose_drift", lambda ours, theirs: "")
    monkeypatch.setattr(verified_build, "prepare", lambda compose, commit=None: verified_build.Preparation(None))

    ok, _detail = await O._run_uat_deploy(
        "nex-manager",
        "skit",
        environment="prod" if deploy_host else "uat",
        customer_slug="skit",
        app="manager",
        full_project_slug="nex-manager",
        deploy_host=deploy_host,
    )

    assert not ok
    assert limits == [LIMIT]


class _Steps:
    """Every ``docker compose`` step with the time it was given."""

    def __init__(self) -> None:
        self.calls: list[tuple[list[str], int]] = []

    async def __call__(self, cmd, timeout, env=None):
        self.calls.append((cmd, timeout))
        return 0, "ok"

    def limit_of_up(self) -> list[int]:
        return [timeout for cmd, timeout in self.calls if "up" in cmd]


@pytest.mark.asyncio
async def test_verifikacia_starts_the_whole_stack_within_the_limit(monkeypatch, tmp_path, limit) -> None:
    (tmp_path / "docker-compose.yml").write_text("services:\n  backend:\n    build: .\n", encoding="utf-8")
    steps = _Steps()
    monkeypatch.setattr(O, "_compose_smoke_step", steps)

    async with O._boot_smoke_stack("p", tmp_path / "docker-compose.yml", {}):
        pass

    assert steps.limit_of_up() == [LIMIT]


@pytest.mark.asyncio
async def test_the_deploy_rehearsal_builds_and_starts_within_the_limit(monkeypatch, tmp_path, limit) -> None:
    folder = tmp_path / "inst"
    folder.mkdir()
    installation = {"services": {"backend": {"build": {"context": "./backend"}, "container_name": "uat-x-backend"}}}
    (folder / "docker-compose.yml").write_text(yaml.safe_dump(installation), encoding="utf-8")
    (folder / ".env").write_text("SECRET_KEY=x\n", encoding="utf-8")
    steps = _Steps()

    async def _ready(*_a, **_k):
        return True, "200"

    async def _throw_away(*_a, **_k):
        return None

    monkeypatch.setattr(O, "_compose_smoke_step", steps)
    monkeypatch.setattr(O, "_await_http_ready", _ready)
    monkeypatch.setattr(O, "throw_away_rehearsal_images", _throw_away)
    monkeypatch.setattr(verified_build, "prepare", lambda compose, commit=None: verified_build.Preparation(None))

    await R._run_one(
        R.Rendered("inštalácia zákazníka x", folder / "docker-compose.yml", folder / ".env"),
        "x-1",
        Path(tmp_path),
        "1.0.0",
    )

    assert steps.limit_of_up() == [LIMIT]
