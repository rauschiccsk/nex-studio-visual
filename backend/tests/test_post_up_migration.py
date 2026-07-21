"""Post-``up`` DB migration (v4.0.21): a deploy migrates an alembic app that ships
no migrate service of its own, so it never boots against an un-migrated schema.

An app WITH a dedicated migrate service already migrates on ``docker compose up``; one
WITHOUT (e.g. nex-shopify) used to boot un-migrated — the launch table simply didn't
exist (crash-test 2026-07-21). ``_run_post_up_migration`` runs the one missing
``alembic upgrade head`` in the backend container, idempotently, and fails the deploy
only when the migration itself fails.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from backend.services import orchestrator
from backend.services.uat_provisioner import has_alembic_migrate_service

# ── has_alembic_migrate_service ──────────────────────────────────────────────


@pytest.mark.parametrize(
    "services,expected",
    [
        ({"migrate": {"command": ["alembic", "upgrade", "head"]}}, True),
        ({"m": {"command": "alembic upgrade head"}}, True),  # string command form
        ({"backend": {"image": "x"}, "db": {"image": "pg"}}, False),  # no migrate
        ({"worker": {"command": ["python", "-m", "app.worker"]}}, False),
        ({}, False),
    ],
)
def test_has_alembic_migrate_service(services, expected) -> None:
    assert has_alembic_migrate_service(services) is expected


# ── _run_post_up_migration ───────────────────────────────────────────────────


class _FakeProc:
    def __init__(self, rc: int, out: bytes = b"") -> None:
        self.returncode = rc
        self._out = out

    async def communicate(self):
        return self._out, None

    def kill(self) -> None:  # pragma: no cover - only on timeout
        pass


def _make_project(root: Path, *, alembic: bool, migrate_service: bool) -> None:
    proj = root / "nex-shopify"
    (proj / "backend").mkdir(parents=True, exist_ok=True)
    if alembic:
        (proj / "backend" / "alembic.ini").write_text("[alembic]\n", encoding="utf-8")
    services: dict = {"backend": {"image": "x"}, "postgres": {"image": "postgres:16"}}
    if migrate_service:
        services["migrate"] = {"command": ["alembic", "upgrade", "head"]}
    (proj / "docker-compose.yml").write_text(yaml.safe_dump({"services": services}), encoding="utf-8")


def _wire(monkeypatch, tmp_path: Path, *, procs=None) -> None:
    """Point PROJECTS_ROOT + the instance compose at tmp; feed queued fake exec results."""
    monkeypatch.setattr(orchestrator.claude_agent, "PROJECTS_ROOT", tmp_path)
    compose = tmp_path / "instance-compose.yml"
    compose.write_text("services: {}\n", encoding="utf-8")
    monkeypatch.setattr(orchestrator, "_uat_compose_path", lambda *a, **k: compose)

    async def _noop_sleep(_):
        return None

    monkeypatch.setattr(orchestrator.asyncio, "sleep", _noop_sleep)

    if procs is not None:
        it = iter(procs)

        async def _fake_exec(*a, **k):
            return next(it)

        monkeypatch.setattr(orchestrator.asyncio, "create_subprocess_exec", _fake_exec)


async def test_skips_when_no_alembic(monkeypatch, tmp_path: Path) -> None:
    _make_project(tmp_path, alembic=False, migrate_service=False)
    _wire(monkeypatch, tmp_path)  # no procs → any exec would raise StopIteration
    ok, detail = await orchestrator._run_post_up_migration("nex-shopify", "andros-shopify")
    assert ok and detail == "OK"


async def test_skips_when_migrate_service_present(monkeypatch, tmp_path: Path) -> None:
    _make_project(tmp_path, alembic=True, migrate_service=True)
    _wire(monkeypatch, tmp_path)  # migrate service already ran on `up` → no exec
    ok, detail = await orchestrator._run_post_up_migration("nex-shopify", "andros-shopify")
    assert ok and detail == "OK"


async def test_runs_migration_when_missing_service(monkeypatch, tmp_path: Path) -> None:
    _make_project(tmp_path, alembic=True, migrate_service=False)
    _wire(monkeypatch, tmp_path, procs=[_FakeProc(0, b"upgraded")])
    ok, detail = await orchestrator._run_post_up_migration("nex-shopify", "andros-shopify")
    assert ok and detail == "OK"


async def test_migration_failure_fails_the_deploy(monkeypatch, tmp_path: Path) -> None:
    _make_project(tmp_path, alembic=True, migrate_service=False)
    _wire(monkeypatch, tmp_path, procs=[_FakeProc(1, b"alembic.util.exc.CommandError: boom")])
    ok, detail = await orchestrator._run_post_up_migration("nex-shopify", "andros-shopify")
    assert not ok
    assert "migrácia zlyhala" in detail and "boom" in detail


async def test_retries_while_container_not_ready(monkeypatch, tmp_path: Path) -> None:
    _make_project(tmp_path, alembic=True, migrate_service=False)
    # First exec: container not execable yet → retry; second: success.
    procs = [_FakeProc(1, b'service "backend" is not running'), _FakeProc(0, b"upgraded")]
    _wire(monkeypatch, tmp_path, procs=procs)
    ok, detail = await orchestrator._run_post_up_migration("nex-shopify", "andros-shopify")
    assert ok and detail == "OK"
