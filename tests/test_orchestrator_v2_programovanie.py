"""CR-V2-012 — Programovanie phase (the AI Agent's SELF-CHECKING coding loop, no per-task Auditor).

Exercised against the real v2 branch DB (4-phase CHECKs). The Programovanie round (``_run_build_round``,
re-homed to the ``programovanie`` phase) rebuilds the v1 per-task-audited build loop:

* **Self-checking loop** — ONE agent (``ai_agent``) executes the Návrh task plan task-by-task; per task it
  implements + runs its OWN tests/verification and commits. The engine's per-task gate is the deterministic
  mechanical commit verify (``verify_mechanical``) — there is **NO per-task Auditor turn** (the independent
  Auditor verifies once at Verifikácia, CR-V2-014).
* **No Coordinator hub-and-spoke** — a mid-loop AI-Agent question, a self-check exhaustion, and an unreadable
  baseline all settle for the Manažér DIRECTLY (the Coordinator relay is retired in v2, design §2.2).
* **Dial-governed stop** — at loop completion the Miera autonómie dial fires the Programovanie schvaľovací
  bod (``plna`` auto-continues to Verifikácia; ``po_kazdej_faze`` stops ``awaiting_manazer``).
* **Safeguards preserved** — lost-work audit (committed-but-lost work surfaced, never dropped), mechanical
  commit verify, resume-safety (orphaned in_progress reclaimed).

``invoke_agent_with_parse_retry`` is monkeypatched (no live ``claude`` CLI); ``verify_mechanical`` is
monkeypatched per test to control the per-task mechanical gate without touching real git.
"""

import json
import uuid

from sqlalchemy import select

from backend.db.models.foundation import User
from backend.db.models.pipeline import PipelineMessage, PipelineState
from backend.db.models.projects import Project
from backend.db.models.tasks import Epic, Feat, Task
from backend.db.models.versions import Version
from backend.services import orchestrator
from backend.services.pipeline_status import ParseFailure, PipelineStatusBlock

# (pytest ``asyncio_mode = auto`` — async tests run without an explicit mark.)


# ── fixtures ──────────────────────────────────────────────────────────────────


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


def _seed_programovanie(db_session, version_id, *, build_dial=None, flow_type="new_version", iteration=0):
    state = PipelineState(
        version_id=version_id,
        flow_type=flow_type,
        current_stage="programovanie",
        current_actor="ai_agent",
        status="agent_working",
        next_action="working",
        miera_autonomie=build_dial,
        iteration=iteration,
    )
    db_session.add(state)
    db_session.flush()
    return state


def _seed_tasks(db_session, version, project, titles):
    """Seed ONE epic + ONE feat + a Task per title (all ``todo``), returning the Task rows in plan order."""
    epic = Epic(project_id=project.id, version_id=version.id, number=1, title="Foundation", status="planned")
    db_session.add(epic)
    db_session.flush()
    feat = Feat(epic_id=epic.id, number=1, title="Schema", status="todo")
    db_session.add(feat)
    db_session.flush()
    tasks = []
    for i, title in enumerate(titles, start=1):
        t = Task(feat_id=feat.id, number=i, title=title, task_type="backend", status="todo")
        db_session.add(t)
        tasks.append(t)
    db_session.flush()
    return epic, feat, tasks


def _msgs(db_session, version_id):
    return (
        db_session.execute(
            select(PipelineMessage).where(PipelineMessage.version_id == version_id).order_by(PipelineMessage.seq.asc())
        )
        .scalars()
        .all()
    )


def _tasks(db_session, version_id):
    return (
        db_session.execute(
            select(Task)
            .join(Feat, Feat.id == Task.feat_id)
            .join(Epic, Epic.id == Feat.epic_id)
            .where(Epic.version_id == version_id)
        )
        .scalars()
        .all()
    )


def _done_block(summary="hotovo"):
    return PipelineStatusBlock(
        stage="programovanie", kind="gate_report", summary=summary, awaiting="manazer", commits=["a" * 40]
    )


def _stub_turns(monkeypatch, blocks):
    """Drive ``invoke_agent_with_parse_retry`` from a scripted list (one per dispatched turn), capturing the
    role/stage/prompt of every call. A short list re-uses its last entry (so a single ``_done_block`` answers
    every task in a multi-task build)."""
    calls = []
    seq = list(blocks)

    async def _fake(db, *, version_id, role, stage, prompt, **_kw):
        calls.append({"role": role, "stage": stage, "prompt": prompt})
        return seq[len(calls) - 1] if len(calls) <= len(seq) else seq[-1]

    monkeypatch.setattr(orchestrator, "invoke_agent_with_parse_retry", _fake)
    return calls


def _stub_mech(monkeypatch, reasons):
    """Drive ``verify_mechanical`` from a scripted list of per-call return values (``None`` = pass, str = fail);
    a short list re-uses its last entry. Decouples the per-task gate from real git."""
    seq = list(reasons)
    state = {"n": 0}

    def _fake(slug, block, baseline_sha=None):
        state["n"] += 1
        return seq[state["n"] - 1] if state["n"] <= len(seq) else seq[-1]

    monkeypatch.setattr(orchestrator, "verify_mechanical", _fake)


def _no_baseline_git(monkeypatch):
    """Make repo-HEAD reads deterministic (a fixed sha) so the loop captures a baseline without real git."""
    monkeypatch.setattr(orchestrator, "_repo_head", lambda root: "b" * 40)


# ── the self-checking multi-task loop ──────────────────────────────────────────


async def test_multi_task_build_self_checks_each_no_per_task_audit(db_session, monkeypatch):
    # The CR gate: a 3-task build runs to completion with per-task self-checks (mechanical verify), NO
    # per-task Auditor verdict messages, NO Coordinator turns.
    version, project = _make_version(db_session, project_dial="po_kazdej_faze")
    _seed_programovanie(db_session, version.id)
    _seed_tasks(db_session, version, project, ["T1", "T2", "T3"])
    _no_baseline_git(monkeypatch)
    calls = _stub_turns(monkeypatch, [_done_block()])  # every task's turn passes its self-check
    _stub_mech(monkeypatch, [None])  # mechanical verify passes for every task
    state = await orchestrator.run_dispatch(db_session, version.id)

    # po_kazdej_faze → the Programovanie schvaľovací bod stops for the Manažér (does not advance)
    assert state.current_stage == "programovanie" and state.status == "awaiting_manazer"
    # all 3 tasks done, each via exactly one ai_agent turn
    assert all(t.status == "done" for t in _tasks(db_session, version.id))
    assert len(calls) == 3
    assert all(c["role"] == "ai_agent" and c["stage"] == "programovanie" for c in calls)
    # the brief is the self-check brief
    assert "PRIEBEŽNE si sám over výsledok" in calls[0]["prompt"]
    # NO per-task audit verdict: no auditor/coordinator authored any message, no task_pass payload exists
    msgs = _msgs(db_session, version.id)
    assert not [m for m in msgs if m.author in ("auditor", "coordinator")]
    assert not [m for m in msgs if m.payload and "task_pass" in m.payload]
    # the per-task summaries carry NO audit_verdict (v2 drop), only the AI Agent's own work + attempts
    summaries = [m for m in msgs if m.payload and m.payload.get("is_task_summary")]
    assert len(summaries) == 3
    assert all("audit_verdict" not in s.payload["task_summary"] for s in summaries)
    assert all(s.payload["task_summary"]["attempts"] == 1 for s in summaries)
    # every recorded message lives under the valid v2 ``programovanie`` stage (4-phase CHECK)
    assert all(m.stage == "programovanie" for m in msgs)


async def test_verify_mechanical_fires_per_task(db_session, monkeypatch):
    # verify_mechanical IS the per-task gate (it still fires) — a mechanical fail re-attempts; a later pass
    # completes the task. No Auditor turn is ever invoked.
    version, project = _make_version(db_session, project_dial="po_kazdej_faze")
    _seed_programovanie(db_session, version.id)
    _seed_tasks(db_session, version, project, ["T1"])
    _no_baseline_git(monkeypatch)
    calls = _stub_turns(monkeypatch, [_done_block()])
    # first mechanical verify FAILS (deliverable missing), the re-attempt PASSES
    _stub_mech(monkeypatch, ["deliverable 'x' missing on disk", None])
    state = await orchestrator.run_dispatch(db_session, version.id)
    assert state.current_stage == "programovanie" and state.status == "awaiting_manazer"
    assert [t.status for t in _tasks(db_session, version.id)] == ["done"]
    assert len(calls) == 2  # one failed self-check + one passing re-attempt (same ai_agent role)
    # a self-check return note carries the mechanical reason (threaded into the next brief)
    returns = [m for m in _msgs(db_session, version.id) if m.kind == "return"]
    assert returns and "missing on disk" in returns[0].content
    # the re-attempt brief threads the prior failure
    assert "Predošlé NEÚSPEŠNÉ pokusy" in calls[1]["prompt"]


async def test_self_check_exhaustion_stops_for_manazer_no_coordinator_relay(db_session, monkeypatch):
    # A task that never passes its mechanical self-check exhausts the bound → settles awaiting_manazer DIRECTLY
    # (no Coordinator relay — retired in v2). The task is marked failed; the build does NOT advance.
    version, project = _make_version(db_session, project_dial="plna")
    _seed_programovanie(db_session, version.id)
    _seed_tasks(db_session, version, project, ["T1"])
    _no_baseline_git(monkeypatch)
    calls = _stub_turns(monkeypatch, [_done_block()])
    _stub_mech(monkeypatch, ["commit not found"])  # always fails
    state = await orchestrator.run_dispatch(db_session, version.id)
    assert state.status == "awaiting_manazer"  # NOT auto-continued despite plná (a failed task halts)
    assert state.current_stage == "programovanie"
    assert [t.status for t in _tasks(db_session, version.id)] == ["failed"]
    assert len(calls) == orchestrator._SELF_CHECK_RETRIES  # exactly 5 self-check attempts
    # no Coordinator/Auditor relay was recorded
    assert not [m for m in _msgs(db_session, version.id) if m.author in ("auditor", "coordinator")]
    assert "self-check" in state.next_action.lower() and "Manažér" in state.next_action


async def test_ai_agent_question_blocks_directly(db_session, monkeypatch):
    # The AI Agent asks the Manažér mid-task → blocked DIRECTLY (no Coordinator relay); the phase does not move.
    version, project = _make_version(db_session)
    _seed_programovanie(db_session, version.id)
    _seed_tasks(db_session, version, project, ["T1"])
    _no_baseline_git(monkeypatch)
    q = PipelineStatusBlock(
        stage="programovanie", kind="question", summary="need detail", awaiting="manazer", question="Aký formát?"
    )
    _stub_turns(monkeypatch, [q])
    state = await orchestrator.run_dispatch(db_session, version.id)
    assert state.status == "blocked" and state.block_reason == "agent_question"
    assert state.current_stage == "programovanie"
    assert "AI Agent (úloha #1) sa pýta" in state.next_action
    assert not [m for m in _msgs(db_session, version.id) if m.author in ("auditor", "coordinator")]


# ── safeguards preserved ────────────────────────────────────────────────────────


async def test_lost_work_surfaced_not_dropped(db_session, monkeypatch):
    # R-BLAST safeguard #3: an envelope-loss whose commit audit found work surfaces "review & continue"
    # (awaiting_manazer with the audit next_action) — committed-but-lost work is NEVER silently dropped.
    version, project = _make_version(db_session)
    _seed_programovanie(db_session, version.id)
    _seed_tasks(db_session, version, project, ["T1"])
    _no_baseline_git(monkeypatch)
    lost = ParseFailure(
        "claude invocation failed: timeout",
        lost_work={"next_action": "Práca možno commitnutá — skontroluj git log a pokračuj."},
    )
    _stub_turns(monkeypatch, [lost])
    state = await orchestrator.run_dispatch(db_session, version.id)
    assert state.status == "awaiting_manazer"
    assert state.next_action == "Práca možno commitnutá — skontroluj git log a pokračuj."
    # the task is NOT marked failed (it may have landed) — it stays in_progress for the resume reclaim
    assert [t.status for t in _tasks(db_session, version.id)] == ["in_progress"]


async def test_resume_safety_reclaims_orphaned_in_progress(db_session, monkeypatch):
    # A task left in_progress by a dispatch that died is reclaimed to todo on entry and re-run.
    version, project = _make_version(db_session, project_dial="po_kazdej_faze")
    _seed_programovanie(db_session, version.id)
    _epic, _feat, (task,) = _seed_tasks(db_session, version, project, ["T1"])
    task.status = "in_progress"  # orphaned mid-build
    task.baseline_sha = "c" * 40  # persisted baseline — re-run against the SAME anchor
    db_session.flush()
    _no_baseline_git(monkeypatch)
    calls = _stub_turns(monkeypatch, [_done_block()])
    _stub_mech(monkeypatch, [None])
    state = await orchestrator.run_dispatch(db_session, version.id)
    assert state.current_stage == "programovanie" and state.status == "awaiting_manazer"
    db_session.refresh(task)
    assert task.status == "done"
    assert task.baseline_sha == "c" * 40  # kept the persisted baseline (never re-anchored to a moved HEAD)
    assert len(calls) == 1


async def test_unreadable_baseline_fails_closed_to_manazer(db_session, monkeypatch):
    # Fail-closed: repo HEAD unreadable → never dispatch on an unknowable base. The task STAYS todo (a
    # precondition failure) and the build surfaces to the Manažér directly (no Coordinator relay).
    version, project = _make_version(db_session)
    _seed_programovanie(db_session, version.id)
    _seed_tasks(db_session, version, project, ["T1"])
    monkeypatch.setattr(orchestrator, "_repo_head", lambda root: None)  # HEAD unreadable
    calls = _stub_turns(monkeypatch, [_done_block()])
    state = await orchestrator.run_dispatch(db_session, version.id)
    assert state.status == "awaiting_manazer" and state.current_stage == "programovanie"
    assert [t.status for t in _tasks(db_session, version.id)] == ["todo"]  # stays todo, auto-retries on resume
    assert "baseline nečitateľný" in state.next_action
    assert len(calls) == 0  # never dispatched on an unknowable base


# ── dial governs the post-Programovanie stop ────────────────────────────────────


async def test_plna_programovanie_boundary_always_stops_new_version(db_session, monkeypatch):
    # A (Director 2026-06-30): a new_version build STOPS at the Programovanie boundary for the Manažér's
    # confirmation ('schvalit' → Verifikácia), INDEPENDENT of the dial — mandatory phase gate even at plná.
    # The tasks still complete; the build just doesn't auto-cross into Verifikácia unattended.
    version, project = _make_version(db_session, project_dial="plna")
    _seed_programovanie(db_session, version.id)
    _seed_tasks(db_session, version, project, ["T1", "T2"])
    _no_baseline_git(monkeypatch)
    _stub_turns(monkeypatch, [_done_block()])
    _stub_mech(monkeypatch, [None])
    state = await orchestrator.run_dispatch(db_session, version.id)
    assert state.current_stage == "programovanie"
    assert state.status == "awaiting_manazer"  # mandatory gate — no auto-advance even at plná
    assert all(t.status == "done" for t in _tasks(db_session, version.id))


async def test_plna_programovanie_fast_fix_auto_continues_to_verifikacia(db_session, monkeypatch):
    # fast_fix keeps its zero-approval lane: a clean build auto-continues into Verifikácia (no Manažér stop).
    version, project = _make_version(db_session, project_dial="plna")
    _seed_programovanie(db_session, version.id, flow_type="fast_fix")
    _seed_tasks(db_session, version, project, ["T1", "T2"])
    _no_baseline_git(monkeypatch)
    _stub_turns(monkeypatch, [_done_block()])
    _stub_mech(monkeypatch, [None])
    state = await orchestrator.run_dispatch(db_session, version.id)
    assert state.current_stage == "verifikacia"
    assert state.status == "agent_working"  # auto-chain continues the build
    assert all(t.status == "done" for t in _tasks(db_session, version.id))


async def test_per_build_dial_beats_project(db_session, monkeypatch):
    # AUTON-6 override order: per-build po_kazdej_faze beats per-project plna → the Programovanie stop fires.
    version, project = _make_version(db_session, project_dial="plna")
    _seed_programovanie(db_session, version.id, build_dial="po_kazdej_faze")
    _seed_tasks(db_session, version, project, ["T1"])
    _no_baseline_git(monkeypatch)
    _stub_turns(monkeypatch, [_done_block()])
    _stub_mech(monkeypatch, [None])
    state = await orchestrator.run_dispatch(db_session, version.id)
    assert state.current_stage == "programovanie" and state.status == "awaiting_manazer"


async def test_fast_fix_auto_continues_to_verifikacia(db_session, monkeypatch):
    # Fast-fix runs at full-auto (dial=plna) regardless of any override → a clean build auto-continues to
    # Verifikácia (the dropped v1 build→release auto-advance is subsumed by the dial + the 4-phase _next_stage).
    version, project = _make_version(db_session, project_dial="po_kazdej_faze")
    _seed_programovanie(db_session, version.id, flow_type="fast_fix")
    _seed_tasks(db_session, version, project, ["T1"])
    _no_baseline_git(monkeypatch)
    _stub_turns(monkeypatch, [_done_block()])
    _stub_mech(monkeypatch, [None])
    state = await orchestrator.run_dispatch(db_session, version.id)
    assert state.current_stage == "verifikacia" and state.status == "agent_working"


# ── two-way comms: a directive seeds attempt 1 ──────────────────────────────────


async def test_directive_seeds_first_attempt(db_session, monkeypatch):
    # An ``uprav`` / ``answer`` re-dispatch threads the Manažér's framed message as the resumed task's brief
    # (two-way comms — the Coordinator relay is retired).
    version, project = _make_version(db_session, project_dial="po_kazdej_faze")
    _seed_programovanie(db_session, version.id)
    _seed_tasks(db_session, version, project, ["T1"])
    _no_baseline_git(monkeypatch)
    calls = _stub_turns(monkeypatch, [_done_block()])
    _stub_mech(monkeypatch, [None])
    await orchestrator.run_dispatch(db_session, version.id, directive="Manažér: použi iný formát dátumu.")
    assert calls[0]["prompt"] == "Manažér: použi iný formát dátumu."  # the directive IS attempt 1's prompt


# ── empty plan: dial fires immediately (the Verifikácia FAIL re-loop seam) ───────


async def test_empty_plan_settles_via_dial(db_session, monkeypatch):
    # A re-entered Programovanie with no todo task (e.g. a Verifikácia FAIL re-loop before CR-V2-014 wires the
    # fix scope) does not hang: it applies the dial immediately. po_kazdej_faze → stop awaiting_manazer.
    version, _ = _make_version(db_session, project_dial="po_kazdej_faze")
    _seed_programovanie(db_session, version.id, iteration=1)

    # nex-studio-visual: the plan is built at Programovanie entry — a re-loop already HAS a materialized plan
    # (done tasks), so plan-gen is skipped and the empty todo-loop reaches the dial-settle. No agent turn runs.
    monkeypatch.setattr(orchestrator, "navrh_plan_materialized", lambda db, vid: True)

    # no tasks seeded; invoke must never be called
    async def _boom(*a, **k):
        raise AssertionError("no agent turn expected when there is no todo task")

    monkeypatch.setattr(orchestrator, "invoke_agent_with_parse_retry", _boom)
    state = await orchestrator.run_dispatch(db_session, version.id)
    assert state.current_stage == "programovanie" and state.status == "awaiting_manazer"


# ── task plan built at Programovanie ENTRY (re-homed from Návrh; nex-studio-visual, Director 2026-07-13) ──
# The EPIC→FEAT→TASK plan is generated at the START of Programovanie (``_run_build_round`` entry), from the
# final design + the Manažér's Vizuál changes — NOT in Návrh (which now produces the design DOCUMENT only).
# These tests (moved from test_orchestrator_v2_navrh.py — coverage preserved, not dropped) pin plan
# generation + the fail-closed plan-pass failure behaviour at the build-round entry seam. There is no
# design-doc turn (the build starts in Programovanie); the incremental skeleton/per-feat passes are stubbed
# via a controllable fake ``invoke_claude`` (the real fence/structured path). The tests call
# ``_run_build_round`` directly (like the fast-fix short-path integration test) so the plan-gen seam is
# exercised without the ``_begin_dispatch`` baseline capture.

_DEFAULT_CROSS = "## Invarianty\n- spoločná transakčná hranica\n- immutable audit"


def _epics(db_session, version_id):
    return db_session.execute(select(Epic).where(Epic.version_id == version_id)).scalars().all()


def _task_plan_fence(obj):
    return (
        "Tu je kostra/úlohy:\n"
        f"<<<TASK_PLAN_JSON>>>\n{json.dumps(obj, ensure_ascii=False)}\n<<<END_TASK_PLAN_JSON>>>\nHotovo."
    )


def _skeleton_dict(plan_spec, cross=_DEFAULT_CROSS, *, flagship=None, safety=None):
    epics = []
    for e_title, feats in plan_spec:
        fs = []
        for f_title, tasks in feats:
            f = {"title": f_title}
            ests = [t[2] for t in tasks if len(t) > 2 and t[2] is not None]
            if ests:
                f["estimated_minutes"] = sum(ests)
            fs.append(f)
        epics.append({"title": e_title, "feats": fs})
    obj = {"epics": epics, "cross_cutting_rules": cross}
    if flagship is not None:  # CR-V2-052 release-coverage declaration
        obj["flagship_features"] = flagship
    if safety is not None:
        obj["safety_properties"] = safety
    return obj


def _feat_tasks_dict(tasks):
    out = []
    for t in tasks:
        d = {"title": t[0], "task_type": t[1]}
        if len(t) > 2 and t[2] is not None:
            d["estimated_minutes"] = t[2]
        out.append(d)
    return {"tasks": out}


def _stub_plan_passes(monkeypatch, plan_spec, *, cross=_DEFAULT_CROSS, text=False, flagship=None, safety=None):
    """Drive the build-entry task-plan passes via a fake ``invoke_claude``: the skeleton pass (prompt contains
    "KOSTRU") → EPIC+FEAT(no tasks)+cross (+ CR-V2-052 flagship/safety declaration); a per-feat pass (the feat
    title appears) → that feat's tasks. ``text=True`` returns the real-env prose + ``<<<TASK_PLAN_JSON>>>``
    fence shape (structured_output=None); ``text=False`` returns the dict as structured_output."""
    feat_by_title = {f_title: tasks for _e, feats in plan_spec for f_title, tasks in feats}

    def _emit(obj):
        return (_task_plan_fence(obj), None, None) if text else ("", None, obj)

    async def _fake_invoke_claude(*, prompt, **_kw):
        if "KOSTRU" in prompt:
            return _emit(_skeleton_dict(plan_spec, cross, flagship=flagship, safety=safety))
        for f_title, tasks in feat_by_title.items():
            if f_title in prompt:
                return _emit(_feat_tasks_dict(tasks))
        raise AssertionError(f"unexpected plan-pass prompt: {prompt[:80]}")

    monkeypatch.setattr(orchestrator, "invoke_claude", _fake_invoke_claude)
    monkeypatch.setattr(orchestrator, "_split_claude_result", lambda r: r)
    monkeypatch.setattr(orchestrator, "_resolve_orch_session", lambda db, slug, role: (uuid.uuid4(), False))
    monkeypatch.setattr(orchestrator, "_resolve_dispatch_overrides", lambda db, vid, role: (None, None))


def _small_plan():
    return [("Foundation", [("Schema", [("users table", "migration", 60), ("audit_log", "migration", 30)])])]


def _stub_plan_passes_faulty(monkeypatch, plan_spec, *, fault_feat, fault, fail_times=None, cross=_DEFAULT_CROSS):
    """Like :func:`_stub_plan_passes`, but the per-feat pass for ``fault_feat`` RAISES ``fault`` (a
    ``ClaudeAgentError`` / ``ClaudeAgentTimeout``). ``fail_times=None`` → always raise (persistent); an int →
    raise that many times then succeed (transient — exercises the bounded re-invoke). Returns a ``calls``
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


def _plan_gate_reports(db_session, version_id):
    """The plan gate_report(s) recorded by the build-entry plan generation — uniquely identified by the
    ``plan`` payload the per-task build gate_reports never carry."""
    return [
        m
        for m in _msgs(db_session, version_id)
        if m.author == "ai_agent" and m.kind == "gate_report" and m.payload and m.payload.get("plan")
    ]


async def test_build_entry_generates_task_plan_via_incremental_passes(db_session, monkeypatch, tmp_path):
    # nex-studio-visual: the plan is built at Programovanie ENTRY (skeleton + per-feat passes) from the final
    # design, then coded — a single coherent materialization moved out of Návrh.
    version, _ = _make_version(db_session, source_path=str(tmp_path), project_dial="po_kazdej_faze")
    state = _seed_programovanie(db_session, version.id)
    _no_baseline_git(monkeypatch)
    _stub_plan_passes(
        monkeypatch,
        [
            ("Foundation", [("Schema", [("GL tables", "migration", 90), ("audit_log", "migration", 30)])]),
            ("Calc", [("Hlavná kniha", [("GL výpočet", "backend", 120)])]),
        ],
        cross="## Invarianty\n- spoločná transakčná hranica\n- immutable audit",
    )
    _stub_turns(monkeypatch, [_done_block()])  # code each generated task
    _stub_mech(monkeypatch, [None])
    settled = await orchestrator._run_build_round(db_session, state)
    # new_version → the Programovanie schvaľovací bod stops for the Manažér (mandatory gate)
    assert settled.current_stage == "programovanie" and settled.status == "awaiting_manazer"
    # EPIC→FEAT→TASK materialized at build entry
    epics = _epics(db_session, version.id)
    assert {e.title for e in epics} == {"Foundation", "Calc"}
    feats = db_session.execute(select(Feat)).scalars().all()
    tasks = db_session.execute(select(Task)).scalars().all()
    assert len(feats) == 2 and len(tasks) == 3
    est = {t.title: t.estimated_minutes for t in tasks}
    assert est["GL tables"] == 90 and est["GL výpočet"] == 120
    # the plan-gen gate_report carries the plan + cross_cutting_rules the build loop re-reads (_fetch_cross...)
    gr = _plan_gate_reports(db_session, version.id)
    assert gr and "transakčná" in gr[-1].payload["cross_cutting_rules"]
    assert orchestrator._fetch_cross_cutting_rules(db_session, version.id) == gr[-1].payload["cross_cutting_rules"]
    # the planning effort is accounted to the Návrh phase even though it runs at build time (metrics_phase)
    assert gr[-1].payload["phase"] == "navrh"


async def test_build_entry_gate_report_carries_release_coverage_declaration(db_session, monkeypatch, tmp_path):
    # CR-V2-052: the AI Agent declares flagship_features + safety_properties with the skeleton; the engine
    # records them on the plan gate_report so the risk-floored oracle (_declared_release_coverage) reads them.
    version, _ = _make_version(db_session, source_path=str(tmp_path), project_dial="po_kazdej_faze")
    state = _seed_programovanie(db_session, version.id)
    _no_baseline_git(monkeypatch)
    _stub_plan_passes(
        monkeypatch,
        [("Foundation", [("Schema", [("t", "migration", 60)])])],
        flagship=["PDF→Peppol export", "supplier auto-match"],
        safety=[
            {"name": "scoping na firmu", "risky_op": "cross-tenant GET /api/faktury"},
            {"name": "read_only blocks writes", "risky_op": "cat x > y under read_only"},
        ],
    )
    _stub_turns(monkeypatch, [_done_block()])
    _stub_mech(monkeypatch, [None])
    await orchestrator._run_build_round(db_session, state)
    gr = _plan_gate_reports(db_session, version.id)
    assert gr, "the plan gate_report was recorded"
    assert gr[-1].payload["flagship_features"] == ["PDF→Peppol export", "supplier auto-match"]
    assert [sp["name"] for sp in gr[-1].payload["safety_properties"]] == ["scoping na firmu", "read_only blocks writes"]
    # the oracle reads the declared coverage: 2 flagship features + 2 safety properties
    assert orchestrator._declared_release_coverage(db_session, version.id) == (2, 2)
    # NO message was recorded under a (now-invalid) "task_plan" stage — the standalone stage is gone
    assert not [m for m in _msgs(db_session, version.id) if m.stage == "task_plan"]


async def test_build_entry_plan_passes_via_text_fence_no_parse_exhaustion(db_session, monkeypatch):
    # Real-env path: the passes return the narrowed JSON as TEXT in a <<<TASK_PLAN_JSON>>> fence
    # (structured_output is dead in the live CLI). A multi-feat plan still assembles pass-by-pass.
    version, _ = _make_version(db_session, project_dial="po_kazdej_faze")
    state = _seed_programovanie(db_session, version.id)
    _no_baseline_git(monkeypatch)
    _stub_plan_passes(
        monkeypatch,
        [
            ("Foundation", [("Schema", [("t1", "migration", 60)])]),
            ("Core", [("Engine", [("e1", "backend", 120)]), ("API", [("a1", "backend", 90)])]),
        ],
        text=True,
    )
    _stub_turns(monkeypatch, [_done_block()])
    _stub_mech(monkeypatch, [None])
    settled = await orchestrator._run_build_round(db_session, state)
    assert settled.current_stage == "programovanie" and settled.status == "awaiting_manazer"
    assert len(_epics(db_session, version.id)) == 2
    assert len(db_session.execute(select(Task)).scalars().all()) == 3


async def test_build_entry_reviewable_task_plan_doc_written(db_session, monkeypatch, tmp_path):
    version, _ = _make_version(db_session, source_path=str(tmp_path), project_dial="po_kazdej_faze")
    state = _seed_programovanie(db_session, version.id)
    _no_baseline_git(monkeypatch)
    _stub_plan_passes(monkeypatch, _small_plan(), cross="## Invarianty\n- x")
    _stub_turns(monkeypatch, [_done_block()])
    _stub_mech(monkeypatch, [None])
    await orchestrator._run_build_round(db_session, state)
    plan_doc = tmp_path / "docs" / "specs" / "versions" / f"v{version.version_number}" / "spec" / "task-plan.md"
    assert plan_doc.is_file()
    md = plan_doc.read_text(encoding="utf-8")
    assert "## Epic 1: Foundation" in md and "### Feat 1.1: Schema" in md
    assert "users table" in md and "`[migration]`" in md


# ── Fail-closed at build entry: a plan-pass exhaustion blocks, writes nothing ──


async def test_build_entry_skeleton_parse_failure_blocks_writes_nothing(db_session, monkeypatch):
    version, _ = _make_version(db_session, project_dial="po_kazdej_faze")
    state = _seed_programovanie(db_session, version.id)

    # the skeleton pass returns garbage (no fence) every time → parse-retries exhaust → blocked
    async def _bad_invoke(*, prompt, **_kw):
        return ("no fence here, just prose", None, None)

    monkeypatch.setattr(orchestrator, "invoke_claude", _bad_invoke)
    monkeypatch.setattr(orchestrator, "_split_claude_result", lambda r: r)
    monkeypatch.setattr(orchestrator, "_resolve_orch_session", lambda db, slug, role: (uuid.uuid4(), False))
    monkeypatch.setattr(orchestrator, "_resolve_dispatch_overrides", lambda db, vid, role: (None, None))
    settled = await orchestrator._run_build_round(db_session, state)
    assert settled.status == "blocked"
    assert settled.block_reason == "parse_exhaustion"
    assert settled.current_stage == "programovanie"
    assert not _epics(db_session, version.id)  # nothing written on a failed plan


async def test_build_entry_per_feat_crash_is_retried_then_succeeds(db_session, monkeypatch):
    # A FAST crash (ClaudeAgentError, not a timeout) in a per-feat pass is re-invoked (bounded) rather than
    # discarding the whole accumulated plan — crash once, then succeed → the FULL plan still materializes.
    version, _ = _make_version(db_session, project_dial="po_kazdej_faze")
    state = _seed_programovanie(db_session, version.id)
    _no_baseline_git(monkeypatch)
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
    _stub_turns(monkeypatch, [_done_block()])
    _stub_mech(monkeypatch, [None])
    settled = await orchestrator._run_build_round(db_session, state)
    assert settled.current_stage == "programovanie" and settled.status == "awaiting_manazer"
    assert calls["Engine"] == 2  # crashed once → re-invoked once → succeeded
    assert {e.title for e in _epics(db_session, version.id)} == {"Foundation", "Core"}
    assert len(db_session.execute(select(Task)).scalars().all()) == 2  # nothing lost


async def test_build_entry_per_feat_persistent_crash_blocks_agent_error_not_parse_exhaustion(db_session, monkeypatch):
    # A crash that keeps failing past the bounded re-invokes is an envelope-loss; with NO dispatch baseline
    # it HALTs blocked with block_reason=agent_error (never the parse_exhaustion mislabel) + writes nothing.
    version, _ = _make_version(db_session, project_dial="po_kazdej_faze")
    state = _seed_programovanie(db_session, version.id)
    calls = _stub_plan_passes_faulty(
        monkeypatch,
        [("Core", [("Engine", [("e1", "backend", 120)])])],
        fault_feat="Engine",
        fault=orchestrator.ClaudeAgentError("claude exited with code 1: persistent boom"),
    )
    settled = await orchestrator._run_build_round(db_session, state)
    assert settled.status == "blocked" and settled.block_reason == "agent_error"
    assert calls["Engine"] == orchestrator._PARSE_RETRIES + 1  # initial attempt + the bounded re-invokes
    assert not _epics(db_session, version.id)  # all-or-nothing: nothing written


async def test_build_entry_per_feat_persistent_crash_with_baseline_settles_review_continue(db_session, monkeypatch):
    # With a dispatch baseline armed, a persistent crash settles awaiting_manazer ("review & continue") and
    # the lost-work message tells the TRUTH — "Agent opakovane zlyhal", not the misleading "Vypršal čas".
    version, _ = _make_version(db_session, project_dial="po_kazdej_faze")
    state = _seed_programovanie(db_session, version.id)
    state.dispatch_baseline_sha = "deadbeefdeadbeef"
    db_session.flush()
    _stub_plan_passes_faulty(
        monkeypatch,
        [("Core", [("Engine", [("e1", "backend", 120)])])],
        fault_feat="Engine",
        fault=orchestrator.ClaudeAgentError("claude exited with code 1: persistent boom"),
    )
    settled = await orchestrator._run_build_round(db_session, state)
    assert settled.status == "awaiting_manazer"
    assert "Agent opakovane zlyhal" in settled.next_action
    assert "Vypršal čas" not in settled.next_action
    assert not _epics(db_session, version.id)


async def test_build_entry_per_feat_timeout_is_not_retried(db_session, monkeypatch):
    # A genuine TIMEOUT (ClaudeAgentTimeout) is NOT re-invoked — re-running just risks another long wait. It
    # settles the R1 lost-work path at once: the per-feat pass is called exactly ONCE; message "Vypršal čas".
    version, _ = _make_version(db_session, project_dial="po_kazdej_faze")
    state = _seed_programovanie(db_session, version.id)
    state.dispatch_baseline_sha = "deadbeefdeadbeef"
    db_session.flush()
    calls = _stub_plan_passes_faulty(
        monkeypatch,
        [("Core", [("Engine", [("e1", "backend", 120)])])],
        fault_feat="Engine",
        fault=orchestrator.ClaudeAgentTimeout("claude invocation timed out after 1200s"),
    )
    settled = await orchestrator._run_build_round(db_session, state)
    assert settled.status == "awaiting_manazer"
    assert calls["Engine"] == 1  # a timeout is NOT retried
    assert "Vypršal čas agenta" in settled.next_action
    assert not _epics(db_session, version.id)
