"""The check reports what it MEASURED, and never names a culprit it did not find (ICCINT-43).

29.08.2026, one hour after v4.2.7 shipped ICCINT-42. The Director read his screen and told Dedo:
*"Niečo zlyhalo - agent zlyhal."*

The agent had not failed. His fix worked — a fresh clone booted the db, ran the migrations, served the
frontend and brought the backend up healthy. Three separate untruths stacked to produce that sentence:

  1. the re-check settled ``block_reason='agent_error'``, which the cockpit renders as "Agent zlyhal";
  2. its message said "appka sa stále nespustí" — the app had started;
  3. no container log came back, so nothing on screen could correct either of the first two.

Underneath all three sat one measurement bug. ``docker compose up --wait`` returns NON-ZERO the moment any
container exits — including a one-shot ``migrate`` that finished perfectly with 0 — and the boot leg took
that exit code as its verdict. Measured on nex-productcatalogs: everything healthy, migrations green, and
``up`` failing with ``container …-migrate-1 exited (0)``. Because nothing had a non-zero exit at that
instant, the log capture correctly found nothing to quote. Every project NEX Studio generates carries a
one-shot migrate service, so this was a false FAIL waiting on all of them.

The verdict now comes from the containers themselves.
"""

from __future__ import annotations

import json
import uuid as _uuid

import pytest
from sqlalchemy import select

from backend.db.models.foundation import User
from backend.db.models.pipeline import BLOCK_REASON_VALUES, PipelineState
from backend.db.models.projects import Project
from backend.db.models.tasks import Epic, Feat, Task
from backend.db.models.versions import Version
from backend.services import orchestrator
from backend.services import task as task_service

# What the smoke really saw on nex-productcatalogs: a clean run that `up --wait` called a failure.
HEALTHY_WITH_A_FINISHED_MIGRATE = [
    {"Service": "db", "State": "running", "ExitCode": 0},
    {"Service": "backend", "State": "running", "ExitCode": 0},
    {"Service": "frontend", "State": "running", "ExitCode": 0},
    {"Service": "migrate", "State": "exited", "ExitCode": 0},
]
A_FAILED_TEST_CONTAINER = HEALTHY_WITH_A_FINISHED_MIGRATE + [
    {"Service": "test", "State": "exited", "ExitCode": 2},
]


def _stack(up_rc: int) -> orchestrator._SmokeStack:
    from pathlib import Path

    return orchestrator._SmokeStack(
        base=["docker", "compose", "-p", "x-smoke"],
        compose=Path("/tmp/docker-compose.yml"),
        override=Path("/tmp/override.yml"),
        project="x-smoke",
        roles={"backend": "backend", "frontend": "frontend", "db": "db"},
        up_rc=up_rc,
        up_detail="container x-smoke-migrate-1 exited (0)",
    )


def _compose_double(monkeypatch, rows: list[dict], *, logs: str = "boom") -> list[list[str]]:
    """Stand in for the docker CLI: ``ps`` answers with `rows`, ``logs`` with `logs`."""
    calls: list[list[str]] = []

    async def _step(cmd, timeout):
        calls.append(cmd)
        if "ps" in cmd:
            return 0, "\n".join(json.dumps(r) for r in rows)
        if "logs" in cmd:
            return 0, logs
        return 0, ""

    monkeypatch.setattr(orchestrator, "_compose_smoke_step", _step)
    return calls


# ── the measurement ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_one_shot_container_that_finished_is_not_a_failed_boot(monkeypatch) -> None:
    """THE defect. ``up --wait`` said 1; every container is fine and migrate exited 0. The app decides."""
    _compose_double(monkeypatch, HEALTHY_WITH_A_FINISHED_MIGRATE)
    probed: list[bool] = []

    async def _probe(stack):
        probed.append(True)
        return True, "backend responded 200 /health"

    monkeypatch.setattr(orchestrator, "_run_app_starts_smoke", _probe)

    ok, detail = await orchestrator._boot_leg(_stack(up_rc=1))

    assert ok is True, "a finished migrate container was still being read as a dead app"
    assert probed, "the app was never asked whether it was up — the compose exit code decided for it"
    # ICCINT-44 replaced the probe's own words with a report of what was measured.
    assert orchestrator._APP_RESPONDS in detail.lower()
    assert "migrate" in detail, "the finished one-shot container went unmentioned"


@pytest.mark.asyncio
async def test_a_container_that_exited_nonzero_is_named_with_its_log(monkeypatch) -> None:
    _compose_double(monkeypatch, A_FAILED_TEST_CONTAINER, logs="\n--- test ---\n17 errors during collection")

    async def _never(stack):
        raise AssertionError("the app probe ran even though a container had already failed")

    monkeypatch.setattr(orchestrator, "_run_app_starts_smoke", _never)

    ok, detail = await orchestrator._boot_leg(_stack(up_rc=1))

    assert ok is False
    assert "test (exit 2)" in detail, "the failing container was not named"
    assert "17 errors during collection" in detail, "the reason was withheld — again (ICCINT-37)"
    # migrate finished cleanly; dragging it in would bury the one line that matters.
    assert "migrate" not in detail


@pytest.mark.asyncio
async def test_when_the_containers_cannot_be_read_the_compose_output_is_kept(monkeypatch) -> None:
    """A verdict must never be invented. If we cannot look, we say what ``up`` said and nothing more."""

    async def _blind(cmd, timeout):
        return 1, "docker daemon gone"

    monkeypatch.setattr(orchestrator, "_compose_smoke_step", _blind)
    ok, detail = await orchestrator._boot_leg(_stack(up_rc=1))
    assert ok is False and "up exit 1" in detail


@pytest.mark.asyncio
async def test_a_clean_up_still_goes_straight_to_the_app_probe(monkeypatch) -> None:
    """The control: nothing about the ordinary passing path changed."""
    _compose_double(monkeypatch, HEALTHY_WITH_A_FINISHED_MIGRATE)

    async def _probe(stack):
        return False, "backend not responding on any path"

    monkeypatch.setattr(orchestrator, "_run_app_starts_smoke", _probe)
    ok, detail = await orchestrator._boot_leg(_stack(up_rc=0))
    assert ok is False and "not responding" in detail


# ── the sentence the Manažér reads ────────────────────────────────────────────


def test_the_headline_never_claims_more_than_was_measured() -> None:
    """When a container failed, the app probe never ran — so the sentence may say which container died and
    must NOT say whether the app booted. The first version said "appka sa stále nespustí" every time."""
    assert orchestrator._recheck_headline("kontajner zlyhal: test (exit 2)\n--- test ---\nx") == (
        "zlyhal kontajner test"
    )
    assert orchestrator._recheck_headline("kontajner zlyhal: backend (exit 1), test (exit 2)") == (
        "zlyhal kontajner backend, test"
    )
    # The app probe DID run and the app did not answer — here the plain claim is the true one.
    assert orchestrator._recheck_headline("backend not responding on /health") == "appka sa nespustila"
    # Could not look at all.
    assert orchestrator._recheck_headline("up exit 1: something") == "spustenie neprešlo"


def test_check_failed_is_a_block_reason_the_database_accepts() -> None:
    """The FE renders every reason as a sentence; this one needed its own so the sentence stops naming a
    culprit. It has to survive the CHECK constraint, or the block site crashes instead of blocking."""
    assert "check_failed" in BLOCK_REASON_VALUES


@pytest.mark.asyncio
async def test_a_failed_check_blocks_without_blaming_the_agent(db_session, monkeypatch) -> None:
    suffix = _uuid.uuid4().hex[:8]
    user = User(username=f"cc_{suffix}", email=f"cc_{suffix}@test.local", password_hash="x", role="ri")
    db_session.add(user)
    db_session.flush()
    project = Project(
        name=f"Blame {suffix}",
        slug=f"blame-{suffix}",
        type="standard",
        auth_mode="password",
        description="check_failed test.",
        created_by=user.id,
        source_path=None,
    )
    db_session.add(project)
    db_session.flush()
    version = Version(project_id=project.id, version_number="0.1.0", status="active")
    db_session.add(version)
    db_session.flush()
    state = PipelineState(
        version_id=version.id,
        flow_type="new_version",
        current_stage="programovanie",
        current_actor="ai_agent",
        status="agent_working",
        mode=None,
    )
    db_session.add(state)
    db_session.flush()
    orchestrator._ensure_verifikacia_fix_task(db_session, version.id, scope="Oprav štart.", findings=None)
    for task in (
        db_session.execute(
            select(Task)
            .join(Feat, Feat.id == Task.feat_id)
            .join(Epic, Epic.id == Feat.epic_id)
            .where(Epic.version_id == version.id)
        )
        .scalars()
        .all()
    ):
        task.status = "done"
        db_session.flush()
        task_service.recompute_feat_status(db_session, task.feat_id)

    async def _test_container_died(slug):
        return False, "kontajner zlyhal: test (exit 2)\n--- test ---\n17 errors during collection"

    monkeypatch.setattr(orchestrator, "_app_starts_after_fix", _test_container_died)
    messages: list = []

    async def _on_message(msg):
        messages.append(msg)

    blocked = await orchestrator._settle_fix_boot_recheck(db_session, state, on_message=_on_message)

    assert blocked is True
    # THE sentence the Director read. Not the agent's fault, and not a claim about the app.
    assert state.block_reason == "check_failed", "the screen would read 'Agent zlyhal' again"
    assert "zlyhal kontajner test" in state.next_action
    assert "nespustí" not in state.next_action, "claimed the app was down without having asked it"
    said = [m for m in messages if (m.payload or {}).get("fix_boot_recheck")][-1]
    assert "zlyhal kontajner test" in said.content
    assert "17 errors during collection" in (said.payload or {}).get("technical_detail", "")
