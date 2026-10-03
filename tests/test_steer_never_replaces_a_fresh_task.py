"""A Manažér's steer never REPLACES the brief of a task it says nothing about (ICCINT-164, 03.10.2026).

``_run_build_round`` hands the dispatch's directive to attempt 1 of the first task it picks, AS the prompt — written
for the task the directive belongs to: one the agent was already on (its question answered, its failure sent back).
On dedo-home the build had stopped at a task boundary; the Manažér sent an unrelated steer ("make the tests
faster"); the loop picked the NEXT plan task, 5.1.1, and the agent's prompt was the steer alone — no title, no
description. The agent did what it was asked, reported, and 5.1.1 was closed as done without ever having been seen.

Whether a task was already being worked on is a durable fact: its ``baseline_sha`` is written when it starts and
survives the reclaim of an orphaned or questioned task. A task without one is fresh — the steer goes IN FRONT of
its brief, never instead of it.
"""

from __future__ import annotations

import uuid

from backend.db.models.foundation import User
from backend.db.models.pipeline import PipelineState
from backend.db.models.projects import Project
from backend.db.models.tasks import Epic, Feat, Task
from backend.db.models.versions import Version
from backend.services import orchestrator
from backend.services.pipeline_status import PipelineStatusBlock

# (pytest ``asyncio_mode = auto`` — async tests run without an explicit mark.)

_STEER = "Manažér ťa vrátil na úpravu fázy 'programovanie': zrýchli skúšky."


def _build_with_one_task(db_session, *, started: bool) -> uuid.UUID:
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
        miera_autonomie="po_kazdej_faze",
    )
    db_session.add(project)
    db_session.flush()
    version = Version(project_id=project.id, version_number=f"1.{uuid.uuid4().hex[:4]}.0")
    db_session.add(version)
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
    epic = Epic(project_id=project.id, version_id=version.id, number=5, title="Audítor", status="planned")
    db_session.add(epic)
    db_session.flush()
    feat = Feat(epic_id=epic.id, number=1, title="Spustenie", status="todo")
    db_session.add(feat)
    db_session.flush()
    task = Task(
        feat_id=feat.id,
        number=1,
        title="Spustenie audítora strojom",
        description="POST /works/{id}/audit — predkontrola ako podmienka spustenia.",
        task_type="backend",
        # A task the agent was already on (asked a question mid-task, or failed and was sent back) carries the
        # baseline it started from; a fresh one does not.
        status="todo",
        baseline_sha="c" * 40 if started else None,
    )
    db_session.add(task)
    db_session.flush()
    return version.id


def _stub(monkeypatch) -> list[str]:
    prompts: list[str] = []

    async def _turn(db, *, version_id, role, stage, prompt, **_kw):
        prompts.append(prompt)
        return PipelineStatusBlock(
            stage="programovanie", kind="gate_report", summary="hotovo", awaiting="manazer", commits=["a" * 40]
        )

    monkeypatch.setattr(orchestrator, "invoke_agent_with_parse_retry", _turn)
    monkeypatch.setattr(orchestrator, "verify_mechanical", lambda slug, block, baseline_sha=None: None)
    monkeypatch.setattr(orchestrator, "_repo_head", lambda root: "b" * 40)
    return prompts


async def test_a_fresh_task_gets_its_own_brief_with_the_steer_in_front(db_session, monkeypatch):
    version_id = _build_with_one_task(db_session, started=False)
    prompts = _stub(monkeypatch)

    await orchestrator.run_dispatch(db_session, version_id, directive=_STEER)

    assert len(prompts) == 1
    prompt = prompts[0]
    assert "zrýchli skúšky" in prompt, "the steer must still reach the agent"
    assert "Spustenie audítora strojom" in prompt, "the task the turn will be closed as must be in its prompt"
    assert "POST /works/{id}/audit" in prompt, "…with its description, not just a name"
    assert prompt.index("zrýchli skúšky") < prompt.index("Spustenie audítora strojom"), "the steer goes first"


async def test_a_task_the_agent_was_already_on_still_gets_the_steer_as_its_brief(db_session, monkeypatch):
    # The case the replacement was written for: an answer to the agent's own question, a failed task sent back.
    # The warm session already holds the task; the Manažér's words ARE the next step on it.
    version_id = _build_with_one_task(db_session, started=True)
    prompts = _stub(monkeypatch)

    await orchestrator.run_dispatch(db_session, version_id, directive=_STEER)

    assert prompts == [_STEER]
