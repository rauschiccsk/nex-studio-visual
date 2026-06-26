"""CR-V2-009 — 4-phase state-machine transitions + preserved-safeguard assertions.

Milestone B rebuilt ``apply_action`` + ``STAGE_ORDER`` to the 4 phases (Príprava → Návrh →
Programovanie → Verifikácia → done) with the AI Agent (doer) + Auditor (verifier). These units exercise
the NEW transitions and assert the 5 R-BLAST safeguards survived the rewrite. The deep per-phase
BEHAVIOURS (Príprava dialogue, Návrh task plan, Programovanie loop, Verifikácia smoke) are Milestone C/D
and are NOT exercised here — this is the state machine, run against the real branch DB (4-phase CHECKs).
"""

import uuid

import pytest

from backend.db.models.foundation import User
from backend.db.models.pipeline import PipelineMessage, PipelineState
from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.services import orchestrator

# (pytest ``asyncio_mode = auto`` — async tests run without an explicit mark.)


def _make_version(db_session):
    user = User(
        username=f"u_{uuid.uuid4().hex[:8]}",
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        password_hash="x",
        role="ri",
    )
    db_session.add(user)
    db_session.flush()
    project = Project(
        name=f"P {uuid.uuid4().hex[:8]}",
        slug=f"p-{uuid.uuid4().hex[:8]}",
        type="standard",
        auth_mode="password",
        description="d",
        created_by=user.id,
    )
    db_session.add(project)
    db_session.flush()
    version = Version(project_id=project.id, version_number=f"1.{uuid.uuid4().hex[:4]}.0")
    db_session.add(version)
    db_session.flush()
    return version, project


@pytest.fixture
def fake_claude(monkeypatch):
    """``invoke_claude`` is never actually reached by these state-machine units (the dispatch runs in the
    background runner, not in apply_action), but the orchestrator imports it — stub so nothing hits a live
    CLI even if a path tries."""

    async def _fake(**_kw):
        return ""

    monkeypatch.setattr(orchestrator, "invoke_claude", _fake)
    return _fake


def _state(db_session, version_id) -> PipelineState:
    return orchestrator._get_state(db_session, version_id)


def _settle(db_session, version_id, status="awaiting_manazer"):
    st = _state(db_session, version_id)
    st.status = status
    db_session.flush()
    return st


def _msgs(db_session, version_id):
    from sqlalchemy import select

    return (
        db_session.execute(
            select(PipelineMessage).where(PipelineMessage.version_id == version_id).order_by(PipelineMessage.seq.asc())
        )
        .scalars()
        .all()
    )


# ── STAGE_ORDER / helpers ──────────────────────────────────────────────────


def test_stage_order_is_four_phases_plus_done():
    assert orchestrator.STAGE_ORDER == ("priprava", "navrh", "programovanie", "verifikacia", "done")
    # The v1 11-stage waterfall is gone.
    for dead in ("kickoff", "gate_a", "gate_e", "task_plan", "build", "gate_g", "release"):
        assert dead not in orchestrator.STAGE_ORDER


def test_fast_fix_skips_navrh():
    assert orchestrator.FAST_FIX_STAGE_ORDER == ("priprava", "programovanie", "verifikacia", "done")


def test_next_stage_new_version_walks_four_phases():
    assert orchestrator._next_stage("priprava", "new_version") == "navrh"
    assert orchestrator._next_stage("navrh", "new_version") == "programovanie"
    assert orchestrator._next_stage("programovanie", "new_version") == "verifikacia"
    assert orchestrator._next_stage("verifikacia", "new_version") == "done"
    assert orchestrator._next_stage("done", "new_version") == "done"  # clamps at terminal


def test_next_stage_fast_fix_priprava_to_programovanie():
    assert orchestrator._next_stage("priprava", "fast_fix") == "programovanie"


def test_stage_actor_two_roles():
    assert orchestrator.STAGE_ACTOR["priprava"] == "ai_agent"
    assert orchestrator.STAGE_ACTOR["navrh"] == "ai_agent"
    assert orchestrator.STAGE_ACTOR["programovanie"] == "ai_agent"
    assert orchestrator.STAGE_ACTOR["verifikacia"] == "auditor"
    assert orchestrator.STAGE_ACTOR.get("done") is None  # terminal — no actor


# ── start ──────────────────────────────────────────────────────────────────


async def test_start_begins_priprava(db_session, fake_claude):
    version, _ = _make_version(db_session)
    state = await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    assert state.current_stage == "priprava"
    assert state.current_actor == "ai_agent"
    assert state.status == "agent_working"
    assert state.flow_type == "new_version"
    # the kickoff message lands in Príprava, manazer→ai_agent, carrying the phase stamp
    msgs = _msgs(db_session, version.id)
    assert msgs[0].stage == "priprava"
    assert msgs[0].author == "manazer" and msgs[0].recipient == "ai_agent"
    assert msgs[0].payload["phase"] == "priprava"


async def test_start_rejects_dropped_flow_type(db_session, fake_claude):
    version, _ = _make_version(db_session)
    with pytest.raises(orchestrator.OrchestratorError, match="Invalid flow_type"):
        await orchestrator.apply_action(db_session, version_id=version.id, action="start", payload={"flow_type": "cr"})


async def test_start_persists_per_build_dial_override(db_session, fake_claude):
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(
        db_session, version_id=version.id, action="start", payload={"miera_autonomie": "po_kazdej_faze"}
    )
    assert _state(db_session, version.id).miera_autonomie == "po_kazdej_faze"


async def test_start_unknown_dial_degrades_to_inherit(db_session, fake_claude):
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(
        db_session, version_id=version.id, action="start", payload={"miera_autonomie": "bogus"}
    )
    assert _state(db_session, version.id).miera_autonomie is None  # inherits per-project / global


async def test_start_unknown_action_raises(db_session, fake_claude):
    version, _ = _make_version(db_session)
    with pytest.raises(orchestrator.OrchestratorError, match="Unknown action"):
        await orchestrator.apply_action(db_session, version_id=version.id, action="apply_coordinator_recommendation")


# ── approve_spec — ALWAYS-mandatory end-Príprava stop (dial-independent) ──────


async def test_approve_spec_advances_priprava_to_navrh(db_session, fake_claude):
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _settle(db_session, version.id)
    state = await orchestrator.apply_action(db_session, version_id=version.id, action="approve_spec")
    assert state.current_stage == "navrh"
    assert state.status == "agent_working"  # re-dispatched into Návrh


async def test_approve_spec_always_offered_in_priprava_regardless_of_dial(db_session, fake_claude):
    # The Špecifikácia approval is dial-INDEPENDENT (design §2.3): it is offered at every autonomy level.
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(
        db_session, version_id=version.id, action="start", payload={"miera_autonomie": "plna"}
    )
    st = _settle(db_session, version.id)
    assert "approve_spec" in orchestrator.determine_available_actions(st)
    # dial logic agrees: approve_spec ALWAYS stops, even at plná autonómia.
    assert orchestrator.dial_stops_at("plna", "approve_spec") is True


async def test_approve_spec_rejected_outside_priprava(db_session, fake_claude):
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    st = _state(db_session, version.id)
    st.current_stage = "navrh"
    st.status = "awaiting_manazer"
    db_session.flush()
    with pytest.raises(orchestrator.OrchestratorError, match="Schváliť špecifikáciu je platné len"):
        await orchestrator.apply_action(db_session, version_id=version.id, action="approve_spec")


async def test_fast_fix_approve_spec_skips_navrh(db_session, fake_claude):
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(
        db_session, version_id=version.id, action="start", payload={"flow_type": "fast_fix", "directive": "fix the bug"}
    )
    _settle(db_session, version.id)
    state = await orchestrator.apply_action(db_session, version_id=version.id, action="approve_spec")
    assert state.current_stage == "programovanie"  # fast_fix has no Návrh


# ── schvalit — dial-governed schvaľovacie body ───────────────────────────────


async def test_schvalit_navrh_advances_to_programovanie(db_session, fake_claude):
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    st = _state(db_session, version.id)
    st.current_stage = "navrh"
    st.status = "awaiting_manazer"
    db_session.flush()
    state = await orchestrator.apply_action(db_session, version_id=version.id, action="schvalit")
    assert state.current_stage == "programovanie"
    assert state.status == "agent_working"


async def test_schvalit_rejected_at_priprava(db_session, fake_claude):
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _settle(db_session, version.id)
    with pytest.raises(orchestrator.OrchestratorError, match="Schváliť je platné len na schvaľovacom bode"):
        await orchestrator.apply_action(db_session, version_id=version.id, action="schvalit")


# ── verdict — Auditor PASS / FAIL loop ───────────────────────────────────────


def _to_verifikacia(db_session, version_id, *, iteration=0):
    st = _state(db_session, version_id)
    st.current_stage = "verifikacia"
    st.current_actor = "auditor"
    st.status = "awaiting_manazer"
    st.iteration = iteration
    db_session.flush()
    return st


async def test_verdict_pass_settles_for_signoff_not_done(db_session, fake_claude):
    # PASS records the Auditor verdict + settles awaiting the Manažér's end sign-off; it does NOT silently
    # jump to Hotovo (preserves the dial-governed end stop + the no-silent-done invariant).
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _to_verifikacia(db_session, version.id)
    state = await orchestrator.apply_action(
        db_session, version_id=version.id, action="verdict", payload={"verdict": "PASS"}
    )
    assert state.current_stage == "verifikacia"
    assert state.status == "awaiting_manazer"
    verdicts = [m for m in _msgs(db_session, version.id) if m.kind == "verdict"]
    assert verdicts[-1].author == "auditor" and verdicts[-1].payload["verdict"] == "PASS"


async def test_schvalit_to_done_requires_pass_verdict(db_session, fake_claude):
    # no-silent-done invariant (safeguard #5, v2 form): a Verifikácia sign-off to Hotovo is REFUSED unless a
    # PASS verdict was recorded — never a silent done.
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _to_verifikacia(db_session, version.id)
    with pytest.raises(orchestrator.OrchestratorError, match="Auditor ešte nevydal PASS"):
        await orchestrator.apply_action(db_session, version_id=version.id, action="schvalit")


async def test_pass_then_schvalit_reaches_hotovo(db_session, fake_claude):
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _to_verifikacia(db_session, version.id)
    await orchestrator.apply_action(db_session, version_id=version.id, action="verdict", payload={"verdict": "PASS"})
    state = await orchestrator.apply_action(db_session, version_id=version.id, action="schvalit")
    assert state.current_stage == "done"
    assert state.status == "done"
    assert "Hotovo" in state.next_action or "dokončená" in state.next_action


async def test_verdict_fail_loops_fix_back_to_ai_agent(db_session, fake_claude):
    # FAIL loops the fix back to the AI Agent → re-enter Programovanie, bump the round counter.
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _to_verifikacia(db_session, version.id, iteration=0)
    state = await orchestrator.apply_action(
        db_session, version_id=version.id, action="verdict", payload={"verdict": "FAIL"}
    )
    assert state.current_stage == "programovanie"
    assert state.current_actor == "ai_agent"
    assert state.is_regate is True
    assert state.iteration == 1
    assert state.status == "agent_working"


async def test_verdict_fail_escalates_after_auditor_loop_max(db_session, fake_claude):
    # After AUDITOR_LOOP_MAX still-failing rounds, STOP + escalate to the Manažér (bounded loop).
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _to_verifikacia(db_session, version.id, iteration=orchestrator.AUDITOR_LOOP_MAX)
    state = await orchestrator.apply_action(
        db_session, version_id=version.id, action="verdict", payload={"verdict": "FAIL"}
    )
    assert state.status == "blocked"
    assert state.block_reason == "agent_error"
    assert "eskalované" in state.next_action.lower()
    # phase did NOT advance — it escalated, not re-looped
    assert state.current_stage == "verifikacia"


async def test_verdict_rejected_outside_verifikacia(db_session, fake_claude):
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _settle(db_session, version.id)
    with pytest.raises(orchestrator.OrchestratorError, match="verdict je platné len vo fáze Verifikácia"):
        await orchestrator.apply_action(
            db_session, version_id=version.id, action="verdict", payload={"verdict": "PASS"}
        )


async def test_verdict_requires_valid_value(db_session, fake_claude):
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _to_verifikacia(db_session, version.id)
    with pytest.raises(orchestrator.OrchestratorError, match="verdict requires"):
        await orchestrator.apply_action(
            db_session, version_id=version.id, action="verdict", payload={"verdict": "MAYBE"}
        )


# ── uprav / ask / answer — direct Manažér↔agent comms (no Coordinator relay) ──


async def test_uprav_reworks_phase_without_advancing(db_session, fake_claude):
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    st = _state(db_session, version.id)
    st.current_stage = "navrh"
    st.status = "awaiting_manazer"
    db_session.flush()
    state = await orchestrator.apply_action(
        db_session, version_id=version.id, action="uprav", payload={"comment": "pridaj audit log"}
    )
    assert state.current_stage == "navrh"  # did NOT advance
    assert state.status == "agent_working"  # re-dispatched the same phase
    msg = _msgs(db_session, version.id)[-1]
    assert msg.author == "manazer" and msg.recipient == "ai_agent" and msg.kind == "return"


async def test_uprav_requires_comment(db_session, fake_claude):
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _settle(db_session, version.id)
    with pytest.raises(orchestrator.OrchestratorError, match="uprav requires"):
        await orchestrator.apply_action(db_session, version_id=version.id, action="uprav", payload={"comment": "  "})


async def test_answer_requires_blocked(db_session, fake_claude):
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _settle(db_session, version.id, status="awaiting_manazer")
    with pytest.raises(orchestrator.OrchestratorError, match="Agent sa na nič nepýta"):
        await orchestrator.apply_action(db_session, version_id=version.id, action="answer", payload={"text": "yes"})


async def test_answer_threads_into_blocked_question(db_session, fake_claude):
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _settle(db_session, version.id, status="blocked")
    state = await orchestrator.apply_action(
        db_session, version_id=version.id, action="answer", payload={"text": "použi PostgreSQL"}
    )
    assert state.status == "agent_working"
    msg = _msgs(db_session, version.id)[-1]
    assert msg.author == "manazer" and msg.kind == "answer"


# ── pause / pokracovat ───────────────────────────────────────────────────────


async def test_pause_only_in_programovanie(db_session, fake_claude):
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")  # priprava/agent_working
    with pytest.raises(orchestrator.OrchestratorError, match="Pauza je možná len počas fázy Programovanie"):
        await orchestrator.apply_action(db_session, version_id=version.id, action="pause")


async def test_pause_then_pokracovat_resumes(db_session, fake_claude):
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    st = _state(db_session, version.id)
    st.current_stage = "programovanie"
    st.current_actor = "ai_agent"
    db_session.flush()  # agent_working in programovanie
    state = await orchestrator.apply_action(db_session, version_id=version.id, action="pause")
    assert state.status == "paused"
    state = await orchestrator.apply_action(db_session, version_id=version.id, action="pokracovat")
    assert state.status == "agent_working"
    assert state.current_stage == "programovanie"


# ── determine_available_actions per phase ────────────────────────────────────


async def test_available_actions_priprava_offers_approve_spec(db_session, fake_claude):
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    st = _settle(db_session, version.id)
    acts = orchestrator.determine_available_actions(st)
    assert "approve_spec" in acts
    assert "schvalit" not in acts  # not a dial-governed schvaľovací bod
    # the retired v1 verbs are gone from the offerable set
    for dead in ("approve", "apply_coordinator_recommendation", "fix", "leave", "end_gate_e", "verdict"):
        assert dead not in acts


async def test_available_actions_verifikacia_offers_verdict_and_schvalit(db_session, fake_claude):
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    st = _to_verifikacia(db_session, version.id)
    acts = orchestrator.determine_available_actions(st)
    assert {"verdict", "schvalit"} <= acts


async def test_available_actions_programovanie_working_offers_pause(db_session, fake_claude):
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    st = _state(db_session, version.id)
    st.current_stage = "programovanie"
    st.current_actor = "ai_agent"
    st.status = "agent_working"
    db_session.flush()
    assert orchestrator.determine_available_actions(st) == {"pause"}


# ── safeguard: sole-mutator (apply_action is the only pipeline_state mutator) ──


def test_apply_action_is_sole_mutator_grep_guard():
    """Belt-and-suspenders: the sole-mutator invariant is also grep-asserted in CI; here we assert the
    state-machine surface (the action verbs) matches the design vocabulary so a drift is caught early."""
    assert orchestrator._ACTIONS == frozenset(
        {"start", "approve_spec", "schvalit", "uprav", "pokracovat", "verdict", "ask", "answer", "pause"}
    )
    # apply_coordinator_recommendation is REMOVED.
    assert "apply_coordinator_recommendation" not in orchestrator._ACTIONS


# ── safeguard: provisional auto_chain bound (R-AUTOCHAIN) ─────────────────────


def test_auto_chain_limit_is_provisional_four_phase_bound(db_session, fake_claude):
    version, _ = _make_version(db_session)
    # No Gate-E ceiling/slack any more — the bound is len(STAGE_ORDER) + the Auditor-loop margin.
    expected = len(orchestrator.STAGE_ORDER) + 2 * orchestrator.AUDITOR_LOOP_MAX
    assert orchestrator.auto_chain_limit(db_session, version.id) == expected


def test_coordinator_triage_retired_returns_none(db_session, fake_claude):
    version, _ = _make_version(db_session)
    st = PipelineState(
        version_id=version.id,
        flow_type="new_version",
        current_stage="navrh",
        current_actor="ai_agent",
        status="awaiting_manazer",
        next_action="x",
    )
    db_session.add(st)
    db_session.flush()
    assert orchestrator.coordinator_triage(db_session, version.id, st) is None
