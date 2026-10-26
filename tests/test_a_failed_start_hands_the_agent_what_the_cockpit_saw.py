"""DEV-61 — when the app does not start in Verifikácia, the one click "Nechaj to opraviť" leads to a fix.

Career Asistent 0.1.0, 11.10.2026: the frontend container ran and refused the connection. The fix brief the one
click hands the AI Agent said "Spusti aplikáciu tak ako engine (`docker compose up`), zreprodukuj zlyhanú skúšku" —
but Programovanie runs in the build sandbox without Docker — and carried only "Connection refused": logs were
collected only from containers that EXITED non-zero, and this one was running. The Director wrote the fix by hand.
Now the brief asks for nothing the fix turn cannot do, and carries the log of the service that did not answer,
secrets masked — it goes to the Manažér's screen and to the agent.
"""

from __future__ import annotations

import json

import pytest
import yaml
from sqlalchemy import select

from backend.db.models.pipeline import PipelineMessage
from backend.services import build_sandbox, orchestrator, uat_provisioner
from backend.tests.integration.test_verifikacia_boot_fail import _seed_state_at_verifikacia

CAREER_ASISTENT_COMPOSE = """\
services:
  backend:
    build: .
    ports:
      - "8000"
  frontend:
    build: ./frontend
    ports:
      - "10250:443"
      - "10251:80"
"""

NGINX_LOG = (
    "40-select-tls: no certificate mounted - plain HTTP (release test stack only)\nDB_PASSWORD=hunter2-not-for-anyone\n"
)
BACKEND_LOG = 'uvicorn: waiting for database\n{"password": "s3cr3t-json"}\n'


class _Stack:
    """``docker compose`` of a stack whose containers all RUN; ``answering`` says which ones answer the probe."""

    def __init__(self, *answering: str) -> None:
        self.answering = set(answering)
        self.asked_logs: list[str] = []

    async def __call__(self, cmd, timeout, env=None):  # noqa: ARG002 — _compose_smoke_step's signature
        if "ps" in cmd:
            rows = [{"Service": s, "State": "running", "ExitCode": 0} for s in ("db", "backend", "frontend")]
            return 0, "\n".join(json.dumps(r) for r in rows)
        if "python" in cmd:
            target = "backend" if "localhost" in " ".join(cmd) else "frontend"
            return (0, "port 80: status 200") if target in self.answering else (1, "port 80: err refused")
        if "logs" in cmd:
            service = cmd[-1]
            self.asked_logs.append(service)
            return 0, {"frontend": NGINX_LOG, "backend": BACKEND_LOG}.get(service, "")
        return 0, ""


def _smoke_stack(tmp_path) -> orchestrator._SmokeStack:
    compose = tmp_path / "docker-compose.yml"
    compose.write_text(CAREER_ASISTENT_COMPOSE)
    roles = uat_provisioner.identify_service_roles(yaml.safe_load(CAREER_ASISTENT_COMPOSE)["services"])
    return orchestrator._SmokeStack(
        base=["docker", "compose", "-p", "cas-smoke", "-f", str(compose)],
        compose=compose,
        override=tmp_path / "o.yml",
        project="cas-smoke",
        roles=roles,
        up_rc=0,
        up_detail="",
    )


@pytest.fixture
def no_waiting(monkeypatch):
    async def _no_sleep(*_a, **_k):
        return None

    monkeypatch.setattr(orchestrator.asyncio, "sleep", _no_sleep)


async def test_a_running_frontend_that_does_not_answer_brings_its_own_log(monkeypatch, tmp_path, no_waiting):
    stack = _Stack("backend")
    monkeypatch.setattr(orchestrator, "_compose_smoke_step", stack)

    ok, detail = await orchestrator._boot_leg(_smoke_stack(tmp_path))

    assert ok is False
    assert stack.asked_logs == ["frontend"], "request the log from the service that did not respond, and only from it"
    assert detail == (
        f"frontend 'frontend' not serving within {orchestrator.ACCEPTANCE_SMOKE_READY_TIMEOUT}s: port 80: err refused"
        "\n\n--- frontend (state: running) ---\n"
        "40-select-tls: no certificate mounted - plain HTTP (release test stack only)\n"
        "DB_PASSWORD=[REDACTED]"
    )


async def test_a_backend_that_does_not_answer_brings_its_own_log(monkeypatch, tmp_path, no_waiting):
    stack = _Stack("frontend")
    monkeypatch.setattr(orchestrator, "_compose_smoke_step", stack)

    ok, detail = await orchestrator._boot_leg(_smoke_stack(tmp_path))

    assert ok is False and stack.asked_logs == ["backend"]
    assert detail == (
        f"app did not boot / not responding within {orchestrator.ACCEPTANCE_SMOKE_READY_TIMEOUT}s: port 80: err refused"
        "\n\n--- backend (state: running) ---\n"
        'uvicorn: waiting for database\n{"password"=[REDACTED]'
    )


async def test_the_log_of_a_container_that_exited_is_masked_too(monkeypatch):
    async def _step(cmd, timeout):  # noqa: ARG001
        if "ps" in cmd:
            return 0, json.dumps({"Service": "migrate", "State": "exited", "ExitCode": 1})
        return 0, "alembic: connecting with POSTGRES_PASSWORD=hunter2-not-for-anyone\nboom"

    monkeypatch.setattr(orchestrator, "_compose_smoke_step", _step)

    detail = await orchestrator._smoke_failure_logs(["docker", "compose"])

    # DEV-232: it returns the whole failure — the service named, then its log under its state, like every other path.
    assert detail == (
        "kontajner zlyhal: migrate (exit 1)"
        "\n\n--- migrate (state: exited) ---\nalembic: connecting with POSTGRES_PASSWORD=[REDACTED]\nboom"
    )


@pytest.mark.parametrize(
    ("line", "masked"),
    [
        ("DB_PASSWORD=hunter2", "DB_PASSWORD=[REDACTED]"),
        ("POSTGRES_PASSWORD: hunter2", "POSTGRES_PASSWORD=[REDACTED]"),
        ('"password": "hunter2"', '"password"=[REDACTED]'),
        ("SETTINGS_ENCRYPTION_KEY=hunter2", "SETTINGS_ENCRYPTION_KEY=[REDACTED]"),
        ("client_secret=hunter2", "client_secret=[REDACTED]"),
        ("max_tokens=1000", "max_tokens=1000"),  # a count, not a secret
        ('"input_tokens": 12', '"input_tokens": 12'),
    ],
)
def test_the_mask_takes_the_shapes_a_container_prints_its_environment_in(line, masked):
    assert orchestrator.claude_agent.redact_secrets(line) == masked


async def test_the_one_click_fix_asks_for_nothing_the_fix_turn_cannot_do(db_session, monkeypatch):
    state = _seed_state_at_verifikacia(db_session)
    measured = (
        "frontend 'frontend' not serving within 120s: port 443: err refused; port 80: err refused"
        "\n\n--- frontend (state: running) ---\n40-select-tls: no certificate mounted - plain HTTP"
    )

    async def _smoke(*_a, **_k):
        return (False, measured), None

    async def _no_auditor(*_a, **_k):
        raise AssertionError("the Auditor does not run when the start fails")

    monkeypatch.setattr(orchestrator, "_run_release_smoke", _smoke)
    monkeypatch.setattr(orchestrator, "_write_release_note_to_disk", lambda *a, **k: None)
    monkeypatch.setattr(orchestrator, "invoke_agent_with_parse_retry", _no_auditor)

    await orchestrator._run_verifikacia_round(db_session, state)
    brief = orchestrator._latest_verifikacia_fix_scope(db_session, state.version_id)
    verdict = db_session.execute(
        select(PipelineMessage).where(PipelineMessage.version_id == state.version_id, PipelineMessage.kind == "verdict")
    ).scalar_one()

    assert verdict.payload["proposed_fix"] == orchestrator.BOOT_FAIL_FIX_BRIEF
    assert brief == (
        "## Verifikácia FAIL — oprav podľa nálezov Auditora (cielená oprava, potom Auditor re-verifikuje)\n"
        f"Konkrétny dôvod zlyhania (zo skúšky po spustení, overené enginom): {measured}\n\n"
        f"{orchestrator.BOOT_FAIL_FIX_BRIEF}\n\n"
        f"- {verdict.content}"
    )


def test_the_brief_asks_for_no_docker_and_says_who_measures_the_start():
    brief = orchestrator.BOOT_FAIL_FIX_BRIEF
    assert "docker compose" not in brief.lower() and "zreprodukuj" not in brief.lower()
    assert brief.endswith("spustenie po oprave overí kokpit sám tou istou skúškou.")


def test_the_fix_turn_really_has_no_docker_so_the_brief_must_not_ask_for_it():
    """The reason the brief is what it is: the turn that carries out the fix is Programovanie, a sandboxed phase.
    Should Programovanie ever leave the sandbox, this fails and the brief is reconsidered with it."""
    assert build_sandbox.phase_uses_sandbox("programovanie")
