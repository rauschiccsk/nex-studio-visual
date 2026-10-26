"""DEV-232 — a service that keeps crashing fails the launch check, named with its own log.

26.10.2026, Author Asistent 0.1.0, Verifikácia: the launch check read „Aplikácia nabehla a odpovedá. Odpovedali:
backend, frontend. Dobehli čisto: migrate. V tom čase ešte bežali: test, test-db, worker“ — and said nothing of
``egress``, the worker's only way to the internet. Squid died at start on line 15 of the project's ``squid.conf``
and Docker restarted it over and over. The release check failed a minute later on „egress proxy not reachable
(gaierror)“ without the one log line that said why, so the Auditor built a copy of the whole app and spent 36 minutes
finding it; that copy fired the alert „Kontajner aa-verify-egress-1 sa dookola reštartuje“. Director: „Súhlasím“.

Measured the same day on a service that exits 1 under ``restart: unless-stopped`` (Docker Compose 2.37.1 and the
cockpit's 5.6.1): ``docker compose ps`` reports it as State ``restarting`` with ExitCode **0** — the code lives only
in the Status text „Restarting (1) … ago“ — and about one look in ten catches it ``running`` between two crashes;
``up --wait`` exits 0 and calls it „Healthy“.

Pinned: a service neither up nor cleanly finished fails the check, with its real exit code and its log; a service
Docker restarted between the look before and the look after the app answered fails it too, even when caught
running; one that restarted only before the first look (it waited for its database) does not; the second look
waits out the window; a failed release check carries the log of a service that crashed meanwhile; and the
Manažér's headline names the service.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from backend.config.settings import settings
from backend.services import claude_agent, failure_framing, orchestrator

WEB_APP = {"backend": "backend", "frontend": "frontend", "db": "db"}
SQUID_LOG = "FATAL: Bungled /etc/squid/squid.conf line 15: acl internal_names dstdomain localhost .localhost"


def _row(service: str, state: str, status: str = "", exit_code: int = 0) -> dict:
    return {"Service": service, "Name": f"x-smoke-{service}-1", "State": state, "Status": status, "ExitCode": exit_code}


#: How Docker Compose reports a service Docker keeps restarting — measured, ExitCode 0 included.
EGRESS_RESTARTING = _row("egress", "restarting", "Restarting (1) 4 seconds ago")
UP = [_row("backend", "running", "Up 9 seconds"), _row("frontend", "running", "Up 9 seconds")]
MIGRATE_DONE = _row("migrate", "exited", "Exited (0) 5 seconds ago")


def _stack(tmp_path: Path) -> orchestrator._SmokeStack:
    compose = tmp_path / "docker-compose.yml"
    compose.write_text("services:\n  backend:\n    build: .\n")
    return orchestrator._SmokeStack(
        base=["docker", "compose", "-p", "x-smoke"],
        compose=compose,
        override=tmp_path / "override.yml",
        project="x-smoke",
        roles=dict(WEB_APP),
        up_rc=0,
        up_detail="",
    )


class _Docker:
    """Stands in for the docker CLI. Before the app probe answers it shows `before`; afterwards `after`."""

    def __init__(self, before: list[dict], after: list[dict] | None = None, restarts=({}, {})) -> None:
        self.rows = (before, before if after is None else after)
        self.restarts = restarts
        self.answered = False
        self.logs_of: list[str] = []

    async def step(self, cmd, timeout, env=None):
        look = 1 if self.answered else 0
        if "ps" in cmd:
            return 0, "\n".join(json.dumps(r) for r in self.rows[look])
        if cmd[:2] == ["docker", "inspect"]:
            counts = self.restarts[look]
            return 0, "\n".join(f"/x-smoke-{s}-1 {n}" for s, n in counts.items())
        if "logs" in cmd:
            self.logs_of.append(cmd[-1])
            return 0, SQUID_LOG if cmd[-1] == "egress" else "ok"
        return 0, ""

    async def probe(self, stack, states=None):
        self.answered = True
        return True, "backend responded 200 /health"


def _wire(monkeypatch, docker: _Docker) -> None:
    monkeypatch.setattr(orchestrator, "_compose_smoke_step", docker.step)
    monkeypatch.setattr(orchestrator, "_run_app_starts_smoke", docker.probe)


@pytest.mark.asyncio
async def test_author_asistents_restarting_egress_fails_the_launch_check(monkeypatch, tmp_path):
    docker = _Docker([*UP, EGRESS_RESTARTING, MIGRATE_DONE])
    _wire(monkeypatch, docker)

    ok, detail = await orchestrator._boot_leg(_stack(tmp_path))

    assert ok is False
    assert detail.split("\n", 1)[0] == "kontajner zlyhal: egress (exit 1, dookola sa reštartuje)"
    assert "--- egress (state: restarting) ---" in detail and "Bungled" in detail
    assert docker.logs_of == ["egress"]
    assert docker.answered is False, "the app was probed although a service was already down"


@pytest.mark.asyncio
async def test_a_service_restarted_between_the_looks_fails_even_when_caught_running(monkeypatch, tmp_path):
    egress_up = _row("egress", "running", "Up Less than a second")
    docker = _Docker([*UP, egress_up], restarts=({"backend": 0, "egress": 3}, {"backend": 0, "egress": 6}))
    _wire(monkeypatch, docker)

    ok, detail = await orchestrator._boot_leg(_stack(tmp_path))

    assert ok is False
    assert detail.split("\n", 1)[0] == "kontajner zlyhal: egress (počas skúšky sa 3× reštartovala)"
    assert "Bungled" in detail and docker.logs_of == ["egress"]


@pytest.mark.asyncio
async def test_a_service_that_restarted_only_before_the_first_look_passes(monkeypatch, tmp_path):
    """The control: a backend that restarted once while its database came up, and then held."""
    docker = _Docker([*UP, MIGRATE_DONE], restarts=({"backend": 1}, {"backend": 1}))
    _wire(monkeypatch, docker)

    ok, detail = await orchestrator._boot_leg(_stack(tmp_path))

    assert (ok, detail) == (
        True,
        "Aplikácia nabehla a odpovedá. Odpovedali: backend, frontend. Dobehli čisto: migrate.",
    )


@pytest.mark.asyncio
async def test_restarts_the_first_look_could_not_count_are_not_judged(monkeypatch, tmp_path):
    """The other control: ``docker inspect`` failed before the probe, so a count after it is no growth."""
    docker = _Docker([*UP], restarts=({}, {"backend": 2}))
    _wire(monkeypatch, docker)

    ok, _detail = await orchestrator._boot_leg(_stack(tmp_path))

    assert ok is True


@pytest.mark.asyncio
async def test_a_service_that_went_down_after_the_app_answered_fails(monkeypatch, tmp_path):
    docker = _Docker([*UP, _row("egress", "running", "Up 1 second")], after=[*UP, EGRESS_RESTARTING])
    _wire(monkeypatch, docker)

    ok, detail = await orchestrator._boot_leg(_stack(tmp_path))

    assert ok is False
    assert detail.startswith("kontajner zlyhal: egress (exit 1, dookola sa reštartuje)")


@pytest.mark.asyncio
async def test_a_service_that_never_started_fails_with_its_state(monkeypatch, tmp_path):
    docker = _Docker([*UP, _row("worker", "created", "Created")])
    _wire(monkeypatch, docker)

    ok, detail = await orchestrator._boot_leg(_stack(tmp_path))

    assert ok is False
    assert detail.split("\n", 1)[0] == "kontajner zlyhal: worker (stav created)"


@pytest.mark.asyncio
async def test_the_second_look_waits_out_the_window(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "smoke_crash_window_seconds", 0.3)
    docker = _Docker([*UP])
    _wire(monkeypatch, docker)

    started = time.monotonic()
    ok, _detail = await orchestrator._boot_leg(_stack(tmp_path))

    assert ok is True
    assert time.monotonic() - started >= 0.3


def _release_script(monkeypatch, tmp_path: Path, out: str) -> None:
    (tmp_path / "aa").mkdir()
    (tmp_path / "aa" / "release_smoke_test.sh").write_text("#!/bin/sh\n")
    monkeypatch.setattr(claude_agent, "PROJECTS_ROOT", tmp_path)

    async def _script(script, env):
        return 1, out

    monkeypatch.setattr(orchestrator, "_run_acceptance_script", _script)


@pytest.mark.asyncio
async def test_a_failed_release_check_carries_the_log_of_a_service_that_crashed(monkeypatch, tmp_path):
    _release_script(monkeypatch, tmp_path, "FAIL egress→10.0.0.1: egress proxy not reachable (gaierror)")
    docker = _Docker([*UP, EGRESS_RESTARTING])
    monkeypatch.setattr(orchestrator, "_compose_smoke_step", docker.step)

    ok, detail, skipped = await orchestrator._run_release_acceptance(_stack(tmp_path), "aa", (0, 0))

    assert (ok, skipped) == (False, False)
    assert detail.startswith("release_smoke_test.sh exit 1: FAIL egress→10.0.0.1: egress proxy not reachable")
    assert "\nkontajner zlyhal: egress (exit 1, dookola sa reštartuje)" in detail and "Bungled" in detail


@pytest.mark.asyncio
async def test_a_failed_release_check_without_a_crash_stays_as_it_was(monkeypatch, tmp_path):
    _release_script(monkeypatch, tmp_path, "2 of 12 failed")
    docker = _Docker([*UP, MIGRATE_DONE])
    monkeypatch.setattr(orchestrator, "_compose_smoke_step", docker.step)

    ok, detail, _skipped = await orchestrator._run_release_acceptance(_stack(tmp_path), "aa", (0, 0))

    assert (ok, detail) == (False, "release_smoke_test.sh exit 1: 2 of 12 failed")
    assert docker.logs_of == []


def test_the_headline_names_the_service():
    assert (
        failure_framing.humanize_release_failure("kontajner zlyhal: egress (exit 1, dookola sa reštartuje)\n--- x")
        == "zlyhala služba egress"
    )
    assert (
        failure_framing.humanize_release_failure("kontajner zlyhal: backend (exit 1), test (exit 2)")
        == "zlyhali služby backend, test"
    )
    assert orchestrator._recheck_headline("kontajner zlyhal: egress (počas skúšky sa 3× reštartovala)") == (
        "zlyhal kontajner egress"
    )
