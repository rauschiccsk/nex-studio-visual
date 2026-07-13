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
from backend.db.models.tasks import Epic, Feat, Task
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


def test_stage_order_is_five_phases_plus_done():
    # CR-1 (nex-studio-visual): the Vizuál live-preview phase sits between Návrh and Programovanie.
    assert orchestrator.STAGE_ORDER == ("priprava", "navrh", "vizual", "programovanie", "verifikacia", "done")
    # The v1 11-stage waterfall is gone.
    for dead in ("kickoff", "gate_a", "gate_e", "task_plan", "build", "gate_g", "release"):
        assert dead not in orchestrator.STAGE_ORDER


def test_fast_fix_skips_navrh():
    assert orchestrator.FAST_FIX_STAGE_ORDER == ("priprava", "programovanie", "verifikacia", "done")


def test_next_stage_new_version_walks_all_phases():
    assert orchestrator._next_stage("priprava", "new_version") == "navrh"
    # CR-1 (nex-studio-visual): Vizuál sits between Návrh and Programovanie.
    assert orchestrator._next_stage("navrh", "new_version") == "vizual"
    assert orchestrator._next_stage("vizual", "new_version") == "programovanie"
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


def _seed_min_plan(db_session, version, project):
    """Seed a minimal MATERIALIZED task plan (1 Epic→Feat→Task) so a schvalit out of Návrh is legitimate
    (CR-V2-037: advancing to Programovanie with 0 tasks is refused — you cannot build nothing)."""
    epic = Epic(project_id=project.id, version_id=version.id, number=1, title="E", status="planned")
    db_session.add(epic)
    db_session.flush()
    feat = Feat(epic_id=epic.id, number=1, title="F", status="todo")
    db_session.add(feat)
    db_session.flush()
    db_session.add(Task(feat_id=feat.id, number=1, title="T", task_type="backend", status="todo"))
    db_session.flush()


async def test_schvalit_navrh_advances_to_vizual(db_session, fake_claude):
    version, project = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    st = _state(db_session, version.id)
    st.current_stage = "navrh"
    st.status = "awaiting_manazer"
    db_session.flush()
    _seed_min_plan(db_session, version, project)  # a materialized plan is harmless (no longer a precondition)
    state = await orchestrator.apply_action(db_session, version_id=version.id, action="schvalit")
    # CR-1 (nex-studio-visual): Návrh now advances to the Vizuál live-preview phase (not straight to Programovanie).
    assert state.current_stage == "vizual"
    assert state.status == "agent_working"


async def test_schvalit_navrh_no_plan_advances_to_vizual(db_session, fake_claude):
    # nex-studio-visual (Director 2026-07-13): the old CR-V2-037 "refuse schvalit out of Návrh with an EMPTY
    # task plan" gate is GONE — Návrh now produces the design DOCUMENT ONLY (the EPIC→FEAT→TASK plan is built
    # at Programovanie start, from the final design + Vizuál changes). So approving a plan-less Návrh is the
    # normal path: it advances to Vizuál. The empty-plan safety moved WITH the plan to _run_build_round
    # (which blocks on a plan-generation failure — Programovanie can never silently run with nothing to build).
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    st = _state(db_session, version.id)
    st.current_stage = "navrh"
    st.status = "awaiting_manazer"
    db_session.flush()
    state = await orchestrator.apply_action(db_session, version_id=version.id, action="schvalit")
    assert state.current_stage == "vizual"  # no plan is required to advance out of Návrh
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


async def test_verdict_fail_loops_targeted_fix_back_to_ai_agent_gated(db_session, fake_claude):
    # A+B (Director 2026-06-30): FAIL loops a TARGETED fix back to the AI Agent → re-enter Programovanie,
    # bump the round counter — but GATED for the Manažér. CR-V2-058 Part A: the gate is a deliberated Decision
    # Card (blocked/decision_needed, source=verifikacia_fix), not the old blind `paused`. The manual verdict
    # path runs no critic → no fix_critique → the card recommends 'guide' (accept_fix hidden, §2 invariant).
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
    assert state.status == "blocked" and state.block_reason == "decision_needed"
    card = _msgs(db_session, version.id)[-1]
    assert card.kind == "consultation" and card.payload["consultation"]["source"] == "verifikacia_fix"


async def test_verdict_fail_escalates_after_auditor_loop_max(db_session, fake_claude):
    # After AUDITOR_LOOP_MAX still-failing rounds, STOP + surface an operator Decision Card (CR-V2-054,
    # bounded loop). block_reason=decision_needed so the DecisionCardStack renders it.
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _to_verifikacia(db_session, version.id, iteration=orchestrator.AUDITOR_LOOP_MAX)
    state = await orchestrator.apply_action(
        db_session, version_id=version.id, action="verdict", payload={"verdict": "FAIL"}
    )
    assert state.status == "blocked"
    assert state.block_reason == "decision_needed"  # CR-V2-054: a Manažér Decision Card, not a bare error
    assert "Decision Card" in state.next_action
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
        # CR-V2-041: + "decide" (the Manažér picks a consultation Decision Card option).
        # CR-V2-057: + "overit_znovu" ("Over znova" — re-verify a drifted version against the current HEAD).
        # STEP 3 (step3-plan-design.md): + "zostav_plan" ("Zostaviť plán" — compose the task plan from the
        # approved Špecifikácia in the conversation register).
        # STEP 4 (step4-programovanie-design.md): + "spustit_stavbu" ("Spustiť stavbu" — move a conversation
        # build priprava→programovanie and run the EXISTING _run_build_round loop; the completion returns to
        # the rozhovor). NOT advancing (like zostav_plan) — it stays in the conversation register.
        # CR-1 (nex-studio-visual): + "spustit_vizual" ("Spustiť vizuál" — move a conversation build
        # priprava→vizual and run the EXISTING _run_vizual_round live-preview loop; offered alongside
        # spustit_stavbu after the plan). NOT advancing (like spustit_stavbu) — mode stays 'conversation'.
        # STEP 5 (step5-kontrola-design.md): + "skontrolovat" ("Skontrolovať" — the partner's honest self-check
        # after Programovanie; STAYS at priprava, emits a gate_report, invisible to the release/deploy path).
        # NOT advancing (like zostav_plan) — it never walks the phase automaton.
        # STEP 6 (step6-hotovo-design.md): + "hotovo" ("Označiť ako hotové" — the Manažér's TERMINAL sign-off
        # after Kontrola; settles the conversation build to 'done' + records a SHA-anchored manager signature
        # version_verified honours → deployable). NOT advancing (a terminal signature, not a phase-walk).
        {
            "start",
            "approve_spec",
            "schvalit",
            "uprav",
            "pokracovat",
            "verdict",
            "ask",
            "answer",
            "pause",
            "decide",
            "overit_znovu",
            "zostav_plan",
            "spustit_stavbu",
            "spustit_vizual",
            "skontrolovat",
            "hotovo",
        }
    )
    # apply_coordinator_recommendation is REMOVED.
    assert "apply_coordinator_recommendation" not in orchestrator._ACTIONS


# ── safeguard: provisional auto_chain bound (R-AUTOCHAIN) ─────────────────────


def test_auto_chain_limit_is_provisional_four_phase_bound(db_session, fake_claude):
    version, _ = _make_version(db_session)
    # No Gate-E ceiling/slack any more — the bound is len(STAGE_ORDER) + the Auditor-loop margin.
    expected = len(orchestrator.STAGE_ORDER) + 2 * orchestrator.AUDITOR_LOOP_MAX
    assert orchestrator.auto_chain_limit(db_session, version.id) == expected


def test_coordinator_triage_helper_removed(db_session, fake_claude):
    """CR-V2-021: the v1 ``coordinator_triage`` board helper (a retired stub since CR-V2-009) is now
    REMOVED entirely with the v1 board route — the 4-phase Vývoj board has no Coordinator triage slot
    (design §2.2 — the Coordinator hub-and-spoke is gone). Assert the symbol no longer exists rather than
    that it returns None (the slot it fed is dropped from PipelineBoardRead)."""
    assert not hasattr(orchestrator, "coordinator_triage")
    assert not hasattr(orchestrator, "autonomous_decisions_summary")


# ── CR-V2-021: the re-authored Vývoj board route contract ─────────────────────


def test_board_route_v2_contract(db_session, fake_claude):
    """CR-V2-021: the re-authored ``_board`` assembler produces the 4-phase Vývoj contract — it carries the
    state + dial-governed available_actions + Programovanie split-view facts + two-agent who's-up, and the
    v1 Gate-E / gate_g / Coordinator board fields are GONE from ``PipelineBoardRead``."""
    from backend.api.routes.pipeline import _board

    version, _ = _make_version(db_session)
    st = PipelineState(
        version_id=version.id,
        flow_type="new_version",
        current_stage="programovanie",
        current_actor="ai_agent",
        status="awaiting_manazer",
        next_action="x",
    )
    db_session.add(st)
    db_session.flush()
    board = _board(db_session, version.id)
    # v2 fields present.
    assert board.state is not None and board.state.current_stage == "programovanie"
    # Bug 1 (cockpit-timeout-and-activity-fix.md): a settled Programovanie whose plan is NOT complete (here: no
    # materialized tasks → build_readiness.all_tasks_done False) offers "Pokračovať" (resume the build), NOT the
    # advance verb "schvalit" — the board post-filter swaps them so a timed-out build gets a clean resume.
    assert "pokracovat" in board.available_actions
    assert {s.role for s in board.agent_sessions} == {orchestrator.AI_AGENT_ROLE, orchestrator.AUDITOR_ROLE}
    # v1 board fields DROPPED from the schema (no longer serialised).
    dumped = board.model_dump()
    for dead in (
        "gate_e_open_findings",
        "release_acceptance_satisfied",
        "regate_proposal",
        "coordinator_triage",
        "autonomous_decisions_summary",
    ):
        assert dead not in dumped, f"v1 board field {dead!r} survived CR-V2-021"


def test_board_route_v2_before_start(db_session, fake_claude):
    """A version whose pipeline never started → ``state`` None + empty offerable actions, no crash from the
    dropped v1 helpers (the route no longer calls coordinator_triage / autonomous_decisions_summary)."""
    from backend.api.routes.pipeline import _board

    version, _ = _make_version(db_session)
    board = _board(db_session, version.id)
    assert board.state is None
    assert board.available_actions == []
    assert board.agent_sessions == []
