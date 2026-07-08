"""Integration tests for the read-only Konzultácia turn (konzultacia-mode.md Part 1).

The v3 cockpit conversation used to DEAD-END on a finished version: a Manažér message on a
``current_stage == 'done'`` version was accepted by ``relay_manazer_message`` but ``_begin_dispatch``
no-oped (``STAGE_ACTOR`` has no ``done`` actor) → the message was recorded, no answer ever came. Part 1
makes such a version ANSWERABLE in a strictly read-only advisory mode:

  * **Reachability** — a consult message on a done/released version PRODUCES an answer (RED: dead-end).
  * **Read-only tool profile** — the consult AI turn is invoked with ``allowed_tools=['Read','Grep','Glob']``
    (no Write/Edit/Bash); a build turn passes ``allowed_tools=None`` (byte-identical).
  * **Build state UNCHANGED** — status/current_stage/baseline SHA are exactly what they were after a consult.
  * **Metrics safety** — consult usage does NOT change the navrh/programovanie/verifikacia phase totals.
  * **Never-built version STILL cold-starts** a build (unchanged); a **mid-build** conversation is UNCHANGED.

Run against the real v2 DB (test DB :9178, SAVEPOINT-isolated ``db_session``), with ``invoke_claude``
monkeypatched (no real ``claude`` subprocess).
"""

from __future__ import annotations

import uuid as _uuid

import pytest
from sqlalchemy import select

from backend.db.models.foundation import User
from backend.db.models.pipeline import PipelineMessage, PipelineState
from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.services import claude_agent, orchestrator
from backend.services.pipeline_metrics import aggregate_usage_by_phase

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _seed_user(db) -> User:
    u = User(
        username=f"cc_{_uuid.uuid4().hex[:8]}",
        email=f"cc_{_uuid.uuid4().hex[:8]}@test.local",
        password_hash="x",
        role="ri",
    )
    db.add(u)
    db.flush()
    return u


def _seed_project(db, *, creator: User) -> Project:
    suffix = _uuid.uuid4().hex[:8]
    project = Project(
        name=f"Konz Proj {suffix}",
        slug=f"konz-{suffix}",
        type="standard",
        auth_mode="password",
        description="Konzultácia Part 1 test project.",
        created_by=creator.id,
        source_path=None,
    )
    db.add(project)
    db.flush()
    return project


def _seed_version(db, project: Project, *, status: str = "active", version_number: str = "1.0.0") -> Version:
    version = Version(project_id=project.id, version_number=version_number, status=status)
    db.add(version)
    db.flush()
    return version


def _seed_done_state(db, version: Version, *, mode: str | None = "conversation") -> PipelineState:
    """A TERMINAL pipeline state — a finished build (``hotovo`` / legacy schvalit-done / released)."""
    state = PipelineState(
        version_id=version.id,
        flow_type="new_version",
        current_stage="done",
        current_actor="ai_agent",
        status="done",
        next_action="Verzia je hotová.",
        mode=mode,
        dispatch_in_flight=False,
        dispatch_baseline_sha=None,
    )
    db.add(state)
    db.flush()
    return state


def _seed_phase_usage_messages(db, version: Version) -> None:
    """Seed one metered build message in each of the three COMPARISON_PHASES so the metrics-safety test has a
    baseline to prove the consult turn does not perturb."""
    for phase, toks in (("navrh", 100), ("programovanie", 300), ("verifikacia", 200)):
        db.add(
            PipelineMessage(
                version_id=version.id,
                stage=phase,
                author="ai_agent",
                recipient="manazer",
                kind="gate_report",
                content=f"{phase} build turn",
                status="delivered",
                payload={
                    "phase": phase,
                    "usage": {"input_tokens": toks, "output_tokens": toks // 2, "model": "claude-opus-4-8"},
                    "timing": {"duration_seconds": 1.0, "parse_attempts": 0},
                },
            )
        )
    db.flush()


def _fake_invoke_claude(calls: list, *, structured: dict, in_tok: int = 42, out_tok: int = 17):
    """A monkeypatch for ``orchestrator.invoke_claude`` that records every call's kwargs (so the test can
    assert the read-only tool profile) and returns a canned ``(text, usage, structured_output)`` triple."""

    async def _fake(**kwargs):
        calls.append(kwargs)
        usage = claude_agent.UsageMetadata(input_tokens=in_tok, output_tokens=out_tok, model="claude-opus-4-8")
        return ("Odpoveď v konzultácii.", usage, structured)

    return _fake


_ANSWER_BLOCK = {"stage": "done", "kind": "answer", "summary": "Vysvetlené.", "awaiting": "manazer"}


# ---------------------------------------------------------------------------
# (i) Reachability + read-only profile + build-state-unchanged + metrics-safety (the core)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_consult_on_done_version_answers_read_only_unchanged_metrics_safe(db_session, monkeypatch) -> None:
    creator = _seed_user(db_session)
    project = _seed_project(db_session, creator=creator)
    version = _seed_version(db_session, project, status="released")  # PROD-released → still consultable
    state = _seed_done_state(db_session, version)
    _seed_phase_usage_messages(db_session, version)

    phase_before = aggregate_usage_by_phase(db_session, version.id)

    calls: list = []
    monkeypatch.setattr(orchestrator, "invoke_claude", _fake_invoke_claude(calls, structured=_ANSWER_BLOCK))

    # RELAY: a Manažér message on a terminal version routes to the consult path (NOT the dead-end apply_action).
    relay = await orchestrator.relay_manazer_message(db_session, version_id=version.id, text="Prečo je to takto?")
    assert relay.deferred is False
    assert relay.action == "consult"
    assert relay.state.status == "agent_working"  # armed (RED today: _begin_dispatch no-op → stayed 'done')
    assert relay.state.current_stage == "done"  # consult never moves the phase
    assert relay.state.dispatch_baseline_sha is None  # NO baseline-SHA capture (Part 1.4)

    # RUN the consult turn (what the runner does when it sees current_stage == 'done' + agent_working).
    settled = await orchestrator.run_consult_turn(db_session, version.id)
    assert settled is not None

    # An ANSWER was produced (the dead-end is fixed) — recorded ai_agent→manazer at stage='done'.
    answer = db_session.execute(
        select(PipelineMessage)
        .where(PipelineMessage.version_id == version.id, PipelineMessage.author == "ai_agent")
        .order_by(PipelineMessage.seq.desc())
        .limit(1)
    ).scalar_one()
    assert answer.kind == "answer"
    assert answer.stage == "done"

    # READ-ONLY tool profile: the consult turn was invoked with Read/Grep/Glob only (no Write/Edit/Bash).
    assert calls, "invoke_claude was never called"
    assert calls[-1]["allowed_tools"] == ["Read", "Grep", "Glob"]
    assert "Bash" not in calls[-1]["allowed_tools"]
    assert "Write" not in calls[-1]["allowed_tools"]
    assert "Edit" not in calls[-1]["allowed_tools"]
    # konzultacia-sidecar-sandbox.md Part 2: the consult turn is routed through the OS-isolated sidecar.
    assert calls[-1]["sandbox"] is True

    # BUILD STATE UNCHANGED — back to terminal rest after the consult answer.
    db_session.refresh(state)
    assert state.status == "done"
    assert state.current_stage == "done"
    assert state.dispatch_baseline_sha is None
    assert state.dispatch_in_flight is False

    # METRICS SAFETY — the consult usage did NOT change any comparison-phase total; it folded into 'done'.
    phase_after = aggregate_usage_by_phase(db_session, version.id)
    for phase in ("navrh", "programovanie", "verifikacia"):
        assert phase_after[phase].input_tokens == phase_before[phase].input_tokens
        assert phase_after[phase].output_tokens == phase_before[phase].output_tokens
    assert phase_after["done"].output_tokens == 17  # the consult turn's tokens landed in system-overhead


# ---------------------------------------------------------------------------
# (ii) A build turn passes allowed_tools=None (the read-only profile is opt-in, build unchanged)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_conversation_turn_uses_no_tool_restriction(db_session, monkeypatch) -> None:
    creator = _seed_user(db_session)
    project = _seed_project(db_session, creator=creator)
    version = _seed_version(db_session, project)
    # A MID-BUILD conversation state (armed) — run_conversation_turn drives it.
    state = PipelineState(
        version_id=version.id,
        flow_type="new_version",
        current_stage="priprava",
        current_actor="ai_agent",
        status="agent_working",
        mode="conversation",
    )
    db_session.add(state)
    db_session.flush()

    calls: list = []
    build_block = {"stage": "priprava", "kind": "answer", "summary": "ok", "awaiting": "manazer"}
    monkeypatch.setattr(orchestrator, "invoke_claude", _fake_invoke_claude(calls, structured=build_block))

    await orchestrator.run_conversation_turn(db_session, version.id, None, "pokračuj")

    assert calls, "invoke_claude was never called"
    assert calls[-1]["allowed_tools"] is None  # build turns keep today's full-auto profile (byte-identical)
    # konzultacia-sidecar-sandbox.md Part 2: a build turn NEVER routes through the sidecar.
    assert calls[-1]["sandbox"] is False


# ---------------------------------------------------------------------------
# (iii) A never-built version STILL raises "Pipeline not started" (cold-start unchanged)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_never_built_version_relay_still_raises(db_session) -> None:
    creator = _seed_user(db_session)
    project = _seed_project(db_session, creator=creator)
    version = _seed_version(db_session, project)
    # No PipelineState — the state-is-None guard is UNCHANGED (the FE cold-starts a build on the first message).
    assert orchestrator._get_state(db_session, version.id) is None

    with pytest.raises(orchestrator.OrchestratorError, match="Pipeline not started"):
        await orchestrator.relay_manazer_message(db_session, version_id=version.id, text="ahoj")


# ---------------------------------------------------------------------------
# (iv) A MID-BUILD version's relay is UNCHANGED (goes through the build ask path, NOT consult)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mid_build_relay_is_not_consult(db_session) -> None:
    creator = _seed_user(db_session)
    project = _seed_project(db_session, creator=creator)
    version = _seed_version(db_session, project)
    state = PipelineState(
        version_id=version.id,
        flow_type="new_version",
        current_stage="priprava",
        current_actor="ai_agent",
        status="awaiting_manazer",  # settled mid-build (not done) — a plain build consult ('ask')
        mode="conversation",
    )
    db_session.add(state)
    db_session.flush()

    relay = await orchestrator.relay_manazer_message(db_session, version_id=version.id, text="uprav to")

    # The build ask path (apply_action → _begin_dispatch) armed the priprava actor — NOT the consult path.
    assert relay.action == "ask"
    assert relay.action != "consult"
    assert relay.state.current_stage == "priprava"  # stage unchanged; the build conversation continues
    assert relay.state.status == "agent_working"  # _begin_dispatch armed a real dispatch (has an actor)
    # No consult marker was recorded.
    msgs = db_session.execute(select(PipelineMessage).where(PipelineMessage.version_id == version.id)).scalars().all()
    assert all(not (m.payload or {}).get("consult") for m in msgs)
