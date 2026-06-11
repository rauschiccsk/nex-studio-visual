"""Tests for the pipeline orchestrator engine (CR-NS-018 Phase 2).

Live claude is replaced by a controllable fake ``invoke_claude`` — the engine
logic (session resolution, message writes, state transitions, FAIL loop,
verify retries) is exercised against synthetic §5.3 blocks.
"""

import json
import uuid

import pytest
from sqlalchemy import select, update

from backend.db.models.foundation import User
from backend.db.models.orchestrator import OrchestratorSession
from backend.db.models.pipeline import PipelineMessage, PipelineState
from backend.db.models.projects import Project
from backend.db.models.tasks import Epic, Feat, Task
from backend.db.models.versions import Version
from backend.services import claude_agent, orchestrator
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
        # ``response`` may be a callable(prompt)->str so a single fake can answer different roles
        # (e.g. Programmer vs Auditor in the CR-NS-020 build loop); else it's a fixed string.
        return self.response(prompt) if callable(self.response) else self.response


@pytest.fixture
def fake_claude(monkeypatch):
    fake = FakeClaude()
    monkeypatch.setattr(orchestrator, "invoke_claude", fake)
    # Mechanical verify is filesystem/git — neutralise to "pass" by default.
    # (signature gained ``baseline_sha`` in CR-NS-020 CR-3 for the per-task diff scope.)
    monkeypatch.setattr(orchestrator, "verify_mechanical", lambda slug, block, baseline_sha=None: None)
    return fake


def _msgs(db_session, version_id):
    # Order by seq (insertion order) like every production query — created_at ties within a
    # transaction (func.now() is constant), so order-dependent assertions need the seq tie-break.
    return (
        db_session.execute(
            select(PipelineMessage).where(PipelineMessage.version_id == version_id).order_by(PipelineMessage.seq.asc())
        )
        .scalars()
        .all()
    )


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


async def test_invoke_agent_parse_failure_is_silent(db_session, fake_claude):
    # CR-NS-022 §2: invoke_agent no longer records a raw system→director dump on a parse failure —
    # it returns the ParseFailure for the dispatch layer to relay (via the Coordinator) only on the
    # FINAL, unrecovered failure. A single invoke records NO Director-facing message (no leak).
    version, _ = _make_version(db_session)
    fake_claude.response = "no status block here"
    result = await orchestrator.invoke_agent(
        db_session, version_id=version.id, role="designer", stage="gate_a", prompt="go"
    )
    assert isinstance(result, ParseFailure)
    assert _msgs(db_session, version.id) == []  # no raw escalation leaked to the Director


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


async def test_pause_at_build_sets_paused(db_session, fake_claude):
    # CR-NS-027: pause at build/agent_working sets a genuine 'paused' status (not just a next_action
    # label) so the build loop stops at its next task boundary; leaving agent_working also stops the
    # action route from re-dispatching (the no-op-pause bug that spawned a 2nd loop).
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _to_build(db_session, version)  # build / agent_working
    state = await orchestrator.apply_action(db_session, version_id=version.id, action="pause")
    assert state.status == "paused"
    assert "Pozastavené" in state.next_action


async def test_pause_rejected_outside_build(db_session, fake_claude):
    # CR-NS-027 (decision A): pause is build-only — a single-turn gate has no cooperative boundary.
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")  # kickoff / agent_working
    with pytest.raises(orchestrator.OrchestratorError):
        await orchestrator.apply_action(db_session, version_id=version.id, action="pause")
    # also at a gate (any non-build stage) — the build-only guard blocks it universally
    st = orchestrator._get_state(db_session, version.id)
    st.current_stage = "gate_a"
    st.current_actor = "designer"
    st.status = "agent_working"
    db_session.flush()
    with pytest.raises(orchestrator.OrchestratorError):
        await orchestrator.apply_action(db_session, version_id=version.id, action="pause")
    st = orchestrator._get_state(db_session, version.id)
    assert st.status == "agent_working"  # unchanged


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
    assert "neprešla overením" in state.next_action  # plain next_action (CR-NS-022 §2 — no raw reason)
    # auto-return messages were recorded (INTERNAL system→worker, unchanged)
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
    # the retry prompt fed the failure reason back to the agent (CR-NS-029: assert the stable re-prompt
    # prefix, robust to the reason text — 35d1a28 rewrote the re-prompt away from the old "nebol platný JSON").
    assert any("sa nepodarilo spracovať" in p for p in fake.prompts)
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
    # worker (1 + _PARSE_RETRIES) + the Coordinator relay attempt (1 + _PARSE_RETRIES) — CR-NS-022 §2
    # routes the final failure via the Coordinator instead of a raw invoke_agent dump.
    assert len(fake.prompts) == 2 * (1 + orchestrator._PARSE_RETRIES)
    # the relay's Coordinator also couldn't parse → a plain system→director fallback note surfaces
    notifs = [
        m
        for m in _msgs(db_session, version.id)
        if m.author == "system" and m.kind == "notification" and m.recipient == "director"
    ]
    assert len(notifs) >= 1
    # CR-NS-022 (fix): even the fallback must NOT leak the raw reason — plain Slovak only.
    fb = notifs[-1].content
    assert "podrobnosti sú v zázname" in fb  # plain user-facing phrase
    assert "PIPELINE_STATUS" not in fb and "no PIPELINE_STATUS block found" not in fb  # no raw dump


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
        ("continue_build", {}),
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


def test_determine_available_actions_matrix():
    # WS-C1 (CR-NS-030): backend-authoritative offerable actions per (stage, status). The function reads
    # only state.current_stage/.status, so a lightweight in-memory PipelineState suffices.
    def acts(stage, status):
        return orchestrator.determine_available_actions(PipelineState(current_stage=stage, status=status))

    # THE live bug: a build-blocked task must NOT offer "approve" (the no-op Designer-gate button)…
    assert "approve" not in acts("build", "blocked")
    # …but it offers the real choices: answer the programmer question, return/continue/end, consult.
    assert {"answer", "return", "continue_build", "end_build", "ask"} <= acts("build", "blocked")
    # a settled build (all tasks done) DOES offer the final sign-off
    assert "approve" in acts("build", "awaiting_director")
    # accept_merged (WS-B2) is offered only at a settled build HALT (awaiting_director), never at a
    # blocked programmer-question (no failed task to recognize there)
    assert "accept_merged" in acts("build", "awaiting_director")
    assert "accept_merged" not in acts("build", "blocked")
    # a gate question DOES offer approve (ratify the blocked-question output → advance)
    assert "approve" in acts("gate_a", "blocked")
    # agent working: only a build can be paused; gates offer nothing
    assert acts("build", "agent_working") == {"pause"}
    assert acts("gate_a", "agent_working") == set()
    # paused: only the resume pair
    assert acts("build", "paused") == {"continue_build", "end_build"}
    # gate_g is a verdict (PASS/FAIL), not an approve; release is uat_accept; done is terminal
    assert "verdict" in acts("gate_g", "awaiting_director") and "approve" not in acts("gate_g", "awaiting_director")
    assert "uat_accept" in acts("release", "awaiting_director")
    assert acts("done", "done") == set()


async def test_build_readiness_reflects_todo_and_failed_tasks(db_session, fake_claude):
    # WS-C1 (CR-NS-030): build_readiness → (all_tasks_done, open_findings), the DB-dependent build
    # facts the FE uses to disable approve/end_build (state-only available_actions can't see them).
    version, project = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _epic, _feat, tasks = _seed_one_feat(db_session, version, project, ["A", "B"])

    # all todo → not done, no open findings (approve disabled; end_build enabled)
    assert orchestrator.build_readiness(db_session, version.id) == (False, 0)

    # all done → ready (approve enabled)
    for t in tasks:
        t.status = "done"
    db_session.flush()
    assert orchestrator.build_readiness(db_session, version.id) == (True, 0)

    # a failed task → no todo remains but an open finding (approve + end_build disabled)
    tasks[0].status = "failed"
    db_session.flush()
    assert orchestrator.build_readiness(db_session, version.id) == (True, 1)


async def test_accept_merged_moves_baseline_to_parent_and_task_repasses(db_session, fake_claude, monkeypatch):
    # WS-B2 (CR-NS-031): a merged task (work in a commit at/before its baseline) dead-ends on
    # "commit predates baseline". accept_merged moves the baseline to the reported commit's PARENT,
    # resets the task to todo, records a director-decision audit message, and the re-dispatched build
    # loop re-verifies it → done. No manual DB edit.
    monkeypatch.setattr(orchestrator, "_repo_head", lambda root: "h" * 40)
    monkeypatch.setattr(orchestrator, "_repo_parent", lambda root, commit: "p" * 40)
    monkeypatch.setattr(orchestrator, "verify_mechanical", lambda slug, block, baseline_sha=None: None)
    version, project = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _epic, _feat, (task,) = _seed_one_feat(db_session, version, project, ["T"])
    # the merged-commit HALT: task failed, baseline = the merged commit, that commit reported by the Programmer
    task.status = "failed"
    task.baseline_sha = "m" * 40
    db_session.flush()
    db_session.add(
        PipelineMessage(
            version_id=version.id,
            stage="build",
            author="implementer",
            recipient="director",
            kind="gate_report",
            content="hotovo (spoločný commit)",
            payload={"task_id": str(task.id), "commits": ["m" * 40]},
        )
    )
    db_session.flush()
    state = _to_build(db_session, version)
    state.status = "awaiting_director"
    db_session.flush()

    state = await orchestrator.apply_action(db_session, version_id=version.id, action="accept_merged")

    db_session.refresh(task)
    assert task.baseline_sha == "p" * 40  # moved to the reported commit's PARENT
    assert task.status == "todo"  # reset → the loop re-verifies
    assert state.status == "agent_working"  # re-dispatched
    audit = [m for m in _msgs(db_session, version.id) if m.kind == "approval" and "spoločný commit" in m.content]
    assert audit and audit[-1].payload.get("new_baseline") == "p" * 40  # director-decision recorded

    # the re-dispatched build loop re-verifies the merged task against the moved baseline → done
    fake_claude.response = _build_fake()
    await orchestrator.run_dispatch(db_session, version.id)
    db_session.refresh(task)
    assert task.status == "done"


async def test_accept_merged_rejected_without_failed_task(db_session, fake_claude):
    # accept_merged needs a failed (merged) task — a clean build offers nothing to recognize.
    version, project = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _seed_one_feat(db_session, version, project, ["T"])  # stays todo, none failed
    state = _to_build(db_session, version)
    state.status = "awaiting_director"
    db_session.flush()
    with pytest.raises(orchestrator.OrchestratorError):
        await orchestrator.apply_action(db_session, version_id=version.id, action="accept_merged")


# ── E7: Coordinator-as-operator — structured directive + executable actions (F-008, CR-NS-032) ─────


def _coord_directive(**over):
    d = {
        "triage_class": "programmer_guidance",
        "proposed_action": "coordinator_reset_task",
        "target": {},
        "params": {},
        "rationale": "r",
        "confidence": 0.9,
    }
    d.update(over)
    return d


def _seed_coordinator_directive(db_session, version_id, directive):
    db_session.add(
        PipelineMessage(
            version_id=version_id,
            stage="build",
            author="coordinator",
            recipient="director",
            kind="gate_report",
            content="Koordinátor: relay",
            payload={"coordinator_directive": directive},
        )
    )
    db_session.flush()


def test_coordinator_directive_executable_gate():
    # F-008 §9: execute only an executable action, non-director_decision, confidence ≥ 0.80; else relay.
    assert orchestrator._coordinator_directive_executable(_coord_directive()) is True
    assert orchestrator._coordinator_directive_executable(_coord_directive(confidence=0.5)) is False
    assert orchestrator._coordinator_directive_executable(_coord_directive(triage_class="director_decision")) is False
    assert orchestrator._coordinator_directive_executable(_coord_directive(proposed_action="relay")) is False
    assert orchestrator._coordinator_directive_executable(None) is False


async def _build_at_halt(db_session, fake_claude):
    version, project = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _epic, _feat, (task,) = _seed_one_feat(db_session, version, project, ["T"])
    task.status = "failed"
    db_session.flush()
    state = _to_build(db_session, version)
    state.status = "awaiting_director"
    db_session.flush()
    return version, project, task


async def test_apply_coordinator_recommendation_executes_reset_task(db_session, fake_claude):
    # F-008 §9 (the no-op fix): an executable directive RUNS its executor (not advisory text).
    version, _project, task = await _build_at_halt(db_session, fake_claude)
    _seed_coordinator_directive(
        db_session,
        version.id,
        _coord_directive(proposed_action="coordinator_reset_task", target={"task_id": str(task.id)}),
    )
    state = await orchestrator.apply_action(
        db_session, version_id=version.id, action="apply_coordinator_recommendation"
    )
    db_session.refresh(task)
    assert task.status == "todo"  # executed (reset), not relayed
    assert state.status == "agent_working"  # re-dispatched
    assert any(
        m.kind == "approval" and "Vykonaný Koordinátorov návrh" in m.content for m in _msgs(db_session, version.id)
    )


async def test_apply_coordinator_recommendation_executes_move_baseline(db_session, fake_claude, monkeypatch):
    monkeypatch.setattr(orchestrator, "_repo_parent", lambda root, commit: "p" * 40)
    version, _project, task = await _build_at_halt(db_session, fake_claude)
    task.baseline_sha = "m" * 40
    db_session.flush()
    _seed_coordinator_directive(
        db_session,
        version.id,
        _coord_directive(
            proposed_action="coordinator_move_baseline", target={"task_id": str(task.id), "commit": "m" * 40}
        ),
    )
    await orchestrator.apply_action(db_session, version_id=version.id, action="apply_coordinator_recommendation")
    db_session.refresh(task)
    assert task.baseline_sha == "p" * 40 and task.status == "todo"  # baseline moved to parent + re-verify


async def test_apply_coordinator_recommendation_relay_when_not_executable(db_session, fake_claude):
    # A director_decision / low-confidence directive is a PURE relay — no execution, advisory approval.
    version, _project, task = await _build_at_halt(db_session, fake_claude)
    _seed_coordinator_directive(
        db_session, version.id, _coord_directive(triage_class="director_decision", proposed_action="relay")
    )
    await orchestrator.apply_action(db_session, version_id=version.id, action="apply_coordinator_recommendation")
    db_session.refresh(task)
    assert task.status == "failed"  # NOT executed — left for the Director to decide
    assert any(m.kind == "approval" and "Schválené odporúčania" in m.content for m in _msgs(db_session, version.id))


async def test_coordinator_clear_session_executor(db_session, fake_claude):
    from backend.db.models.orchestrator import OrchestratorSession

    version, project, _task = await _build_at_halt(db_session, fake_claude)
    db_session.add(OrchestratorSession(project_slug=project.slug, role="designer", claude_session_id=uuid.uuid4()))
    db_session.flush()
    _seed_coordinator_directive(
        db_session,
        version.id,
        _coord_directive(proposed_action="coordinator_clear_session", target={"role": "designer"}),
    )
    await orchestrator.apply_action(db_session, version_id=version.id, action="apply_coordinator_recommendation")
    row = db_session.execute(
        select(OrchestratorSession).where(
            OrchestratorSession.project_slug == project.slug, OrchestratorSession.role == "designer"
        )
    ).scalar_one_or_none()
    assert row is None  # session cleared


async def test_coordinator_escalate_dedo_writes_and_is_non_blocking(db_session, fake_claude, monkeypatch, tmp_path):
    monkeypatch.setattr(orchestrator.claude_agent, "PROJECTS_ROOT", tmp_path)
    version, project, _task = await _build_at_halt(db_session, fake_claude)
    _seed_coordinator_directive(
        db_session,
        version.id,
        _coord_directive(
            triage_class="nex_studio_bug",
            proposed_action="coordinator_escalate_dedo",
            params={"topic": "merged-dead-end"},
        ),
    )
    state = await orchestrator.apply_action(
        db_session, version_id=version.id, action="apply_coordinator_recommendation"
    )
    assert state.status == "awaiting_director"  # non-blocking — NOT re-dispatched
    files = list((tmp_path / project.slug / ".dedo-channel" / "inbox").glob("coordinator-to-dedo-*-escalation.md"))
    assert files and "from: coordinator" in files[0].read_text(encoding="utf-8")


# ── E7 A2: triage charter + emit-directive prompts (F-008 §3, CR-NS-033) ───────────────────────────


def test_coordinator_charter_template_has_triage_section():
    # CR-NS-033: the charter instructs the Coordinator to triage + emit a coordinator_directive.
    from pathlib import Path

    charter = (Path(__file__).resolve().parent.parent / "templates" / "coordinator-charter.md").read_text(
        encoding="utf-8"
    )
    assert "Triage framework" in charter and "coordinator_directive" in charter
    for cls in ("spec_problem", "programmer_guidance", "nex_studio_bug", "director_decision"):
        assert cls in charter
    assert "0.80" in charter  # the conservative confidence bound is documented for the agent


async def test_verify_done_prompt_instructs_triage_emit(db_session, monkeypatch):
    # CR-NS-033: the verify_done prompt tells the Coordinator to triage a flagged problem + append a directive.
    captured = {}

    async def _capture(db, *, version_id, role, stage, prompt, **kw):
        captured["prompt"] = prompt
        return PipelineStatusBlock(stage=stage, kind="gate_report", summary="ok", awaiting="director")

    monkeypatch.setattr(orchestrator, "invoke_agent", _capture)
    monkeypatch.setattr(orchestrator, "verify_mechanical", lambda slug, block, baseline_sha=None: None)
    version, _ = _make_version(db_session)
    block = PipelineStatusBlock(stage="gate_a", kind="gate_report", summary="ok", awaiting="director")
    await orchestrator.verify_done(db_session, version.id, block)
    assert "coordinator_directive" in captured["prompt"] and "triage" in captured["prompt"].lower()


async def test_coordinator_relay_prompt_instructs_triage_emit(db_session, monkeypatch):
    # CR-NS-033: the worker-question relay prompt tells the Coordinator to triage + append a directive.
    captured = {}

    async def _capture(db, *, version_id, role, stage, prompt, **kw):
        captured["prompt"] = prompt
        return PipelineStatusBlock(stage=stage, kind="gate_report", summary="relay", awaiting="director")

    monkeypatch.setattr(orchestrator, "invoke_agent_with_parse_retry", _capture)
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    state = orchestrator._get_state(db_session, version.id)
    state.current_stage = "build"
    state.current_actor = "implementer"
    db_session.flush()
    worker_block = PipelineStatusBlock(
        stage="build", kind="question", summary="?", awaiting="director", question="prečo zlyháva baseline?"
    )
    await orchestrator._coordinator_relay(db_session, state, worker_block)
    assert "coordinator_directive" in captured["prompt"] and "triage" in captured["prompt"].lower()


# ── E7: route_to_designer — build→Designer spec-fix round-trip (F-008 §10, CR-NS-034) ──────────────


def test_route_to_designer_is_executable():
    # CR-NS-034: the spec_problem action is now an executable directive (not a relay).
    assert orchestrator._coordinator_directive_executable(
        _coord_directive(triage_class="spec_problem", proposed_action="coordinator_route_to_designer")
    )


async def test_coordinator_route_to_designer_round_trip(db_session, fake_claude, monkeypatch):
    # F-008 §10: a spec_problem directive at a build HALT → approve → the Designer fixes the spec → the
    # held failed task resets to todo + the build re-attempts against the corrected spec → done.
    monkeypatch.setattr(orchestrator, "_repo_head", lambda root: "a" * 40)
    monkeypatch.setattr(orchestrator, "verify_mechanical", lambda slug, block, baseline_sha=None: None)
    version, project, task = await _build_at_halt(db_session, fake_claude)
    _seed_coordinator_directive(
        db_session,
        version.id,
        _coord_directive(
            triage_class="spec_problem",
            proposed_action="coordinator_route_to_designer",
            target={"task_id": str(task.id)},
            params={"section": "§4 duplicate-detection"},
        ),
    )

    # approve → route_to_designer sets up the Designer dispatch (task held failed, returns_to='build')
    state = await orchestrator.apply_action(
        db_session, version_id=version.id, action="apply_coordinator_recommendation"
    )
    assert state.current_actor == "designer" and state.returns_to == "build" and state.status == "agent_working"
    db_session.refresh(task)
    assert task.status == "failed"  # held until the Designer's DONE (seam)

    # the background dispatch: Designer spec-fix DONE → reset task → re-enter the build loop → re-built
    fake_claude.response = _build_fake()
    await orchestrator.run_dispatch(db_session, version.id)
    db_session.refresh(task)
    assert task.status == "done"  # re-attempted against the corrected spec
    state = orchestrator._get_state(db_session, version.id)
    assert state.returns_to is None  # marker cleared on the Designer's DONE
    assert any(m.author == "designer" and m.stage == "build" for m in _msgs(db_session, version.id))  # spec-fix turn


async def test_route_to_designer_parse_failure_clears_marker_and_blocks(db_session, fake_claude, monkeypatch):
    # CR-NS-034 (review fix): a Designer spec-fix that can't be parsed CLEARS returns_to (marker is for
    # one dispatch only) + blocks — no dangling marker that would hijack the Director's next action.
    monkeypatch.setattr(orchestrator, "_repo_head", lambda root: "a" * 40)
    version, _project, task = await _build_at_halt(db_session, fake_claude)
    _seed_coordinator_directive(
        db_session,
        version.id,
        _coord_directive(
            triage_class="spec_problem",
            proposed_action="coordinator_route_to_designer",
            target={"task_id": str(task.id)},
        ),
    )
    await orchestrator.apply_action(db_session, version_id=version.id, action="apply_coordinator_recommendation")

    fake_claude.response = lambda prompt: "garbage — no status block"  # the Designer turn won't parse
    state = await orchestrator.run_dispatch(db_session, version.id)

    assert state.status == "blocked"
    assert state.returns_to is None  # cleared → return / continue_build now behave normally
    db_session.refresh(task)
    assert task.status == "failed"  # still held — the spec-fix didn't complete


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


# ── Gate E per-question loop (F-007-gate-e revised §2, CR-NS-018) ───────────────


def _to_gate_e(db_session, version):
    """Put the pipeline at gate_e / customer / agent_working (round entry)."""
    state = orchestrator._get_state(db_session, version.id)
    state.current_stage = "gate_e"
    state.current_actor = "customer"
    state.status = "agent_working"
    db_session.flush()
    return state


def _at_gate_e_gap(db_session, version, proposed_fix="Pridať tok reset hesla do §4.2"):
    """Settle at a gate_e per-question stop with a Designer answer that flagged a gap
    (Branch B) — gates the `fix` / `leave` actions."""
    state = orchestrator._get_state(db_session, version.id)
    state.current_stage = "gate_e"
    state.current_actor = "customer"
    state.status = "awaiting_director"
    db_session.flush()
    db_session.add(
        PipelineMessage(
            version_id=version.id,
            stage="gate_e",
            author="designer",
            recipient="director",
            kind="answer",
            content="medzera",
            payload={"gap_found": True, "proposed_fix": proposed_fix},
        )
    )
    db_session.flush()
    return state


async def test_gate_e_branch_a_one_question_then_stops(db_session, monkeypatch):
    """Per-question gating: Customer Q → Designer answer (no gap) → STOP. No chaining."""
    seq = SequenceClaude(
        [
            _block(stage="gate_e", kind="question", summary="?", question="Ako sa rieši reset hesla?"),
            _block(stage="gate_e", kind="answer", summary="Reset cez email, pokryté v §4.2", awaiting="none"),
            _block(stage="gate_e", kind="question", summary="?", question="NEMALO by sa zavolať"),
        ]
    )
    monkeypatch.setattr(orchestrator, "invoke_claude", seq)
    monkeypatch.setattr(orchestrator, "verify_mechanical", lambda slug, block: None)

    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _to_gate_e(db_session, version)
    state = await orchestrator.run_dispatch(db_session, version.id)

    assert state.status == "awaiting_director"
    assert state.current_stage == "gate_e"
    assert len(seq.prompts) == 2  # customer → designer → STOP (no 3rd customer turn)
    # the Designer was told NOT to edit
    designer_prompt = seq.prompts[1]
    assert "NEUPRAVUJ žiadny súbor" in designer_prompt
    assert "schváliť → ďalšia otázka" in state.next_action


async def test_gate_e_branch_b_coordinator_reviews_gap(db_session, monkeypatch):
    """Designer flags a gap → Coordinator reviews the proposal (upward leg) → STOP."""
    seq = SequenceClaude(
        [
            _block(stage="gate_e", kind="question", summary="?", question="Je reset hesla pokrytý?"),
            _block(
                stage="gate_e",
                kind="answer",
                summary="medzera",
                awaiting="none",
                gap_found=True,
                proposed_fix="Pridať tok reset hesla do §4.2",
            ),
            _block(stage="gate_e", kind="gate_report", summary="odporúčam pridať reset hesla", awaiting="director"),
        ]
    )
    monkeypatch.setattr(orchestrator, "invoke_claude", seq)
    monkeypatch.setattr(orchestrator, "verify_mechanical", lambda slug, block: None)

    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _to_gate_e(db_session, version)
    state = await orchestrator.run_dispatch(db_session, version.id)

    assert state.status == "awaiting_director"
    assert len(seq.prompts) == 3  # customer → designer (gap) → coordinator review
    assert any(m.author == "coordinator" for m in _msgs(db_session, version.id))
    assert "medzeru" in state.next_action


async def test_gate_e_topic_boundary_stops(db_session, monkeypatch):
    seq = SequenceClaude(
        [
            _block(
                stage="gate_e",
                kind="gate_report",
                summary="okruh prihlásenie hotový",
                awaiting="director",
                topic="prihlasenie",
                topic_done=True,
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
    assert len(seq.prompts) == 1  # boundary on the Customer turn — no Designer routing
    assert "prihlasenie" in state.next_action


async def test_gate_e_fix_edits_then_next_question(db_session, monkeypatch):
    """designer_edit (Branch B fix): Designer edits per the relayed directive, then the
    round continues to the next Customer question → Designer answer → STOP."""
    seq = SequenceClaude(
        [
            _block(stage="gate_e", kind="answer", summary="opravené podľa návrhu", awaiting="none"),  # designer edit
            _block(stage="gate_e", kind="question", summary="?", question="Ďalšia otázka?"),  # customer
            _block(stage="gate_e", kind="answer", summary="pokryté", awaiting="none"),  # designer answer
        ]
    )
    monkeypatch.setattr(orchestrator, "invoke_claude", seq)
    monkeypatch.setattr(orchestrator, "verify_mechanical", lambda slug, block: None)

    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _to_gate_e(db_session, version)
    state = await orchestrator.run_dispatch(
        db_session,
        version.id,
        directive="Koordinátor odovzdáva pokyn: uprav podľa návrhu",
        gate_e_dispatch="designer_edit",
    )

    assert state.status == "awaiting_director"
    assert seq.prompts[0] == "Koordinátor odovzdáva pokyn: uprav podľa návrhu"  # Designer edits first
    assert len(seq.prompts) == 3  # edit → customer Q → designer A
    # symmetric relay (§5): the Customer's prompt carries the fix outcome (Designer summary)
    assert "opravené podľa návrhu" in seq.prompts[1]


async def test_gate_e_designer_parse_failure_blocks(db_session, monkeypatch):
    seq = SequenceClaude(
        [
            _block(stage="gate_e", kind="question", summary="?", question="Otázka pre Návrhára?"),
            "garbage — no status block",  # designer primary
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


# ── Gate E Branch B actions: fix / leave (Coordinator-relayed) ──────────────────


async def test_fix_requires_open_gap(db_session, fake_claude):
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    state = orchestrator._get_state(db_session, version.id)
    state.current_stage = "gate_e"
    state.current_actor = "customer"
    state.status = "awaiting_director"
    db_session.flush()  # no Designer gap answer present
    with pytest.raises(orchestrator.OrchestratorError):
        await orchestrator.apply_action(db_session, version_id=version.id, action="fix")


async def test_fix_with_gap_dispatches_and_composes_coordinator_relayed_directive(db_session, fake_claude):
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _at_gate_e_gap(db_session, version, proposed_fix="Pridať tok reset hesla")
    db_session.add(
        PipelineMessage(
            version_id=version.id,
            stage="gate_e",
            author="coordinator",
            recipient="director",
            kind="gate_report",
            content="Odporúčam pridať, je to reálna medzera",
        )
    )
    db_session.flush()

    state = await orchestrator.apply_action(db_session, version_id=version.id, action="fix")
    assert state.status == "agent_working"
    assert state.current_stage == "gate_e"
    directive = orchestrator.dispatch_directive(db_session, version.id, "fix", {}, "gate_e")
    # the directive carries the Coordinator's recommendation, NOT the raw Designer proposal
    assert "Odporúčam pridať" in directive
    assert "Koordinátor" in directive
    assert "Pridať tok reset hesla" not in directive  # stale proposed_fix must not leak


async def test_leave_with_gap_continues_without_edit(db_session, fake_claude):
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _at_gate_e_gap(db_session, version)
    state = await orchestrator.apply_action(db_session, version_id=version.id, action="leave")
    assert state.status == "agent_working"
    # leave decision recorded as Coordinator-relayed (director → coordinator)
    decision = [m for m in _msgs(db_session, version.id) if m.kind == "approval" and m.recipient == "coordinator"][-1]
    assert "ponechal" in decision.content.lower()
    directive = orchestrator.dispatch_directive(db_session, version.id, "leave", {}, "gate_e")
    assert "pokračuj" in directive.lower()


async def test_fix_outside_gate_e_errors(db_session, fake_claude):
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _settle(db_session, version.id)  # kickoff, awaiting (past the status guard)
    with pytest.raises(orchestrator.OrchestratorError):
        await orchestrator.apply_action(db_session, version_id=version.id, action="fix")


# ── Director ↔ Coordinator only: consult (ask) / return @ gate_e (§2) ───────────


async def test_ask_at_gate_e_routes_to_coordinator(db_session, fake_claude):
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _at_gate_e_gap(db_session, version)
    state = await orchestrator.apply_action(
        db_session, version_id=version.id, action="ask", payload={"text": "Predvaha má 7 stĺpcov"}
    )
    assert state.status == "agent_working"
    # the consult message is addressed to the Coordinator (never the Customer/Designer)
    q = [m for m in _msgs(db_session, version.id) if m.kind == "question" and m.author == "director"][-1]
    assert q.recipient == "coordinator"
    directive = orchestrator.dispatch_directive(
        db_session, version.id, "ask", {"text": "Predvaha má 7 stĺpcov"}, "gate_e"
    )
    assert "Predvaha má 7 stĺpcov" in directive
    assert "Prepracuj svoje odporúčanie" in directive


async def test_coordinator_consult_dispatch_revises_recommendation(db_session, monkeypatch):
    """coordinator_consult dispatch invokes ONLY the Coordinator → revised recommendation
    → awaiting_director (no Customer/Designer turn)."""
    seq = SequenceClaude(
        [_block(stage="gate_e", kind="gate_report", summary="prepracované: 7 stĺpcov", awaiting="director")]
    )
    monkeypatch.setattr(orchestrator, "invoke_claude", seq)
    monkeypatch.setattr(orchestrator, "verify_mechanical", lambda slug, block: None)

    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _to_gate_e(db_session, version)
    state = await orchestrator.run_dispatch(
        db_session, version.id, directive="Director konzultuje: 7 stĺpcov", gate_e_dispatch="coordinator_consult"
    )
    assert state.status == "awaiting_director"
    assert len(seq.prompts) == 1  # only the Coordinator, no Customer/Designer
    assert any(m.author == "coordinator" for m in _msgs(db_session, version.id))


async def test_return_at_gate_e_routes_to_coordinator(db_session, fake_claude):
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _at_gate_e_gap(db_session, version)
    await orchestrator.apply_action(
        db_session, version_id=version.id, action="return", payload={"comment": "Zváž ešte raz"}
    )
    ret = [m for m in _msgs(db_session, version.id) if m.kind == "return" and m.author == "director"][-1]
    assert ret.recipient == "coordinator"  # via the Coordinator, never the worker directly


async def test_fix_after_consult_delivers_revised_recommendation_not_stale_proposal(db_session, fake_claude):
    """The refinement: after a consult, approval must hand the Designer the Coordinator's
    REVISED recommendation (7), never the stale Designer proposed_fix (6)."""
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _at_gate_e_gap(db_session, version, proposed_fix="predvaha má 6 stĺpcov")
    # initial coordinator recommendation, then a consult-revised one (the latest by seq)
    for content in ("Odporúčam 6 stĺpcov", "Po konzultácii: predvaha má 7 stĺpcov"):
        db_session.add(
            PipelineMessage(
                version_id=version.id,
                stage="gate_e",
                author="coordinator",
                recipient="director",
                kind="gate_report",
                content=content,
            )
        )
        db_session.flush()
    directive = orchestrator.dispatch_directive(db_session, version.id, "fix", {}, "gate_e")
    assert "7 stĺpcov" in directive  # the revised recommendation
    assert "6 stĺpcov" not in directive  # neither the stale proposal nor the stale recommendation


# ── symmetric relay: Designer answer/outcome carried back to the Customer (§5) ──


def _seed_gate_e_designer_answer(db_session, version, content, **payload):
    db_session.add(
        PipelineMessage(
            version_id=version.id,
            stage="gate_e",
            author="designer",
            recipient="director",
            kind="answer",
            content=content,
            payload=payload or None,
        )
    )
    db_session.flush()


def test_gate_e_approve_relays_designer_answer_branch_a(db_session):
    version, _ = _make_version(db_session)
    _seed_gate_e_designer_answer(db_session, version, "Reset hesla je pokrytý v §4.2", gap_found=False)
    d = orchestrator.dispatch_directive(db_session, version.id, "approve", {}, "gate_e")
    assert "Návrhár odpovedal" in d
    assert "Reset hesla je pokrytý v §4.2" in d


def test_gate_e_approve_topic_boundary_is_generic_no_stale_answer(db_session):
    version, _ = _make_version(db_session)
    # an earlier designer answer, then a later Customer gate_report (the latest milestone)
    _seed_gate_e_designer_answer(db_session, version, "stará odpoveď", gap_found=False)
    db_session.add(
        PipelineMessage(
            version_id=version.id,
            stage="gate_e",
            author="customer",
            recipient="director",
            kind="gate_report",
            content="okruh dokončený",
            payload={"topic_done": True},
        )
    )
    db_session.flush()
    d = orchestrator.dispatch_directive(db_session, version.id, "approve", {}, "gate_e")
    assert "Návrhár odpovedal" not in d
    assert "stará odpoveď" not in d
    assert "ďalším okruhom" in d


def test_gate_e_leave_directive_states_decision(db_session):
    version, _ = _make_version(db_session)
    d = orchestrator.dispatch_directive(db_session, version.id, "leave", {}, "gate_e")
    assert "ponechať" in d.lower()
    assert "Pokračuj" in d


# ── robustness: bounded transient retry × parse-retry, then settle (CR-NS-018) ──


async def test_persistent_transient_stays_bounded_through_parse_retry(db_session, monkeypatch):
    """A persistent 529 must not multiply unboundedly: invoke_claude's transient
    retry (4 attempts) nests under invoke_agent_with_parse_retry (3 invoke_agent
    calls) → exactly 12 _invoke_once calls, then a ParseFailure (→ blocked upstream)."""
    calls = {"n": 0}

    async def _always_529(**kwargs):
        calls["n"] += 1
        raise claude_agent.ClaudeAgentError("API Error 529 Overloaded")

    async def _no_sleep(_seconds):
        return None

    monkeypatch.setattr(claude_agent, "_invoke_once", _always_529)
    monkeypatch.setattr(claude_agent.asyncio, "sleep", _no_sleep)
    monkeypatch.setattr(orchestrator, "verify_mechanical", lambda slug, block: None)

    version, _ = _make_version(db_session)
    result = await orchestrator.invoke_agent_with_parse_retry(
        db_session, version_id=version.id, role="designer", stage="gate_a", prompt="go"
    )
    assert isinstance(result, ParseFailure)
    expected = (1 + orchestrator._PARSE_RETRIES) * (len(claude_agent._TRANSIENT_BACKOFF) + 1)
    assert calls["n"] == expected  # bounded, no unbounded multiplication


# ── Gate E boundary actions + coverage/end (F-007-gate-e §3/§4, CR-NS-018 Phase 3) ─


def _at_gate_e_boundary(db_session, version, *, coverage_complete=False, findings=None, open_gaps=0):
    """Settle at a gate_e boundary (awaiting_director) with a Customer gate_report.

    ``open_gaps`` seeds N UNRESOLVED Designer gaps (gap_found, no fix/leave) — the
    deterministic open-finding source (§5). ``findings`` is the Customer's self-report
    array, now informational only — the gate must ignore it."""
    state = orchestrator._get_state(db_session, version.id)
    state.current_stage = "gate_e"
    state.current_actor = "customer"
    state.status = "awaiting_director"
    db_session.flush()
    for i in range(open_gaps):
        db_session.add(
            PipelineMessage(
                version_id=version.id,
                stage="gate_e",
                author="designer",
                recipient="coordinator",
                kind="answer",
                content=f"gap{i}",
                payload={"gap_found": True},
            )
        )
    db_session.add(
        PipelineMessage(
            version_id=version.id,
            stage="gate_e",
            author="customer",
            recipient="director",
            kind="gate_report",
            content="okruh",
            payload={"coverage_complete": coverage_complete, "findings": findings or []},
        )
    )
    db_session.flush()
    return state


async def test_invoke_agent_persists_gate_e_signals(db_session, fake_claude):
    version, _ = _make_version(db_session)
    fake_claude.response = _block(
        stage="gate_e",
        kind="gate_report",
        summary="ok",
        awaiting="director",
        topic="moduly",
        topic_done=True,
        coverage_complete=True,
        findings=["nález X"],
    )
    await orchestrator.invoke_agent(db_session, version_id=version.id, role="customer", stage="gate_e", prompt="go")
    msg = [m for m in _msgs(db_session, version.id) if m.author == "customer"][-1]
    assert msg.payload["topic"] == "moduly"
    assert msg.payload["topic_done"] is True
    assert msg.payload["coverage_complete"] is True
    assert msg.payload["findings"] == ["nález X"]


def test_dispatch_directive_gate_e_approve_continues(db_session):
    version, _ = _make_version(db_session)
    d = orchestrator.dispatch_directive(db_session, version.id, "approve", {}, "gate_e")
    assert d is not None and "ďalším okruhom" in d
    # a final approve has already advanced past gate_e (→ task_plan) → no gate_e continue directive
    assert orchestrator.dispatch_directive(db_session, version.id, "approve", {}, "task_plan") is None


async def test_gate_e_topic_boundary_approve_continues(db_session, fake_claude):
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _at_gate_e_boundary(db_session, version, coverage_complete=False)
    state = await orchestrator.apply_action(db_session, version_id=version.id, action="approve")
    assert state.current_stage == "gate_e"  # stays — next topic
    assert state.status == "agent_working"


async def test_gate_e_final_approve_advances_to_task_plan(db_session, fake_claude):
    # CR-NS-020 CR-2: gate_e now advances to task_plan (not build) — task_plan is inserted
    # at STAGE_ORDER index 6, so _next_stage("gate_e") → "task_plan".
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _at_gate_e_boundary(db_session, version, coverage_complete=True, findings=[])
    state = await orchestrator.apply_action(db_session, version_id=version.id, action="approve")
    assert state.current_stage == "task_plan"
    # §4 audit record written before closing
    assert any(m.author == "system" and "Gate E audit" in m.content for m in _msgs(db_session, version.id))


async def test_gate_e_final_approve_not_blocked_by_customer_findings_array(db_session, fake_claude):
    """The exact bug (§5): the Customer's findings array is non-empty (a resolved summary)
    but there are NO unresolved deterministic gaps → the close must NOT be blocked."""
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _at_gate_e_boundary(
        db_session, version, coverage_complete=True, findings=["a", "b", "c", "d", "e", "f"], open_gaps=0
    )
    state = await orchestrator.apply_action(db_session, version_id=version.id, action="approve")
    assert state.current_stage == "task_plan"  # deterministic open == 0 wins over the array


async def test_gate_e_final_approve_blocked_by_open_findings(db_session, fake_claude):
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _at_gate_e_boundary(db_session, version, coverage_complete=True, open_gaps=1)  # a real unresolved gap
    with pytest.raises(orchestrator.OrchestratorError):
        await orchestrator.apply_action(db_session, version_id=version.id, action="approve")


async def test_end_gate_e_advances_when_no_open_findings(db_session, fake_claude):
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _at_gate_e_boundary(db_session, version, coverage_complete=False, findings=[])
    state = await orchestrator.apply_action(db_session, version_id=version.id, action="end_gate_e")
    assert state.current_stage == "task_plan"
    assert any(m.author == "system" and "Gate E audit" in m.content for m in _msgs(db_session, version.id))


async def test_end_gate_e_blocked_by_open_findings(db_session, fake_claude):
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _at_gate_e_boundary(db_session, version, coverage_complete=False, open_gaps=1)  # a real unresolved gap
    with pytest.raises(orchestrator.OrchestratorError):
        await orchestrator.apply_action(db_session, version_id=version.id, action="end_gate_e")


async def test_end_gate_e_outside_gate_e_errors(db_session, fake_claude):
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _settle(db_session, version.id)  # kickoff, awaiting (past the status guard)
    with pytest.raises(orchestrator.OrchestratorError):
        await orchestrator.apply_action(db_session, version_id=version.id, action="end_gate_e")


# ── deterministic open-finding count (§5: orchestrator record, not self-report) ─


def _director_resolution(db_session, version):
    db_session.add(
        PipelineMessage(
            version_id=version.id,
            stage="gate_e",
            author="director",
            recipient="coordinator",
            kind="approval",
            content="rozhodnuté",
            payload={"resolves_gap": True},
        )
    )
    db_session.flush()


def test_open_findings_counts_raised_minus_resolved(db_session):
    version, _ = _make_version(db_session)
    assert orchestrator._gate_e_open_findings(db_session, version.id) == 0
    _seed_gate_e_designer_answer(db_session, version, "medzera 1", gap_found=True)
    assert orchestrator._gate_e_open_findings(db_session, version.id) == 1
    _director_resolution(db_session, version)  # a fix/leave decision
    assert orchestrator._gate_e_open_findings(db_session, version.id) == 0


def test_open_findings_ignores_customer_findings_array(db_session):
    version, _ = _make_version(db_session)
    # no deterministic gaps, but the Customer's gate_report findings array is non-empty
    db_session.add(
        PipelineMessage(
            version_id=version.id,
            stage="gate_e",
            author="customer",
            recipient="director",
            kind="gate_report",
            content="súhrn",
            payload={"coverage_complete": True, "findings": ["a", "b", "c", "d", "e", "f"]},
        )
    )
    db_session.flush()
    assert orchestrator._gate_e_open_findings(db_session, version.id) == 0  # the array is ignored


def test_open_findings_consult_does_not_change_count(db_session):
    version, _ = _make_version(db_session)
    _seed_gate_e_designer_answer(db_session, version, "medzera", gap_found=True)  # open = 1
    # a consult: a Director question (no resolves_gap) + a Coordinator gate_report (no gap_found)
    db_session.add(
        PipelineMessage(
            version_id=version.id,
            stage="gate_e",
            author="director",
            recipient="coordinator",
            kind="question",
            content="konzultácia",
        )
    )
    db_session.add(
        PipelineMessage(
            version_id=version.id,
            stage="gate_e",
            author="coordinator",
            recipient="director",
            kind="gate_report",
            content="prepracované odporúčanie",
        )
    )
    db_session.flush()
    assert orchestrator._gate_e_open_findings(db_session, version.id) == 1  # unchanged by the consult


def test_open_findings_edit_turn_never_reraises(db_session):
    """A fix EDIT turn (is_fix_edit) must NEVER raise a gap, even if its status block
    erroneously carries gap_found — it executes an approved fix, not a review (§5)."""
    version, _ = _make_version(db_session)
    _seed_gate_e_designer_answer(db_session, version, "medzera", gap_found=True)  # raised 1
    _director_resolution(db_session, version)  # resolved 1 → 0
    db_session.add(  # the edit turn (is_fix_edit) wrongly sets gap_found — must be ignored
        PipelineMessage(
            version_id=version.id,
            stage="gate_e",
            author="designer",
            recipient="coordinator",
            kind="answer",
            content="opravené",
            payload={"gap_found": True, "is_fix_edit": True},
        )
    )
    db_session.flush()
    assert orchestrator._gate_e_open_findings(db_session, version.id) == 0  # edit turn never re-raises


async def test_gate_e_fix_edit_message_is_tagged_is_fix_edit(db_session, monkeypatch):
    """The designer_edit dispatch tags its recorded message ``is_fix_edit`` so the
    deterministic count can exclude it (guards the double-count blocker)."""
    seq = SequenceClaude(
        [
            _block(
                stage="gate_e", kind="answer", summary="opravené", awaiting="none", gap_found=True
            ),  # edit (misbehaving)
            _block(stage="gate_e", kind="question", summary="?", question="ďalšia?"),  # next customer Q
            _block(stage="gate_e", kind="answer", summary="pokryté", awaiting="none"),  # designer answer
        ]
    )
    monkeypatch.setattr(orchestrator, "invoke_claude", seq)
    monkeypatch.setattr(orchestrator, "verify_mechanical", lambda slug, block: None)

    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _to_gate_e(db_session, version)
    await orchestrator.run_dispatch(
        db_session, version.id, directive="uprav podľa odporúčania", gate_e_dispatch="designer_edit"
    )

    edits = [
        m
        for m in _msgs(db_session, version.id)
        if m.author == "designer" and m.payload and m.payload.get("is_fix_edit")
    ]
    assert len(edits) == 1  # the edit turn is tagged
    # even though the edit's block had gap_found=true, it does not raise a gap
    assert orchestrator._gate_e_open_findings(db_session, version.id) == 0


def _gm(author, kind, content, **payload):
    return PipelineMessage(
        version_id=uuid.uuid4(),
        stage="gate_e",
        author=author,
        recipient="director",
        kind=kind,
        content=content,
        payload=payload or None,
    )


def test_gate_e_audit_markdown_assembles():
    msgs = [
        _gm("customer", "question", "Ako sa rieši reset hesla?"),
        _gm("designer", "answer", "Reset cez email, pokryté v §4.2"),
        _gm("customer", "gate_report", "okruh prihlásenie hotový", topic="prihlasenie", topic_done=True),
        _gm(
            "customer",
            "gate_report",
            "všetky okruhy pokryté",
            topic="integracie",
            topic_done=True,
            coverage_complete=True,
            findings=[],
        ),
    ]
    md = orchestrator.gate_e_audit_markdown(msgs, "0.2.0")
    assert "v0.2.0" in md
    assert "prihlasenie" in md and "integracie" in md  # covered topics
    assert "Zákazník" in md and "Návrhár" in md  # role-labelled transcript
    assert "Ako sa rieši reset hesla?" in md
    assert "Reset cez email" in md


# ── recipient chain Z→N→K→D + real active role (cockpit accuracy, §5) ───────────


async def test_gate_e_recipients_follow_the_chain(db_session, monkeypatch):
    seq = SequenceClaude(
        [
            _block(stage="gate_e", kind="question", summary="?", question="Je X pokryté?"),
            _block(stage="gate_e", kind="answer", summary="áno, §4.2", awaiting="none"),
        ]
    )
    monkeypatch.setattr(orchestrator, "invoke_claude", seq)
    monkeypatch.setattr(orchestrator, "verify_mechanical", lambda slug, block: None)

    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _to_gate_e(db_session, version)
    await orchestrator.run_dispatch(db_session, version.id)

    msgs = _msgs(db_session, version.id)
    customer = [m for m in msgs if m.author == "customer"][-1]
    designer = [m for m in msgs if m.author == "designer"][-1]
    assert customer.recipient == "designer"  # Z→N
    assert designer.recipient == "coordinator"  # N→K


async def test_gate_e_coordinator_recipient_is_director(db_session, monkeypatch):
    seq = SequenceClaude(
        [
            _block(stage="gate_e", kind="question", summary="?", question="Je X pokryté?"),
            _block(
                stage="gate_e",
                kind="answer",
                summary="medzera",
                awaiting="none",
                gap_found=True,
                proposed_fix="pridať X",
            ),
            _block(stage="gate_e", kind="gate_report", summary="odporúčam pridať X", awaiting="director"),
        ]
    )
    monkeypatch.setattr(orchestrator, "invoke_claude", seq)
    monkeypatch.setattr(orchestrator, "verify_mechanical", lambda slug, block: None)

    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _to_gate_e(db_session, version)
    await orchestrator.run_dispatch(db_session, version.id)

    coordinator = [m for m in _msgs(db_session, version.id) if m.author == "coordinator"][-1]
    assert coordinator.recipient == "director"  # K→D


async def test_invoke_agent_default_recipient_is_director(db_session, fake_claude):
    version, _ = _make_version(db_session)
    await orchestrator.invoke_agent(db_session, version_id=version.id, role="designer", stage="gate_a", prompt="go")
    msg = [m for m in _msgs(db_session, version.id) if m.author == "designer"][-1]
    assert msg.recipient == "director"  # non-gate_e unchanged


async def test_invoke_agent_emits_active_role_tagged_with_role(db_session, fake_claude):
    captured: list = []

    async def _cap(evt):
        captured.append(evt)

    version, _ = _make_version(db_session)
    await orchestrator.invoke_agent(
        db_session, version_id=version.id, role="designer", stage="gate_e", prompt="go", on_event=_cap
    )
    # the per-turn active-role signal carries the REAL role so the rail steps Z→N→K
    assert captured[0] == {"type": "active_role", "_role": "designer"}


# ── task_plan stage + write-path (F-007 §5, CR-NS-020 CR-2) ─────────────────────


def _to_task_plan(db_session, version):
    """Put the pipeline at task_plan / designer / agent_working (Designer planning turn)."""
    state = orchestrator._get_state(db_session, version.id)
    state.current_stage = "task_plan"
    state.current_actor = "designer"
    state.status = "agent_working"
    db_session.flush()
    return state


def _plan(*epics) -> dict:
    """Build a task_plan ``plan`` payload from (epic_title, [(feat_title, [(task_title, task_type)])])."""
    return {
        "epics": [
            {
                "title": e_title,
                "feats": [
                    {"title": f_title, "tasks": [{"title": t_title, "task_type": t_type} for t_title, t_type in tasks]}
                    for f_title, tasks in feats
                ],
            }
            for e_title, feats in epics
        ]
    }


def _epics_of(db_session, version):
    return db_session.execute(select(Epic).where(Epic.version_id == version.id)).scalars().all()


async def test_task_plan_write_path_materializes_hierarchy(db_session, fake_claude):
    version, project = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _to_task_plan(db_session, version)
    fake_claude.response = _block(
        stage="task_plan",
        kind="gate_report",
        summary="plán rozložený",
        awaiting="director",
        plan=_plan(
            ("Foundation", [("Schema", [("GL+AA+AP tables", "migration"), ("audit_log", "migration")])]),
            ("Calc cores", [("Hlavná kniha", [("GL výpočet", "backend")])]),
        ),
        cross_cutting_rules="## Invarianty\n- spoločná transakčná hranica\n- immutable audit",
    )
    state = await orchestrator.run_dispatch(db_session, version.id)

    assert state.status == "awaiting_director"
    assert state.current_stage == "task_plan"
    epics = _epics_of(db_session, version)
    assert len(epics) == 2
    assert {e.title for e in epics} == {"Foundation", "Calc cores"}
    assert all(e.project_id == project.id and e.status == "planned" for e in epics)
    feats = db_session.execute(select(Feat)).scalars().all()
    tasks = db_session.execute(select(Task)).scalars().all()
    assert len(feats) == 2 and len(tasks) == 3
    assert all(t.status == "todo" and t.baseline_sha is None for t in tasks)
    assert {t.task_type for t in tasks} == {"migration", "backend"}
    # the cross-cutting rules persist in the Designer's gate_report payload (CR-3 re-reads them)
    designer = [m for m in _msgs(db_session, version.id) if m.author == "designer" and m.stage == "task_plan"][-1]
    assert "transakčná" in designer.payload["cross_cutting_rules"]
    # a system summary message is recorded for the audit trail + future TaskPlanPanel
    assert any(
        m.author == "system" and m.stage == "task_plan" and "Plán úloh zapísaný" in m.content
        for m in _msgs(db_session, version.id)
    )


async def test_task_plan_gate_report_without_plan_blocks(db_session, fake_claude):
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _to_task_plan(db_session, version)
    fake_claude.response = _block(
        stage="task_plan", kind="gate_report", summary="zabudol som plán", awaiting="director"
    )
    state = await orchestrator.run_dispatch(db_session, version.id)
    assert state.status == "blocked"  # parse_status_block rejects a planless task_plan gate_report
    assert _epics_of(db_session, version) == []  # nothing written


async def test_task_plan_replan_replaces_no_duplicates(db_session, fake_claude):
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _to_task_plan(db_session, version)
    fake_claude.response = _block(
        stage="task_plan",
        kind="gate_report",
        summary="v1",
        awaiting="director",
        plan=_plan(("E1", [("F1", [("T1", "backend")])]), ("E2", [("F2", [("T2", "backend")])])),
    )
    await orchestrator.run_dispatch(db_session, version.id)
    assert len(_epics_of(db_session, version)) == 2

    # Director returned → Designer re-plans (fewer epics). The write-path must REPLACE.
    _to_task_plan(db_session, version)
    fake_claude.response = _block(
        stage="task_plan",
        kind="gate_report",
        summary="v2",
        awaiting="director",
        plan=_plan(("E1 only", [("F1", [("T1", "backend")])])),
    )
    await orchestrator.run_dispatch(db_session, version.id)
    epics = _epics_of(db_session, version)
    assert len(epics) == 1  # replaced, not appended
    assert epics[0].title == "E1 only"
    assert len(db_session.execute(select(Task)).scalars().all()) == 1


async def test_approve_at_task_plan_advances_to_build(db_session, fake_claude):
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _to_task_plan(db_session, version)
    fake_claude.response = _block(
        stage="task_plan",
        kind="gate_report",
        summary="plán",
        awaiting="director",
        plan=_plan(("E1", [("F1", [("T1", "backend")])])),
    )
    await orchestrator.run_dispatch(db_session, version.id)  # → awaiting_director
    state = await orchestrator.apply_action(db_session, version_id=version.id, action="approve")
    assert state.current_stage == "build"


# ── build per-task loop (F-007 §6, CR-NS-020 CR-3) ──────────────────────────────


def _to_build(db_session, version):
    """Put the pipeline at build / implementer / agent_working (loop entry)."""
    state = orchestrator._get_state(db_session, version.id)
    state.current_stage = "build"
    state.current_actor = "implementer"
    state.status = "agent_working"
    db_session.flush()
    return state


def _seed_one_feat(db_session, version, project, titles, *, types=None):
    """Create 1 epic / 1 feat / N todo tasks under the version (plan order = task.number)."""
    epic = Epic(project_id=project.id, version_id=version.id, number=1, title="E1", status="planned")
    db_session.add(epic)
    db_session.flush()
    feat = Feat(epic_id=epic.id, number=1, title="F1", description="", status="todo")
    db_session.add(feat)
    db_session.flush()
    tasks = []
    for i, title in enumerate(titles, 1):
        task = Task(
            feat_id=feat.id,
            number=i,
            title=title,
            task_type=(types[i - 1] if types else "backend"),
            status="todo",
        )
        db_session.add(task)
        tasks.append(task)
    db_session.flush()
    return epic, feat, tasks


def _seed_cross_cutting(db_session, version, text):
    """Persist the cross_cutting_rules the build loop re-reads (a task_plan gate_report)."""
    db_session.add(
        PipelineMessage(
            version_id=version.id,
            stage="task_plan",
            author="designer",
            recipient="director",
            kind="gate_report",
            content="plán",
            payload={"cross_cutting_rules": text},
        )
    )
    db_session.flush()


def _build_report() -> str:
    return _block(
        stage="build", kind="gate_report", summary="hotovo", awaiting="director", commits=["c1"], deliverables=["f"]
    )


def _audit(task_pass: bool, findings=None) -> str:
    return _block(
        stage="build",
        kind="gate_report",
        summary="audit",
        awaiting="director",
        task_pass=task_pass,
        findings=findings or [],
    )


def _build_fake(*, audit_pass=True, audit_findings=None):
    """Role-aware fake_claude response (CR-NS-020 CR-4): the Programmer's build report vs the
    Auditor's verdict (the per-task audit prompt starts with 'Audítor')."""

    def _resp(prompt: str) -> str:
        return _audit(audit_pass, audit_findings) if prompt.startswith("Audítor") else _build_report()

    return _resp


async def test_build_loop_runs_tasks_in_order_then_awaits_director(db_session, fake_claude, monkeypatch):
    monkeypatch.setattr(orchestrator, "_repo_head", lambda root: "a" * 40)
    version, project = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _seed_cross_cutting(db_session, version, "## Invarianty\n- podvojnosť")
    _epic, feat, tasks = _seed_one_feat(db_session, version, project, ["T-one", "T-two"])
    _to_build(db_session, version)
    fake_claude.response = _build_fake()

    state = await orchestrator.run_dispatch(db_session, version.id)

    assert state.status == "awaiting_director" and state.current_stage == "build"
    for t in tasks:
        db_session.refresh(t)
        assert t.status == "done"
        assert t.baseline_sha == "a" * 40  # baseline captured per task
    briefs = [c["prompt"] for c in fake_claude.calls if c["prompt"].startswith("Programátor")]
    assert "T-one" in briefs[0] and "T-two" in briefs[1]  # dispatched in plan order
    assert all("podvojnosť" in b for b in briefs)  # cross-cutting block injected into every brief
    # the Auditor was dispatched per task (CR-4 per-task audit turn), tagged with task_id (CR-5)
    auditor_msgs = [m for m in _msgs(db_session, version.id) if m.author == "auditor" and m.stage == "build"]
    assert auditor_msgs and all(m.payload and m.payload.get("task_id") for m in auditor_msgs)
    # final sign-off advances build → gate_g
    state = await orchestrator.apply_action(db_session, version_id=version.id, action="approve")
    assert state.current_stage == "gate_g"


async def test_build_records_task_start_notification_once_per_task(db_session, fake_claude, monkeypatch):
    # CR-NS-025 Part 1: each dispatched task emits a system→director "▶ Úloha #N…" notification with a
    # task_id payload, broadcast via on_message BEFORE the Programmer turn, so TaskPlanPanel refetches
    # and the in_progress task shows live (the panel keys its refetch on messages.length).
    monkeypatch.setattr(orchestrator, "_repo_head", lambda root: "a" * 40)
    version, project = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _seed_cross_cutting(db_session, version, "## Invarianty")
    _epic, _feat, tasks = _seed_one_feat(db_session, version, project, ["T-one", "T-two"])
    _to_build(db_session, version)
    fake_claude.response = _build_fake()

    broadcast: list[tuple[str, str, str]] = []  # (author, kind, content) in on_message order

    async def _collect(msg):
        broadcast.append((msg.author, msg.kind, msg.content))

    await orchestrator.run_dispatch(db_session, version.id, on_message=_collect)

    starts = [
        m for m in _msgs(db_session, version.id) if m.kind == "notification" and m.content.startswith("▶ Úloha #")
    ]
    assert len(starts) == len(tasks)  # exactly one start per task
    for m, task in zip(starts, tasks):
        assert m.author == "system" and m.recipient == "director" and m.stage == "build"
        assert m.payload == {"task_id": str(task.id), "task_number": task.number}
        assert task.title in m.content
    # broadcast via on_message: EVERY task's breadcrumb precedes that task's own Programmer gate_report
    for task in tasks:
        start_i = next(i for i, (_a, k, c) in enumerate(broadcast) if k == "notification" and f"#{task.number}:" in c)
        prog_i = next(
            i for i, (a, k, _c) in enumerate(broadcast) if a == "implementer" and k == "gate_report" and i > start_i
        )
        assert start_i < prog_i


async def test_build_task_start_notification_not_repeated_on_auto_fix(db_session, fake_claude, monkeypatch):
    # CR-NS-025 Part 1: a task that auto-fixes (fail twice → pass on the 3rd) still emits exactly ONE
    # start notification — the breadcrumb is per task; the auto-fix retries are separate return messages.
    monkeypatch.setattr(orchestrator, "_repo_head", lambda root: "b" * 40)
    calls = {"n": 0}

    def _verify(slug, block, baseline_sha=None):
        calls["n"] += 1
        return "diff prázdny" if calls["n"] < 3 else None

    monkeypatch.setattr(orchestrator, "verify_mechanical", _verify)
    version, project = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _epic, _feat, (_task,) = _seed_one_feat(db_session, version, project, ["T"])
    _to_build(db_session, version)
    fake_claude.response = _build_fake()

    await orchestrator.run_dispatch(db_session, version.id)

    starts = [
        m for m in _msgs(db_session, version.id) if m.kind == "notification" and m.content.startswith("▶ Úloha #")
    ]
    assert len(starts) == 1  # one start despite three attempts
    autofix = [m for m in _msgs(db_session, version.id) if m.kind == "return" and "Auto-fix" in m.content]
    assert len(autofix) == 2  # the retries are separate return messages, not start notifications


async def test_build_pause_observed_at_task_boundary(db_session, fake_claude, monkeypatch):
    # CR-NS-027 (the visibility crux): a pause committed mid-build by the Director's SEPARATE request
    # session must be observed by the bg loop. SessionLocal is expire_on_commit=False, so the loop's
    # db.refresh(state) is load-bearing. We simulate the separate commit with a Core UPDATE — it
    # bypasses the ORM identity map, so the cached state.status stays 'agent_working' and ONLY
    # db.refresh can see the change. Expect: task #1 finishes cleanly, the loop stops at the boundary,
    # task #2 is never dispatched (stays todo), final status 'paused'. (Without db.refresh this fails:
    # the cached agent_working would carry the loop into task #2.)
    monkeypatch.setattr(orchestrator, "_repo_head", lambda root: "a" * 40)
    version, project = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _seed_cross_cutting(db_session, version, "## Invarianty")
    _epic, _feat, tasks = _seed_one_feat(db_session, version, project, ["T-one", "T-two"])
    _to_build(db_session, version)

    def _resp(prompt: str) -> str:
        if prompt.startswith("Programátor"):
            db_session.execute(
                update(PipelineState).where(PipelineState.version_id == version.id).values(status="paused")
            )
            db_session.flush()
        return _audit(True) if prompt.startswith("Audítor") else _build_report()

    fake_claude.response = _resp

    state = await orchestrator.run_dispatch(db_session, version.id)

    assert state.status == "paused"  # the loop observed the committed pause and settled
    for t in tasks:
        db_session.refresh(t)
    assert tasks[0].status == "done"  # task #1 finished cleanly (no mid-task kill)
    assert tasks[1].status == "todo"  # task #2 never dispatched — pause took effect at the boundary


async def test_build_auto_fix_retries_then_passes(db_session, fake_claude, monkeypatch):
    monkeypatch.setattr(orchestrator, "_repo_head", lambda root: "b" * 40)
    calls = {"n": 0}

    def _verify(slug, block, baseline_sha=None):
        calls["n"] += 1
        return "diff prázdny" if calls["n"] < 3 else None  # fail twice, pass on the 3rd

    monkeypatch.setattr(orchestrator, "verify_mechanical", _verify)
    version, project = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _epic, feat, (task,) = _seed_one_feat(db_session, version, project, ["T"])
    _to_build(db_session, version)
    fake_claude.response = _build_fake()

    state = await orchestrator.run_dispatch(db_session, version.id)

    assert state.status == "awaiting_director"
    db_session.refresh(task)
    assert task.status == "done"
    returns = [
        m
        for m in _msgs(db_session, version.id)
        if m.author == "system" and m.kind == "return" and "Auto-fix" in m.content
    ]
    assert len(returns) == 2  # two failed attempts before the pass
    db_session.refresh(feat)
    assert feat.auto_fix_count == 2


async def test_build_auto_fix_exhausted_marks_failed_and_halts(db_session, fake_claude, monkeypatch):
    monkeypatch.setattr(orchestrator, "_repo_head", lambda root: "c" * 40)
    monkeypatch.setattr(orchestrator, "verify_mechanical", lambda slug, block, baseline_sha=None: "vždy zlyhá")
    version, project = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _epic, _feat, (task,) = _seed_one_feat(db_session, version, project, ["T"])
    _to_build(db_session, version)
    fake_claude.response = _build_fake()

    state = await orchestrator.run_dispatch(db_session, version.id)

    assert state.status == "awaiting_director"  # HALT
    db_session.refresh(task)
    assert task.status == "failed"
    returns = [m for m in _msgs(db_session, version.id) if m.author == "system" and m.kind == "return"]
    assert len(returns) == orchestrator._AUTO_FIX_RETRIES  # exactly 5 attempts
    assert orchestrator._build_open_findings(db_session, version.id) == 1
    # a Coordinator relay message was recorded for the Director
    assert any(m.author == "coordinator" and m.stage == "build" for m in _msgs(db_session, version.id))
    # CR-NS-033: the failed-task HALT relay prompt instructs the Coordinator to triage + emit a directive
    assert any("coordinator_directive" in c["prompt"] for c in fake_claude.calls)
    # the failed task blocks the close (approve to gate_g)
    with pytest.raises(orchestrator.OrchestratorError):
        await orchestrator.apply_action(db_session, version_id=version.id, action="approve")


async def test_end_build_blocked_by_failed_task(db_session, fake_claude, monkeypatch):
    monkeypatch.setattr(orchestrator, "_repo_head", lambda root: "d" * 40)
    monkeypatch.setattr(orchestrator, "verify_mechanical", lambda slug, block, baseline_sha=None: "zlyhá")
    version, project = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _seed_one_feat(db_session, version, project, ["T"])
    _to_build(db_session, version)
    fake_claude.response = _build_fake()
    await orchestrator.run_dispatch(db_session, version.id)  # → failed + HALT
    with pytest.raises(orchestrator.OrchestratorError):
        await orchestrator.apply_action(db_session, version_id=version.id, action="end_build")


async def test_end_build_advances_with_unstarted_tasks(db_session, fake_claude):
    # end_build = "zvyšok do auditu": todo tasks don't block (only failed/in_progress do).
    version, project = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _seed_one_feat(db_session, version, project, ["T-a", "T-b"])  # both stay todo
    state = _to_build(db_session, version)
    state.status = "awaiting_director"
    db_session.flush()
    state = await orchestrator.apply_action(db_session, version_id=version.id, action="end_build")
    assert state.current_stage == "gate_g"


async def test_return_at_build_halt_resets_failed_and_reattempts(db_session, fake_claude, monkeypatch):
    monkeypatch.setattr(orchestrator, "_repo_head", lambda root: "e" * 40)
    monkeypatch.setattr(orchestrator, "verify_mechanical", lambda slug, block, baseline_sha=None: "zlyhá")
    version, project = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _epic, _feat, (task,) = _seed_one_feat(db_session, version, project, ["T"])
    _to_build(db_session, version)
    fake_claude.response = _build_fake()
    await orchestrator.run_dispatch(db_session, version.id)  # → failed + HALT
    db_session.refresh(task)
    assert task.status == "failed"

    # Director returns → failed task reset to todo → re-dispatch → now verify passes
    monkeypatch.setattr(orchestrator, "verify_mechanical", lambda slug, block, baseline_sha=None: None)
    await orchestrator.apply_action(
        db_session, version_id=version.id, action="return", payload={"comment": "skús inak"}
    )
    db_session.refresh(task)
    assert task.status == "todo"  # reset for re-attempt
    n_calls_before = len(fake_claude.calls)
    state = await orchestrator.run_dispatch(db_session, version.id, directive="Director ťa vrátil: skús inak")
    db_session.refresh(task)
    assert task.status == "done"
    assert state.status == "awaiting_director"
    # the Director's framed return reached the re-attempt brief (resumption fix, not lost)
    reattempt_prompts = [c["prompt"] for c in fake_claude.calls[n_calls_before:]]
    assert any("skús inak" in p for p in reattempt_prompts)


async def test_build_resume_reclaims_orphaned_in_progress(db_session, fake_claude, monkeypatch):
    # A dispatch that died mid-loop leaves a task in_progress; the loop reclaims it on re-entry
    # and re-runs from its PERSISTED baseline_sha (Dedo 2026-06-08) — not a freshly-read HEAD.
    monkeypatch.setattr(orchestrator, "_repo_head", lambda root: "f" * 40)
    seen = {}

    def _verify(slug, block, baseline_sha=None):
        seen["baseline"] = baseline_sha
        return None

    monkeypatch.setattr(orchestrator, "verify_mechanical", _verify)
    version, project = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _epic, _feat, (task,) = _seed_one_feat(db_session, version, project, ["T"])
    task.status = "in_progress"  # orphaned by a prior (dead) dispatch
    task.baseline_sha = "0" * 40  # the baseline captured by that prior dispatch
    db_session.flush()
    _to_build(db_session, version)
    fake_claude.response = _build_fake()
    state = await orchestrator.run_dispatch(db_session, version.id)
    db_session.refresh(task)
    assert task.status == "done"  # reclaimed → re-run → completed
    assert state.status == "awaiting_director"
    assert task.baseline_sha == "0" * 40  # persisted baseline kept (not overwritten by HEAD)
    assert seen["baseline"] == "0" * 40  # and that persisted baseline reached verify_mechanical


async def test_build_verify_receives_captured_baseline(db_session, fake_claude, monkeypatch):
    # Regression guard (CR-3 blocker): the captured HEAD must reach verify_mechanical as the
    # baseline_sha — a stale in-memory Task would pass None and silently skip the diff-scope.
    monkeypatch.setattr(orchestrator, "_repo_head", lambda root: "9" * 40)
    seen = {}

    def _verify(slug, block, baseline_sha=None):
        seen["baseline"] = baseline_sha
        return None

    monkeypatch.setattr(orchestrator, "verify_mechanical", _verify)
    version, project = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _epic, _feat, (task,) = _seed_one_feat(db_session, version, project, ["T"])
    _to_build(db_session, version)
    fake_claude.response = _build_fake()
    await orchestrator.run_dispatch(db_session, version.id)
    assert seen["baseline"] == "9" * 40  # fresh task anchored to HEAD; the SHA reached verify
    db_session.refresh(task)
    assert task.baseline_sha == "9" * 40


async def test_build_audit_fail_escalates_findings_then_passes(db_session, fake_claude, monkeypatch):
    # CR-4: mechanical passes but the Auditor fails twice (findings) → auto-fix escalates the
    # findings into the Programmer's next brief; a later audit pass → task done.
    monkeypatch.setattr(orchestrator, "_repo_head", lambda root: "7" * 40)
    audit_calls = {"n": 0}

    def _resp(prompt: str) -> str:
        if prompt.startswith("Audítor"):
            audit_calls["n"] += 1
            if audit_calls["n"] < 3:
                return _audit(False, findings=["chýba podvojnosť"])
            return _audit(True)
        return _build_report()

    fake_claude.response = _resp
    version, project = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _epic, feat, (task,) = _seed_one_feat(db_session, version, project, ["T"])
    _to_build(db_session, version)

    state = await orchestrator.run_dispatch(db_session, version.id)

    assert state.status == "awaiting_director"
    db_session.refresh(task)
    assert task.status == "done"
    assert audit_calls["n"] == 3  # audited each attempt; passed on the 3rd
    # the audit findings were escalated into a later Programmer brief
    prog_briefs = [c["prompt"] for c in fake_claude.calls if c["prompt"].startswith("Programátor")]
    assert any("podvojnosť" in b for b in prog_briefs)
    db_session.refresh(feat)
    assert feat.auto_fix_count == 2


async def test_build_mechanical_fail_short_circuits_auditor(db_session, fake_claude, monkeypatch):
    # CR-4: a mechanical failure must NOT dispatch the Auditor (no point auditing a missing commit).
    monkeypatch.setattr(orchestrator, "_repo_head", lambda root: "8" * 40)
    monkeypatch.setattr(orchestrator, "verify_mechanical", lambda slug, block, baseline_sha=None: "commit chýba")
    fake_claude.response = _build_fake()  # would audit-pass IF the Auditor were ever called
    version, project = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _epic, _feat, (task,) = _seed_one_feat(db_session, version, project, ["T"])
    _to_build(db_session, version)

    state = await orchestrator.run_dispatch(db_session, version.id)

    assert state.status == "awaiting_director"  # HALT after 5 mechanical fails
    db_session.refresh(task)
    assert task.status == "failed"
    assert not any(m.author == "auditor" for m in _msgs(db_session, version.id))  # short-circuited


async def test_build_baseline_unreadable_halts_fail_closed(db_session, fake_claude, monkeypatch):
    # CR-4.1: repo HEAD unreadable (_repo_head → None) → fail-closed HALT, never dispatch on an
    # unknowable base; the task stays todo (auto-retried on resume).
    monkeypatch.setattr(orchestrator, "_repo_head", lambda root: None)
    fake_claude.response = _build_fake()  # would build/audit IF anything were ever dispatched
    version, project = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _epic, _feat, (task,) = _seed_one_feat(db_session, version, project, ["T"])
    _to_build(db_session, version)

    state = await orchestrator.run_dispatch(db_session, version.id)

    assert state.status == "awaiting_director"  # HALT, fail-closed
    db_session.refresh(task)
    assert task.status == "todo"  # precondition failure (not a failed attempt) → retried on resume
    assert task.baseline_sha is None
    msgs = _msgs(db_session, version.id)
    assert not any(m.author == "implementer" for m in msgs)  # never built on an unknowable base
    assert not any(m.author == "auditor" for m in msgs)
    assert any(m.author == "coordinator" and m.stage == "build" for m in msgs)  # relayed to the Director
    # CR-NS-033: the baseline-HALT relay prompt instructs the Coordinator to triage + emit a directive
    assert any("coordinator_directive" in c["prompt"] for c in fake_claude.calls)


async def test_approve_at_build_blocked_while_todo_remains(db_session, fake_claude):
    # CR-4.1 (option B): the final build sign-off is invalid while a task is still todo — closes
    # the baseline-HALT hole (a todo task isn't counted by _build_open_findings).
    version, project = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _seed_one_feat(db_session, version, project, ["T-a", "T-b"])  # both todo
    state = _to_build(db_session, version)
    state.status = "awaiting_director"
    db_session.flush()
    with pytest.raises(orchestrator.OrchestratorError):
        await orchestrator.apply_action(db_session, version_id=version.id, action="approve")


async def test_continue_build_resumes_at_halt(db_session, fake_claude, monkeypatch):
    # CR-5 §7.2: continue_build re-dispatches the build loop after a HALT, no comment, no stage
    # advance; the audit record is Director↔Coordinator (the engine re-dispatches the Implementer).
    monkeypatch.setattr(orchestrator, "_repo_head", lambda root: "c" * 40)
    fake_claude.response = _build_fake()
    version, project = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _seed_one_feat(db_session, version, project, ["T"])
    state = _to_build(db_session, version)
    state.status = "awaiting_director"  # HALT
    db_session.flush()

    state = await orchestrator.apply_action(db_session, version_id=version.id, action="continue_build")

    assert state.current_stage == "build"  # stage unchanged — does NOT advance to gate_g
    assert state.status == "agent_working"  # re-dispatch initiated (the route then schedules the loop)
    cont = [m for m in _msgs(db_session, version.id) if m.kind == "approval" and "pokračuje" in m.content]
    assert cont and cont[-1].author == "director" and cont[-1].recipient == "coordinator"  # §6/§7 rule


async def test_continue_build_resumes_from_paused(db_session, fake_claude):
    # CR-NS-027: continue_build is valid from 'paused' ('paused' is in the advancing-action allow-list)
    # and re-dispatches the loop → agent_working (the pause↔resume pair).
    version, project = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _seed_one_feat(db_session, version, project, ["T"])
    state = _to_build(db_session, version)
    state.status = "paused"  # Director paused the build
    db_session.flush()

    state = await orchestrator.apply_action(db_session, version_id=version.id, action="continue_build")

    assert state.current_stage == "build"
    assert state.status == "agent_working"  # resumed — the route then schedules the loop


async def test_end_build_from_paused_advances_to_gate_g(db_session, fake_claude):
    # CR-NS-027: end_build is the other half of the resume pair valid from 'paused' — skip the rest →
    # gate_g (todo tasks don't block end_build).
    version, project = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _seed_one_feat(db_session, version, project, ["T-a", "T-b"])  # stay todo
    state = _to_build(db_session, version)
    state.status = "paused"
    db_session.flush()

    state = await orchestrator.apply_action(db_session, version_id=version.id, action="end_build")

    assert state.current_stage == "gate_g"


async def test_ask_rejected_from_paused(db_session, fake_claude):
    # CR-NS-027: from 'paused' only continue_build / end_build are valid. 'ask' (not in
    # _ADVANCING_ACTIONS) must NOT silently un-pause — without the dedicated paused guard it would fall
    # to its handler, call _begin_dispatch and flip the status back to agent_working.
    version, project = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _seed_one_feat(db_session, version, project, ["T"])
    state = _to_build(db_session, version)
    state.status = "paused"
    db_session.flush()

    with pytest.raises(orchestrator.OrchestratorError):
        await orchestrator.apply_action(db_session, version_id=version.id, action="ask", payload={"text": "otázka?"})
    st = orchestrator._get_state(db_session, version.id)
    assert st.status == "paused"  # still paused — ask did not un-pause


async def test_continue_build_rejected_outside_build(db_session, fake_claude):
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _settle(db_session, version.id)  # kickoff / awaiting_director (past the status guard)
    with pytest.raises(orchestrator.OrchestratorError):
        await orchestrator.apply_action(db_session, version_id=version.id, action="continue_build")


# ── restart-mid-build recovery (F-007 §7.3, CR-NS-021) ──────────────────────────


async def test_recover_orphaned_build_at_agent_working(db_session, fake_claude):
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    state = _to_build(db_session, version)  # build / agent_working (stranded by a restart)
    assert state.status == "agent_working"

    n = orchestrator.recover_orphaned_builds_on_startup(db_session)

    assert n == 1
    state = orchestrator._get_state(db_session, version.id)
    assert state.current_stage == "build"
    assert state.status == "awaiting_director"
    assert "Pokračovať v builde" in state.next_action
    notif = [
        m
        for m in _msgs(db_session, version.id)
        if m.author == "system" and m.recipient == "director" and m.kind == "notification" and m.stage == "build"
    ]
    assert notif and "reštartom" in notif[-1].content


async def test_recover_leaves_awaiting_director_build_untouched(db_session, fake_claude):
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    state = _to_build(db_session, version)
    state.status = "awaiting_director"  # already settled — not stranded
    db_session.flush()
    assert orchestrator.recover_orphaned_builds_on_startup(db_session) == 0


async def test_recover_leaves_non_build_agent_working_untouched(db_session, fake_claude):
    # kickoff/agent_working after start — a non-build stage is NOT recovered (build-only).
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    assert orchestrator._get_state(db_session, version.id).status == "agent_working"
    assert orchestrator.recover_orphaned_builds_on_startup(db_session) == 0
    assert orchestrator._get_state(db_session, version.id).status == "agent_working"  # untouched


async def test_recover_then_continue_build_reclaims_and_continues(db_session, fake_claude, monkeypatch):
    # End-to-end: a build stranded mid-task → recover → continue_build → _run_build_round reclaims
    # the orphaned in_progress task (from its persisted baseline) and finishes it.
    monkeypatch.setattr(orchestrator, "_repo_head", lambda root: "a" * 40)
    fake_claude.response = _build_fake()
    version, project = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _epic, _feat, (task,) = _seed_one_feat(db_session, version, project, ["T"])
    task.status = "in_progress"  # the Programmer was mid-task when the backend restarted
    task.baseline_sha = "0" * 40  # baseline persisted by that dispatch
    _to_build(db_session, version)
    db_session.flush()

    assert orchestrator.recover_orphaned_builds_on_startup(db_session) == 1
    state = await orchestrator.apply_action(db_session, version_id=version.id, action="continue_build")
    assert state.status == "agent_working"
    state = await orchestrator.run_dispatch(db_session, version.id)

    db_session.refresh(task)
    assert task.status == "done"  # reclaimed (in_progress→todo) + re-run → completed
    assert state.status == "awaiting_director"


# ── CR-NS-022: comms reform + re-run blocked task_plan ──────────────────────────


async def test_parse_retry_recovers_without_director_leak(db_session, monkeypatch):
    # §2a: an intermediate parse failure that the retry recovers must NOT leak a system→director
    # notification — only a FINAL, unrecovered failure surfaces (and via the Coordinator).
    fake = SequenceClaude(
        ["garbage — no block", _block(stage="gate_a", kind="gate_report", summary="ok", awaiting="director")]
    )
    monkeypatch.setattr(orchestrator, "invoke_claude", fake)
    version, _ = _make_version(db_session)
    result = await orchestrator.invoke_agent_with_parse_retry(
        db_session, version_id=version.id, role="designer", stage="gate_a", prompt="go"
    )
    assert isinstance(result, PipelineStatusBlock)  # recovered on the retry
    assert not any(m.author == "system" and m.recipient == "director" for m in _msgs(db_session, version.id))


async def test_return_at_task_plan_keeps_designer_session_for_refine(db_session, fake_claude):
    # CR-NS-024: return@task_plan is INCREMENTAL refine — it KEEPS the (slug, designer) --resume
    # session so the Designer remembers the prior plan and applies just the Director's edit (the
    # comment threads into the brief), instead of re-decomposing from scratch. (CR-NS-022 §3 used to
    # delete the session for a one-time charter reload; that need is satisfied and is not paid on
    # every refine-return.)
    from backend.db.models.orchestrator import OrchestratorSession

    version, project = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    state = orchestrator._get_state(db_session, version.id)
    state.current_stage = "task_plan"
    state.current_actor = "designer"
    state.status = "awaiting_director"  # plan is on the table, Director refines it
    db_session.flush()
    seeded_id = uuid.uuid4()
    db_session.add(OrchestratorSession(project_slug=project.slug, role="designer", claude_session_id=seeded_id))
    db_session.flush()

    comment = "rozdeľ poslednú úlohu na dve, zvyšok plánu nechaj"
    state = await orchestrator.apply_action(
        db_session, version_id=version.id, action="return", payload={"comment": comment}
    )

    assert state.status == "agent_working"  # return still re-dispatches the Designer
    row = db_session.execute(
        select(OrchestratorSession).where(
            OrchestratorSession.project_slug == project.slug, OrchestratorSession.role == "designer"
        )
    ).scalar_one_or_none()
    assert row is not None  # session KEPT → next dispatch is --resume (refine, not rebuild)
    assert row.claude_session_id == seeded_id  # the SAME session — charter is NOT re-injected
    # The Director's edit threads into the brief: it is recorded as a return message at task_plan.
    returns = [
        m
        for m in _msgs(db_session, version.id)
        if m.author == "director" and m.kind == "return" and m.stage == "task_plan"
    ]
    assert returns and returns[-1].content == comment


async def test_return_at_task_plan_blocked_also_keeps_session(db_session, fake_claude):
    # CR-NS-024 regression: the return handler does NOT branch on status, so a return reachable from
    # task_plan/BLOCKED (the prior live nex-ledger condition, surfaced via the CR-NS-018 questionBlock)
    # keeps the Designer session just like the awaiting_director refine path. Guards the equivalence the
    # main test relies on, so the blocked origin stays covered after CR-NS-022's delete was dropped.
    from backend.db.models.orchestrator import OrchestratorSession

    version, project = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    state = orchestrator._get_state(db_session, version.id)
    state.current_stage = "task_plan"
    state.current_actor = "designer"
    state.status = "blocked"
    db_session.flush()
    seeded_id = uuid.uuid4()
    db_session.add(OrchestratorSession(project_slug=project.slug, role="designer", claude_session_id=seeded_id))
    db_session.flush()

    state = await orchestrator.apply_action(
        db_session, version_id=version.id, action="return", payload={"comment": "oprav module_id a doplň testy"}
    )

    assert state.status == "agent_working"  # return re-dispatches from blocked too
    row = db_session.execute(
        select(OrchestratorSession).where(
            OrchestratorSession.project_slug == project.slug, OrchestratorSession.role == "designer"
        )
    ).scalar_one_or_none()
    assert row is not None and row.claude_session_id == seeded_id  # kept → --resume, no charter re-inject


async def test_start_resets_all_agent_sessions(db_session, fake_claude):
    # WS-B1 (CR-NS-029): a new-version kickoff ("start") drops ALL of the project's OrchestratorSession
    # rows so every agent starts fresh — no stale cross-version --resume context.
    from backend.db.models.orchestrator import OrchestratorSession

    version, project = _make_version(db_session)
    for role in ("designer", "coordinator", "implementer", "auditor", "customer"):
        db_session.add(OrchestratorSession(project_slug=project.slug, role=role, claude_session_id=uuid.uuid4()))
    db_session.flush()

    await orchestrator.apply_action(db_session, version_id=version.id, action="start")

    rows = (
        db_session.execute(select(OrchestratorSession).where(OrchestratorSession.project_slug == project.slug))
        .scalars()
        .all()
    )
    assert rows == []  # all 5 agent sessions reset on kickoff


async def test_regate_preserves_agent_sessions(db_session, fake_claude):
    # WS-B1 / Director decision D2: a re-gate (verdict FAIL → rewind) PRESERVES sessions — it's a
    # refinement, not a fresh start, and never reaches the "start" reset branch.
    from backend.db.models.orchestrator import OrchestratorSession

    version, project = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    seeded_id = uuid.uuid4()
    db_session.add(OrchestratorSession(project_slug=project.slug, role="designer", claude_session_id=seeded_id))
    db_session.flush()
    state = orchestrator._get_state(db_session, version.id)
    state.current_stage = "gate_g"
    state.current_actor = "auditor"
    state.status = "awaiting_director"
    db_session.flush()

    state = await orchestrator.apply_action(
        db_session, version_id=version.id, action="verdict", payload={"verdict": "FAIL", "entry_stage": "gate_a"}
    )

    assert state.is_regate is True and state.current_stage == "gate_a"  # re-gate rewound
    row = db_session.execute(
        select(OrchestratorSession).where(
            OrchestratorSession.project_slug == project.slug, OrchestratorSession.role == "designer"
        )
    ).scalar_one_or_none()
    assert row is not None and row.claude_session_id == seeded_id  # session kept (D2)
