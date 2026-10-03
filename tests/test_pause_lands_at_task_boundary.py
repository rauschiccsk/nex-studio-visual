"""A pause pressed while the agent works is a REQUEST until the loop lands it (ICCINT-163, 03.10.2026).

The Programovanie loop stops only at a task boundary — the agent finishes the task it is on, which can take
an hour. ``pause`` nevertheless wrote ``status='paused'`` the moment it was pressed, and the status listener
read that as a settle and dropped ``dispatch_in_flight``. For the rest of that task the cockpit said
"pozastavené", offered "Vrátiť agentovi na doplnenie", and accepted it: the Manažér's text was recorded as
delivered, ``_begin_dispatch`` flipped the build back to ``agent_working``, ``schedule_dispatch`` found the
loop still running and skipped the new dispatch — dropping the text — and the loop, reaching its boundary,
saw ``agent_working`` and carried on. Measured on dedo-home: pause at 13:14:54, steer at 13:15:17 (logged as
delivered, absent from the agent's prompt), next task started 13:20:29. The second pause showed "paused" at
13:34:11; the agent really stopped at 13:47:39.

So: while a dispatch is in flight, ``pause`` records the request (``pause_reason='manazer'``, status stays
``agent_working``, the single-flight flag stays armed) and the loop writes ``paused`` at its next boundary.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from backend.db.models.foundation import User
from backend.db.models.pipeline import PipelineMessage, PipelineState
from backend.db.models.projects import Project
from backend.db.models.tasks import Epic, Feat, Task
from backend.db.models.versions import Version
from backend.services import orchestrator, pipeline_runner
from backend.services.pipeline_status import PipelineStatusBlock

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
        miera_autonomie="po_kazdej_faze",
    )
    db_session.add(project)
    db_session.flush()
    version = Version(project_id=project.id, version_number=f"1.{uuid.uuid4().hex[:4]}.0")
    db_session.add(version)
    db_session.flush()
    return version, project


def _running_build(db_session, version_id, *, in_flight: bool = True) -> PipelineState:
    """A Programovanie build whose loop is running — exactly what ``_begin_dispatch`` leaves behind."""
    state = PipelineState(
        version_id=version_id,
        flow_type="new_version",
        current_stage="programovanie",
        current_actor="ai_agent",
        status="agent_working",
        next_action="working",
        dispatch_in_flight=in_flight,
    )
    db_session.add(state)
    db_session.flush()
    return state


def _seed_tasks(db_session, version, project, n):
    epic = Epic(project_id=project.id, version_id=version.id, number=1, title="E", status="planned")
    db_session.add(epic)
    db_session.flush()
    feat = Feat(epic_id=epic.id, number=1, title="F", status="todo")
    db_session.add(feat)
    db_session.flush()
    tasks = [
        Task(feat_id=feat.id, number=i, title=f"T{i}", task_type="backend", status="todo") for i in range(1, n + 1)
    ]
    db_session.add_all(tasks)
    db_session.flush()
    return tasks


def _state(db_session, version_id) -> PipelineState:
    return orchestrator._get_state(db_session, version_id)


def _manazer_messages(db_session, version_id):
    return (
        db_session.execute(
            select(PipelineMessage).where(PipelineMessage.version_id == version_id, PipelineMessage.author == "manazer")
        )
        .scalars()
        .all()
    )


@pytest.fixture
def fake_claude(monkeypatch):
    async def _fake(**_kw):
        return ""

    monkeypatch.setattr(orchestrator, "invoke_claude", _fake)


# ── the action: a request, not a status ───────────────────────────────────────


async def test_pause_while_the_loop_runs_does_not_claim_the_build_is_paused(db_session, fake_claude):
    version, _ = _make_version(db_session)
    _running_build(db_session, version.id)

    state = await orchestrator.apply_action(db_session, version_id=version.id, action="pause")

    assert state.status == "agent_working", "the agent is still on its task — 'paused' here is the lie"
    assert state.pause_reason == "manazer", "the request must be recorded, or the loop has nothing to honour"
    assert state.dispatch_in_flight is True, "the single-flight guard must keep knowing the loop runs"
    assert "dokonč" in state.next_action, "the screen must say the agent finishes its task first"


async def test_nothing_is_offered_while_the_pause_is_pending(db_session, fake_claude):
    version, _ = _make_version(db_session)
    _running_build(db_session, version.id)
    state = await orchestrator.apply_action(db_session, version_id=version.id, action="pause")

    assert orchestrator.determine_available_actions(state) == set()


@pytest.mark.parametrize(
    ("action", "payload"),
    [("uprav", {"comment": "zrýchli skúšky"}), ("pokracovat", None)],
)
async def test_a_steer_or_resume_during_the_pending_pause_is_refused(db_session, fake_claude, action, payload):
    version, _ = _make_version(db_session)
    _running_build(db_session, version.id)
    await orchestrator.apply_action(db_session, version_id=version.id, action="pause")

    with pytest.raises(orchestrator.OrchestratorError, match="ešte pracuje"):
        await orchestrator.apply_action(db_session, version_id=version.id, action=action, payload=payload)
    assert _manazer_messages(db_session, version.id) == [], "a refused steer must not be on record as sent"


async def test_a_chat_message_during_the_pending_pause_is_refused_not_queued(db_session, fake_claude):
    # Queued, it would be drained the moment the loop lands the pause — and the drain re-arms the dispatch,
    # i.e. it would quietly undo the pause the Manažér just asked for.
    version, _ = _make_version(db_session)
    _running_build(db_session, version.id)
    await orchestrator.apply_action(db_session, version_id=version.id, action="pause")

    with pytest.raises(orchestrator.OrchestratorError, match="pozastav"):
        await orchestrator.relay_manazer_message(db_session, version_id=version.id, text="ešte niečo")
    assert not orchestrator.has_pending_relay(version.id)


async def test_a_truly_paused_build_is_not_described_as_still_pausing(db_session, fake_claude):
    # ``pause_reason='manazer'`` stays on the row after the loop lands the pause. "Pending" is only the
    # agent_working half of it — on a stopped build "AI Agent dokončuje rozrobenú úlohu" would be the new lie.
    version, _ = _make_version(db_session)
    _running_build(db_session, version.id, in_flight=False)
    state = await orchestrator.apply_action(db_session, version_id=version.id, action="pause")
    assert state.status == "paused" and not orchestrator.pause_pending(state)

    with pytest.raises(orchestrator.OrchestratorError) as refused:
        await orchestrator.relay_manazer_message(db_session, version_id=version.id, text="ešte niečo")
    assert "dokončuje" not in str(refused.value)


async def test_pause_with_no_dispatch_in_flight_still_pauses_at_once(db_session, fake_claude):
    # Nothing runs to finish a task, so there is no boundary to wait for — the old immediate pause is the
    # truthful one here (and it is how a stuck build is stopped).
    version, _ = _make_version(db_session)
    _running_build(db_session, version.id, in_flight=False)

    state = await orchestrator.apply_action(db_session, version_id=version.id, action="pause")

    assert state.status == "paused" and state.pause_reason == "manazer"


# ── the loop: lands the request at the boundary ───────────────────────────────


def _done_block():
    return PipelineStatusBlock(
        stage="programovanie", kind="gate_report", summary="hotovo", awaiting="manazer", commits=["a" * 40]
    )


async def test_the_loop_lands_the_pause_after_the_current_task(db_session, monkeypatch):
    version, project = _make_version(db_session)
    _running_build(db_session, version.id)
    tasks = _seed_tasks(db_session, version, project, 3)
    monkeypatch.setattr(orchestrator, "_repo_head", lambda root: "b" * 40)
    monkeypatch.setattr(orchestrator, "verify_mechanical", lambda slug, block, baseline_sha=None: None)

    seen_during_the_task: dict = {}
    turns: list[int] = []

    async def _turn(db, *, version_id, role, stage, prompt, **_kw):
        turns.append(1)
        if len(turns) == 1:
            # The Manažér presses "Pozastaviť" while the first task is being worked on…
            mid = await orchestrator.apply_action(db, version_id=version_id, action="pause")
            seen_during_the_task["status"] = mid.status
            # …and then presses the steer button his screen offered him in the old code.
            try:
                await orchestrator.apply_action(
                    db, version_id=version_id, action="uprav", payload={"comment": "zrýchli skúšky"}
                )
                seen_during_the_task["steer"] = "accepted"
            except orchestrator.OrchestratorError:
                seen_during_the_task["steer"] = "refused"
        return _done_block()

    monkeypatch.setattr(orchestrator, "invoke_agent_with_parse_retry", _turn)

    state = await orchestrator.run_dispatch(db_session, version.id)

    assert seen_during_the_task == {"status": "agent_working", "steer": "refused"}
    assert len(turns) == 1, "the loop must stop at the first boundary after the pause, not run on"
    assert state.status == "paused" and state.pause_reason == "manazer"
    assert state.dispatch_in_flight is False, "now the loop really stopped — the flag may drop"
    statuses = {
        t.number: t.status for t in db_session.execute(select(Task).where(Task.id.in_([t.id for t in tasks]))).scalars()
    }
    assert statuses == {1: "done", 2: "todo", 3: "todo"}


async def test_a_dispatch_that_ends_without_a_boundary_still_lands_the_pause(db_session):
    # The runner's backstop: a dispatch can end while the row still says agent_working (an auto-chain that
    # exhausted its guard). A pending pause must not be left on screen as "Pozastavujem…" forever.
    version, _ = _make_version(db_session)
    state = _running_build(db_session, version.id)
    state.pause_reason = "manazer"
    db_session.flush()

    pipeline_runner._clear_dispatch_flags(db_session, version.id)

    state = _state(db_session, version.id)
    db_session.refresh(state)
    assert state.status == "paused" and state.pause_reason == "manazer"
    assert state.dispatch_in_flight is False
