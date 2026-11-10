"""DEV-260 — the next task of a build does not start on a main branch the version's own tasks left red.

03.11.2026, NEX Manager 1.5.0: task #35.2.1 committed a test file that failed the backend's format check, and the main
branch stayed red through task #35.2.2 until task #36.1.1 fixed it, sent back by the CI gate at Verifikácia. The rule
„every task leaves the main branch green“ was a sentence in the brief. Now the cockpit looks at GitHub's runs before a
task: when the newest run of a workflow on the main branch finished red on a commit of this version's tasks, the
agent's turn starts with the fix, and the Manažér is told. Director 05.11.2026: „Súhlasím“.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from backend.db.models.foundation import User
from backend.db.models.pipeline import PipelineMessage, PipelineState
from backend.db.models.projects import Project
from backend.db.models.tasks import Epic, Feat, Task
from backend.db.models.versions import Version
from backend.services import ci_runs, ci_watch, orchestrator, red_main_branch
from backend.services.pipeline_status import PipelineStatusBlock

FIX_FIRST = "Hlavná vetva projektu je po úlohách tejto verzie červená"


def _run(subject: str, state: str, *, workflow: str = "CI", branch: str = "main", step: str | None = None, n: int = 1):
    when = datetime(2026, 11, 3, 15, n, tzinfo=timezone.utc)
    return ci_runs.Run(
        id=n,
        number=n,
        attempt=1,
        workflow=workflow,
        subject=subject,
        event="push",
        state=state,
        state_label=state,
        branch=branch,
        sha=f"{n:040d}",
        actor="nex-studio",
        created_at=when,
        started_at=when,
        updated_at=when,
        url=f"https://github.com/rauschiccsk/nex-demo/actions/runs/{n}",
        failed_step=step,
    )


def _build(db_session) -> uuid.UUID:
    """A build at task #35.2.2 of version 1.5.0; task #35.2.1 is done, and an older version had task #34.1.1."""
    user = User(
        username=f"u_{uuid.uuid4().hex[:8]}", email=f"{uuid.uuid4().hex[:8]}@x.sk", password_hash="x", role="ri"
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
        miera_autonomie="po_kazdej_faze",
        repo_url="https://github.com/rauschiccsk/nex-demo",
    )
    db_session.add(project)
    db_session.flush()
    older = Version(project_id=project.id, version_number="1.4.2")
    version = Version(project_id=project.id, version_number="1.5.0")
    db_session.add_all([older, version])
    db_session.flush()
    db_session.add(
        PipelineState(
            version_id=version.id,
            flow_type="new_version",
            current_stage="programovanie",
            current_actor="ai_agent",
            status="agent_working",
            next_action="working",
        )
    )
    for v, epic_no, tasks in ((older, 34, [(1, 1, "done")]), (version, 35, [(2, 1, "done"), (2, 2, "todo")])):
        epic = Epic(project_id=project.id, version_id=v.id, number=epic_no, title=f"E{epic_no}", status="planned")
        db_session.add(epic)
        db_session.flush()
        feats: dict[int, Feat] = {}
        for feat_no, task_no, status in tasks:
            if feat_no not in feats:
                feats[feat_no] = Feat(epic_id=epic.id, number=feat_no, title=f"F{feat_no}", status="todo")
                db_session.add(feats[feat_no])
                db_session.flush()
            db_session.add(
                Task(
                    feat_id=feats[feat_no].id,
                    number=task_no,
                    title=f"Úloha {epic_no}.{feat_no}.{task_no}",
                    description="Prihlásenie cez reláciu šablóny.",
                    task_type="backend",
                    status=status,
                )
            )
        db_session.flush()
    return version.id


def _stub(monkeypatch, runs, *, error=None, has_ci=True, first_attempt_fails=False) -> dict:
    seen: dict = {"prompts": [], "asked": 0}

    async def _turn(db, *, version_id, role, stage, prompt, **_kw):
        seen["prompts"].append(prompt)
        return PipelineStatusBlock(
            stage="programovanie", kind="gate_report", summary="hotovo", awaiting="manazer", commits=["a" * 40]
        )

    async def _listing(repo, *, timeout):
        seen["asked"] += 1
        return ci_runs.Listing(repo=repo, runs=list(runs), error=error, stale=error is not None)

    checks = iter([("formát", "zlyhal")] if first_attempt_fails else [])
    monkeypatch.setattr(orchestrator, "invoke_agent_with_parse_retry", _turn)
    monkeypatch.setattr(orchestrator, "verify_mechanical", lambda slug, block, baseline_sha=None: next(checks, None))
    monkeypatch.setattr(orchestrator, "_repo_head", lambda root: "b" * 40)
    monkeypatch.setattr(orchestrator, "_project_has_ci", lambda root: has_ci)
    monkeypatch.setattr(ci_watch, "checkout_branch", lambda root: "main")
    monkeypatch.setattr(ci_runs, "listing", _listing)
    return seen


def _told(db_session, version_id) -> list[str]:
    return [
        m.content
        for m in db_session.query(PipelineMessage).filter(PipelineMessage.version_id == version_id).all()
        if (m.payload or {}).get("red_main_branch")
    ]


async def test_a_red_main_branch_of_this_version_is_fixed_before_the_task(db_session, monkeypatch):
    version_id = _build(db_session)
    red = _run("test(v1.5.0): the session gate (TASK #35.2.1)", ci_runs.FAILURE, step="lint › Backend format check")
    seen = _stub(monkeypatch, [red])

    await orchestrator.run_dispatch(db_session, version_id)

    prompt = seen["prompts"][0]
    assert prompt.startswith(FIX_FIRST)
    assert "úloha #35.2.1 (Úloha 35.2.1): kontrola „CI“ zlyhala v časti „lint › Backend format check“" in prompt
    assert red.url in prompt
    assert prompt.index("oprav hlavnú vetvu") < prompt.index("Úloha 35.2.2"), "the fix first, the task after it"
    assert _told(db_session, version_id) == [
        "Hlavná vetva je červená po úlohe #35.2.1 — Staviteľ ju opraví skôr, než pokračuje úlohou #35.2.2."
    ]


async def test_a_green_main_branch_lets_the_task_start(db_session, monkeypatch):
    version_id = _build(db_session)
    newer_green = _run("fix(v1.5.0): format (TASK #35.2.1)", ci_runs.SUCCESS, n=2)
    seen = _stub(monkeypatch, [newer_green, _run("x (TASK #35.2.1)", ci_runs.FAILURE, n=1)])

    await orchestrator.run_dispatch(db_session, version_id)

    assert FIX_FIRST not in seen["prompts"][0]
    assert _told(db_session, version_id) == []


async def test_a_fix_still_running_does_not_stop_the_task(db_session, monkeypatch):
    version_id = _build(db_session)
    running = _run("fix(v1.5.0): format (TASK #35.2.1)", ci_runs.RUNNING, n=2)
    seen = _stub(monkeypatch, [running, _run("x (TASK #35.2.1)", ci_runs.FAILURE, n=1)])

    await orchestrator.run_dispatch(db_session, version_id)

    assert FIX_FIRST not in seen["prompts"][0]


async def test_a_red_of_another_versions_task_or_a_side_branch_does_not_stop_it(db_session, monkeypatch):
    version_id = _build(db_session)
    other_version = _run("feat(v1.4.2): groups (TASK #34.1.1)", ci_runs.FAILURE, workflow="CI", n=2)
    side_branch = _run("x (TASK #35.2.1)", ci_runs.FAILURE, workflow="Release gate", branch="proof/red", n=1)
    seen = _stub(monkeypatch, [other_version, side_branch])

    await orchestrator.run_dispatch(db_session, version_id)

    assert FIX_FIRST not in seen["prompts"][0]


async def test_an_old_answer_of_github_stops_nothing(db_session, monkeypatch):
    version_id = _build(db_session)
    seen = _stub(monkeypatch, [_run("x (TASK #35.2.1)", ci_runs.FAILURE)], error="GitHub neodpovedal")

    await orchestrator.run_dispatch(db_session, version_id)

    assert FIX_FIRST not in seen["prompts"][0]


async def test_a_project_without_checks_does_not_ask_github(db_session, monkeypatch):
    version_id = _build(db_session)
    seen = _stub(monkeypatch, [_run("x (TASK #35.2.1)", ci_runs.FAILURE)], has_ci=False)

    await orchestrator.run_dispatch(db_session, version_id)

    assert seen["asked"] == 0 and FIX_FIRST not in seen["prompts"][0]


async def test_the_fix_is_asked_once_not_at_every_attempt_of_the_task(db_session, monkeypatch):
    version_id = _build(db_session)
    seen = _stub(monkeypatch, [_run("x (TASK #35.2.1)", ci_runs.FAILURE)], first_attempt_fails=True)

    await orchestrator.run_dispatch(db_session, version_id)

    assert len(seen["prompts"]) >= 2, "the first attempt failed its check and the task ran again"
    assert seen["prompts"][0].startswith(FIX_FIRST)
    assert FIX_FIRST not in seen["prompts"][1]


def test_the_newest_run_of_each_workflow_on_the_branch_decides():
    runs = [
        _run("b", ci_runs.SUCCESS, workflow="CI", n=4),
        _run("a", ci_runs.FAILURE, workflow="CI", n=3),
        _run("c", ci_runs.FAILURE, workflow="Release gate", n=2),
        _run("d", ci_runs.FAILURE, workflow="CI", branch="feature", n=1),
    ]

    assert [r.workflow for r in red_main_branch.red_runs(runs, "main")] == ["Release gate"]
