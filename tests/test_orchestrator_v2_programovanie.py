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


async def test_plna_auto_continues_to_verifikacia(db_session, monkeypatch):
    # Plná autonómia: a clean build auto-continues into Verifikácia (no Manažér stop).
    version, project = _make_version(db_session, project_dial="plna")
    _seed_programovanie(db_session, version.id)
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

    # no tasks seeded; invoke must never be called
    async def _boom(*a, **k):
        raise AssertionError("no agent turn expected when there is no todo task")

    monkeypatch.setattr(orchestrator, "invoke_agent_with_parse_retry", _boom)
    state = await orchestrator.run_dispatch(db_session, version.id)
    assert state.current_stage == "programovanie" and state.status == "awaiting_manazer"
