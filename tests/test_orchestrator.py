"""Tests for the pipeline orchestrator engine (CR-NS-018 Phase 2).

Live claude is replaced by a controllable fake ``invoke_claude`` — the engine
logic (session resolution, message writes, state transitions, FAIL loop,
verify retries) is exercised against synthetic §5.3 blocks.
"""

import json
import uuid

import pytest
from sqlalchemy import select

from backend.db.models.foundation import User
from backend.db.models.orchestrator import OrchestratorSession
from backend.db.models.pipeline import PipelineMessage, PipelineState
from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.services import orchestrator
from backend.services.pipeline_status import ParseFailure, PipelineStatusBlock


def _block(stage="gate_a", kind="gate_report", summary="ok", awaiting="director", **extra) -> str:
    body = {"stage": stage, "kind": kind, "summary": summary, "awaiting": awaiting}
    body.update(extra)
    return f"<<<PIPELINE_STATUS>>>\n{json.dumps(body)}\n<<<END_PIPELINE_STATUS>>>"


def _make_version(db_session):
    user = User(
        username=f"u_{uuid.uuid4().hex[:8]}",
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        password_hash="hashed_password_placeholder",
        role="ri",
    )
    db_session.add(user)
    db_session.flush()
    project = Project(
        name=f"P {uuid.uuid4().hex[:8]}",
        slug=f"p-{uuid.uuid4().hex[:8]}",
        category="singlemodule",
        description="d",
        created_by=user.id,
    )
    db_session.add(project)
    db_session.flush()
    version = Version(project_id=project.id, version_number=f"1.{uuid.uuid4().hex[:4]}.0")
    db_session.add(version)
    db_session.flush()
    return version, project


class FakeClaude:
    """Controllable async stand-in for ``invoke_claude``. Default: gate_report."""

    def __init__(self):
        self.response = _block()
        self.calls = []

    async def __call__(self, *, project_slug, claude_session_id, prompt, charter_path=None, timeout=180, on_event=None):
        self.calls.append({"project_slug": project_slug, "session": claude_session_id, "prompt": prompt})
        return self.response


@pytest.fixture
def fake_claude(monkeypatch):
    fake = FakeClaude()
    monkeypatch.setattr(orchestrator, "invoke_claude", fake)
    # Mechanical verify is filesystem/git — neutralise to "pass" by default.
    monkeypatch.setattr(orchestrator, "verify_mechanical", lambda slug, block: None)
    return fake


def _msgs(db_session, version_id):
    return db_session.execute(select(PipelineMessage).where(PipelineMessage.version_id == version_id)).scalars().all()


def _settle(db_session, version_id, status="awaiting_director"):
    """Mark the pipeline as settled (agent done) so a Director advancing action is
    valid — the status guard rejects acting while ``agent_working`` (CR-NS-018)."""
    st = orchestrator._get_state(db_session, version_id)
    st.status = status
    db_session.flush()
    return st


# ── session resolution ────────────────────────────────────────────────────────


def test_resolve_orch_session_creates_then_reuses(db_session):
    version, project = _make_version(db_session)
    sid1, first1 = orchestrator._resolve_orch_session(db_session, project.slug, "designer")
    assert first1 is True
    sid2, first2 = orchestrator._resolve_orch_session(db_session, project.slug, "designer")
    assert first2 is False
    assert sid1 == sid2
    rows = (
        db_session.execute(select(OrchestratorSession).where(OrchestratorSession.project_slug == project.slug))
        .scalars()
        .all()
    )
    assert len(rows) == 1


# ── invoke_agent ──────────────────────────────────────────────────────────────


async def test_invoke_agent_records_message(db_session, fake_claude):
    version, _ = _make_version(db_session)
    fake_claude.response = _block(stage="gate_b", kind="gate_report", summary="14 endpoints", commits=["abc123"])
    result = await orchestrator.invoke_agent(
        db_session, version_id=version.id, role="designer", stage="gate_b", prompt="go"
    )
    assert isinstance(result, PipelineStatusBlock)
    msgs = _msgs(db_session, version.id)
    assert len(msgs) == 1
    assert msgs[0].author == "designer"
    assert msgs[0].kind == "gate_report"
    assert msgs[0].payload["commits"] == ["abc123"]


async def test_invoke_agent_parse_failure_escalates(db_session, fake_claude):
    version, _ = _make_version(db_session)
    fake_claude.response = "no status block here"
    result = await orchestrator.invoke_agent(
        db_session, version_id=version.id, role="designer", stage="gate_a", prompt="go"
    )
    assert isinstance(result, ParseFailure)
    msgs = _msgs(db_session, version.id)
    assert len(msgs) == 1
    assert msgs[0].author == "system"
    assert msgs[0].kind == "notification"


# ── apply_action ──────────────────────────────────────────────────────────────


async def test_start_returns_working_without_running_agent(db_session, fake_claude):
    """Async dispatch: ``start`` returns instantly in ``agent_working`` and does
    NOT invoke claude in-request (the agent runs later via ``run_dispatch``)."""
    version, _ = _make_version(db_session)
    fake_claude.response = _block(stage="kickoff", kind="done", summary="discovery ok", awaiting="director")
    state = await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    assert state.current_stage == "kickoff"
    assert state.status == "agent_working"
    # only the director kickoff message; no agent invocation happened in-request.
    assert [m.author for m in _msgs(db_session, version.id)] == ["director"]
    assert fake_claude.calls == []
    # double start rejected
    with pytest.raises(orchestrator.OrchestratorError):
        await orchestrator.apply_action(db_session, version_id=version.id, action="start")


async def test_run_dispatch_settles_awaiting(db_session, fake_claude):
    version, _ = _make_version(db_session)
    fake_claude.response = _block(stage="kickoff", kind="done", summary="discovery ok", awaiting="director")
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    state = await orchestrator.run_dispatch(db_session, version.id)
    assert state.status == "awaiting_director"
    assert fake_claude.calls  # agent invoked in the background dispatch
    assert any(m.author == "coordinator" for m in _msgs(db_session, version.id))


async def test_run_dispatch_claude_error_blocks(db_session, monkeypatch):
    async def _boom(**kwargs):
        raise orchestrator.ClaudeAgentError("timed out after 900s")

    monkeypatch.setattr(orchestrator, "invoke_claude", _boom)
    monkeypatch.setattr(orchestrator, "verify_mechanical", lambda slug, block: None)
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    state = await orchestrator.run_dispatch(db_session, version.id)
    assert state.status == "blocked"
    assert any(m.author == "system" and m.kind == "notification" for m in _msgs(db_session, version.id))


async def test_run_dispatch_unparseable_blocks(db_session, fake_claude):
    version, _ = _make_version(db_session)
    fake_claude.response = "garbage — no status block"
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    state = await orchestrator.run_dispatch(db_session, version.id)
    assert state.status == "blocked"


async def test_approve_advances_stage(db_session, fake_claude):
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _settle(db_session, version.id)
    state = await orchestrator.apply_action(db_session, version_id=version.id, action="approve")
    assert state.current_stage == "gate_a"
    assert state.current_actor == "designer"


async def test_return_requires_comment(db_session, fake_claude):
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _settle(db_session, version.id)
    with pytest.raises(orchestrator.OrchestratorError):
        await orchestrator.apply_action(db_session, version_id=version.id, action="return", payload={})
    state = await orchestrator.apply_action(
        db_session, version_id=version.id, action="return", payload={"comment": "doplň X"}
    )
    assert state is not None


async def test_agent_question_blocks(db_session, fake_claude):
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _settle(db_session, version.id)
    fake_claude.response = _block(stage="gate_a", kind="blocked", summary="ctx", question="Ktorý port?")
    await orchestrator.apply_action(db_session, version_id=version.id, action="approve")
    state = await orchestrator.run_dispatch(db_session, version.id)
    assert state.status == "blocked"
    assert "Ktorý port" in state.next_action


async def test_verdict_pass_to_release(db_session, fake_claude):
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _settle(db_session, version.id)
    state = await orchestrator.apply_action(
        db_session, version_id=version.id, action="verdict", payload={"verdict": "PASS"}
    )
    assert state.current_stage == "release"


async def test_verdict_fail_regate(db_session, fake_claude):
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _settle(db_session, version.id)
    state = await orchestrator.apply_action(
        db_session,
        version_id=version.id,
        action="verdict",
        payload={"verdict": "FAIL", "entry_stage": "build"},
    )
    assert state.is_regate is True
    assert state.iteration == 1
    assert state.current_stage == "build"


async def test_uat_accept_done(db_session, fake_claude):
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _settle(db_session, version.id)
    state = await orchestrator.apply_action(db_session, version_id=version.id, action="uat_accept")
    assert state.current_stage == "done"
    assert state.status == "done"
    assert any(m.kind == "notification" for m in _msgs(db_session, version.id))


async def test_pause_freezes(db_session, fake_claude):
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    before = db_session.execute(select(PipelineState).where(PipelineState.version_id == version.id)).scalar_one()
    prev_status = before.status
    state = await orchestrator.apply_action(db_session, version_id=version.id, action="pause")
    assert state.status == prev_status
    assert "Pozastavené" in state.next_action


# ── verify retries → blocked ──────────────────────────────────────────────────


async def test_verify_failure_retries_then_blocks(db_session, monkeypatch):
    fake = FakeClaude()  # always gate_report
    monkeypatch.setattr(orchestrator, "invoke_claude", fake)
    # Mechanical verify always fails → exhaust retries → blocked.
    monkeypatch.setattr(orchestrator, "verify_mechanical", lambda slug, block: "deliverable missing")

    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _settle(db_session, version.id)
    await orchestrator.apply_action(db_session, version_id=version.id, action="approve")
    state = await orchestrator.run_dispatch(db_session, version.id)
    assert state.status == "blocked"
    assert "Verify zlyhal" in state.next_action
    # auto-return messages were recorded
    returns = [m for m in _msgs(db_session, version.id) if m.kind == "return" and m.author == "system"]
    assert len(returns) >= 1


# ── directive threading (CR-NS-018: Director's return/answer/ask reaches agent) ─


def test_directive_for_action_frames_interactive_actions():
    assert orchestrator.directive_for_action("return", {"comment": "Zlaď X"}, "gate_a") == (
        "Director ťa vrátil na opravu fázy 'gate_a': Zlaď X"
    )
    assert orchestrator.directive_for_action("answer", {"text": "Schvaľujem"}, "gate_b") == (
        "Director odpovedal na tvoju otázku: Schvaľujem"
    )
    assert orchestrator.directive_for_action("ask", {"text": "Ktorý port?"}, "gate_c") == (
        "Director sa pýta: Ktorý port?"
    )


def test_directive_for_action_fresh_stage_is_none():
    # start / approve / verdict are fresh-stage dispatches → generic directive.
    assert orchestrator.directive_for_action("start", {}, "kickoff") is None
    assert orchestrator.directive_for_action("approve", {"comment": "ok"}, "gate_a") is None
    assert orchestrator.directive_for_action("verdict", {"verdict": "PASS"}, "gate_g") is None
    # empty/whitespace content → None (defensive; apply_action already rejects it).
    assert orchestrator.directive_for_action("return", {"comment": "   "}, "gate_a") is None
    assert orchestrator.directive_for_action("ask", {}, "gate_a") is None


async def test_run_dispatch_threads_directive_as_prompt(db_session, fake_claude):
    """When a directive is supplied it IS the agent prompt (not the generic one)."""
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    fake_claude.response = _block(stage="kickoff", kind="blocked", summary="x", question="?")
    await orchestrator.run_dispatch(
        db_session, version.id, directive="Director ťa vrátil na opravu fázy 'kickoff': oprav súčet"
    )
    assert fake_claude.calls[-1]["prompt"] == "Director ťa vrátil na opravu fázy 'kickoff': oprav súčet"


async def test_run_dispatch_generic_directive_without_override(db_session, fake_claude):
    """Without a directive the agent gets the generic per-stage directive."""
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    fake_claude.response = _block(stage="kickoff", kind="blocked", summary="x", question="?")
    await orchestrator.run_dispatch(db_session, version.id)
    assert "Pokračuj fázou 'kickoff'" in fake_claude.calls[-1]["prompt"]


# ── parse-failure auto-retry (CR-NS-018: a single JSON typo must not halt) ──────


class SequenceClaude:
    """Async ``invoke_claude`` stand-in that returns a fixed sequence of outputs
    (last one repeats once exhausted) and records every prompt it was given."""

    def __init__(self, responses: list[str]):
        self.responses = responses
        self.prompts: list[str] = []

    async def __call__(self, *, prompt, **kwargs):
        idx = min(len(self.prompts), len(self.responses) - 1)
        self.prompts.append(prompt)
        return self.responses[idx]


async def test_run_dispatch_parse_retry_recovers(db_session, monkeypatch):
    """ParseFailure then a valid block on retry → the pipeline proceeds."""
    fake = SequenceClaude(
        [
            "garbage — not a valid status block",  # invalid JSON → ParseFailure
            _block(stage="kickoff", kind="done", summary="discovery ok", awaiting="director"),
        ]
    )
    monkeypatch.setattr(orchestrator, "invoke_claude", fake)
    monkeypatch.setattr(orchestrator, "verify_mechanical", lambda slug, block: None)

    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    state = await orchestrator.run_dispatch(db_session, version.id)

    assert state.status == "awaiting_director"
    # the retry prompt fed the JSON error back to the agent
    assert any("nebol platný JSON" in p for p in fake.prompts)
    assert len(fake.prompts) == 2  # primary + one recovery re-emit


async def test_run_dispatch_parse_retry_exhausted_blocks(db_session, monkeypatch):
    """Still-invalid after ``_PARSE_RETRIES`` → blocked + system notification."""
    fake = SequenceClaude(["still no valid block here"])  # always invalid
    monkeypatch.setattr(orchestrator, "invoke_claude", fake)
    monkeypatch.setattr(orchestrator, "verify_mechanical", lambda slug, block: None)

    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    state = await orchestrator.run_dispatch(db_session, version.id)

    assert state.status == "blocked"
    # primary + _PARSE_RETRIES re-invokes
    assert len(fake.prompts) == 1 + orchestrator._PARSE_RETRIES
    # every failed parse was escalated as a system→director notification
    notifs = [m for m in _msgs(db_session, version.id) if m.author == "system" and m.kind == "notification"]
    assert len(notifs) >= 1


# ── apply_coordinator_recommendation (CR-NS-018: one-click accept Coordinator fix) ─


def _seed_coordinator_report(db_session, version_id, content, *, created_at=None):
    """Add one coordinator gate_report message (optionally with explicit created_at)."""
    kwargs = dict(
        version_id=version_id,
        stage="gate_a",
        author="coordinator",
        recipient="director",
        kind="gate_report",
        content=content,
    )
    if created_at is not None:
        kwargs["created_at"] = created_at
    db_session.add(PipelineMessage(**kwargs))
    db_session.flush()


def test_latest_coordinator_report_picks_most_recent(db_session):
    from datetime import datetime, timedelta, timezone

    version, _ = _make_version(db_session)
    base = datetime(2026, 6, 5, 10, 0, 0, tzinfo=timezone.utc)
    _seed_coordinator_report(db_session, version.id, "prvé", created_at=base)
    _seed_coordinator_report(db_session, version.id, "druhé", created_at=base + timedelta(minutes=1))
    # a designer report at the same instant must be ignored (author-filtered)
    db_session.add(
        PipelineMessage(
            version_id=version.id,
            stage="gate_a",
            author="designer",
            recipient="director",
            kind="gate_report",
            content="designer",
            created_at=base + timedelta(minutes=2),
        )
    )
    db_session.flush()
    assert orchestrator.latest_coordinator_report(db_session, version.id) == "druhé"


def test_dispatch_directive_routes_by_action(db_session):
    version, _ = _make_version(db_session)
    # payload-based delegates to directive_for_action
    assert "Zlaď X" in orchestrator.dispatch_directive(
        db_session, version.id, "return", {"comment": "Zlaď X"}, "gate_a"
    )
    assert orchestrator.dispatch_directive(db_session, version.id, "approve", {}, "gate_a") is None
    # coordinator action with no report → None
    assert (
        orchestrator.dispatch_directive(db_session, version.id, "apply_coordinator_recommendation", {}, "gate_a")
        is None
    )
    # with a report → framed directive carrying its content
    _seed_coordinator_report(db_session, version.id, "odporúčania X")
    framed = orchestrator.dispatch_directive(db_session, version.id, "apply_coordinator_recommendation", {}, "gate_a")
    assert "odporúčania X" in framed and "Koordinátora" in framed


async def test_apply_coordinator_recommendation_redispatches_with_report(db_session, fake_claude):
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _seed_coordinator_report(db_session, version.id, "Oprav súčet DPH na riadku 3.")
    # move to a designer gate awaiting the Director
    state = orchestrator._get_state(db_session, version.id)
    state.current_stage = "gate_a"
    state.current_actor = "designer"
    state.status = "awaiting_director"
    db_session.flush()

    state = await orchestrator.apply_action(
        db_session, version_id=version.id, action="apply_coordinator_recommendation"
    )
    assert state.status == "agent_working"
    assert state.current_stage == "gate_a"  # stage does NOT advance

    directive = orchestrator.dispatch_directive(
        db_session, version.id, "apply_coordinator_recommendation", {}, state.current_stage
    )
    assert "Oprav súčet DPH na riadku 3." in directive
    await orchestrator.run_dispatch(db_session, version.id, directive=directive)
    assert any("Oprav súčet DPH na riadku 3." in c["prompt"] for c in fake_claude.calls)


async def test_apply_coordinator_recommendation_no_report_errors(db_session, fake_claude):
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _settle(db_session, version.id)  # past the status guard → exercise the no-report path
    with pytest.raises(orchestrator.OrchestratorError):
        await orchestrator.apply_action(db_session, version_id=version.id, action="apply_coordinator_recommendation")


# ── worker question routed through the Coordinator (hub-and-spoke, CR-NS-018) ────


def _to_designer_gate(db_session, version):
    """Put the pipeline at a designer gate, agent_working (worker about to run)."""
    state = orchestrator._get_state(db_session, version.id)
    state.current_stage = "gate_a"
    state.current_actor = "designer"
    state.status = "agent_working"
    db_session.flush()
    return state


async def test_worker_question_routed_through_coordinator(db_session, monkeypatch):
    seq = SequenceClaude(
        [
            _block(stage="gate_a", kind="blocked", summary="potrebujem rozhodnutie", question="Ktorý formát dátumu?"),
            _block(stage="gate_a", kind="question", summary="relay", question="Návrhár potrebuje formát dátumu — ISO?"),
        ]
    )
    monkeypatch.setattr(orchestrator, "invoke_claude", seq)
    monkeypatch.setattr(orchestrator, "verify_mechanical", lambda slug, block: None)

    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _to_designer_gate(db_session, version)
    state = await orchestrator.run_dispatch(db_session, version.id)

    assert state.status == "blocked"
    # worker invoked, then the Coordinator reviewed (2 invocations)
    assert len(seq.prompts) == 2
    # the Coordinator's relay is recorded as a thread message…
    msgs = _msgs(db_session, version.id)
    assert any(m.author == "coordinator" for m in msgs)
    # …and the relay text is surfaced
    assert "formát dátumu" in state.next_action


async def test_coordinator_own_question_not_double_reviewed(db_session, fake_claude):
    # kickoff actor IS the coordinator → surface directly, no relay invocation
    fake_claude.response = _block(stage="kickoff", kind="blocked", summary="ctx", question="Ktorý port?")
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    state = await orchestrator.run_dispatch(db_session, version.id)

    assert state.status == "blocked"
    assert len(fake_claude.calls) == 1  # only the coordinator itself, no review pass
    assert "Ktorý port?" in state.next_action


async def test_coordinator_relay_unparseable_falls_back_to_worker_question(db_session, monkeypatch):
    seq = SequenceClaude(
        [
            _block(stage="gate_a", kind="blocked", summary="x", question="Ktorý formát?"),
            "garbage — no status block",  # relay primary
            "garbage — no status block",  # relay retry 1
            "garbage — no status block",  # relay retry 2
        ]
    )
    monkeypatch.setattr(orchestrator, "invoke_claude", seq)
    monkeypatch.setattr(orchestrator, "verify_mechanical", lambda slug, block: None)

    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _to_designer_gate(db_session, version)
    state = await orchestrator.run_dispatch(db_session, version.id)

    assert state.status == "blocked"
    # relay unparseable → fall back to the worker's original question (no dead-end)
    assert "Ktorý formát?" in state.next_action


async def test_answer_after_routed_question_reaches_worker(db_session, fake_claude):
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    state = orchestrator._get_state(db_session, version.id)
    state.current_stage = "gate_a"
    state.current_actor = "designer"
    state.status = "blocked"
    db_session.flush()

    await orchestrator.apply_action(
        db_session, version_id=version.id, action="answer", payload={"text": "Použi ISO 8601"}
    )
    directive = orchestrator.dispatch_directive(db_session, version.id, "answer", {"text": "Použi ISO 8601"}, "gate_a")
    assert "Použi ISO 8601" in directive
    # the answer message is addressed to the worker (designer), not the coordinator
    answer_msg = [m for m in _msgs(db_session, version.id) if m.kind == "answer"][-1]
    assert answer_msg.recipient == "designer"


# ── status guard: never act on / advance past a working agent (CR-NS-018) ───────


@pytest.mark.parametrize(
    "action,payload",
    [
        ("approve", {}),
        ("verdict", {"verdict": "PASS"}),
        ("uat_accept", {}),
        ("return", {"comment": "x"}),
        ("apply_coordinator_recommendation", {}),
    ],
)
async def test_advancing_actions_rejected_while_agent_working(db_session, fake_claude, action, payload):
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")  # agent_working
    with pytest.raises(orchestrator.OrchestratorError):
        await orchestrator.apply_action(db_session, version_id=version.id, action=action, payload=payload)
    # no advance / mutation happened
    st = orchestrator._get_state(db_session, version.id)
    assert st.current_stage == "kickoff"
    assert st.status == "agent_working"


async def test_answer_rejected_when_not_blocked(db_session, fake_claude):
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")  # agent_working
    with pytest.raises(orchestrator.OrchestratorError):
        await orchestrator.apply_action(db_session, version_id=version.id, action="answer", payload={"text": "x"})
    _settle(db_session, version.id, status="awaiting_director")  # still not a question
    with pytest.raises(orchestrator.OrchestratorError):
        await orchestrator.apply_action(db_session, version_id=version.id, action="answer", payload={"text": "x"})


async def test_advancing_action_allowed_when_blocked(db_session, fake_claude):
    # the intentional ratify-out-of-a-question case: approve/return work from blocked
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _settle(db_session, version.id, status="blocked")
    state = await orchestrator.apply_action(db_session, version_id=version.id, action="approve")
    assert state.current_stage == "gate_a"


# ── Gate E orchestrator loop (F-007-gate-e §2/§3/§5, CR-NS-018 Phase 2) ──────────


def _to_gate_e(db_session, version):
    """Put the pipeline at gate_e / customer / agent_working (loop entry)."""
    state = orchestrator._get_state(db_session, version.id)
    state.current_stage = "gate_e"
    state.current_actor = "customer"
    state.status = "agent_working"
    db_session.flush()
    return state


class GateELoopClaude:
    """invoke_claude stand-in for the gate_e loop: Customer always asks, Designer
    always answers (distinguished by the Designer-relay prompt) → never converges,
    so the round exhausts its exchange budget. Records every prompt."""

    def __init__(self):
        self.prompts: list[str] = []

    async def __call__(self, *, prompt, **kwargs):
        self.prompts.append(prompt)
        if "Zákazník vo fáze Gate E sa pýta" in prompt:  # Designer turn
            return _block(stage="gate_e", kind="answer", summary="vysvetlené, pokryté", awaiting="none")
        return _block(stage="gate_e", kind="question", summary="?", question="ďalšia otázka?")  # Customer asks


async def test_gate_e_loop_routes_question_then_settles_at_boundary(db_session, monkeypatch):
    seq = SequenceClaude(
        [
            _block(stage="gate_e", kind="question", summary="?", question="Ako sa rieši reset hesla?"),
            _block(stage="gate_e", kind="answer", summary="Reset cez email, pokryté v §4.2", awaiting="none"),
            _block(
                stage="gate_e",
                kind="gate_report",
                summary="okruh prihlásenie hotový",
                awaiting="director",
                topic="prihlasenie",
                topic_done=True,
                findings=["reset hesla pokrytý"],
            ),
        ]
    )
    monkeypatch.setattr(orchestrator, "invoke_claude", seq)
    monkeypatch.setattr(orchestrator, "verify_mechanical", lambda slug, block: None)

    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _to_gate_e(db_session, version)
    state = await orchestrator.run_dispatch(db_session, version.id)

    assert state.status == "awaiting_director"
    assert state.current_stage == "gate_e"  # stays in gate_e at a topic boundary
    assert len(seq.prompts) == 3  # customer → designer → customer
    msgs = _msgs(db_session, version.id)
    assert any(m.author == "customer" for m in msgs)
    assert any(m.author == "designer" for m in msgs)
    # the Designer was asked the Customer's question
    assert any("reset hesla" in p.lower() for p in seq.prompts)
    assert "prihlasenie" in state.next_action


async def test_gate_e_needs_director_decision_pauses_mid_round(db_session, monkeypatch):
    seq = SequenceClaude(
        [
            _block(
                stage="gate_e",
                kind="blocked",
                summary="politika hesiel",
                awaiting="director",
                question="Vynútiť zmenu hesla pri prvom prihlásení?",
                needs_director_decision=True,
            ),
        ]
    )
    monkeypatch.setattr(orchestrator, "invoke_claude", seq)
    monkeypatch.setattr(orchestrator, "verify_mechanical", lambda slug, block: None)

    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _to_gate_e(db_session, version)
    state = await orchestrator.run_dispatch(db_session, version.id)

    assert state.status == "blocked"
    assert len(seq.prompts) == 1  # paused immediately, no Designer routing
    assert "rozhodnutie Directora" in state.next_action


async def test_gate_e_round_exhaustion_blocks(db_session, monkeypatch):
    fake = GateELoopClaude()
    monkeypatch.setattr(orchestrator, "invoke_claude", fake)
    monkeypatch.setattr(orchestrator, "verify_mechanical", lambda slug, block: None)

    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _to_gate_e(db_session, version)
    state = await orchestrator.run_dispatch(db_session, version.id)

    assert state.status == "blocked"
    assert "limit výmen" in state.next_action
    # customer + designer per iteration, for _GATE_E_MAX_EXCHANGES iterations
    assert len(fake.prompts) == 2 * orchestrator._GATE_E_MAX_EXCHANGES


async def test_gate_e_designer_parse_failure_blocks(db_session, monkeypatch):
    seq = SequenceClaude(
        [
            _block(stage="gate_e", kind="question", summary="?", question="Otázka pre Návrhára?"),
            "garbage — no status block",  # designer relay primary
            "garbage — no status block",  # retry 1
            "garbage — no status block",  # retry 2
        ]
    )
    monkeypatch.setattr(orchestrator, "invoke_claude", seq)
    monkeypatch.setattr(orchestrator, "verify_mechanical", lambda slug, block: None)

    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _to_gate_e(db_session, version)
    state = await orchestrator.run_dispatch(db_session, version.id)

    assert state.status == "blocked"  # unparseable Designer turn → escalate, never guess
