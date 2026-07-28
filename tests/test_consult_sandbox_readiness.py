"""The consult sidecar must never be inert-but-silent (audit finding).

``consult_sandbox_image`` defaulted to a tag that does not exist on this host, so EVERY Konzultácia raised
:class:`SidecarUnavailable`, degraded to the in-process turn, and the KERNEL-enforced read-only guarantee the
design promises was simply not in effect — on every single consult, while ``/health`` said ``status: ok``.
These pin the surfaces that now make that visible: a probed readiness verdict published on ``GET /health``,
an ERROR at boot, and a per-turn degradation tally.
"""

from __future__ import annotations

import logging
import subprocess
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from backend.api.routes import health
from backend.services import claude_agent, consult_sandbox

_READ_ONLY = ["Read", "Grep", "Glob"]


@pytest.fixture(autouse=True)
def _fresh_state(monkeypatch):
    """Every test starts with an unprobed cache and an empty degradation ledger (both are process-global)."""
    monkeypatch.setattr(consult_sandbox, "_preflight_cache", None)
    monkeypatch.setattr(consult_sandbox, "_ledger", consult_sandbox._DegradationLedger())
    monkeypatch.delenv("CONSULT_SANDBOX", raising=False)
    # ``backend.main`` detaches the ``backend`` logger from the root (propagate=False) so uvicorn's access
    # log stays intact; caplog attaches to the root, so re-attach it for the duration of a log assertion.
    monkeypatch.setattr(logging.getLogger("backend"), "propagate", True)


def _stub_docker(monkeypatch, *, present: bool, returncode: int = 0, stderr: str = "") -> None:
    """Stub the docker CLI lookup + ``docker image inspect`` so no real daemon is touched."""
    monkeypatch.setattr(consult_sandbox.shutil, "which", lambda _name: "/usr/bin/docker" if present else None)
    monkeypatch.setattr(
        consult_sandbox.subprocess,
        "run",
        lambda *_a, **_k: subprocess.CompletedProcess(args=[], returncode=returncode, stdout="", stderr=stderr),
    )
    # The auth dir is a separate precondition; keep it satisfied unless a test says otherwise.
    monkeypatch.setattr(consult_sandbox, "_auth_dir_present", lambda: True)


# ── the readiness verdict ───────────────────────────────────────────────────


def test_missing_image_is_reported_as_not_ready_with_the_remedy(monkeypatch) -> None:
    _stub_docker(
        monkeypatch,
        present=True,
        returncode=1,
        stderr="Error response from daemon: No such image: nex-studio-visual-backend:v3.0.0",
    )
    status = consult_sandbox.preflight()
    assert status.enabled is True
    assert status.ready is False, "an image that does not exist cannot be a ready sandbox"
    problems = " ".join(status.problems)
    assert "does not exist on this host" in problems
    assert status.image in problems
    assert "CONSULT_SANDBOX_IMAGE" in problems, "the report must name the knob that fixes it"


def test_unreachable_daemon_is_not_misreported_as_a_missing_image(monkeypatch) -> None:
    # "Error response from daemon: No such image" also contains the word "daemon" — the two causes must be
    # told apart by the daemon-specific signature, not by that word.
    _stub_docker(
        monkeypatch,
        present=True,
        returncode=1,
        stderr="Cannot connect to the Docker daemon at unix:///var/run/docker.sock. Is the docker daemon running?",
    )
    problems = " ".join(consult_sandbox.preflight().problems)
    assert "daemon is unreachable" in problems
    assert "does not exist on this host" not in problems


def test_absent_docker_cli_short_circuits(monkeypatch) -> None:
    _stub_docker(monkeypatch, present=False)
    status = consult_sandbox.preflight()
    assert status.ready is False
    assert "docker CLI is not on PATH" in " ".join(status.problems)


def test_missing_auth_dir_is_its_own_problem(monkeypatch) -> None:
    _stub_docker(monkeypatch, present=True, returncode=0)
    monkeypatch.setattr(consult_sandbox, "_auth_dir_present", lambda: False)
    problems = " ".join(consult_sandbox.preflight().problems)
    assert "auth/config dir" in problems


def test_everything_present_is_ready(monkeypatch) -> None:
    _stub_docker(monkeypatch, present=True, returncode=0)
    status = consult_sandbox.preflight()
    assert status.ready is True
    assert status.problems == ()


def test_kill_switch_is_reported_as_deliberate_but_still_not_ready(monkeypatch) -> None:
    monkeypatch.setenv("CONSULT_SANDBOX", "0")
    status = consult_sandbox.preflight()
    assert status.enabled is False
    assert status.ready is False, "the kernel guarantee is genuinely not in effect when the sandbox is off"
    assert "switched off" in " ".join(status.problems)


def test_probe_is_cached_then_refreshable(monkeypatch) -> None:
    calls: list[int] = []

    def _run(*_a, **_k):
        calls.append(1)
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    monkeypatch.setattr(consult_sandbox.shutil, "which", lambda _n: "/usr/bin/docker")
    monkeypatch.setattr(consult_sandbox.subprocess, "run", _run)
    monkeypatch.setattr(consult_sandbox, "_auth_dir_present", lambda: True)

    consult_sandbox.preflight()
    consult_sandbox.preflight()
    assert len(calls) == 1, "/health is polled every 30s — the daemon round-trip must be cached"
    consult_sandbox.preflight(refresh=True)
    assert len(calls) == 2


# ── the degradation tally ───────────────────────────────────────────────────


async def test_a_degraded_consult_is_counted_and_published(monkeypatch) -> None:
    _stub_docker(monkeypatch, present=True, returncode=1, stderr="Error: No such image: x")
    monkeypatch.setattr(consult_sandbox, "sandbox_enabled", lambda: True)
    monkeypatch.setattr(
        consult_sandbox,
        "run_consult_in_sandbox",
        AsyncMock(side_effect=consult_sandbox.SidecarUnavailable("consult sidecar could not start: no image")),
    )
    proc = AsyncMock()
    proc.returncode = 0
    proc.communicate = AsyncMock(return_value=(b'{"result": "ok"}', b""))
    monkeypatch.setattr(claude_agent.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc))

    assert consult_sandbox.preflight().degraded_turns == 0
    await claude_agent.invoke_claude(
        project_slug="p", claude_session_id=uuid4(), prompt="otázka", allowed_tools=_READ_ONLY, sandbox=True
    )

    status = consult_sandbox.preflight()
    assert status.degraded_turns == 1, "a consult that lost the kernel guarantee must be counted"
    assert "no image" in (status.last_degradation or "")


def test_degradation_is_logged_at_error(monkeypatch, caplog) -> None:
    _stub_docker(monkeypatch, present=True, returncode=1, stderr="Error: No such image: x")
    with caplog.at_level("ERROR", logger=consult_sandbox.logger.name):
        consult_sandbox.record_degradation("image absent")
    assert any(r.levelname == "ERROR" for r in caplog.records), "a lapsed security boundary is not a warning"
    assert "NOT kernel-enforced read-only" in caplog.text


def test_startup_logs_error_when_enabled_but_unready(monkeypatch, caplog) -> None:
    _stub_docker(monkeypatch, present=True, returncode=1, stderr="Error: No such image: x")
    with caplog.at_level("INFO", logger=consult_sandbox.logger.name):
        status = consult_sandbox.log_startup_readiness()
    assert status.ready is False
    assert any(r.levelname == "ERROR" for r in caplog.records)
    assert "ENABLED but NOT READY" in caplog.text


def test_startup_is_quiet_when_ready(monkeypatch, caplog) -> None:
    _stub_docker(monkeypatch, present=True, returncode=0)
    with caplog.at_level("INFO", logger=consult_sandbox.logger.name):
        consult_sandbox.log_startup_readiness()
    assert not [r for r in caplog.records if r.levelname == "ERROR"]
    assert "READY" in caplog.text


# ── the /health surface ─────────────────────────────────────────────────────


def test_health_publishes_the_sandbox_verdict(monkeypatch) -> None:
    _stub_docker(monkeypatch, present=True, returncode=1, stderr="Error: No such image: x")
    payload = health.health_check()
    assert payload["consult_sandbox"]["ready"] is False
    assert payload["consult_sandbox"]["image"] == consult_sandbox.settings.consult_sandbox_image
    assert payload["consult_sandbox"]["problems"], "an unready sandbox must say why on the health surface"
    # The host-path translation is REPORTED (a container cannot stat a host path it does not mount) so an
    # operator can see a prefix that does not exist rather than discovering it through a wrong answer.
    assert payload["consult_sandbox"]["host_bind_sources"]["/opt/projects"]


def test_health_reports_a_ready_sandbox_without_problems(monkeypatch) -> None:
    _stub_docker(monkeypatch, present=True, returncode=0)
    payload = health.health_check()
    assert payload["consult_sandbox"]["ready"] is True
    assert payload["consult_sandbox"]["problems"] == []
