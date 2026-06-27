"""Milestone-I — live coverage of the v2 background runner loop (``pipeline_runner._run`` & friends).

``_run`` is live v2 code, but the v2 phase tests cover ``run_dispatch`` (the engine) + ``_maybe_notify``
(comms) separately and never drive the RUNNER loop end-to-end. The v1 ``test_pipeline_runner.py`` is
deferred because its fakes write v1 kickoff/coordinator rows and use the dropped ``gate_e_dispatch`` param
+ ``awaiting_director`` status. This file re-expresses the still-load-bearing runner invariants in v2
vocabulary (recipient ``manazer``; settled status ``awaiting_manazer``; ``run_dispatch`` 4-arg signature),
run against the real v2 branch DB:

  * **Incremental broadcast** — each dispatch message is broadcast via ``on_message`` the moment it is
    recorded (message_added frames mid-dispatch), then the final settled state_changed.
  * **Engine auto-chain** — ``_run`` keeps dispatching in the SAME task while ``run_dispatch`` returns
    ``agent_working`` (broadcasting one state_changed per advance) until it settles, bounded by
    ``auto_chain_limit``.
  * **Always-settle-blocked** — when ``run_dispatch`` raises, ``_run`` settles to ``blocked`` (never stuck
    at agent_working), records a system notification, and keeps any pre-crash streamed message.
  * **Single-flight per version** — a 2nd ``schedule_dispatch`` for an in-flight version is skipped.
  * **Dispatch-flag backstop** — ``_clear_dispatch_flags`` clears the durable flag + baseline on settle,
    is safe on a missing state, and ``_run`` clears the flag even when a bulk UPDATE bypassed the listener.
"""

import uuid

from sqlalchemy import select
from sqlalchemy import update as _update

from backend.db.models.foundation import User
from backend.db.models.pipeline import PipelineMessage, PipelineState
from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.services import orchestrator, pipeline_runner

# (pytest ``asyncio_mode = auto`` — async tests run without an explicit mark.)


class _FakeRegistry:
    def __init__(self):
        self.events: list = []
        self.present: set = set()
        self.away: bool = False

    async def broadcast(self, vid, payload):
        self.events.append((vid, payload))

    def present_director_ids(self, vid):
        return self.present

    def active_director_ids(self, vid):
        return set() if self.away else self.present

    def away_director_ids(self, vid):
        return self.present if self.away else set()


def _make_version(db_session) -> Version:
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
    return version


def _seed_working_state(db_session, version_id, *, stage="programovanie", actor="ai_agent") -> PipelineState:
    state = PipelineState(
        version_id=version_id,
        flow_type="new_version",
        current_stage=stage,
        current_actor=actor,
        status="agent_working",
        next_action="working",
    )
    db_session.add(state)
    db_session.flush()
    return state


def _wire_runner(db_session, monkeypatch) -> _FakeRegistry:
    fake_reg = _FakeRegistry()
    monkeypatch.setattr(pipeline_runner, "registry", fake_reg)
    monkeypatch.setattr(pipeline_runner, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)
    monkeypatch.setattr(db_session, "commit", db_session.flush)
    monkeypatch.setattr(db_session, "rollback", lambda: None)
    return fake_reg


# ── incremental broadcast: message_added mid-dispatch, state_changed at settle ──


async def test_run_broadcasts_state_and_incremental_messages(db_session, monkeypatch):
    """Each dispatch message is broadcast via on_message the moment it's recorded (incremental), not batched
    at round end; plus the settled state_changed last."""
    version = _make_version(db_session)
    _seed_working_state(db_session, version.id)
    fake_reg = _wire_runner(db_session, monkeypatch)

    async def fake_run_dispatch(db, vid, on_event=None, directive=None, *, on_message=None):
        for author in ("ai_agent", "auditor"):
            msg = orchestrator._record_message(
                db,
                version_id=vid,
                stage="programovanie",
                author=author,
                recipient="manazer",
                kind="gate_report",
                content="x",
            )
            await on_message(msg)  # incremental broadcast, mid-dispatch (before the settle)
        st = db.execute(select(PipelineState).where(PipelineState.version_id == vid)).scalar_one()
        st.status = "awaiting_manazer"
        st.next_action = "Manažér: posúdiť."
        db.flush()
        return st

    monkeypatch.setattr(orchestrator, "run_dispatch", fake_run_dispatch)

    await pipeline_runner._run(version.id)

    kinds = [p["type"] for _, p in fake_reg.events]
    # incremental: BOTH message_added frames arrive DURING the dispatch, before the end-of-run state_changed.
    assert kinds == ["message_added", "message_added", "state_changed"]
    added = [p["message"] for _, p in fake_reg.events if p["type"] == "message_added"]
    assert [m["author"] for m in added] == ["ai_agent", "auditor"]
    state_evt = next(p for _, p in fake_reg.events if p["type"] == "state_changed")
    assert state_evt["state"]["status"] == "awaiting_manazer"


# ── engine auto-chain: run_dispatch returning agent_working continues the chain ──


async def test_run_auto_chain_continues_until_settled(db_session, monkeypatch):
    """A run_dispatch that returns agent_working (a dial-governed auto-advance) makes _run CONTINUE the
    chain in the SAME task — broadcasting each intermediate state — until it settles awaiting_manazer.
    Models the 4-phase auto-chain: programovanie→verifikacia (agent_working) → verifikacia settles."""
    version = _make_version(db_session)
    _seed_working_state(db_session, version.id, stage="programovanie")
    fake_reg = _wire_runner(db_session, monkeypatch)

    steps = iter([("verifikacia", "auditor", "agent_working"), ("verifikacia", "auditor", "awaiting_manazer")])

    async def fake_run_dispatch(db, vid, on_event=None, directive=None, *, on_message=None):
        st = db.execute(select(PipelineState).where(PipelineState.version_id == vid)).scalar_one()
        st.current_stage, st.current_actor, st.status = next(steps)
        db.flush()
        return st

    monkeypatch.setattr(orchestrator, "run_dispatch", fake_run_dispatch)

    await pipeline_runner._run(version.id)

    settled = db_session.execute(select(PipelineState).where(PipelineState.version_id == version.id)).scalar_one()
    assert settled.current_stage == "verifikacia" and settled.status == "awaiting_manazer"
    # one state_changed per advance + the final settle — the board steps live through the auto-advance.
    state_evts = [p["state"] for _, p in fake_reg.events if p["type"] == "state_changed"]
    assert [s["current_stage"] for s in state_evts] == ["verifikacia", "verifikacia"]
    assert [s["status"] for s in state_evts] == ["agent_working", "awaiting_manazer"]


async def test_run_settled_result_does_not_auto_chain(db_session, monkeypatch):
    """Regression: a settled (awaiting_manazer) run_dispatch result never triggers the auto-chain loop —
    exactly one dispatch, one state_changed."""
    version = _make_version(db_session)
    _seed_working_state(db_session, version.id)
    fake_reg = _wire_runner(db_session, monkeypatch)
    calls = {"n": 0}

    async def fake_run_dispatch(db, vid, on_event=None, directive=None, *, on_message=None):
        calls["n"] += 1
        st = db.execute(select(PipelineState).where(PipelineState.version_id == vid)).scalar_one()
        st.status = "awaiting_manazer"
        db.flush()
        return st

    monkeypatch.setattr(orchestrator, "run_dispatch", fake_run_dispatch)

    await pipeline_runner._run(version.id)

    assert calls["n"] == 1  # no continuation loop
    assert len([p for _, p in fake_reg.events if p["type"] == "state_changed"]) == 1


# ── always-settle-blocked robustness (the board never stays agent_working) ───────


async def test_run_unexpected_exception_marks_blocked(db_session, monkeypatch):
    version = _make_version(db_session)
    _seed_working_state(db_session, version.id)
    fake_reg = _wire_runner(db_session, monkeypatch)

    async def boom(db, vid, on_event=None, directive=None, *, on_message=None):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(orchestrator, "run_dispatch", boom)

    await pipeline_runner._run(version.id)

    state = db_session.execute(select(PipelineState).where(PipelineState.version_id == version.id)).scalar_one()
    assert state.status == "blocked"  # never stuck at agent_working
    sys_msgs = (
        db_session.execute(
            select(PipelineMessage).where(PipelineMessage.version_id == version.id, PipelineMessage.author == "system")
        )
        .scalars()
        .all()
    )
    assert len(sys_msgs) == 1
    assert any(p["type"] == "state_changed" for _, p in fake_reg.events)


async def test_crash_after_message_keeps_it_and_settles_blocked(db_session, monkeypatch):
    """Incremental persistence: a mid-round crash AFTER a message streamed leaves that message persisted,
    settles blocked, and broadcasts the blocked message."""
    version = _make_version(db_session)
    _seed_working_state(db_session, version.id)
    fake_reg = _wire_runner(db_session, monkeypatch)

    async def fake_run_dispatch(db, vid, on_event=None, directive=None, *, on_message=None):
        msg = orchestrator._record_message(
            db,
            version_id=vid,
            stage="programovanie",
            author="ai_agent",
            recipient="manazer",
            kind="gate_report",
            content="partial",
        )
        await on_message(msg)  # streamed + committed before the crash
        raise RuntimeError("boom mid-round")

    monkeypatch.setattr(orchestrator, "run_dispatch", fake_run_dispatch)

    await pipeline_runner._run(version.id)

    state = db_session.execute(select(PipelineState).where(PipelineState.version_id == version.id)).scalar_one()
    assert state.status == "blocked"  # always-settle, never agent_working
    msgs = db_session.execute(select(PipelineMessage).where(PipelineMessage.version_id == version.id)).scalars().all()
    assert any(m.author == "ai_agent" and m.content == "partial" for m in msgs)  # pre-crash message kept
    assert any(m.author == "system" for m in msgs)  # blocked notification recorded
    added = [p["message"] for _, p in fake_reg.events if p["type"] == "message_added"]
    assert any(m["author"] == "ai_agent" for m in added)  # streamed incrementally
    assert any(m["author"] == "system" for m in added)  # crash-path blocked message broadcast once


# ── single-flight per version (CR-NS-027) ───────────────────────────────────────


async def test_schedule_dispatch_single_flight_per_version(monkeypatch):
    """While one _run is in-flight for a version, a 2nd schedule_dispatch for the SAME version is skipped —
    never a 2nd concurrent loop. The per-version entry clears when the task completes."""
    import asyncio

    vid = uuid.uuid4()
    started = {"n": 0}
    gate = asyncio.Event()

    async def _fake_run(version_id, directive=None):
        started["n"] += 1
        await gate.wait()  # hold the task in-flight

    monkeypatch.setattr(pipeline_runner, "_run", _fake_run)
    pipeline_runner._ACTIVE_DISPATCH.pop(vid, None)  # clean slate (module-global)

    pipeline_runner.schedule_dispatch(vid)
    await asyncio.sleep(0)  # let the first task start + await the gate
    task = pipeline_runner._ACTIVE_DISPATCH.get(vid)
    assert task is not None and started["n"] == 1

    pipeline_runner.schedule_dispatch(vid)  # duplicate while in-flight
    await asyncio.sleep(0)
    assert started["n"] == 1  # the 2nd call was skipped — no 2nd loop
    assert pipeline_runner._ACTIVE_DISPATCH.get(vid) is task  # still the same in-flight task

    gate.set()
    await task
    await asyncio.sleep(0)  # drain the done-callbacks
    assert vid not in pipeline_runner._ACTIVE_DISPATCH  # cleared on completion → a fresh dispatch is allowed


# ── dispatch-flag cleanup backstop ──────────────────────────────────────────────


def test_clear_dispatch_flags_clears_when_set(db_session, monkeypatch):
    version = _make_version(db_session)
    state = _seed_working_state(db_session, version.id)
    state.dispatch_in_flight = True
    state.dispatch_baseline_sha = "b" * 40
    db_session.flush()
    monkeypatch.setattr(db_session, "commit", db_session.flush)  # SAVEPOINT-safe

    pipeline_runner._clear_dispatch_flags(db_session, version.id)

    db_session.refresh(state)
    assert state.dispatch_in_flight is False
    assert state.dispatch_baseline_sha is None


def test_clear_dispatch_flags_missing_state_is_safe(db_session):
    """Seam #3: a missing PipelineState row (version deleted mid-flight) must not crash."""
    pipeline_runner._clear_dispatch_flags(db_session, uuid.uuid4())  # no raise


async def test_run_clears_durable_flag_on_settle(db_session, monkeypatch):
    """After a dispatch settles, _run leaves dispatch_in_flight cleared + the baseline reset — even when the
    settle bypassed the ORM status listener (a bulk UPDATE), exercising the runner backstop."""
    version = _make_version(db_session)
    state = _seed_working_state(db_session, version.id)
    state.dispatch_in_flight = True
    state.dispatch_baseline_sha = "b" * 40
    db_session.flush()
    _wire_runner(db_session, monkeypatch)

    async def fake_run_dispatch(db, vid, on_event=None, directive=None, *, on_message=None):
        # bulk UPDATE bypasses the ORM 'set' listener → the runner backstop is what must clear the flag.
        db.execute(_update(PipelineState).where(PipelineState.version_id == vid).values(status="awaiting_manazer"))
        return db.execute(select(PipelineState).where(PipelineState.version_id == vid)).scalar_one()

    monkeypatch.setattr(orchestrator, "run_dispatch", fake_run_dispatch)

    await pipeline_runner._run(version.id)

    settled = db_session.execute(select(PipelineState).where(PipelineState.version_id == version.id)).scalar_one()
    assert settled.dispatch_in_flight is False
    assert settled.dispatch_baseline_sha is None
