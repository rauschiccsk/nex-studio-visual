"""Tests for the v3 three-honest-phases metrics stamp (docs/specs/metrics-v3-three-phases.md).

Part 1 (orchestrator) — the v3 CONVERSATION flow stamps ``payload['phase']`` on the agent's
usage-bearing turn to the metrics phase for the round that produced it
(navrh = pre-build conversation + task plan; verifikacia = self-check/kontrola; programovanie = build)
WITHOUT touching ``msg.stage`` / ``current_stage`` (the deploy/release gate reads the STAGE). A legacy
caller (``metrics_phase=None``) is byte-for-byte unchanged — no ``phase`` key, so
``aggregate_usage_by_phase`` falls back to ``msg.stage``.

Part 2 (metrics) — ``_build_phases`` emits ONLY the comparison phases that actually did work
(input+output tokens > 0), in canonical ``COMPARISON_PHASES`` order — no phantom empty rows — with the
token footing preserved.
"""

from __future__ import annotations

import json
import uuid as _uuid

import pytest
from sqlalchemy import select

from backend.db.models.foundation import User
from backend.db.models.pipeline import PipelineMessage, PipelineState
from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.services import claude_agent, orchestrator
from backend.services.metrics import COMPARISON_PHASES, _build_phases
from backend.services.pipeline_metrics import UsageTotals, aggregate_usage_by_phase
from backend.services.pipeline_status import (
    TASK_PLAN_SKELETON_JSON_SCHEMA,
    ParseFailure,
    parse_task_plan_skeleton,
)

# ---------------------------------------------------------------------------
# Seeding helpers
# ---------------------------------------------------------------------------


def _seed_user(db) -> User:
    u = User(
        username=f"mp_{_uuid.uuid4().hex[:8]}",
        email=f"mp_{_uuid.uuid4().hex[:8]}@test.local",
        password_hash="x",
        role="ri",
    )
    db.add(u)
    db.flush()
    return u


def _seed_project(db, *, creator: User) -> Project:
    suffix = _uuid.uuid4().hex[:8]
    project = Project(
        name=f"Metrics Phase Proj {suffix}",
        slug=f"metrics-phase-{suffix}",
        type="standard",
        auth_mode="password",
        description="metrics-v3-three-phases stamp test project.",
        created_by=creator.id,
        source_path=None,
    )
    db.add(project)
    db.flush()
    return project


def _seed_version(db, project: Project, version_number: str = "2.0.0") -> Version:
    version = Version(project_id=project.id, version_number=version_number, status="active")
    db.add(version)
    db.flush()
    return version


def _seed_state(db, version: Version, *, mode: str = "conversation", stage: str = "priprava") -> PipelineState:
    state = PipelineState(
        version_id=version.id,
        flow_type="new_version",
        mode=mode,
        current_stage=stage,
        current_actor="ai_agent",
        status="agent_working",
    )
    db.add(state)
    db.flush()
    return state


def _latest_msg(db, version_id, author: str) -> PipelineMessage:
    return db.execute(
        select(PipelineMessage)
        .where(PipelineMessage.version_id == version_id, PipelineMessage.author == author)
        .order_by(PipelineMessage.seq.desc())
        .limit(1)
    ).scalar_one()


def _status_block(*, stage: str = "priprava", kind: str = "gate_report", summary: str = "ok") -> str:
    obj = {"stage": stage, "kind": kind, "summary": summary, "awaiting": "manazer"}
    return f"<<<PIPELINE_STATUS>>>\n{json.dumps(obj)}\n<<<END_PIPELINE_STATUS>>>"


def _mock_claude(monkeypatch, text: str, *, input_tokens: int = 1000, output_tokens: int = 500) -> None:
    """Monkeypatch the CLI boundary (``orchestrator.invoke_claude``) so no real claude is spawned. Returns the
    R3 3-tuple ``(text, usage, structured_output)`` — structured None → the fence in ``text`` is parsed."""
    usage = claude_agent.UsageMetadata(input_tokens=input_tokens, output_tokens=output_tokens, model="claude-opus-4-8")

    async def _fake(**_kw):
        return (text, usage, None)

    monkeypatch.setattr(orchestrator, "invoke_claude", _fake)


def _ut(inp: int, out: int, *, model: str = "claude-opus-4-8", duration: float = 1.0) -> UsageTotals:
    t = UsageTotals()
    t.add(input_tokens=inp, output_tokens=out, duration_seconds=duration, model=model)
    return t


# ===========================================================================
# Part 1 — invoke_agent record path (metrics_phase set / unset)
# ===========================================================================


@pytest.mark.asyncio
async def test_invoke_agent_stamps_verifikacia_while_stage_stays_priprava(db_session, monkeypatch) -> None:
    """The self-check case: a turn stamped ``verifikacia`` for metrics keeps ``msg.stage == 'priprava'`` so
    the deploy/release gate (which reads the STAGE) is untouched."""
    creator = _seed_user(db_session)
    project = _seed_project(db_session, creator=creator)
    version = _seed_version(db_session, project)
    _mock_claude(monkeypatch, _status_block(stage="priprava"))

    await orchestrator.invoke_agent(
        db_session,
        version_id=version.id,
        role=orchestrator.AI_AGENT_ROLE,
        stage="priprava",
        prompt="x",
        metrics_phase="verifikacia",
    )

    msg = _latest_msg(db_session, version.id, orchestrator.AI_AGENT_ROLE)
    assert msg.stage == "priprava"  # control-flow STAGE unchanged (deploy gate reads this)
    assert msg.payload["phase"] == "verifikacia"  # metrics attribute here instead of falling back to stage


@pytest.mark.asyncio
async def test_invoke_agent_stamps_navrh(db_session, monkeypatch) -> None:
    creator = _seed_user(db_session)
    project = _seed_project(db_session, creator=creator)
    version = _seed_version(db_session, project)
    _mock_claude(monkeypatch, _status_block(stage="priprava"))

    await orchestrator.invoke_agent(
        db_session,
        version_id=version.id,
        role=orchestrator.AI_AGENT_ROLE,
        stage="priprava",
        prompt="x",
        metrics_phase="navrh",
    )

    msg = _latest_msg(db_session, version.id, orchestrator.AI_AGENT_ROLE)
    assert msg.stage == "priprava"
    assert msg.payload["phase"] == "navrh"


@pytest.mark.asyncio
async def test_invoke_agent_stamps_programovanie(db_session, monkeypatch) -> None:
    creator = _seed_user(db_session)
    project = _seed_project(db_session, creator=creator)
    version = _seed_version(db_session, project)
    _mock_claude(monkeypatch, _status_block(stage="programovanie"))

    await orchestrator.invoke_agent(
        db_session,
        version_id=version.id,
        role=orchestrator.AI_AGENT_ROLE,
        stage="programovanie",
        prompt="x",
        metrics_phase="programovanie",
    )

    msg = _latest_msg(db_session, version.id, orchestrator.AI_AGENT_ROLE)
    assert msg.payload["phase"] == "programovanie"


@pytest.mark.asyncio
async def test_invoke_agent_legacy_no_metrics_phase_omits_key_and_falls_back_to_stage(db_session, monkeypatch) -> None:
    """Legacy caller (no ``metrics_phase``): the payload carries NO ``phase`` key (byte-for-byte unchanged)
    and ``aggregate_usage_by_phase`` attributes the tokens to ``msg.stage``."""
    creator = _seed_user(db_session)
    project = _seed_project(db_session, creator=creator)
    version = _seed_version(db_session, project)
    _mock_claude(monkeypatch, _status_block(stage="programovanie"))

    await orchestrator.invoke_agent(
        db_session,
        version_id=version.id,
        role=orchestrator.AI_AGENT_ROLE,
        stage="programovanie",
        prompt="x",
    )

    msg = _latest_msg(db_session, version.id, orchestrator.AI_AGENT_ROLE)
    assert "phase" not in msg.payload  # legacy: no stamp
    by_phase = aggregate_usage_by_phase(db_session, version.id)
    assert "programovanie" in by_phase  # fell back to msg.stage
    assert by_phase["programovanie"].input_tokens == 1000


# ===========================================================================
# Part 1 — round wiring (real invoke_agent_with_parse_retry / _invoke_plan_pass)
# ===========================================================================


@pytest.mark.asyncio
async def test_run_conversation_turn_stamps_navrh(db_session, monkeypatch) -> None:
    """A pre-build conversation turn attributes to ``navrh`` (was ``priprava`` via stage fallback)."""
    creator = _seed_user(db_session)
    project = _seed_project(db_session, creator=creator)
    version = _seed_version(db_session, project)
    _seed_state(db_session, version)
    _mock_claude(monkeypatch, _status_block(stage="priprava"))

    await orchestrator.run_conversation_turn(db_session, version.id)

    msg = _latest_msg(db_session, version.id, "ai_agent")
    assert msg.stage == "priprava"  # STAGE (routing / gate) unchanged
    assert msg.payload["phase"] == "navrh"


@pytest.mark.asyncio
async def test_kontrola_round_stamps_verifikacia(db_session, monkeypatch) -> None:
    """The honest self-check round attributes the partner turn to ``verifikacia`` while staying
    ``stage='priprava'`` (invisible to the deploy path)."""
    creator = _seed_user(db_session)
    project = _seed_project(db_session, creator=creator)
    version = _seed_version(db_session, project)
    state = _seed_state(db_session, version)
    _mock_claude(monkeypatch, _status_block(stage="priprava"))

    async def _fake_smoke(_slug, _label, _coverage):
        return (True, "boot ok"), (True, "acceptance ok", False)

    monkeypatch.setattr(orchestrator, "_run_release_smoke", _fake_smoke)

    await orchestrator._run_conversation_kontrola_round(db_session, state)

    msg = _latest_msg(db_session, version.id, "ai_agent")
    assert msg.stage == "priprava"
    assert msg.payload["phase"] == "verifikacia"
    assert msg.payload.get("kontrola") is True


@pytest.mark.asyncio
async def test_plan_pass_stamps_metrics_phase_navrh_keeps_stage(db_session, monkeypatch) -> None:
    """The task-plan pass path (``_invoke_plan_pass``) stamps the synthetic note's ``phase`` to the
    ``metrics_phase`` (navrh) while ``msg.stage`` stays the conversation register's ``priprava``."""
    creator = _seed_user(db_session)
    project = _seed_project(db_session, creator=creator)
    version = _seed_version(db_session, project)
    state = _seed_state(db_session, version)
    skeleton = json.dumps({"epics": [{"title": "E1", "feats": [{"title": "F1"}]}], "cross_cutting_rules": "none"})
    _mock_claude(monkeypatch, f"<<<TASK_PLAN_JSON>>>\n{skeleton}\n<<<END_TASK_PLAN_JSON>>>")

    await orchestrator._invoke_plan_pass(
        db_session,
        state,
        prompt="x",
        json_schema=TASK_PLAN_SKELETON_JSON_SCHEMA,
        parser=parse_task_plan_skeleton,
        label_fn=lambda _s: "skeleton",
        metrics_phase="navrh",
        stage="priprava",
    )

    msg = _latest_msg(db_session, version.id, "ai_agent")
    assert msg.stage == "priprava"
    assert msg.payload["phase"] == "navrh"


@pytest.mark.asyncio
async def test_plan_pass_legacy_phase_equals_stage(db_session, monkeypatch) -> None:
    """Legacy plan pass (no ``metrics_phase``) keeps the pre-STEP-3 behaviour: ``phase == stage``."""
    creator = _seed_user(db_session)
    project = _seed_project(db_session, creator=creator)
    version = _seed_version(db_session, project)
    state = _seed_state(db_session, version, mode=None, stage="navrh")
    skeleton = json.dumps({"epics": [{"title": "E1", "feats": [{"title": "F1"}]}], "cross_cutting_rules": "none"})
    _mock_claude(monkeypatch, f"<<<TASK_PLAN_JSON>>>\n{skeleton}\n<<<END_TASK_PLAN_JSON>>>")

    await orchestrator._invoke_plan_pass(
        db_session,
        state,
        prompt="x",
        json_schema=TASK_PLAN_SKELETON_JSON_SCHEMA,
        parser=parse_task_plan_skeleton,
        label_fn=lambda _s: "skeleton",
        stage="navrh",
    )

    msg = _latest_msg(db_session, version.id, "ai_agent")
    assert msg.stage == "navrh"
    assert msg.payload["phase"] == "navrh"


# ===========================================================================
# Part 1 — FAILURE-path stamping (C1, metrics-v3-followup.md): the two SHARED
# failure helpers must attribute a usage/timing-bearing failed turn to the
# round's metrics phase, NOT leak 'priprava' via the stage fallback.
# ===========================================================================


def _fail(reason: str = "no status block") -> ParseFailure:
    """A hard parse failure that STILL carries ``timing`` — so ``_failure_metrics_payload`` folds it and
    the message counts toward per-phase metrics (the whole reason the phase stamp matters). ``lost_work``
    stays ``None`` so ``_settle_plan_pass_failure`` takes the notification-recording hard-failure branch."""
    return ParseFailure(reason=reason, raw="junk", timing={"duration_seconds": 2.0})


@pytest.mark.asyncio
async def test_conversation_turn_parse_exhaustion_stamps_navrh_not_priprava(db_session, monkeypatch) -> None:
    """C1: a parse-exhaustion on a pre-build conversation turn stamps the system notification
    ``payload['phase']=='navrh'`` (metrics) while its ``stage`` stays ``'priprava'`` (routing/deploy gate)."""
    creator = _seed_user(db_session)
    project = _seed_project(db_session, creator=creator)
    version = _seed_version(db_session, project)
    _seed_state(db_session, version)

    async def _fake_retry(*_a, **_kw):
        return _fail()

    monkeypatch.setattr(orchestrator, "invoke_agent_with_parse_retry", _fake_retry)

    await orchestrator.run_conversation_turn(db_session, version.id)

    note = _latest_msg(db_session, version.id, "system")
    assert note.stage == "priprava"  # control-flow STAGE untouched (deploy gate reads this)
    assert note.payload["phase"] == "navrh"  # metrics attribute to Návrh, NOT the priprava leak


@pytest.mark.asyncio
async def test_kontrola_round_parse_exhaustion_stamps_verifikacia(db_session, monkeypatch) -> None:
    """C1: a parse-exhaustion in the honest self-check round stamps ``payload['phase']=='verifikacia'``
    while ``stage`` stays ``'priprava'`` (invisible to the release/deploy path)."""
    creator = _seed_user(db_session)
    project = _seed_project(db_session, creator=creator)
    version = _seed_version(db_session, project)
    state = _seed_state(db_session, version)

    async def _fake_smoke(_slug, _label, _coverage):
        return (True, "boot ok"), (True, "acceptance ok", False)

    async def _fake_retry(*_a, **_kw):
        return _fail()

    monkeypatch.setattr(orchestrator, "_run_release_smoke", _fake_smoke)
    monkeypatch.setattr(orchestrator, "invoke_agent_with_parse_retry", _fake_retry)

    await orchestrator._run_conversation_kontrola_round(db_session, state)

    note = _latest_msg(db_session, version.id, "system")  # the exhaustion note is the last system msg
    assert note.stage == "priprava"
    assert note.payload["phase"] == "verifikacia"


@pytest.mark.asyncio
async def test_plan_pass_failure_under_navrh_metrics_phase_stamps_navrh(db_session, monkeypatch) -> None:
    """C1: a plan-pass failure inside ``_generate_incremental_plan(metrics_phase='navrh')`` (STEP-3
    conversation register, ``stage='priprava'``) stamps the settle notification ``payload['phase']=='navrh'``
    while ``stage`` stays ``'priprava'``."""
    creator = _seed_user(db_session)
    project = _seed_project(db_session, creator=creator)
    version = _seed_version(db_session, project)
    state = _seed_state(db_session, version)

    async def _fake_pass(*_a, **_kw):
        return _fail("agent nevrátil platnú kostru plánu")

    monkeypatch.setattr(orchestrator, "_invoke_plan_pass", _fake_pass)

    settled = await orchestrator._generate_incremental_plan(
        db_session,
        state,
        stage="priprava",
        on_event=None,
        directive=None,
        on_message=None,
        metrics_phase="navrh",
    )
    assert settled is not None and settled.status == "blocked"

    note = _latest_msg(db_session, version.id, "system")
    assert note.stage == "priprava"
    assert note.payload["phase"] == "navrh"


@pytest.mark.asyncio
async def test_record_parse_exhaustion_legacy_stamps_stage(db_session) -> None:
    """C1 regression guard: a legacy caller (no ``metrics_phase``) is byte-identical — ``phase == stage``."""
    creator = _seed_user(db_session)
    project = _seed_project(db_session, creator=creator)
    version = _seed_version(db_session, project)
    state = _seed_state(db_session, version, mode=None, stage="navrh")

    await orchestrator._record_parse_exhaustion(
        db_session,
        state,
        stage="navrh",
        result=_fail(),
        human_hint="skús znova",
        on_message=None,
    )

    note = _latest_msg(db_session, version.id, "system")
    assert note.stage == "navrh"
    assert note.payload["phase"] == "navrh"  # legacy: no override → phase == stage


@pytest.mark.asyncio
async def test_settle_plan_pass_failure_legacy_stamps_stage(db_session) -> None:
    """C1 regression guard: legacy ``_settle_plan_pass_failure`` (no ``metrics_phase``) → ``phase == stage``."""
    creator = _seed_user(db_session)
    project = _seed_project(db_session, creator=creator)
    version = _seed_version(db_session, project)
    state = _seed_state(db_session, version, mode=None, stage="navrh")

    await orchestrator._settle_plan_pass_failure(
        db_session,
        state,
        _fail("hard fail"),
        note="plán zlyhal",
        on_message=None,
        stage="navrh",
    )

    note = _latest_msg(db_session, version.id, "system")
    assert note.stage == "navrh"
    assert note.payload["phase"] == "navrh"


# ===========================================================================
# Part 2 — _build_phases: data-driven phase list (no phantom empty rows)
# ===========================================================================


def test_build_phases_drops_zero_token_phases_keeps_canonical_order(db_session) -> None:
    """A v3 project (navrh/programovanie/verifikacia non-zero, priprava absent) → exactly those three rows in
    canonical order — NO phantom ``priprava`` row."""
    by_phase = {
        "navrh": _ut(100, 50),
        "programovanie": _ut(2000, 800),
        "verifikacia": _ut(300, 120),
    }
    rows = _build_phases(db_session, by_phase, 0.0, 0.0)
    assert [r.phase for r in rows] == ["navrh", "programovanie", "verifikacia"]


def test_build_phases_priprava_zero_row_dropped(db_session) -> None:
    """A ``priprava`` bucket present but with 0 tokens is DROPPED (no empty row)."""
    by_phase = {
        "priprava": UsageTotals(),  # present but zero → dropped
        "navrh": _ut(100, 50),
        "programovanie": _ut(200, 80),
        "verifikacia": _ut(30, 12),
    }
    rows = _build_phases(db_session, by_phase, 0.0, 0.0)
    assert [r.phase for r in rows] == ["navrh", "programovanie", "verifikacia"]
    assert "priprava" not in [r.phase for r in rows]


def test_build_phases_footing_preserved(db_session) -> None:
    """Token footing: the sum over the emitted phase rows equals the grand total (dropped phases were 0)."""
    by_phase = {
        "navrh": _ut(100, 50),
        "programovanie": _ut(2000, 800),
        "verifikacia": _ut(300, 120),
    }
    rows = _build_phases(db_session, by_phase, 0.0, 0.0)
    grand = sum(t.input_tokens + t.output_tokens for t in by_phase.values())
    assert sum(r.input_tokens + r.output_tokens for r in rows) == grand


def test_build_phases_legacy_all_four_when_all_nonzero(db_session) -> None:
    """A legacy project that truly used all four phases still renders all four (regression guard)."""
    by_phase = {p: _ut(10, 5) for p in COMPARISON_PHASES}
    rows = _build_phases(db_session, by_phase, 0.0, 0.0)
    assert [r.phase for r in rows] == list(COMPARISON_PHASES)


def test_build_phases_empty_by_phase_yields_no_rows(db_session) -> None:
    assert _build_phases(db_session, {}, 0.0, 0.0) == []


# ---------------------------------------------------------------------------
# Part 2 — C2 (metrics-v3-followup.md): the drop predicate must be metered-
# ACTIVITY, not tokens alone — a 0-token-but-real-time phase must survive.
# ---------------------------------------------------------------------------


def _ut_time_only(duration: float = 5.0, *, model: str = "claude-opus-4-8") -> UsageTotals:
    """A phase with real wall-clock but NO tokens (a failed turn whose envelope carried timing, no usage)."""
    t = UsageTotals()
    t.add(input_tokens=0, output_tokens=0, duration_seconds=duration, model=model)
    return t


def test_build_phases_keeps_zero_token_nonzero_duration_phase(db_session) -> None:
    """C2: a 0-token phase with non-zero ``duration_seconds`` is KEPT — dropping it would erase its
    ``active_seconds`` from the x_faster denominator (a one-sided bias flattering the agent)."""
    by_phase = {
        "navrh": _ut_time_only(5.0),  # 0 tokens, real time
        "programovanie": _ut(2000, 800),
    }
    rows = _build_phases(db_session, by_phase, 0.0, 0.0)
    assert [r.phase for r in rows] == ["navrh", "programovanie"]  # navrh NOT dropped
    navrh_row = next(r for r in rows if r.phase == "navrh")
    assert navrh_row.active_seconds == 5.0


def test_build_phases_keeps_zero_token_nonzero_parse_attempts_phase(db_session) -> None:
    """C2: a 0-token phase with ``parse_attempts > 0`` (rework evidence) is KEPT, in canonical order."""
    t_pa = UsageTotals()
    t_pa.add(input_tokens=0, output_tokens=0, duration_seconds=0.0, parse_attempts=2, model="claude-opus-4-8")
    by_phase = {"verifikacia": t_pa, "programovanie": _ut(100, 40)}
    rows = _build_phases(db_session, by_phase, 0.0, 0.0)
    assert [r.phase for r in rows] == ["programovanie", "verifikacia"]  # canonical order, verifikacia kept


def test_build_phases_still_drops_fully_empty_phase(db_session) -> None:
    """C2: a phase with NO metered activity (0 tokens, 0 time, 0 parse attempts) is still DROPPED."""
    by_phase = {"priprava": UsageTotals(), "navrh": _ut(100, 50)}
    rows = _build_phases(db_session, by_phase, 0.0, 0.0)
    assert [r.phase for r in rows] == ["navrh"]


def test_build_phases_duration_footing_with_zero_token_phase(db_session) -> None:
    """C2: both TOKEN and DURATION footing hold vs the grand total once a 0-token/non-zero-time phase is kept."""
    by_phase = {
        "navrh": _ut_time_only(5.0),
        "programovanie": _ut(2000, 800, duration=12.0),
    }
    rows = _build_phases(db_session, by_phase, 0.0, 0.0)
    grand_tok = sum(t.input_tokens + t.output_tokens for t in by_phase.values())
    grand_dur = sum(t.duration_seconds for t in by_phase.values())
    assert sum(r.input_tokens + r.output_tokens for r in rows) == grand_tok
    assert sum(r.active_seconds for r in rows) == grand_dur
