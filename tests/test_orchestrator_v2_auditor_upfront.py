"""CR-V2-013 — Auditor UPFRONT spec/design review (replaces the Gate-E Customer function).

Exercised against the real v2 branch DB (4-phase + 2-role CHECKs). After Návrh (design doc + task plan
persisted), the engine runs ONE independent Auditor invocation that scans the Špecifikácia + design doc for
holes / ambiguities / contradictions and emits ONE ``kind=verdict`` block:

* a clean review (``verdict=true``) → the post-Návrh schvaľovací bod is governed by the Miera autonómie dial
  (``plna`` auto-continues to Programovanie; ``po_kazdej_faze`` stops);
* a HOLE (``verdict=false``) → ESCALATES to the Manažér (AUD-4): the build STOPS at the post-Návrh stop
  regardless of the dial, with the findings surfaced (no auto-continue into Programovanie);
* a parse failure of the review is NON-BLOCKING (visible + metered, treated as "no hole" — never wedges).

These tests assert the Auditor's verdict message lands with VALID v2 DB CHECK tokens (``author=auditor``,
``recipient=manazer``, ``stage=navrh``, ``kind=verdict``) — a happy-path-only test would not catch an
invalid insert, so the FAIL/hole/escalation branch is exercised against the LIVE DB.

The whole Návrh round is driven through ``run_dispatch``; the design-doc turn + the Auditor review turn are
both ``invoke_agent_with_parse_retry`` calls, stubbed by a ROLE-DISPATCHING fake so the AI-Agent turn and the
Auditor turn return their own blocks. The folded task-plan passes are stubbed via a fake ``invoke_claude``.
"""

import uuid

from sqlalchemy import select

from backend.db.models.foundation import User
from backend.db.models.pipeline import PipelineMessage, PipelineState
from backend.db.models.projects import Project
from backend.db.models.tasks import Epic
from backend.db.models.versions import Version
from backend.services import orchestrator
from backend.services.pipeline_status import ParseFailure, PipelineStatusBlock

# (pytest ``asyncio_mode = auto`` — async tests run without an explicit mark.)


def _make_version(db_session, *, project_dial=None):
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
        source_path=None,  # library/no-checkout → artifacts live in the DB audit trail
        miera_autonomie=project_dial,
    )
    db_session.add(project)
    db_session.flush()
    version = Version(project_id=project.id, version_number=f"1.{uuid.uuid4().hex[:4]}.0")
    db_session.add(version)
    db_session.flush()
    return version, project


def _seed_navrh(db_session, version_id, *, build_dial=None):
    state = PipelineState(
        version_id=version_id,
        flow_type="new_version",
        current_stage="navrh",
        current_actor="ai_agent",
        status="agent_working",
        next_action="working",
        miera_autonomie=build_dial,
    )
    db_session.add(state)
    db_session.flush()
    return state


def _msgs(db_session, version_id):
    return (
        db_session.execute(
            select(PipelineMessage).where(PipelineMessage.version_id == version_id).order_by(PipelineMessage.seq.asc())
        )
        .scalars()
        .all()
    )


def _epics(db_session, version_id):
    return db_session.execute(select(Epic).where(Epic.version_id == version_id)).scalars().all()


def _design_done():
    """The AI-Agent design-doc turn (kind=done, no inline plan → the engine folds the plan in)."""
    return PipelineStatusBlock(stage="navrh", kind="done", summary="návrh hotový", awaiting="manazer")


def _audit_pass(findings=None):
    return PipelineStatusBlock(
        stage="navrh",
        kind="verdict",
        summary="Špecifikácia + Návrh bez blokujúcej medzery.",
        awaiting="manazer",
        verdict=True,
        findings=findings or [],
    )


def _audit_hole(findings, proposed_fix="Vyjasni dátový model a hraničné prípady."):
    return PipelineStatusBlock(
        stage="navrh",
        kind="verdict",
        summary="Auditor našiel medzeru v Špecifikácii/Návrhu.",
        awaiting="manazer",
        verdict=False,
        findings=findings,
        proposed_fix=proposed_fix,
    )


# ── Role-dispatching stub for the two invoke_agent_with_parse_retry turns ─────


def _stub_turns(monkeypatch, *, design_block, audit_block):
    """Make ``invoke_agent_with_parse_retry`` return *design_block* for the ai_agent turn and *audit_block*
    for the auditor turn; capture what each was prompted with."""
    captured = {}

    async def _fake(db, *, version_id, role, stage, prompt, **_kw):
        if role == orchestrator.AUDITOR_ROLE:
            captured["audit_prompt"] = prompt
            captured["audit_stage"] = stage
            # Record the verdict message the same way invoke_agent would (so the DB sees a real auditor row).
            if not isinstance(audit_block, ParseFailure):
                orchestrator._record_message(
                    db,
                    version_id=version_id,
                    stage=stage,
                    author=role,
                    recipient=_kw.get("recipient", "manazer"),
                    kind="verdict",
                    content=audit_block.summary,
                    payload={
                        "verdict": "PASS" if audit_block.verdict else "FAIL",
                        "findings": audit_block.findings,
                        "proposed_fix": audit_block.proposed_fix,
                        "phase": "navrh",
                        **(_kw.get("extra_payload") or {}),
                    },
                )
            return audit_block
        captured["design_prompt"] = prompt
        captured["design_role"] = role
        return design_block

    monkeypatch.setattr(orchestrator, "invoke_agent_with_parse_retry", _fake)
    return captured


def _stub_plan_passes(monkeypatch):
    """Fold the task plan in via a fake invoke_claude: one EPIC/one FEAT/one TASK."""
    skeleton = {
        "epics": [{"title": "Foundation", "feats": [{"title": "Schema", "estimated_minutes": 60}]}],
        "cross_cutting_rules": "## Invarianty\n- spoločná transakčná hranica",
    }
    feat_tasks = {"tasks": [{"title": "users table", "task_type": "migration", "estimated_minutes": 60}]}

    async def _fake_invoke_claude(*, prompt, **_kw):
        if "KOSTRU" in prompt:
            return ("", None, skeleton)
        return ("", None, feat_tasks)

    monkeypatch.setattr(orchestrator, "invoke_claude", _fake_invoke_claude)
    monkeypatch.setattr(orchestrator, "_split_claude_result", lambda r: r)
    monkeypatch.setattr(orchestrator, "_resolve_orch_session", lambda db, slug, role: (uuid.uuid4(), False))
    monkeypatch.setattr(orchestrator, "_resolve_dispatch_overrides", lambda db, vid, role: (None, None))


# ── The upfront-review brief (DESIGN-BEARING) ─────────────────────────────────


async def test_upfront_brief_instructs_independent_read_only_review(db_session, monkeypatch):
    version, _ = _make_version(db_session, project_dial="po_kazdej_faze")
    _seed_navrh(db_session, version.id)
    cap = _stub_turns(monkeypatch, design_block=_design_done(), audit_block=_audit_pass())
    _stub_plan_passes(monkeypatch)
    await orchestrator.run_dispatch(db_session, version.id)
    p = cap["audit_prompt"]
    assert cap["audit_stage"] == "navrh"
    assert "specification.md" in p  # reads the Špecifikácia
    assert "design.md" in p  # reads the design doc
    assert "READ + RUN-ONLY" in p  # independence: read only, never edits/commits
    assert "kind=verdict" in p
    assert "po Návrhu" in p  # surfaces at the post-Návrh stop


async def test_upfront_brief_depth_scales_with_dial(db_session, monkeypatch):
    # OQ-9: higher autonomy → DÔKLADNÚ (deeper) review; lower autonomy → ZAMERANÚ (lighter).
    hi, _ = _make_version(db_session, project_dial="plna")
    _seed_navrh(db_session, hi.id)
    cap_hi = _stub_turns(monkeypatch, design_block=_design_done(), audit_block=_audit_pass())
    _stub_plan_passes(monkeypatch)
    await orchestrator.run_dispatch(db_session, hi.id)
    assert "DÔKLADNÚ" in cap_hi["audit_prompt"]

    lo, _ = _make_version(db_session, project_dial="po_kazdej_faze")
    _seed_navrh(db_session, lo.id)
    cap_lo = _stub_turns(monkeypatch, design_block=_design_done(), audit_block=_audit_pass())
    _stub_plan_passes(monkeypatch)
    await orchestrator.run_dispatch(db_session, lo.id)
    assert "ZAMERANÚ" in cap_lo["audit_prompt"]


# ── PASS review: the dial governs the post-Návrh stop ─────────────────────────


async def test_upfront_pass_lets_dial_auto_continue(db_session, monkeypatch):
    # Plná autonómia + a clean Auditor verdict → the post-Návrh stop does NOT halt; auto-continue to
    # Programovanie (the Auditor found no hole, so it does not override the dial).
    version, _ = _make_version(db_session, project_dial="plna")
    _seed_navrh(db_session, version.id)
    _stub_turns(monkeypatch, design_block=_design_done(), audit_block=_audit_pass())
    _stub_plan_passes(monkeypatch)
    state = await orchestrator.run_dispatch(db_session, version.id)
    assert state.current_stage == "programovanie"
    assert state.status == "agent_working"
    # the Auditor verdict landed with VALID v2 tokens
    verdicts = [m for m in _msgs(db_session, version.id) if m.kind == "verdict"]
    assert verdicts and verdicts[-1].author == "auditor" and verdicts[-1].recipient == "manazer"
    assert verdicts[-1].stage == "navrh" and verdicts[-1].payload["verdict"] == "PASS"


async def test_upfront_pass_still_stops_when_dial_stops(db_session, monkeypatch):
    # Po každej fáze + a clean verdict → the dial stops the post-Návrh schvaľovací bod normally.
    version, _ = _make_version(db_session, project_dial="po_kazdej_faze")
    _seed_navrh(db_session, version.id)
    _stub_turns(monkeypatch, design_block=_design_done(), audit_block=_audit_pass(findings=["pozn.: zváž index"]))
    _stub_plan_passes(monkeypatch)
    state = await orchestrator.run_dispatch(db_session, version.id)
    assert state.current_stage == "navrh" and state.status == "awaiting_manazer"
    assert "posúdiť návrh" in state.next_action  # the ordinary (non-hole) review next_action


# ── HOLE review: escalates to the Manažér, overrides the dial (AUD-4) ──────────


async def test_upfront_hole_escalates_and_overrides_plna_dial(db_session, monkeypatch):
    # THE GATE: a brief with an OBVIOUS hole → the Auditor surfaces it at the post-Návrh stop, and the stop
    # FIRES even at plná autonómia (where a clean review would auto-continue). AUD-4 escalation.
    version, _ = _make_version(db_session, project_dial="plna")
    _seed_navrh(db_session, version.id)
    hole = _audit_hole(["Špecifikácia neuvádza, ako sa počíta DPH pri reverse-charge."])
    _stub_turns(monkeypatch, design_block=_design_done(), audit_block=hole)
    _stub_plan_passes(monkeypatch)
    state = await orchestrator.run_dispatch(db_session, version.id)
    # the dial would auto-continue on a clean review; the hole OVERRIDES it → stop for the Manažér
    assert state.current_stage == "navrh", "a hole must NOT auto-continue into Programovanie"
    assert state.status == "awaiting_manazer"
    assert "Auditor našiel medzeru" in state.next_action
    # the FAIL verdict landed with VALID v2 tokens (the live CHECK accepted it)
    verdicts = [m for m in _msgs(db_session, version.id) if m.kind == "verdict"]
    assert verdicts[-1].author == "auditor" and verdicts[-1].recipient == "manazer"
    assert verdicts[-1].payload["verdict"] == "FAIL"
    assert verdicts[-1].payload["findings"]  # the concrete hole is recorded
    # the escalation notification (system→manazer) was recorded — surfaces on the board / Telegram
    notes = [m for m in _msgs(db_session, version.id) if m.payload and m.payload.get("upfront_review_hole")]
    assert notes and notes[-1].author == "system" and notes[-1].recipient == "manazer"
    # plan was still materialized (the review runs AFTER the plan folds in)
    assert _epics(db_session, version.id)


async def test_upfront_hole_stops_under_stopping_dial_too(db_session, monkeypatch):
    # Even when the dial would stop anyway, a hole produces the HOLE next_action (clarify), not the plain one.
    version, _ = _make_version(db_session, project_dial="po_kazdej_faze")
    _seed_navrh(db_session, version.id)
    _stub_turns(monkeypatch, design_block=_design_done(), audit_block=_audit_hole(["Chýba auth-mode."]))
    _stub_plan_passes(monkeypatch)
    state = await orchestrator.run_dispatch(db_session, version.id)
    assert state.current_stage == "navrh" and state.status == "awaiting_manazer"
    assert "Auditor našiel medzeru" in state.next_action


async def test_upfront_absent_verdict_is_treated_as_hole_fail_closed(db_session, monkeypatch):
    # Fail-closed: a verdict block WITHOUT an explicit verdict=true is a hole (mirrors _verifikacia_passed).
    version, _ = _make_version(db_session, project_dial="plna")
    _seed_navrh(db_session, version.id)
    no_verdict = PipelineStatusBlock(
        stage="navrh", kind="verdict", summary="nejasné", awaiting="manazer", findings=["?"]
    )  # verdict defaults to None → not True → hole
    _stub_turns(monkeypatch, design_block=_design_done(), audit_block=no_verdict)
    _stub_plan_passes(monkeypatch)
    state = await orchestrator.run_dispatch(db_session, version.id)
    assert state.current_stage == "navrh" and state.status == "awaiting_manazer"  # did NOT auto-continue


# ── Parse failure of the review is NON-BLOCKING ───────────────────────────────


async def test_upfront_parse_failure_is_non_blocking(db_session, monkeypatch):
    # A flaky Auditor turn must never wedge the build: it is recorded visibly + metered (system→manazer) and
    # treated as "no hole" → the dial governs the stop (here plná → auto-continue to Programovanie).
    version, _ = _make_version(db_session, project_dial="plna")
    _seed_navrh(db_session, version.id)
    pf = ParseFailure(
        "auditor returned no parseable block", usage={"input_tokens": 5, "output_tokens": 3, "model": "m"}
    )
    _stub_turns(monkeypatch, design_block=_design_done(), audit_block=pf)
    _stub_plan_passes(monkeypatch)
    state = await orchestrator.run_dispatch(db_session, version.id)
    assert state.current_stage == "programovanie" and state.status == "agent_working"
    # the failure is visible + the tokens are metered (NOT a director note — a v2 system→manazer note)
    notes = [
        m
        for m in _msgs(db_session, version.id)
        if m.author == "system" and m.recipient == "manazer" and "Upfront previerka Auditora sa nepodarila" in m.content
    ]
    assert notes and notes[-1].payload and notes[-1].payload.get("usage")


# ── The verifikacia (end) verdict is unaffected — no cross-talk with the upfront review ──


async def test_upfront_review_does_not_satisfy_end_verification(db_session, monkeypatch):
    # The upfront PASS verdict is at stage=navrh; _verifikacia_passed only counts stage=verifikacia verdicts.
    version, _ = _make_version(db_session, project_dial="po_kazdej_faze")
    _seed_navrh(db_session, version.id)
    _stub_turns(monkeypatch, design_block=_design_done(), audit_block=_audit_pass())
    _stub_plan_passes(monkeypatch)
    await orchestrator.run_dispatch(db_session, version.id)
    assert orchestrator._verifikacia_passed(db_session, version.id) is False


# ── The Gate-E machinery is gone (no Customer↔Designer ping-pong remains) ──────


def test_gate_e_machinery_removed():
    # The per-question Gate-E sub-state-machine + helpers are removed wholesale (CR-V2-013).
    for gone in (
        "_run_gate_e_round",
        "_gate_e_scope_directive",
        "_gate_e_continue_prompt",
        "_gate_e_question_budget",
        "_gate_e_question_count",
        "_maybe_autonomous_gate_e_continue",
        "_gate_e_budget_reached",
        "gate_e_audit_markdown",
        "_write_gate_e_audit",
        "_block_failed",
        "_coordinator_review_gap",
    ):
        assert not hasattr(orchestrator, gone), f"{gone} should be removed (Gate-E machinery)"
    # The Auditor upfront review replaces it.
    assert hasattr(orchestrator, "_run_auditor_upfront_review")
    assert hasattr(orchestrator, "_auditor_upfront_directive")


async def test_no_gate_e_dispatch_to_director_or_coordinator_tokens(db_session, monkeypatch):
    # Belt-and-suspenders against the LANDMINE: the live Návrh→upfront-review path writes only valid v2
    # participant/stage tokens (auditor/system/manazer; navrh) — never director/coordinator/gate_e.
    version, _ = _make_version(db_session, project_dial="plna")
    _seed_navrh(db_session, version.id)
    _stub_turns(monkeypatch, design_block=_design_done(), audit_block=_audit_hole(["x"]))
    _stub_plan_passes(monkeypatch)
    await orchestrator.run_dispatch(db_session, version.id)
    for m in _msgs(db_session, version.id):
        assert m.author in ("ai_agent", "auditor", "manazer", "system"), m.author
        assert m.recipient in ("ai_agent", "auditor", "manazer", "system"), m.recipient
        assert m.stage in ("priprava", "navrh", "programovanie", "verifikacia", "done"), m.stage
