"""CR-V2-011 — Návrh phase (ONE design doc + the task plan folds in).

Exercised against the real v2 branch DB (4-phase CHECKs). The Návrh round:

* **Design-doc turn** — ``run_dispatch`` injects the Návrh brief (read the approved Špecifikácia, write
  ONE coherent design ``.md``), persists + verifies the design-doc artifact, and surfaces the AI Agent's
  clarification questions at the post-Návrh stop (the SEAM the Auditor's upfront review hooks into in
  CR-V2-013).
* **Task plan folds in** — the EPIC→FEAT→TASK plan is the design doc's LAST part, generated via the folded
  incremental skeleton/per-feat passes (NO standalone ``task_plan`` stage; a large plan still generates
  pass-by-pass, no parse exhaustion). Materialized into Epic/Feat/Task rows + a reviewable spec/task-plan.md.
* **Dial-governed stop** — the post-Návrh schvaľovací bod fires per the Miera autonómie dial (``plna``
  auto-continues to Programovanie; ``po_kazdej_faze`` stops).

The design-doc turn is stubbed via ``invoke_agent_with_parse_retry`` (no live ``claude``); the folded
task-plan passes are stubbed via a controllable fake ``invoke_claude`` (the real fence/structured path).
"""

import json
import uuid
from pathlib import Path

from sqlalchemy import select

from backend.db.models.foundation import User
from backend.db.models.pipeline import PipelineMessage, PipelineState
from backend.db.models.projects import Project
from backend.db.models.tasks import Epic, Feat, Task
from backend.db.models.versions import Version
from backend.services import orchestrator
from backend.services.pipeline_status import PipelineStatusBlock

# (pytest ``asyncio_mode = auto`` — async tests run without an explicit mark.)


def _make_version(db_session, *, source_path=None, project_dial=None):
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
        source_path=source_path,
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


# ── Stub the design-doc turn (invoke_agent_with_parse_retry) ──────────────────


def _stub_design_turn(monkeypatch, block):
    """Make the design-doc turn (``invoke_agent_with_parse_retry``) return *block* and capture its prompt.

    CR-V2-013 added a SECOND ``invoke_agent_with_parse_retry`` call inside the Návrh round — the Auditor's
    upfront review (role=auditor). These navrh-focused tests assert only the AI-Agent DESIGN turn, so the
    fake captures ONLY the ``ai_agent`` turn and returns a clean PASS ``verdict`` for the ``auditor`` turn
    (a no-hole upfront review → the dial governs the post-Návrh stop exactly as before this CR)."""
    captured = {}

    async def _fake(db, *, version_id, role, stage, prompt, **_kw):
        if role == orchestrator.AUDITOR_ROLE:
            # No-hole upfront review (PASS) — does not perturb the dial-governed post-Návrh stop.
            return PipelineStatusBlock(
                stage="navrh", kind="verdict", summary="bez medzery", awaiting="manazer", verdict=True
            )
        captured["prompt"] = prompt
        captured["stage"] = stage
        captured["role"] = role
        return block

    monkeypatch.setattr(orchestrator, "invoke_agent_with_parse_retry", _fake)
    return captured


def _design_done():
    """A design-doc turn that finished the doc (kind=done) WITHOUT an inline plan → the engine folds the
    task plan in via the incremental passes."""
    return PipelineStatusBlock(stage="navrh", kind="done", summary="návrh hotový", awaiting="manazer")


# ── Stub the folded task-plan passes (invoke_claude) ──────────────────────────

_DEFAULT_CROSS = "## Invarianty\n- spoločná transakčná hranica\n- immutable audit"


def _task_plan_fence(obj: dict) -> str:
    return (
        "Tu je kostra/úlohy:\n"
        f"<<<TASK_PLAN_JSON>>>\n{json.dumps(obj, ensure_ascii=False)}\n<<<END_TASK_PLAN_JSON>>>\nHotovo."
    )


def _skeleton_dict(plan_spec, cross=_DEFAULT_CROSS) -> dict:
    epics = []
    for e_title, feats in plan_spec:
        fs = []
        for f_title, tasks in feats:
            f: dict = {"title": f_title}
            ests = [t[2] for t in tasks if len(t) > 2 and t[2] is not None]
            if ests:
                f["estimated_minutes"] = sum(ests)
            fs.append(f)
        epics.append({"title": e_title, "feats": fs})
    return {"epics": epics, "cross_cutting_rules": cross}


def _feat_tasks_dict(tasks) -> dict:
    out = []
    for t in tasks:
        d: dict = {"title": t[0], "task_type": t[1]}
        if len(t) > 2 and t[2] is not None:
            d["estimated_minutes"] = t[2]
        out.append(d)
    return {"tasks": out}


def _stub_plan_passes(monkeypatch, plan_spec, *, cross=_DEFAULT_CROSS, text=False):
    """Drive the folded task-plan passes via a fake ``invoke_claude``: the skeleton pass (prompt contains
    "KOSTRU") → EPIC+FEAT(no tasks)+cross; a per-feat pass (the feat title appears) → that feat's tasks.

    ``text=True`` returns the real-env shape (prose + ``<<<TASK_PLAN_JSON>>>`` fence as TEXT,
    structured_output=None); ``text=False`` returns the dict as structured_output."""
    feat_by_title = {f_title: tasks for _e, feats in plan_spec for f_title, tasks in feats}

    def _emit(obj: dict):
        return (_task_plan_fence(obj), None, None) if text else ("", None, obj)

    async def _fake_invoke_claude(*, prompt, **_kw):
        if "KOSTRU" in prompt:
            return _emit(_skeleton_dict(plan_spec, cross))
        for f_title, tasks in feat_by_title.items():
            if f_title in prompt:
                return _emit(_feat_tasks_dict(tasks))
        raise AssertionError(f"unexpected plan-pass prompt: {prompt[:80]}")

    # _plan_pass_once calls _split_claude_result(invoke_claude(...)); patch invoke_claude (the source) and
    # _split_claude_result (which unpacks the tuple) to pass the (text, usage, structured) tuple straight.
    monkeypatch.setattr(orchestrator, "invoke_claude", _fake_invoke_claude)
    monkeypatch.setattr(orchestrator, "_split_claude_result", lambda r: r)
    # Skip the orch-session resolve (no real DB session rows needed for the passes).
    monkeypatch.setattr(orchestrator, "_resolve_orch_session", lambda db, slug, role: (uuid.uuid4(), False))
    monkeypatch.setattr(orchestrator, "_resolve_dispatch_overrides", lambda db, vid, role: (None, None))


def _small_plan():
    return [("Foundation", [("Schema", [("users table", "migration", 60), ("audit_log", "migration", 30)])])]


# ── Návrh round: design-doc turn + brief ─────────────────────────────────────


async def test_navrh_brief_instructs_one_design_doc_with_plan_last(db_session, monkeypatch):
    version, _ = _make_version(db_session)
    _seed_navrh(db_session, version.id)
    captured = _stub_design_turn(monkeypatch, _design_done())
    _stub_plan_passes(monkeypatch, _small_plan())
    await orchestrator.run_dispatch(db_session, version.id)
    p = captured["prompt"]
    assert captured["stage"] == "navrh" and captured["role"] == "ai_agent"
    assert "JEDEN koherentný návrhový dokument" in p  # ONE design doc, not a multi-doc tree
    assert "specification.md" in p  # reads the approved Špecifikácia
    assert "design.md" in p  # writes the design doc
    assert "EPIC → FEAT → TASK" in p and "POSLEDNÁ časť" in p  # the task plan is the doc's last part


async def test_navrh_question_settles_blocked_before_plan(db_session, monkeypatch):
    # A design ambiguity → the AI Agent asks BEFORE the plan folds in; the phase does NOT advance.
    version, _ = _make_version(db_session)
    _seed_navrh(db_session, version.id)
    q = PipelineStatusBlock(
        stage="navrh", kind="question", summary="need detail", awaiting="manazer", question="Aký dátový model?"
    )
    _stub_design_turn(monkeypatch, q)
    state = await orchestrator.run_dispatch(db_session, version.id)
    assert state.status == "blocked"
    assert state.block_reason == "agent_question"
    assert state.current_stage == "navrh"
    assert not _epics(db_session, version.id)  # no plan materialized


# ── Task plan folds in (the standalone task_plan stage is gone) ───────────────


async def test_navrh_folds_task_plan_via_incremental_passes(db_session, monkeypatch, tmp_path):
    # The whole point: the design doc + the EPIC→FEAT→TASK plan come from ONE Návrh round; the plan is built
    # incrementally (skeleton + per-feat), then materialized — a single coherent artifact.
    version, _ = _make_version(db_session, source_path=str(tmp_path), project_dial="po_kazdej_faze")
    _seed_navrh(db_session, version.id)
    # the design doc on disk (the agent claims it; the engine verifies it)
    rel = orchestrator._navrh_design_doc_rel(version.version_number)
    doc = Path(tmp_path) / rel
    doc.parent.mkdir(parents=True, exist_ok=True)
    doc.write_text("# Návrh\n\n## Prehľad\n...", encoding="utf-8")
    _stub_design_turn(
        monkeypatch,
        PipelineStatusBlock(stage="navrh", kind="done", summary="ok", awaiting="manazer", deliverables=[rel]),
    )
    _stub_plan_passes(
        monkeypatch,
        [
            ("Foundation", [("Schema", [("GL tables", "migration", 90), ("audit_log", "migration", 30)])]),
            ("Calc", [("Hlavná kniha", [("GL výpočet", "backend", 120)])]),
        ],
        cross="## Invarianty\n- spoločná transakčná hranica\n- immutable audit",
    )
    state = await orchestrator.run_dispatch(db_session, version.id)
    # po_kazdej_faze → the Návrh schvaľovací bod stops for the Manažér (does not advance to programovanie)
    assert state.current_stage == "navrh" and state.status == "awaiting_manazer"
    # EPIC→FEAT→TASK materialized
    epics = _epics(db_session, version.id)
    assert {e.title for e in epics} == {"Foundation", "Calc"}
    feats = db_session.execute(select(Feat)).scalars().all()
    tasks = db_session.execute(select(Task)).scalars().all()
    assert len(feats) == 2 and len(tasks) == 3
    assert all(t.status == "todo" for t in tasks)
    est = {t.title: t.estimated_minutes for t in tasks}
    assert est["GL tables"] == 90 and est["GL výpočet"] == 120
    # the AI-Agent navrh gate_report carries the plan + cross_cutting_rules (the build loop re-reads these)
    gr = [
        m
        for m in _msgs(db_session, version.id)
        if m.author == "ai_agent" and m.stage == "navrh" and m.kind == "gate_report"
    ]
    assert gr and "transakčná" in gr[-1].payload["cross_cutting_rules"]
    assert orchestrator._fetch_cross_cutting_rules(db_session, version.id) == gr[-1].payload["cross_cutting_rules"]
    # the durable design-doc artifact note exists (the Vývoj → Návrh tab reads it)
    notes = [m for m in _msgs(db_session, version.id) if m.payload and m.payload.get("navrh_design_doc")]
    assert notes and notes[-1].payload["path"] == rel
    # NO message was recorded under a (now-invalid) "task_plan" stage — the standalone stage is gone
    assert not [m for m in _msgs(db_session, version.id) if m.stage == "task_plan"]


async def test_navrh_plan_passes_via_text_fence_no_parse_exhaustion(db_session, monkeypatch):
    # Real-env path: the passes return the narrowed JSON as TEXT in a <<<TASK_PLAN_JSON>>> fence
    # (structured_output is dead in the live CLI). A multi-feat plan still assembles pass-by-pass.
    version, _ = _make_version(db_session, project_dial="po_kazdej_faze")
    _seed_navrh(db_session, version.id)
    _stub_design_turn(monkeypatch, _design_done())
    _stub_plan_passes(
        monkeypatch,
        [
            ("Foundation", [("Schema", [("t1", "migration", 60)])]),
            ("Core", [("Engine", [("e1", "backend", 120)]), ("API", [("a1", "backend", 90)])]),
        ],
        text=True,
    )
    state = await orchestrator.run_dispatch(db_session, version.id)
    assert state.current_stage == "navrh" and state.status == "awaiting_manazer"
    assert len(_epics(db_session, version.id)) == 2
    assert len(db_session.execute(select(Task)).scalars().all()) == 3


async def test_navrh_reviewable_task_plan_doc_written(db_session, monkeypatch, tmp_path):
    version, project = _make_version(db_session, source_path=str(tmp_path), project_dial="po_kazdej_faze")
    _seed_navrh(db_session, version.id)
    rel = orchestrator._navrh_design_doc_rel(version.version_number)
    doc = Path(tmp_path) / rel
    doc.parent.mkdir(parents=True, exist_ok=True)
    doc.write_text("# Návrh\n", encoding="utf-8")
    _stub_design_turn(
        monkeypatch,
        PipelineStatusBlock(stage="navrh", kind="done", summary="ok", awaiting="manazer", deliverables=[rel]),
    )
    _stub_plan_passes(monkeypatch, _small_plan(), cross="## Invarianty\n- x")
    await orchestrator.run_dispatch(db_session, version.id)
    plan_doc = tmp_path / "docs" / "specs" / "versions" / f"v{version.version_number}" / "spec" / "task-plan.md"
    assert plan_doc.is_file()
    md = plan_doc.read_text(encoding="utf-8")
    assert "## Epic 1: Foundation" in md and "### Feat 1.1: Schema" in md
    assert "users table" in md and "`[migration]`" in md


# ── Design-doc artifact gate ──────────────────────────────────────────────────


async def test_navrh_missing_design_doc_blocks(db_session, monkeypatch, tmp_path):
    # Checkout exists but the agent never wrote design.md → blocked, the phase does NOT close, no plan passes.
    version, _ = _make_version(db_session, source_path=str(tmp_path))
    _seed_navrh(db_session, version.id)
    rel = orchestrator._navrh_design_doc_rel(version.version_number)
    _stub_design_turn(
        monkeypatch,
        PipelineStatusBlock(stage="navrh", kind="done", summary="ok", awaiting="manazer", deliverables=[rel]),
    )
    # plan-pass stub present but should never be reached (the artifact gate fails first)
    _stub_plan_passes(monkeypatch, _small_plan())
    state = await orchestrator.run_dispatch(db_session, version.id)
    assert state.status == "blocked"
    assert state.block_reason == "agent_error"
    assert state.current_stage == "navrh"
    assert not _epics(db_session, version.id)


async def test_navrh_no_checkout_records_db_only_artifact(db_session, monkeypatch):
    # A library/no-checkout project: the design lives in the gate_report payload (DB audit trail); the
    # artifact note is still recorded and the plan still folds in.
    version, _ = _make_version(db_session, source_path=None, project_dial="po_kazdej_faze")
    _seed_navrh(db_session, version.id)
    _stub_design_turn(monkeypatch, _design_done())
    _stub_plan_passes(monkeypatch, _small_plan())
    state = await orchestrator.run_dispatch(db_session, version.id)
    assert state.current_stage == "navrh" and state.status == "awaiting_manazer"
    notes = [m for m in _msgs(db_session, version.id) if m.payload and m.payload.get("navrh_design_doc")]
    assert notes  # DB-only artifact note recorded
    assert len(_epics(db_session, version.id)) == 1  # plan still materialized


# ── Inline plan (small project, plan in one turn) ─────────────────────────────


async def test_navrh_inline_plan_materialized_without_extra_passes(db_session, monkeypatch):
    # If the design turn already carries a non-empty plan (a small project), it is materialized directly —
    # the incremental passes are NOT invoked (invoke_claude would raise if hit).
    version, _ = _make_version(db_session, project_dial="po_kazdej_faze")
    _seed_navrh(db_session, version.id)
    inline = PipelineStatusBlock(
        stage="navrh",
        kind="gate_report",
        summary="malý návrh + plán",
        awaiting="manazer",
        plan={
            "epics": [
                {
                    "title": "Foundation",
                    "feats": [
                        {
                            "title": "Schema",
                            "description": "DB",
                            "tasks": [{"title": "users", "task_type": "migration"}],
                        }
                    ],
                }
            ]
        },
        cross_cutting_rules="## Invarianty\n- jedna firma",
    )
    _stub_design_turn(monkeypatch, inline)

    # invoke_claude must NOT be called on the inline path — make it explode if it is.
    async def _boom(*a, **k):
        raise AssertionError("incremental plan passes must not run on the inline-plan path")

    monkeypatch.setattr(orchestrator, "invoke_claude", _boom)
    state = await orchestrator.run_dispatch(db_session, version.id)
    assert state.current_stage == "navrh" and state.status == "awaiting_manazer"
    assert len(_epics(db_session, version.id)) == 1
    gr = [
        m
        for m in _msgs(db_session, version.id)
        if m.author == "ai_agent" and m.stage == "navrh" and m.kind == "gate_report"
    ]
    assert gr and "jedna firma" in gr[-1].payload["cross_cutting_rules"]


# ── Dial governs the post-Návrh stop ──────────────────────────────────────────


async def test_navrh_boundary_always_stops_new_version_even_at_plna(db_session, monkeypatch):
    # A (Director 2026-06-30): a new_version build STOPS at the Návrh schvaľovací bod for the Manažér's
    # confirmation ('schvalit' → Programovanie), INDEPENDENT of the dial — mandatory phase gate even at plná.
    # The plan is still materialized; the build just doesn't auto-cross into Programovanie unattended.
    version, _ = _make_version(db_session, project_dial="plna")
    _seed_navrh(db_session, version.id)
    _stub_design_turn(monkeypatch, _design_done())
    _stub_plan_passes(monkeypatch, _small_plan())
    state = await orchestrator.run_dispatch(db_session, version.id)
    assert state.current_stage == "navrh"
    assert state.status == "awaiting_manazer"  # mandatory gate — no auto-advance even at plná
    assert len(_epics(db_session, version.id)) == 1  # plan materialized before the gate


async def test_navrh_per_build_dial_beats_project(db_session, monkeypatch):
    # AUTON-6 override order in the Návrh settle: per-build po_kazdej_faze beats per-project plna → stop.
    version, _ = _make_version(db_session, project_dial="plna")
    _seed_navrh(db_session, version.id, build_dial="po_kazdej_faze")
    _stub_design_turn(monkeypatch, _design_done())
    _stub_plan_passes(monkeypatch, _small_plan())
    state = await orchestrator.run_dispatch(db_session, version.id)
    assert state.current_stage == "navrh" and state.status == "awaiting_manazer"


# ── Fail-closed: a plan-pass exhaustion blocks, writes nothing ────────────────


async def test_navrh_skeleton_parse_failure_blocks_writes_nothing(db_session, monkeypatch):
    version, _ = _make_version(db_session, project_dial="po_kazdej_faze")
    _seed_navrh(db_session, version.id)
    _stub_design_turn(monkeypatch, _design_done())

    # the skeleton pass returns garbage (no fence) every time → parse-retries exhaust → blocked
    async def _bad_invoke(*, prompt, **_kw):
        return ("no fence here, just prose", None, None)

    monkeypatch.setattr(orchestrator, "invoke_claude", _bad_invoke)
    monkeypatch.setattr(orchestrator, "_split_claude_result", lambda r: r)
    monkeypatch.setattr(orchestrator, "_resolve_orch_session", lambda db, slug, role: (uuid.uuid4(), False))
    monkeypatch.setattr(orchestrator, "_resolve_dispatch_overrides", lambda db, vid, role: (None, None))
    state = await orchestrator.run_dispatch(db_session, version.id)
    assert state.status == "blocked"
    assert state.block_reason == "parse_exhaustion"
    assert state.current_stage == "navrh"
    assert not _epics(db_session, version.id)  # nothing written on a failed plan


def test_navrh_design_doc_rel_path_convention():
    assert orchestrator._navrh_design_doc_rel("0.1.0") == "docs/specs/versions/v0.1.0/design.md"


def test_task_plan_skeleton_directive_mandates_coarse_granularity():
    """CR-V2-036: the skeleton pass decides the FEAT count, so the coarse-granularity rule + the hard cap
    must be stated HERE (not only in the per-feat task pass — too late). Without it the agent over-
    decomposed (46 feats > MAX_PLAN_FEATS) and the engine rejected the whole plan."""
    d = orchestrator._task_plan_skeleton_directive()
    assert "HRUBOZRNNÁ" in d  # coarse granularity mandated in the skeleton pass
    assert "modul ≈ úloha" in d
    assert str(orchestrator.MAX_PLAN_FEATS) in d  # the cap is named so the agent stays well under it


# ── CR-V2-037: per-feat pass resilience (a fast crash is retried, a timeout is not) ───────────────


def _stub_plan_passes_faulty(monkeypatch, plan_spec, *, fault_feat, fault, fail_times=None, cross=_DEFAULT_CROSS):
    """Like :func:`_stub_plan_passes`, but the per-feat pass for ``fault_feat`` RAISES ``fault`` (a
    ``ClaudeAgentError`` / ``ClaudeAgentTimeout``). ``fail_times=None`` → always raise (persistent); an int
    → raise that many times then succeed (transient — exercises the bounded re-invoke). Returns a ``calls``
    dict counting the skeleton + fault-feat invocations so a test can assert retry vs no-retry."""
    feat_by_title = {f_title: tasks for _e, feats in plan_spec for f_title, tasks in feats}
    calls = {"skeleton": 0, fault_feat: 0}

    async def _fake_invoke_claude(*, prompt, **_kw):
        if "KOSTRU" in prompt:
            calls["skeleton"] += 1
            return ("", None, _skeleton_dict(plan_spec, cross))
        for f_title, tasks in feat_by_title.items():
            if f_title in prompt:
                if f_title == fault_feat:
                    calls[fault_feat] += 1
                    if fail_times is None or calls[fault_feat] <= fail_times:
                        raise fault
                return ("", None, _feat_tasks_dict(tasks))
        raise AssertionError(f"unexpected plan-pass prompt: {prompt[:80]}")

    monkeypatch.setattr(orchestrator, "invoke_claude", _fake_invoke_claude)
    monkeypatch.setattr(orchestrator, "_split_claude_result", lambda r: r)
    monkeypatch.setattr(orchestrator, "_resolve_orch_session", lambda db, slug, role: (uuid.uuid4(), False))
    monkeypatch.setattr(orchestrator, "_resolve_dispatch_overrides", lambda db, vid, role: (None, None))
    return calls


async def test_navrh_per_feat_crash_is_retried_then_succeeds(db_session, monkeypatch):
    # A FAST crash (ClaudeAgentError, not a timeout) in a per-feat pass is re-invoked (bounded) rather than
    # discarding the whole accumulated plan — crash once, then succeed → the FULL plan still materializes.
    version, _ = _make_version(db_session, project_dial="po_kazdej_faze")
    _seed_navrh(db_session, version.id)
    _stub_design_turn(monkeypatch, _design_done())
    plan = [
        ("Foundation", [("Schema", [("t1", "migration", 60)])]),
        ("Core", [("Engine", [("e1", "backend", 120)])]),
    ]
    calls = _stub_plan_passes_faulty(
        monkeypatch,
        plan,
        fault_feat="Engine",
        fault=orchestrator.ClaudeAgentError("claude exited with code 1: transient boom"),
        fail_times=1,
    )
    state = await orchestrator.run_dispatch(db_session, version.id)
    assert state.current_stage == "navrh" and state.status == "awaiting_manazer"
    assert calls["Engine"] == 2  # crashed once → re-invoked once → succeeded
    assert {e.title for e in _epics(db_session, version.id)} == {"Foundation", "Core"}
    assert len(db_session.execute(select(Task)).scalars().all()) == 2  # nothing lost


async def test_navrh_per_feat_persistent_crash_blocks_agent_error_not_parse_exhaustion(db_session, monkeypatch):
    # A crash that keeps failing past the bounded re-invokes is an envelope-loss; with NO dispatch baseline
    # it HALTs blocked with block_reason=agent_error (never the parse_exhaustion mislabel) + writes nothing.
    version, _ = _make_version(db_session, project_dial="po_kazdej_faze")
    _seed_navrh(db_session, version.id)
    _stub_design_turn(monkeypatch, _design_done())
    calls = _stub_plan_passes_faulty(
        monkeypatch,
        [("Core", [("Engine", [("e1", "backend", 120)])])],
        fault_feat="Engine",
        fault=orchestrator.ClaudeAgentError("claude exited with code 1: persistent boom"),
    )
    state = await orchestrator.run_dispatch(db_session, version.id)
    assert state.status == "blocked" and state.block_reason == "agent_error"
    assert calls["Engine"] == orchestrator._PARSE_RETRIES + 1  # initial attempt + the bounded re-invokes
    assert not _epics(db_session, version.id)  # all-or-nothing: nothing written


async def test_navrh_per_feat_persistent_crash_with_baseline_settles_review_continue(db_session, monkeypatch):
    # With a dispatch baseline armed, a persistent crash settles awaiting_manazer ("review & continue") and
    # the lost-work message tells the TRUTH — "Agent opakovane zlyhal", not the misleading "Vypršal čas".
    version, _ = _make_version(db_session, project_dial="po_kazdej_faze")
    _seed_navrh(db_session, version.id)
    state0 = orchestrator._get_state(db_session, version.id)
    state0.dispatch_baseline_sha = "deadbeefdeadbeef"
    db_session.flush()
    _stub_design_turn(monkeypatch, _design_done())
    _stub_plan_passes_faulty(
        monkeypatch,
        [("Core", [("Engine", [("e1", "backend", 120)])])],
        fault_feat="Engine",
        fault=orchestrator.ClaudeAgentError("claude exited with code 1: persistent boom"),
    )
    state = await orchestrator.run_dispatch(db_session, version.id)
    assert state.status == "awaiting_manazer"
    assert "Agent opakovane zlyhal" in state.next_action
    assert "Vypršal čas" not in state.next_action
    assert not _epics(db_session, version.id)


async def test_navrh_per_feat_timeout_is_not_retried(db_session, monkeypatch):
    # A genuine TIMEOUT (ClaudeAgentTimeout) is NOT re-invoked — re-running just risks another long wait. It
    # settles the R1 lost-work path at once: the per-feat pass is called exactly ONCE; message "Vypršal čas".
    version, _ = _make_version(db_session, project_dial="po_kazdej_faze")
    _seed_navrh(db_session, version.id)
    state0 = orchestrator._get_state(db_session, version.id)
    state0.dispatch_baseline_sha = "deadbeefdeadbeef"
    db_session.flush()
    _stub_design_turn(monkeypatch, _design_done())
    calls = _stub_plan_passes_faulty(
        monkeypatch,
        [("Core", [("Engine", [("e1", "backend", 120)])])],
        fault_feat="Engine",
        fault=orchestrator.ClaudeAgentTimeout("claude invocation timed out after 1200s"),
    )
    state = await orchestrator.run_dispatch(db_session, version.id)
    assert state.status == "awaiting_manazer"
    assert calls["Engine"] == 1  # a timeout is NOT retried
    assert "Vypršal čas agenta" in state.next_action
    assert not _epics(db_session, version.id)
