"""Tests for the pipeline orchestrator engine (CR-NS-018 Phase 2).

Live claude is replaced by a controllable fake ``invoke_claude`` — the engine
logic (session resolution, message writes, state transitions, FAIL loop,
verify retries) is exercised against synthetic §5.3 blocks.
"""

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, update

from backend.db.models.backlog import BacklogItem
from backend.db.models.foundation import User, UserAgentSettings
from backend.db.models.orchestrator import OrchestratorSession
from backend.db.models.pipeline import PipelineMessage, PipelineState
from backend.db.models.projects import Project
from backend.db.models.tasks import Epic, Feat, Task
from backend.db.models.versions import Version
from backend.services import claude_agent, orchestrator
from backend.services import task as task_service
from backend.services.pipeline_status import ParseFailure, PipelineStatusBlock, parse_status_block


def _block(stage="gate_a", kind="gate_report", summary="ok", awaiting="director", **extra) -> str:
    body = {"stage": stage, "kind": kind, "summary": summary, "awaiting": awaiting}
    body.update(extra)
    return f"<<<PIPELINE_STATUS>>>\n{json.dumps(body)}\n<<<END_PIPELINE_STATUS>>>"


def _block_dict(stage="gate_a", kind="gate_report", summary="ok", awaiting="director", **extra) -> dict:
    """The status block as a plain dict — the shape claude returns in ``structured_output`` (R3)."""
    body = {"stage": stage, "kind": kind, "summary": summary, "awaiting": awaiting}
    body.update(extra)
    return body


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

    async def __call__(
        self,
        *,
        project_slug,
        claude_session_id,
        prompt,
        charter_path=None,
        timeout=180,
        on_event=None,
        model=None,
        effort=None,
        json_schema=None,
    ):
        self.calls.append(
            {
                "project_slug": project_slug,
                "session": claude_session_id,
                "prompt": prompt,
                "model": model,
                "effort": effort,
                "json_schema": json_schema,
            }
        )
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


# ── R3 (v0.7.0): native structured output preferred; fence is the fallback ──────


async def test_invoke_agent_prefers_structured_output(db_session, fake_claude):
    """R3 D1/D2: when claude returns a grammar-constrained ``structured_output`` object, the engine
    validates + records IT — no fence in the result text is needed."""
    version, _ = _make_version(db_session)
    # The result text carries NO fence — only the structured_output object does.
    fake_claude.response = (
        "no fence here — just prose",
        None,
        _block_dict(stage="gate_b", kind="gate_report", summary="from structured", commits=["s1"]),
    )
    result = await orchestrator.invoke_agent(
        db_session, version_id=version.id, role="designer", stage="gate_b", prompt="go"
    )
    assert isinstance(result, PipelineStatusBlock)
    assert result.summary == "from structured"
    msgs = _msgs(db_session, version.id)
    assert len(msgs) == 1
    assert msgs[0].content == "from structured"
    assert msgs[0].payload["commits"] == ["s1"]


async def test_invoke_agent_structured_invalid_falls_back_to_fence(db_session, fake_claude):
    """R3 D2: a structured_output that fails the content contract degrades to the fence parse of the
    result text — non-breaking + rollout-safe (the fence parser STAYS as the fallback)."""
    version, _ = _make_version(db_session)
    fake_claude.response = (
        _block(stage="gate_b", kind="gate_report", summary="from fence"),  # valid fence in the text
        None,
        _block_dict(stage="not_a_real_stage", summary="bogus"),  # structured fails (unknown stage)
    )
    result = await orchestrator.invoke_agent(
        db_session, version_id=version.id, role="designer", stage="gate_b", prompt="go"
    )
    assert isinstance(result, PipelineStatusBlock)
    assert result.summary == "from fence"  # fence fallback won


async def test_invoke_agent_structured_invalid_no_fence_is_parsefailure(db_session, fake_claude):
    """R3 D2/D3: structured_output invalid AND no fence in the text → the SAME silent ParseFailure the
    fence path returns, which the dispatch layer feeds to the bounded parse-retry → escalation."""
    version, _ = _make_version(db_session)
    fake_claude.response = (
        "prose with no fence",
        None,
        _block_dict(stage="bogus_stage"),  # structured invalid; nothing to fall back to
    )
    result = await orchestrator.invoke_agent(
        db_session, version_id=version.id, role="designer", stage="gate_a", prompt="go"
    )
    assert isinstance(result, ParseFailure)
    assert _msgs(db_session, version.id) == []  # silent (no Director leak), like the fence path


async def test_invoke_agent_passes_status_schema_to_claude(db_session, fake_claude):
    """R3: the engine always invokes the agent with the PipelineStatusBlock JSON Schema (Gate E is the
    only no-schema path — that's dialogue.py, not invoke_agent)."""
    version, _ = _make_version(db_session)
    fake_claude.response = _block(stage="gate_b", kind="gate_report", summary="ok")
    await orchestrator.invoke_agent(db_session, version_id=version.id, role="designer", stage="gate_b", prompt="go")
    assert fake_claude.calls  # the fake captured the call
    assert fake_claude.calls[-1]["json_schema"] == orchestrator.PIPELINE_STATUS_JSON_SCHEMA


# ── WS-D metrics: usage + timing capture (CR-NS-036) ────────────────────────────


async def test_invoke_agent_records_usage_and_timing(db_session, fake_claude):
    """A turn's token usage + dispatch wall-clock land in payload.usage / payload.timing (WS-D)."""
    version, _ = _make_version(db_session)
    # FakeClaude returns whatever `response` is — a (text, UsageMetadata) tuple here, like real claude.
    fake_claude.response = (
        _block(stage="gate_b", kind="gate_report", summary="ok"),
        claude_agent.UsageMetadata(input_tokens=100, output_tokens=40, model="claude-z"),
    )
    await orchestrator.invoke_agent(db_session, version_id=version.id, role="designer", stage="gate_b", prompt="go")
    msg = _msgs(db_session, version.id)[0]
    assert msg.payload["usage"] == {"input_tokens": 100, "output_tokens": 40, "model": "claude-z"}
    assert msg.payload["timing"]["parse_attempts"] == 1
    assert msg.payload["timing"]["duration_seconds"] >= 0.0


async def test_invoke_agent_no_usage_records_none_not_zeros(db_session, fake_claude):
    """A bare-text response (no usage envelope) → payload.usage is None, never fabricated zeros (WS-D)."""
    version, _ = _make_version(db_session)
    fake_claude.response = _block(stage="gate_b", kind="gate_report", summary="ok")  # bare str → usage None
    await orchestrator.invoke_agent(db_session, version_id=version.id, role="designer", stage="gate_b", prompt="go")
    msg = _msgs(db_session, version.id)[0]
    assert msg.payload["usage"] is None
    assert msg.payload["timing"]["parse_attempts"] == 1


# ── CR-NS-040: per-dispatch model/effort from the project owner's config ─────────


def _make_version_with_owner_config(db_session, configs):
    """version+project whose OWNER has the given user_agent_settings rows.

    ``configs``: iterable of ``(agent_role, model, effort)``. Returns ``(version, owner)``.
    """
    owner = User(
        username=f"o_{uuid.uuid4().hex[:8]}",
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        password_hash="x",
        role="ri",
    )
    db_session.add(owner)
    db_session.flush()
    project = Project(
        name=f"P {uuid.uuid4().hex[:8]}",
        slug=f"p-{uuid.uuid4().hex[:8]}",
        category="singlemodule",
        description="d",
        created_by=owner.id,
        owner_id=owner.id,
    )
    db_session.add(project)
    db_session.flush()
    version = Version(project_id=project.id, version_number=f"1.{uuid.uuid4().hex[:4]}.0")
    db_session.add(version)
    db_session.flush()
    for role, model, effort in configs:
        db_session.add(UserAgentSettings(user_id=owner.id, agent_role=role, model=model, effort=effort))
    db_session.flush()
    return version, owner


def test_resolve_overrides_owner_config_applies(db_session):
    version, _ = _make_version_with_owner_config(db_session, [("designer", "claude-sonnet-4-6", "high")])
    assert orchestrator._resolve_dispatch_overrides(db_session, version.id, "designer") == (
        "claude-sonnet-4-6",
        "high",
    )


def test_resolve_overrides_unset_role_no_flags(db_session):
    version, _ = _make_version_with_owner_config(db_session, [("designer", "claude-sonnet-4-6", "high")])
    assert orchestrator._resolve_dispatch_overrides(db_session, version.id, "auditor") == (None, None)


def test_resolve_overrides_coordinator_defaults_max(db_session):
    version, _ = _make_version_with_owner_config(db_session, [])
    assert orchestrator._resolve_dispatch_overrides(db_session, version.id, "coordinator") == (None, "max")


def test_resolve_overrides_coordinator_explicit_overrides_default(db_session):
    version, _ = _make_version_with_owner_config(db_session, [("coordinator", "claude-opus-4-8", "low")])
    assert orchestrator._resolve_dispatch_overrides(db_session, version.id, "coordinator") == (
        "claude-opus-4-8",
        "low",
    )


def test_resolve_overrides_no_owner_falls_back(db_session):
    # _make_version leaves owner_id NULL → no config; coordinator still defaults to max, others no flags.
    version, _ = _make_version(db_session)
    assert orchestrator._resolve_dispatch_overrides(db_session, version.id, "designer") == (None, None)
    assert orchestrator._resolve_dispatch_overrides(db_session, version.id, "coordinator") == (None, "max")


async def test_invoke_agent_threads_owner_model_effort(db_session, fake_claude):
    version, _ = _make_version_with_owner_config(db_session, [("designer", "claude-sonnet-4-6", "high")])
    await orchestrator.invoke_agent(db_session, version_id=version.id, role="designer", stage="gate_b", prompt="go")
    assert fake_claude.calls[-1]["model"] == "claude-sonnet-4-6"
    assert fake_claude.calls[-1]["effort"] == "high"


async def test_invoke_agent_unset_role_no_flags(db_session, fake_claude):
    version, _ = _make_version_with_owner_config(db_session, [])
    await orchestrator.invoke_agent(db_session, version_id=version.id, role="designer", stage="gate_b", prompt="go")
    assert fake_claude.calls[-1]["model"] is None
    assert fake_claude.calls[-1]["effort"] is None


async def test_parse_retry_keeps_model_effort(db_session, fake_claude):
    """Each parse-retry re-enters invoke_agent → re-resolves + re-applies the owner config (no loss)."""
    version, _ = _make_version_with_owner_config(db_session, [("designer", "claude-sonnet-4-6", "high")])
    # Primary (prompt "go") fails to parse; the retry (prompt starts "Tvoj…") emits a valid block.
    fake_claude.response = lambda prompt: (
        _block(stage="gate_b", kind="gate_report", summary="ok") if prompt.startswith("Tvoj") else "no status block"
    )
    result = await orchestrator.invoke_agent_with_parse_retry(
        db_session, version_id=version.id, role="designer", stage="gate_b", prompt="go"
    )
    assert isinstance(result, PipelineStatusBlock)
    assert len(fake_claude.calls) >= 2  # primary + at least one retry
    assert all(c["model"] == "claude-sonnet-4-6" and c["effort"] == "high" for c in fake_claude.calls)


# ── CR-NS-042: gate_a backlog injection ─────────────────────────────────────────


def _add_included_backlog(db_session, project_id, version_id, *, number, title, description=None):
    item = BacklogItem(
        project_id=project_id,
        number=number,
        title=title,
        description=description,
        status="included",
        version_id=version_id,
    )
    db_session.add(item)
    db_session.flush()
    return item


def test_augment_brief_injects_included_at_gate_a(db_session):
    version, project = _make_version(db_session)
    _add_included_backlog(db_session, project.id, version.id, number=1, title="PDF rotácia", description="Otočiť skeny")
    _add_included_backlog(db_session, project.id, version.id, number=2, title="IBAN validácia")

    out = orchestrator._augment_brief_with_backlog(db_session, version.id, "gate_a", "PÔVODNÝ BRIEF")

    assert "Zákaznícke požiadavky (z backlogu)" in out
    assert "REQ-1: PDF rotácia" in out and "Otočiť skeny" in out
    assert "REQ-2: IBAN validácia" in out
    assert out.endswith("PÔVODNÝ BRIEF")  # the original brief is preserved, block prepended


def test_augment_brief_noop_other_stage(db_session):
    version, project = _make_version(db_session)
    _add_included_backlog(db_session, project.id, version.id, number=1, title="x")
    # gate_b/c/d read what gate_a wrote — no re-injection
    assert orchestrator._augment_brief_with_backlog(db_session, version.id, "gate_b", "BRIEF") == "BRIEF"


def test_augment_brief_noop_when_no_included(db_session):
    version, project = _make_version(db_session)
    # an OPEN (not included) item must not inject
    db_session.add(BacklogItem(project_id=project.id, number=1, title="open-only", status="open"))
    db_session.flush()
    assert orchestrator._augment_brief_with_backlog(db_session, version.id, "gate_a", "BRIEF") == "BRIEF"


# ── CR-NS-042: E7 capture_backlog_item ──────────────────────────────────────────


def _build_state(db_session, version):
    state = PipelineState(
        version_id=version.id,
        flow_type="new_version",
        current_stage="build",
        current_actor="implementer",
        status="awaiting_director",
        next_action="x",
    )
    db_session.add(state)
    db_session.flush()
    return state


def test_capture_backlog_item_executor(db_session):
    version, project = _make_version(db_session)
    state = _build_state(db_session, version)
    directive = {
        "proposed_action": "capture_backlog_item",
        "triage_class": "programmer_guidance",
        "confidence": 0.9,
        "params": {"title": "Nová požiadavka", "description": "Popis", "priority": "high"},
        "rationale": "r",
    }

    returned = orchestrator._execute_coordinator_directive(db_session, state, directive)
    assert returned is state  # non-blocking — no re-dispatch, stays settled

    items = db_session.execute(select(BacklogItem).where(BacklogItem.project_id == project.id)).scalars().all()
    assert len(items) == 1
    assert items[0].title == "Nová požiadavka"
    assert items[0].priority == "high"
    assert items[0].status == "open"
    assert items[0].number == 1  # REQ-1
    # the director→coordinator audit message was recorded
    msgs = _msgs(db_session, version.id)
    assert any(m.author == "director" and m.recipient == "coordinator" and "REQ-1" in m.content for m in msgs)


def test_capture_invalid_priority_falls_back(db_session):
    version, project = _make_version(db_session)
    state = _build_state(db_session, version)
    directive = {"proposed_action": "capture_backlog_item", "params": {"title": "X", "priority": "urgent"}}
    orchestrator._execute_coordinator_directive(db_session, state, directive)
    item = db_session.execute(select(BacklogItem).where(BacklogItem.project_id == project.id)).scalar_one()
    assert item.priority == "medium"  # out-of-enum priority defended → medium


def test_capture_missing_title_raises(db_session):
    version, _ = _make_version(db_session)
    state = _build_state(db_session, version)
    directive = {"proposed_action": "capture_backlog_item", "params": {"description": "no title"}}
    with pytest.raises(orchestrator.OrchestratorError):
        orchestrator._execute_coordinator_directive(db_session, state, directive)


def test_capture_is_gate_exempt(db_session):
    # Director-instructed write → executable regardless of triage_class / confidence.
    assert orchestrator._coordinator_directive_executable({"proposed_action": "capture_backlog_item"}) is True
    assert (
        orchestrator._coordinator_directive_executable(
            {"proposed_action": "capture_backlog_item", "triage_class": "director_decision", "confidence": 0.1}
        )
        is True
    )
    # the existing triage actions keep their triage_class/confidence gate
    assert (
        orchestrator._coordinator_directive_executable(
            {"proposed_action": "coordinator_reset_task", "triage_class": "director_decision", "confidence": 0.9}
        )
        is False
    )


async def test_parse_retry_accumulates_usage_and_attempts(db_session, monkeypatch):
    """Failed parse re-emits burn tokens too — the surviving message sums across the primary + every
    retry, and timing.parse_attempts counts them (WS-D)."""
    seq = [
        ("garbage — not a valid status block", claude_agent.UsageMetadata(10, 5, "m")),  # ParseFailure
        (_block(stage="gate_b", kind="gate_report", summary="ok"), claude_agent.UsageMetadata(20, 8, "m")),
    ]
    calls = {"n": 0}

    async def _fake(*, prompt, **kwargs):
        i = min(calls["n"], len(seq) - 1)
        calls["n"] += 1
        return seq[i]

    monkeypatch.setattr(orchestrator, "invoke_claude", _fake)
    version, _ = _make_version(db_session)
    result = await orchestrator.invoke_agent_with_parse_retry(
        db_session, version_id=version.id, role="designer", stage="gate_b", prompt="go"
    )
    assert isinstance(result, PipelineStatusBlock)
    msg = [m for m in _msgs(db_session, version.id) if m.payload and "usage" in m.payload][-1]
    assert msg.payload["usage"]["input_tokens"] == 30  # 10 (failed re-emit) + 20 (success)
    assert msg.payload["usage"]["output_tokens"] == 13  # 5 + 8
    assert msg.payload["timing"]["parse_attempts"] == 2  # primary + one recovery re-emit


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
    assert state.block_reason == "parse_exhaustion"  # R4 (D1): no parseable output after retries


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
    assert state.block_reason == "agent_question"  # R4 (D1): worker question→blocked
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


# ── rerun_release_audit (v0.7.6: re-run the release audit at a settled gate_g) ───


async def test_rerun_release_audit_redispatches_auditor_without_advancing(db_session, fake_claude):
    # v0.7.6: at a settled gate_g the action re-dispatches the Auditor WITHOUT advancing the stage
    # (mirrors continue_build, NOT verdict) — status→agent_working, stage stays gate_g, actor=auditor.
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    st = orchestrator._get_state(db_session, version.id)
    st.current_stage = "gate_g"
    st.current_actor = "auditor"
    st.status = "awaiting_director"
    db_session.flush()
    state = await orchestrator.apply_action(db_session, version_id=version.id, action="rerun_release_audit")
    assert state.current_stage == "gate_g"  # NOT advanced (unlike verdict PASS → release)
    assert state.status == "agent_working"
    assert state.current_actor == "auditor"
    # a director→auditor directive message carrying the re-audit brief was recorded
    directives = [
        m
        for m in _msgs(db_session, version.id)
        if m.kind == "directive" and m.author == "director" and m.recipient == "auditor"
    ]
    assert len(directives) == 1
    assert "release audit" in directives[0].content
    assert directives[0].payload == {"rerun_release_audit": True}


async def test_rerun_release_audit_rejected_off_gate_g(db_session, fake_claude):
    # v0.7.6: the handler asserts current_stage == gate_g — invalid anywhere else (e.g. a settled gate_a).
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    st = orchestrator._get_state(db_session, version.id)
    st.current_stage = "gate_a"
    st.current_actor = "designer"
    st.status = "awaiting_director"
    db_session.flush()
    with pytest.raises(orchestrator.OrchestratorError, match="gate_g"):
        await orchestrator.apply_action(db_session, version_id=version.id, action="rerun_release_audit")
    st = orchestrator._get_state(db_session, version.id)
    assert st.current_stage == "gate_a" and st.status == "awaiting_director"  # unchanged


async def test_rerun_release_audit_rejected_while_auditor_working(db_session, fake_claude):
    # Stale-board guard: rerun_release_audit is in _ADVANCING_ACTIONS, so a stale/double-click POST at
    # gate_g while the Auditor is mid-audit (agent_working) is rejected — never re-dispatches on top of a
    # working agent (CR-NS-018 class). The FE never offers it there (determine_available_actions = {}).
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    st = orchestrator._get_state(db_session, version.id)
    st.current_stage = "gate_g"
    st.current_actor = "auditor"
    st.status = "agent_working"
    db_session.flush()
    with pytest.raises(orchestrator.OrchestratorError, match="ešte pracuje"):
        await orchestrator.apply_action(db_session, version_id=version.id, action="rerun_release_audit")
    st = orchestrator._get_state(db_session, version.id)
    assert st.status == "agent_working" and st.current_stage == "gate_g"  # unchanged


def test_rerun_release_audit_offered_only_at_settled_gate_g():
    # v0.7.6: offered at gate_g ONLY when settled (awaiting_director) — not while the Auditor works, not on
    # a blocked scope-escalation, and never at another stage.
    def acts(stage, status):
        return orchestrator.determine_available_actions(PipelineState(current_stage=stage, status=status))

    assert "rerun_release_audit" in acts("gate_g", "awaiting_director")
    assert "rerun_release_audit" not in acts("gate_g", "agent_working")
    assert "rerun_release_audit" not in acts("gate_g", "blocked")
    assert "rerun_release_audit" not in acts("release", "awaiting_director")
    assert "rerun_release_audit" not in acts("build", "awaiting_director")


def test_rerun_release_audit_absent_for_fast_fix():
    # v0.7.6 §6: gated to gate_g, which the fast-fix lane never reaches (FAST_FIX_STAGE_ORDER has no
    # gate_g) → never offered anywhere on that lane → byte-identical for fast-fix.
    for stage in orchestrator.FAST_FIX_STAGE_ORDER:
        for status in ("agent_working", "awaiting_director", "blocked", "paused", "done"):
            assert "rerun_release_audit" not in orchestrator.determine_available_actions(
                PipelineState(current_stage=stage, status=status)
            )


def test_directive_for_action_rerun_release_audit():
    # v0.7.6: a static re-audit brief (no payload), ending with the status-block instruction since it IS
    # the agent prompt when the route threads it (overriding the generic per-stage directive).
    directive = orchestrator.directive_for_action("rerun_release_audit", {}, "gate_g")
    assert directive is not None
    assert "release audit" in directive
    assert "acceptance" in directive
    assert "<<<PIPELINE_STATUS>>>" in directive


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
    assert state.block_reason == "system_error"  # R4 (D1): gate mechanical verify failed (engine-side)
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
    # primary + one recovery re-emit. No synthesis turn: kickoff is coordinator-authored, and the
    # synthesis fires ONLY for worker-authored decision points (CR-NS-053 fix-round 1 guard).
    assert len(fake.prompts) == 2
    assert not any("ZHRŇ" in p for p in fake.prompts)


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
    assert state.block_reason == "agent_question"  # R4 (D1): worker question routed via the Coordinator
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


# ── R4 operator-legibility board aggregations (v0.7.0, D3/D4/D5) ────────────────────────────────────


def _state_row(db_session, version, **over):
    defaults = dict(
        version_id=version.id,
        flow_type="new_version",
        current_stage="build",
        current_actor="implementer",
        status="blocked",
        next_action="",
    )
    defaults.update(over)
    st = PipelineState(**defaults)
    db_session.add(st)
    db_session.flush()
    return st


def _seed_autonomous_note(
    db_session, version_id, *, task_number, action="coordinator_reset_task", rationale="r", confidence=0.9
):
    return orchestrator._record_message(
        db_session,
        version_id=version_id,
        stage="build",
        author="coordinator",
        recipient="director",
        kind="notification",
        content=f"Koordinátor rozhodol (úloha #{task_number}): {rationale}",
        payload={
            "is_autonomous": True,
            "task_id": str(uuid.uuid4()),
            "task_number": task_number,
            "action": action,
            "rationale": rationale,
            "confidence": confidence,
        },
    )


def test_coordinator_triage_latest_at_settled_state(db_session):
    # D3: at a settled state the LATEST relay/escalation directive surfaces — even a NON-executable one
    # (director_decision / low-confidence), unlike the executable proposal WhosTurnBoard already shows.
    version, _ = _make_version(db_session)
    st = _state_row(db_session, version, status="blocked")
    _seed_coordinator_directive(
        db_session, version.id, _coord_directive(proposed_action="coordinator_reset_task", confidence=0.95)
    )
    _seed_coordinator_directive(
        db_session,
        version.id,
        _coord_directive(triage_class="director_decision", proposed_action="coordinator_escalate_dedo", confidence=0.4),
    )
    triage = orchestrator.coordinator_triage(db_session, version.id, st)
    assert triage == {
        "triage_class": "director_decision",
        "proposed_action": "coordinator_escalate_dedo",
        "confidence": 0.4,
    }


def test_coordinator_triage_absent_when_working(db_session):
    # D3: present only at a settled (awaiting_director / blocked) state — not while an agent is working.
    version, _ = _make_version(db_session)
    st = _state_row(db_session, version, status="agent_working")
    _seed_coordinator_directive(db_session, version.id, _coord_directive())
    assert orchestrator.coordinator_triage(db_session, version.id, st) is None


def test_coordinator_triage_none_without_directive(db_session):
    # D3: a directive-less coordinator synthesis (JSON-null) must NOT surface as a triage.
    version, _ = _make_version(db_session)
    st = _state_row(db_session, version, status="blocked")
    orchestrator._record_message(
        db_session,
        version_id=version.id,
        stage="build",
        author="coordinator",
        recipient="director",
        kind="gate_report",
        content="synthesis",
        payload={"coordinator_directive": None},
    )
    assert orchestrator.coordinator_triage(db_session, version.id, st) is None


def test_autonomous_decisions_summary_counts_and_recent(db_session):
    # D4: count = all is_autonomous coordinator notes; recent = newest-first, capped at 5.
    version, _ = _make_version(db_session)
    for i in range(1, 8):  # 7 autonomous notes
        _seed_autonomous_note(db_session, version.id, task_number=i, rationale=f"r{i}")
    # a non-autonomous coordinator note must NOT be counted
    orchestrator._record_message(
        db_session,
        version_id=version.id,
        stage="build",
        author="coordinator",
        recipient="director",
        kind="gate_report",
        content="not autonomous",
        payload={"coordinator_directive": None},
    )
    summary = orchestrator.autonomous_decisions_summary(db_session, version.id)
    assert summary["count"] == 7
    assert len(summary["recent"]) == 5  # capped
    assert summary["recent"][0]["task"] == 7  # newest first
    assert summary["recent"][0]["rationale"] == "r7"


def test_autonomous_decisions_summary_empty(db_session):
    version, _ = _make_version(db_session)
    assert orchestrator.autonomous_decisions_summary(db_session, version.id) == {"count": 0, "recent": []}


def test_agent_sessions_active_idle_stale(db_session):
    # D5: active = state is agent_working for the role; stale = last_input_at older than 30 min; else idle;
    # a missing session → idle.
    version, project = _make_version(db_session)
    st = _state_row(db_session, version, status="agent_working", current_actor="implementer")
    now = datetime.now(timezone.utc)
    db_session.add_all(
        [
            # implementer last_input is recent, but it IS the working role → active
            OrchestratorSession(
                project_slug=project.slug, role="implementer", claude_session_id=uuid.uuid4(), last_input_at=now
            ),
            # designer idle for 45 min → stale
            OrchestratorSession(
                project_slug=project.slug,
                role="designer",
                claude_session_id=uuid.uuid4(),
                last_input_at=now - timedelta(minutes=45),
            ),
            # auditor idle for 5 min → idle
            OrchestratorSession(
                project_slug=project.slug,
                role="auditor",
                claude_session_id=uuid.uuid4(),
                last_input_at=now - timedelta(minutes=5),
            ),
        ]
    )
    db_session.flush()
    sessions = {s["role"]: s["status"] for s in orchestrator.agent_sessions(db_session, version.id, st)}
    assert sessions == {
        "coordinator": "idle",  # missing session → idle
        "designer": "stale",
        "customer": "idle",  # missing session → idle
        "implementer": "active",
        "auditor": "idle",
    }


def test_agent_sessions_none_state_all_idle(db_session):
    version, project = _make_version(db_session)
    db_session.add(
        OrchestratorSession(
            project_slug=project.slug,
            role="designer",
            claude_session_id=uuid.uuid4(),
            last_input_at=datetime.now(timezone.utc),
        )
    )
    db_session.flush()
    # No state → no working role; the recent designer session is idle (not active).
    sessions = {s["role"]: s["status"] for s in orchestrator.agent_sessions(db_session, version.id, None)}
    assert sessions["designer"] == "idle"
    assert all(v == "idle" for v in sessions.values())


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
    assert state.block_reason == "agent_error"  # R4 (D1): _block_failed — the worker spec-fix turn failed
    assert state.returns_to is None  # cleared → return / continue_build now behave normally
    db_session.refresh(task)
    assert task.status == "failed"  # still held — the spec-fix didn't complete


# ── WS-C2: "kto je na rade" current build task (CR-NS-035) ──────────────────────────────────────────


async def test_current_build_task_picks_in_progress_then_failed(db_session, fake_claude):
    # CR-NS-035: the board's current-task = in_progress while building, else the held failed task.
    version, project = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _epic, _feat, (t1, t2) = _seed_one_feat(db_session, version, project, ["A", "B"])
    assert orchestrator.current_build_task(db_session, version.id) is None  # all todo → none in focus
    t2.status = "in_progress"
    db_session.flush()
    assert orchestrator.current_build_task(db_session, version.id).number == t2.number  # in_progress wins
    t2.status = "done"
    t1.status = "failed"
    db_session.flush()
    assert orchestrator.current_build_task(db_session, version.id).number == t1.number  # else the failed (held)


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
            # CR-NS-053 §A.2 site 3: the Coordinator synthesis turn fires at the boundary before settling
            # (kind ∈ BLOCK_KINDS — "done"; the FE distinguishes the synthesis via payload.is_synthesis).
            _block(stage="gate_e", kind="done", summary="Okruh prihlásenie uzavretý — rozhodni.", awaiting="director"),
        ]
    )
    monkeypatch.setattr(orchestrator, "invoke_claude", seq)
    monkeypatch.setattr(orchestrator, "verify_mechanical", lambda slug, block: None)

    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _to_gate_e(db_session, version)
    state = await orchestrator.run_dispatch(db_session, version.id)

    assert state.status == "awaiting_director"
    # Customer boundary turn + the Coordinator synthesis turn — no Designer routing (that would be a 3rd turn).
    assert len(seq.prompts) == 2
    # The synthesis drives next_action (its summary), replacing the old raw boundary line.
    assert state.next_action == "Okruh prihlásenie uzavretý — rozhodni."
    # The synthesis is recorded as a coordinator→director message marked is_synthesis (site 3).
    syn = [m for m in _msgs(db_session, version.id) if m.payload.get("is_synthesis")]
    assert len(syn) == 1 and syn[0].author == "coordinator" and syn[0].recipient == "director"


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
    """Build a task_plan ``plan`` payload from (epic_title, [(feat_title, [task,...])]).

    A task is ``(title, task_type)`` or ``(title, task_type, estimated_minutes)`` (E5, CR-NS-045 —
    human-effort minutes; feat-level is derived as Σ of its tasks' estimates).
    """

    def _task(t) -> dict:
        d = {"title": t[0], "task_type": t[1]}
        if len(t) > 2 and t[2] is not None:
            d["estimated_minutes"] = t[2]
        return d

    def _feat(f_title, tasks) -> dict:
        task_dicts = [_task(t) for t in tasks]
        ests = [d["estimated_minutes"] for d in task_dicts if "estimated_minutes" in d]
        feat: dict = {"title": f_title, "tasks": task_dicts}
        if ests:
            feat["estimated_minutes"] = sum(ests)
        return feat

    return {
        "epics": [
            {"title": e_title, "feats": [_feat(f_title, tasks) for f_title, tasks in feats]} for e_title, feats in epics
        ]
    }


def _epics_of(db_session, version):
    return db_session.execute(select(Epic).where(Epic.version_id == version.id)).scalars().all()


# ── (v0.7.3) incremental task_plan generation — narrowed multi-pass fake (CR-1) ──
# The task_plan stage generates the plan in bounded passes (skeleton + per-feat). In the REAL CLI
# ``--json-schema`` yields NO structured_output — the model emits the narrowed JSON as TEXT in a
# ``<<<TASK_PLAN_JSON>>>`` sentinel fence (live root-cause 2026-06-18). ``_plan_fake`` drives both:
# ``text=False`` returns a structured_output dict (forward-compat path); ``text=True`` returns the
# real-env TEXT+fence with structured=None. The post-write Coordinator synthesis gets a status-block fence.

_DEFAULT_CROSS = "## Invarianty\n- spoločná transakčná hranica\n- immutable audit"


def _task_plan_fence(obj: dict) -> str:
    """Wrap a narrowed-pass dict in the ``<<<TASK_PLAN_JSON>>>`` sentinel fence, amid prose noise — the
    shape real claude emits (the model writes commentary around the fenced JSON)."""
    return (
        "Tu je kostra/úlohy ako si žiadal:\n"
        f"<<<TASK_PLAN_JSON>>>\n{json.dumps(obj, ensure_ascii=False)}\n<<<END_TASK_PLAN_JSON>>>\n"
        "Hotovo."
    )


def _skeleton_dict(plan_spec, cross=_DEFAULT_CROSS) -> dict:
    """Skeleton-pass structured_output (EPIC + FEAT, NO tasks) from the same (epic, [(feat, [task,...])])
    spec shape :func:`_plan` uses. Feat ``estimated_minutes`` derived as Σ of its tasks' estimates."""
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
    """Per-feat-pass structured_output (``{tasks: [...]}``) from a ``[(title, type[, est]), ...]`` list."""
    out = []
    for t in tasks:
        d: dict = {"title": t[0], "task_type": t[1]}
        if len(t) > 2 and t[2] is not None:
            d["estimated_minutes"] = t[2]
        out.append(d)
    return {"tasks": out}


def _plan_fake(plan_spec, *, cross=_DEFAULT_CROSS, usage=None, text=False):
    """A ``callable(prompt)`` stand-in for ``invoke_claude`` driving ``_run_task_plan_round``'s passes.

    Keys on the prompt: the skeleton pass (contains ``"KOSTRU"``) → EPIC+FEAT(no tasks)+cross; a per-feat
    pass (the feat title appears) → that feat's tasks; anything else (the post-write Coordinator synthesis)
    → a valid task_plan status-block fence. Feat titles must be DISTINCT + non-substring within a spec.

    ``text=False`` returns the narrowed dict as ``structured_output`` (``("", usage, dict)`` — forward-compat
    path). ``text=True`` returns the REAL-ENV shape: prose + a ``<<<TASK_PLAN_JSON>>>`` fence as the result
    TEXT with ``structured_output=None`` (``(text, usage, None)``)."""
    feat_by_title = {f_title: tasks for _e, feats in plan_spec for f_title, tasks in feats}

    def _emit(obj: dict):
        return (_task_plan_fence(obj), usage, None) if text else ("", usage, obj)

    def _resp(prompt):
        if "KOSTRU" in prompt:
            return _emit(_skeleton_dict(plan_spec, cross))
        for f_title, tasks in feat_by_title.items():
            if f_title in prompt:
                return _emit(_feat_tasks_dict(tasks))
        return _block(
            stage="task_plan", kind="done", summary="Plán pripravený — schváľ alebo vráť.", awaiting="director"
        )

    return _resp


async def test_task_plan_write_path_materializes_hierarchy(db_session, fake_claude):
    version, project = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _to_task_plan(db_session, version)
    fake_claude.response = _plan_fake(
        [
            ("Foundation", [("Schema", [("GL+AA+AP tables", "migration", 90), ("audit_log", "migration", 30)])]),
            ("Calc cores", [("Hlavná kniha", [("GL výpočet", "backend", 120)])]),
        ],
        cross="## Invarianty\n- spoločná transakčná hranica\n- immutable audit",
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
    # E5 (CR-NS-045): per-task human-effort estimate round-trips; feat-level = Σ of its tasks.
    est_by_task = {t.title: t.estimated_minutes for t in tasks}
    assert est_by_task["GL+AA+AP tables"] == 90
    assert est_by_task["audit_log"] == 30
    assert est_by_task["GL výpočet"] == 120
    schema_feat = next(f for f in feats if f.title == "Schema")
    assert schema_feat.estimated_minutes == 120  # 90 + 30
    # the cross-cutting rules persist in the Designer's gate_report payload (CR-3 re-reads them)
    designer = [m for m in _msgs(db_session, version.id) if m.author == "designer" and m.stage == "task_plan"][-1]
    assert "transakčná" in designer.payload["cross_cutting_rules"]
    # a system summary message is recorded for the audit trail + future TaskPlanPanel
    assert any(
        m.author == "system" and m.stage == "task_plan" and "Plán úloh zapísaný" in m.content
        for m in _msgs(db_session, version.id)
    )


async def test_task_plan_skeleton_exhaustion_blocks(db_session, fake_claude):
    # v0.7.3 CR-1: the skeleton pass that never yields a valid skeleton (here: no epics → fails
    # TaskPlanSkeleton min_length) exhausts its per-pass parse-retries → blocked (parse_exhaustion),
    # nothing written — the same parse-exhaustion path the old whole-tree planless gate_report took.
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _to_task_plan(db_session, version)
    fake_claude.response = lambda _prompt: ("", None, {"epics": []})  # invalid skeleton, every attempt
    state = await orchestrator.run_dispatch(db_session, version.id)
    assert state.status == "blocked"
    assert state.block_reason == "parse_exhaustion"
    assert _epics_of(db_session, version) == []  # nothing written


async def test_task_plan_write_fail_blocks_system_error(db_session, fake_claude, monkeypatch):
    # R4 (D1): a task_plan WRITE failure (engine-side, plan parsed OK but the materialize step failed) →
    # blocked with block_reason=system_error.
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _to_task_plan(db_session, version)
    monkeypatch.setattr(orchestrator, "_write_task_plan", lambda db, state, block: "simulovaná chyba zápisu plánu")
    fake_claude.response = _plan_fake([("E1", [("F1", [("T1", "backend")])])])
    state = await orchestrator.run_dispatch(db_session, version.id)
    assert state.status == "blocked"
    assert state.block_reason == "system_error"


async def test_task_plan_replan_replaces_no_duplicates(db_session, fake_claude):
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _to_task_plan(db_session, version)
    fake_claude.response = _plan_fake([("E1", [("F1", [("T1", "backend")])]), ("E2", [("F2", [("T2", "backend")])])])
    await orchestrator.run_dispatch(db_session, version.id)
    assert len(_epics_of(db_session, version)) == 2

    # Director returned → Designer re-plans (fewer epics). The write-path must REPLACE.
    _to_task_plan(db_session, version)
    fake_claude.response = _plan_fake([("E1 only", [("F1 only", [("T1", "backend")])])])
    await orchestrator.run_dispatch(db_session, version.id)
    epics = _epics_of(db_session, version)
    assert len(epics) == 1  # replaced, not appended
    assert epics[0].title == "E1 only"
    assert len(db_session.execute(select(Task)).scalars().all()) == 1


async def test_approve_at_task_plan_advances_to_build(db_session, fake_claude):
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _to_task_plan(db_session, version)
    fake_claude.response = _plan_fake([("E1", [("F1", [("T1", "backend")])])])
    await orchestrator.run_dispatch(db_session, version.id)  # → awaiting_director
    state = await orchestrator.apply_action(db_session, version_id=version.id, action="approve")
    assert state.current_stage == "build"


# ── (v0.7.3) incremental task_plan generation — multi-pass / fail-closed (CR-1) ──


async def test_task_plan_passes_use_narrowed_schema_invoke_agent_untouched(db_session, fake_claude):
    """The plan passes use the NARROWED schemas; the post-write Coordinator synthesis (via the
    untouched invoke_agent) still uses the FULL PIPELINE_STATUS_JSON_SCHEMA — byte-identical guarantee."""
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _to_task_plan(db_session, version)
    fake_claude.response = _plan_fake([("E1", [("F1", [("T1", "backend")])])])
    await orchestrator.run_dispatch(db_session, version.id)
    schemas = [c["json_schema"] for c in fake_claude.calls]
    # deterministic order: skeleton pass, per-feat pass, then the Coordinator synthesis turn.
    assert schemas[0] == orchestrator.TASK_PLAN_SKELETON_JSON_SCHEMA
    assert schemas[1] == orchestrator.TASK_PLAN_FEAT_TASKS_JSON_SCHEMA
    assert schemas[2] == orchestrator.PIPELINE_STATUS_JSON_SCHEMA  # synthesis — invoke_agent untouched
    assert orchestrator.TASK_PLAN_SKELETON_JSON_SCHEMA != orchestrator.PIPELINE_STATUS_JSON_SCHEMA


async def test_task_plan_passes_record_synthetic_notes_with_usage(db_session, fake_claude):
    """Each pass records ONE synthetic audit note (author=designer, kind=notification) carrying the
    turn's WS-D usage — the trail/metrics are preserved even though invoke_agent isn't used."""
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _to_task_plan(db_session, version)
    fake_claude.response = _plan_fake(
        [("E1", [("F1", [("T1", "backend")]), ("F2", [("T2", "backend")])])],
        usage=claude_agent.UsageMetadata(input_tokens=50, output_tokens=20, model="m"),
    )
    await orchestrator.run_dispatch(db_session, version.id)
    notes = [
        m
        for m in _msgs(db_session, version.id)
        if m.author == "designer" and m.kind == "notification" and m.stage == "task_plan"
    ]
    assert len(notes) == 3  # 1 skeleton + 1 per-feat note per feat (2 feats)
    assert sum(m.content.startswith("Plán — kostra:") for m in notes) == 1
    assert sum(m.content.startswith("Plán — funkcia „") for m in notes) == 2
    assert all(m.payload["usage"] == {"input_tokens": 50, "output_tokens": 20, "model": "m"} for m in notes)


async def test_task_plan_assembles_in_skeleton_order(db_session, fake_claude):
    """The full plan is assembled in SKELETON order, so _write_task_plan's MAX+1 numbering matches what
    the Director reviewed (not per-feat arrival order)."""
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _to_task_plan(db_session, version)
    fake_claude.response = _plan_fake(
        [
            ("Epic A", [("Feat A1", [("ta1", "backend")]), ("Feat A2", [("ta2", "backend")])]),
            ("Epic B", [("Feat B1", [("tb1", "backend")])]),
        ]
    )
    await orchestrator.run_dispatch(db_session, version.id)
    epics = sorted(_epics_of(db_session, version), key=lambda e: e.number)
    assert [e.title for e in epics] == ["Epic A", "Epic B"]
    # feat/task numbers are MAX+1 PER parent, so traverse the hierarchy in (epic, feat) order.
    feats_a = db_session.execute(select(Feat).where(Feat.epic_id == epics[0].id).order_by(Feat.number)).scalars().all()
    feats_b = db_session.execute(select(Feat).where(Feat.epic_id == epics[1].id).order_by(Feat.number)).scalars().all()
    assert [f.title for f in feats_a] == ["Feat A1", "Feat A2"]
    assert [f.title for f in feats_b] == ["Feat B1"]
    tasks_a1 = (
        db_session.execute(select(Task).where(Task.feat_id == feats_a[0].id).order_by(Task.number)).scalars().all()
    )
    assert [t.title for t in tasks_a1] == ["ta1"]


async def test_task_plan_per_feat_parse_retry_recovers(db_session, fake_claude):
    """Acceptance: a per-pass parse-retry recovers a single-feat typo WITHOUT re-emitting the whole
    tree (the skeleton is emitted once; only the failing feat's pass re-emits)."""
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _to_task_plan(db_session, version)
    counters = {"skeleton": 0, "feat_typo": 0}

    def _fake(prompt):
        if "KOSTRU" in prompt:
            counters["skeleton"] += 1
            return ("", None, _skeleton_dict([("E1", [("F1", [("T1", "backend")])])]))
        if "emituj IBA jej úlohy" in prompt:  # FIRST per-feat attempt → a typo (empty tasks → invalid)
            counters["feat_typo"] += 1
            return ("", None, {"tasks": []})
        if "nepodarilo spracovať" in prompt:  # the per-pass parse-retry re-prompt → valid this time
            return ("", None, _feat_tasks_dict([("T1", "backend")]))
        return _block(stage="task_plan", kind="done", summary="OK", awaiting="director")  # synthesis

    fake_claude.response = _fake
    state = await orchestrator.run_dispatch(db_session, version.id)
    assert state.status == "awaiting_director"
    assert counters["skeleton"] == 1  # the whole tree was NOT re-emitted — skeleton once
    assert counters["feat_typo"] == 1  # only the first feat attempt was the typo; the retry recovered
    tasks = db_session.execute(select(Task)).scalars().all()
    assert len(tasks) == 1 and tasks[0].title == "T1"


async def test_task_plan_per_feat_failure_halts_naming_feat_writes_nothing(db_session, fake_claude, monkeypatch):
    """Fail-closed: a per-feat pass exhausting its retries HALTs to blocked NAMING the feat and writes
    NO Epic/Feat/Task rows (the write happens only after EVERY feat succeeds — never a half-plan)."""
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _to_task_plan(db_session, version)
    captured: dict = {}

    async def _capture_relay(db, version_id, stage, reason, on_message=None, *, failed=None):
        captured["reason"] = reason

    monkeypatch.setattr(orchestrator, "_coordinator_relay_engine_failure", _capture_relay)

    def _fake(prompt):
        if "KOSTRU" in prompt:
            return ("", None, _skeleton_dict([("E1", [("Hlavná kniha", [("T1", "backend")])])]))
        return ("", None, {"tasks": []})  # per-feat (+ every retry) → invalid → exhausts

    fake_claude.response = _fake
    state = await orchestrator.run_dispatch(db_session, version.id)
    assert state.status == "blocked" and state.block_reason == "parse_exhaustion"
    assert "Hlavná kniha" in captured["reason"]  # the relay names the failing feat
    assert _epics_of(db_session, version) == []
    assert db_session.execute(select(Feat)).scalars().all() == []
    assert db_session.execute(select(Task)).scalars().all() == []


async def test_task_plan_max_feats_cap_blocks(db_session, fake_claude, monkeypatch):
    """MAX_PLAN_FEATS caps total feats: a skeleton exceeding it HALTs (system_error) BEFORE any per-feat
    pass, writing nothing — an over-fine decomposition, never a runaway loop."""
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _to_task_plan(db_session, version)
    monkeypatch.setattr(orchestrator, "MAX_PLAN_FEATS", 2)
    fake_claude.response = _plan_fake(
        [("E1", [("F1", [("T1", "backend")]), ("F2", [("T2", "backend")]), ("F3", [("T3", "backend")])])]
    )
    state = await orchestrator.run_dispatch(db_session, version.id)
    assert state.status == "blocked" and state.block_reason == "system_error"
    assert _epics_of(db_session, version) == []  # nothing written
    # only the skeleton pass ran (no per-feat passes) — the cap fires before them.
    feat_pass_calls = [c for c in fake_claude.calls if "emituj IBA jej úlohy" in c["prompt"]]
    assert feat_pass_calls == []


async def test_task_plan_skeleton_timeout_surfaces_lost_work_not_blocked(db_session, monkeypatch):
    """Envelope-loss parity (R1, audit 2026-06-18): a ClaudeAgentError (timeout) in the SKELETON pass with
    committed work settles to awaiting_director ('review & continue') with the lost-work audit — NOT the
    pre-fix blocked/parse_exhaustion dead-end (task_plan was never carved out of R1)."""

    async def _boom(**kwargs):
        raise orchestrator.ClaudeAgentError("claude invocation timed out after 1200s")

    monkeypatch.setattr(orchestrator, "_repo_head", lambda root: "h" * 40)
    monkeypatch.setattr(orchestrator, "_rev_list_count", lambda root, baseline: 2)
    monkeypatch.setattr(orchestrator, "invoke_claude", _boom)
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")  # arms baseline=h*40
    _to_task_plan(db_session, version)

    state = await orchestrator.run_dispatch(db_session, version.id)
    assert state.status == "awaiting_director"  # R1: never a blocked dead-end
    assert state.block_reason != "parse_exhaustion"  # a timeout is NOT mislabeled as parse_exhaustion
    assert "2 commitov" in state.next_action
    assert len(_lost_work_notifs(db_session, version.id)) == 1
    assert _epics_of(db_session, version) == []  # nothing materialized


async def test_task_plan_per_feat_timeout_surfaces_lost_work_no_rows(db_session, monkeypatch):
    """Envelope-loss parity (R1) in a PER-FEAT pass: the skeleton succeeds, then a feat pass times out →
    awaiting_director (audit recorded), NO Epic/Feat/Task rows (the write happens only after ALL feats)."""
    calls = {"n": 0}

    async def _seq(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:  # pass 1 — skeleton succeeds (grammar-constrained structured_output)
            return ("", None, _skeleton_dict([("E1", [("F1", [("T1", "backend")])])]))
        raise orchestrator.ClaudeAgentError("claude invocation timed out after 1200s")  # per-feat times out

    monkeypatch.setattr(orchestrator, "_repo_head", lambda root: "h" * 40)
    monkeypatch.setattr(orchestrator, "_rev_list_count", lambda root, baseline: 1)
    monkeypatch.setattr(orchestrator, "invoke_claude", _seq)
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _to_task_plan(db_session, version)

    state = await orchestrator.run_dispatch(db_session, version.id)
    assert state.status == "awaiting_director"
    assert state.block_reason != "parse_exhaustion"
    assert "1 commitov" in state.next_action
    assert len(_lost_work_notifs(db_session, version.id)) == 1
    assert _epics_of(db_session, version) == []  # no half-plan
    assert db_session.execute(select(Task)).scalars().all() == []


async def test_task_plan_claude_error_no_baseline_blocks_agent_error(db_session, monkeypatch):
    """Accurate block_reason: a ClaudeAgentError with NO audit baseline (lost_work None) → blocked
    AGENT_ERROR (still a timeout/crash), never the parse_exhaustion mislabel reserved for unparseable output."""

    async def _boom(**kwargs):
        raise orchestrator.ClaudeAgentError("claude invocation timed out after 1200s")

    monkeypatch.setattr(orchestrator, "invoke_claude", _boom)
    version, _ = _make_version(db_session)
    state = PipelineState(
        version_id=version.id,
        flow_type="new_version",
        current_stage="task_plan",
        current_actor="designer",
        status="agent_working",
        next_action="working",
    )
    db_session.add(state)
    db_session.flush()  # dispatch_baseline_sha stays NULL → _audit_lost_work is a no-op

    result = await orchestrator.run_dispatch(db_session, version.id)
    assert result.status == "blocked"
    assert result.block_reason == "agent_error"  # NOT parse_exhaustion
    assert _lost_work_notifs(db_session, version.id) == []  # no baseline → no lost-work audit


async def test_task_plan_text_fence_path_materializes(db_session, fake_claude):
    """REAL-ENV path (the gap that masked the live failure 2026-06-18): claude returns TEXT with a
    <<<TASK_PLAN_JSON>>> fence and NO structured_output (structured=None) for BOTH the skeleton and every
    per-feat pass. The fix extracts+parses the fenced JSON (mirroring invoke_agent's parse_status_block
    fallback), yielding a complete materialized plan — pre-fix this blocked on parse_exhaustion."""
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _to_task_plan(db_session, version)
    fake_claude.response = _plan_fake(
        [
            ("Foundation", [("Schema", [("GL tables", "migration", 90)])]),
            ("Calc", [("Hlavná kniha", [("GL výpočet", "backend", 120)])]),
        ],
        text=True,  # TEXT + sentinel fence, structured_output=None — the real-env shape
    )
    state = await orchestrator.run_dispatch(db_session, version.id)
    assert state.status == "awaiting_director"  # NOT blocked — the text/fence path was exercised
    epics = _epics_of(db_session, version)
    assert {e.title for e in epics} == {"Foundation", "Calc"}
    tasks = db_session.execute(select(Task)).scalars().all()
    assert {t.title for t in tasks} == {"GL tables", "GL výpočet"}
    # the narrowed schemas are STILL sent (forward-compat) even though structured_output came back None
    assert fake_claude.calls[0]["json_schema"] == orchestrator.TASK_PLAN_SKELETON_JSON_SCHEMA
    # cross_cutting_rules still persists on the designer gate_report (build loop re-reads it)
    gr = [m for m in _msgs(db_session, version.id) if m.author == "designer" and m.kind == "gate_report"][-1]
    assert "transakčná" in gr.payload["cross_cutting_rules"]


async def test_task_plan_text_fence_tolerates_features_drift(db_session, fake_claude):
    """Drift tolerance (the exact live model output): the skeleton fence uses `features` (not `feats`)
    plus extra `id`/`project`/`version` keys — the parser normalises `features`→`feats` and drops the
    unknowns, still materializing. Guards against the precise shape the live root-cause captured."""
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _to_task_plan(db_session, version)

    def _fake(prompt):
        if "KOSTRU" in prompt:
            drift = {
                "project": "nex-asistent",
                "version": "0.1.0",
                "level": "skeleton",
                "epics": [{"id": "EPIC-1", "title": "Foundation", "features": [{"id": "FEAT-1", "title": "Schema"}]}],
                "cross_cutting_rules": "invarianty",
            }
            return (_task_plan_fence(drift), None, None)
        if "Schema" in prompt:
            return (_task_plan_fence({"tasks": [{"title": "GL", "task_type": "migration"}]}), None, None)
        return _block(stage="task_plan", kind="done", summary="ok", awaiting="director")

    fake_claude.response = _fake
    state = await orchestrator.run_dispatch(db_session, version.id)
    assert state.status == "awaiting_director"
    assert [f.title for f in db_session.execute(select(Feat)).scalars().all()] == ["Schema"]  # features→feats
    assert [t.title for t in db_session.execute(select(Task)).scalars().all()] == ["GL"]


async def test_task_plan_text_no_fence_blocks_parse_exhaustion(db_session, fake_claude):
    """A skeleton turn with NO <<<TASK_PLAN_JSON>>> fence (and no structured_output) → extraction fails →
    parse_exhaustion (the text path's genuine-parse-failure branch), nothing written."""
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _to_task_plan(db_session, version)
    fake_claude.response = lambda _p: ("Tu je plán, ale zabudol som sentinel blok.", None, None)
    state = await orchestrator.run_dispatch(db_session, version.id)
    assert state.status == "blocked" and state.block_reason == "parse_exhaustion"
    assert _epics_of(db_session, version) == []


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


async def test_recover_orphaned_non_build_stage_recovered(db_session, fake_claude):
    # R1-d (D4): orphan recovery now covers ALL stages, not just build. A kickoff/agent_working stranded
    # by a restart is flipped to awaiting_director with a generic stage-parametrized message + commit audit,
    # and the durable single-flight flag is cleared (a killed process left it set — Seam #2).
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    state = orchestrator._get_state(db_session, version.id)
    assert state.status == "agent_working" and state.dispatch_in_flight is True

    assert orchestrator.recover_orphaned_builds_on_startup(db_session) == 1

    state = orchestrator._get_state(db_session, version.id)
    assert state.current_stage == "kickoff"
    assert state.status == "awaiting_director"
    assert "kickoff" in state.next_action and "prerušená" in state.next_action
    assert state.dispatch_in_flight is False  # cleared on recovery
    notif = [
        m
        for m in _msgs(db_session, version.id)
        if m.author == "system"
        and m.kind == "notification"
        and m.stage == "kickoff"
        and (m.payload or {}).get("recovery_audit")
    ]
    assert notif and "reštartom" in notif[-1].content


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


# ── WS-D Option A: exhausted-retry metric capture across escalation sites (CR-NS-036) ─────────────
# Dedo's coverage mandate: every terminal-ParseFailure escalation that records a Director-facing
# message must carry the failed worker's usage/timing (else aggregate_pipeline_usage undercounts).
# Per-site tests so a future escalation path can't silently re-introduce the undercount.


def test_failure_metrics_helpers():
    """The single shared carry helpers (drift-proof source of the attachment): empty/None unless a
    ParseFailure actually captured usage (never fabricated)."""
    from backend.services.pipeline_status import ParseFailure

    assert orchestrator._failure_metrics_payload("not a failure") == {}
    assert orchestrator._failure_metrics_payload(ParseFailure("r")) == {}  # usage None → nothing to carry
    assert orchestrator._seed_metrics_from_failure(ParseFailure("r")) is None
    pf = ParseFailure(
        "r",
        usage={"input_tokens": 12, "output_tokens": 5, "model": "m"},
        timing={"duration_seconds": 1.5, "parse_attempts": 3},
    )
    assert orchestrator._failure_metrics_payload(pf) == {"usage": pf.usage, "timing": pf.timing}
    seed = orchestrator._seed_metrics_from_failure(pf)
    assert seed.saw_usage and (seed.input_tokens, seed.output_tokens, seed.attempts) == (12, 5, 3)
    assert seed.usage_payload() == {"input_tokens": 12, "output_tokens": 5, "model": "m"}


async def test_parse_retry_exhaustion_attaches_accumulated_metrics_to_failure(db_session, monkeypatch):
    """The terminal ParseFailure carries the SUM of every exhausted attempt's tokens (WS-D step 1)."""

    async def _fake(*, prompt, **kwargs):
        return ("garbage not a block", claude_agent.UsageMetadata(10, 4, "m"))

    monkeypatch.setattr(orchestrator, "invoke_claude", _fake)
    version, _ = _make_version(db_session)
    result = await orchestrator.invoke_agent_with_parse_retry(
        db_session, version_id=version.id, role="designer", stage="gate_a", prompt="go"
    )
    assert isinstance(result, ParseFailure)
    n = orchestrator._PARSE_RETRIES + 1  # primary + bounded retries
    assert result.usage == {"input_tokens": 10 * n, "output_tokens": 4 * n, "model": "m"}
    assert result.timing["parse_attempts"] == n


async def test_main_dispatch_relay_carries_worker_metrics(db_session, monkeypatch):
    """Gate worker parse-exhaustion → the Coordinator relay message counts worker + Coordinator tokens
    (no extra notification, no undercount)."""
    seq = SequenceClaude(
        [
            ("garbage", claude_agent.UsageMetadata(10, 4, "m")),  # kickoff worker primary
            ("garbage", claude_agent.UsageMetadata(10, 4, "m")),  # retry 1
            ("garbage", claude_agent.UsageMetadata(10, 4, "m")),  # retry 2 (exhausted)
            (  # the Coordinator relay succeeds
                _block(stage="kickoff", kind="gate_report", summary="relay Directorovi", awaiting="director"),
                claude_agent.UsageMetadata(7, 3, "m"),
            ),
        ]
    )
    monkeypatch.setattr(orchestrator, "invoke_claude", seq)
    monkeypatch.setattr(orchestrator, "verify_mechanical", lambda slug, block: None)
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    state = await orchestrator.run_dispatch(db_session, version.id)

    assert state.status == "blocked"
    relays = [
        m for m in _msgs(db_session, version.id) if m.author == "coordinator" and m.payload and m.payload.get("usage")
    ]
    assert len(relays) == 1
    # worker 3×(10,4) seeded + Coordinator (7,3) → folded into the one relay message
    assert relays[0].payload["usage"] == {"input_tokens": 37, "output_tokens": 15, "model": "m"}
    assert relays[0].payload["timing"]["parse_attempts"] == 4  # 3 worker + 1 relay


async def test_build_parsefailure_attempts_carry_worker_metrics(db_session, fake_claude, monkeypatch):
    """A Programmer attempt that never parses produces no message of its own — its tokens ride on the
    auto-fix-return message (keyed by task_id) so aggregate_pipeline_usage rolls them up to the task."""
    monkeypatch.setattr(orchestrator, "_repo_head", lambda root: "e" * 40)
    version, project = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _epic, _feat, (task,) = _seed_one_feat(db_session, version, project, ["T"])
    _to_build(db_session, version)
    # implementer never emits a parseable block → ParseFailure every attempt (with usage)
    fake_claude.response = lambda prompt: ("garbage — no status block", claude_agent.UsageMetadata(8, 3, "m"))

    await orchestrator.run_dispatch(db_session, version.id)

    db_session.refresh(task)
    assert task.status == "failed"
    returns = [m for m in _msgs(db_session, version.id) if m.author == "system" and m.kind == "return"]
    assert len(returns) == orchestrator._AUTO_FIX_RETRIES
    for m in returns:
        # each attempt exhausted its 3 parse-retries → accumulated 3×(8,3); attributed to the task
        assert m.payload["usage"] == {"input_tokens": 24, "output_tokens": 9, "model": "m"}
        assert m.payload["timing"]["parse_attempts"] == 3
        assert m.payload["task_id"] == str(task.id)

    from backend.services.pipeline_metrics import aggregate_pipeline_usage

    agg = aggregate_pipeline_usage(db_session, version.id)
    assert agg.by_task[task.id].input_tokens == 24 * orchestrator._AUTO_FIX_RETRIES


async def test_gate_e_block_records_worker_metrics(db_session, monkeypatch):
    """A Gate E parse-exhaustion routes to _block_failed (records no relay of its own) — WS-D records a
    metrics-bearing system→director note so the failed Designer's tokens are counted."""
    seq = SequenceClaude(
        [
            (
                _block(stage="gate_e", kind="question", summary="?", question="Otázka pre Návrhára?"),
                claude_agent.UsageMetadata(5, 2, "m"),
            ),  # customer turn (its own message carries its metrics)
            ("garbage — no status block", claude_agent.UsageMetadata(10, 4, "m")),  # designer primary
            ("garbage — no status block", claude_agent.UsageMetadata(10, 4, "m")),  # retry 1
            ("garbage — no status block", claude_agent.UsageMetadata(10, 4, "m")),  # retry 2
        ]
    )
    monkeypatch.setattr(orchestrator, "invoke_claude", seq)
    monkeypatch.setattr(orchestrator, "verify_mechanical", lambda slug, block: None)
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _to_gate_e(db_session, version)
    state = await orchestrator.run_dispatch(db_session, version.id)

    assert state.status == "blocked"
    notes = [
        m
        for m in _msgs(db_session, version.id)
        if m.author == "system" and m.recipient == "director" and m.payload and m.payload.get("usage")
    ]
    assert len(notes) == 1  # the _block_failed metrics note (not a duplicate — this path had no relay)
    assert notes[0].payload["usage"] == {"input_tokens": 30, "output_tokens": 12, "model": "m"}  # 3×(10,4)
    assert notes[0].payload["timing"]["parse_attempts"] == 3


async def test_route_to_designer_block_records_worker_metrics(db_session, fake_claude, monkeypatch):
    """E7 Designer spec-fix parse-exhaustion → _block_failed records the failed Designer's tokens (WS-D)."""
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
    fake_claude.response = lambda prompt: ("garbage — no status block", claude_agent.UsageMetadata(9, 6, "m"))

    state = await orchestrator.run_dispatch(db_session, version.id)

    assert state.status == "blocked" and state.returns_to is None
    notes = [
        m
        for m in _msgs(db_session, version.id)
        if m.author == "system" and m.recipient == "director" and m.payload and m.payload.get("usage")
    ]
    assert len(notes) == 1
    assert notes[0].payload["usage"] == {"input_tokens": 27, "output_tokens": 18, "model": "m"}  # 3×(9,6)
    assert notes[0].payload["timing"]["parse_attempts"] == 3


# ── WS-E: internal-turn parse-failure observability (CR-NS-037, Class F) ───────────────────────────
# Each Class-F site, on an internal-turn parse-exhaustion, must (a) record a Director-visible note
# naming the failed turn, (b) carry its tokens so aggregate_pipeline_usage counts them, while (c) the
# pipeline's settled state + fallback stay UNCHANGED (HARD constraint — no control-flow change).

_U = claude_agent.UsageMetadata


async def test_record_internal_turn_parse_failure_helper(db_session):
    """The shared recorder: note ALWAYS (visibility ≠ metrics); metrics payload only when present."""
    version, _ = _make_version(db_session)
    captured = []

    async def on_msg(m):
        captured.append(m)

    await orchestrator._record_internal_turn_parse_failure(
        db_session,
        version.id,
        "gate_a",
        turn_label="X",
        failed=ParseFailure(
            "r", usage={"input_tokens": 7, "output_tokens": 3, "model": "m"}, timing={"parse_attempts": 2}
        ),
        on_message=on_msg,
    )
    await orchestrator._record_internal_turn_parse_failure(
        db_session,
        version.id,
        "gate_a",
        turn_label="Y",
        failed=ParseFailure("r"),  # no usage
    )
    notes = [m for m in _msgs(db_session, version.id) if m.author == "system" and m.kind == "notification"]
    assert len(notes) == 2  # BOTH recorded — the note is for visibility, recorded even without metrics
    with_metrics = [m for m in notes if m.payload]
    assert len(with_metrics) == 1 and with_metrics[0].payload["usage"]["input_tokens"] == 7
    assert len([m for m in notes if not m.payload]) == 1  # the no-usage note has NULL payload
    assert len(captured) == 1  # only the call with on_message broadcast


async def test_coordinator_relay_parse_failure_visible_note(db_session, monkeypatch):
    """Site 1 — `_coordinator_relay`: relay exhausts → note + metrics; caller still falls back to the
    raw worker question (UNCHANGED)."""
    seq = SequenceClaude(
        [
            (_block(stage="gate_a", kind="question", summary="?", question="Akú DB schému použiť?"), _U(5, 2, "m")),
            ("garbage", _U(9, 4, "m")),
            ("garbage", _U(9, 4, "m")),
            ("garbage", _U(9, 4, "m")),
        ]
    )
    monkeypatch.setattr(orchestrator, "invoke_claude", seq)
    monkeypatch.setattr(orchestrator, "verify_mechanical", lambda slug, block: None)
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    st = orchestrator._get_state(db_session, version.id)
    st.current_stage, st.current_actor, st.status = "gate_a", "designer", "agent_working"
    db_session.flush()
    state = await orchestrator.run_dispatch(db_session, version.id)

    assert state.status == "blocked"
    assert "Akú DB schému použiť?" in state.next_action  # FALLBACK to the raw worker question UNCHANGED
    notes = [m for m in _msgs(db_session, version.id) if "Posúdenie otázky workera Koordinátorom" in m.content]
    assert len(notes) == 1 and notes[0].author == "system" and notes[0].recipient == "director"
    assert notes[0].payload["usage"] == {"input_tokens": 27, "output_tokens": 12, "model": "m"}
    assert notes[0].payload["timing"]["parse_attempts"] == 3


async def test_coordinator_review_gap_parse_failure_visible_note(db_session, monkeypatch):
    """Site 2 — `_coordinator_review_gap`: silent no-op → note + metrics; still non-blocking advisory."""
    seq = SequenceClaude(
        [
            (_block(stage="gate_e", kind="question", summary="?", question="Reset hesla?"), _U(5, 2, "m")),
            (
                _block(
                    stage="gate_e",
                    kind="answer",
                    summary="medzera",
                    awaiting="none",
                    gap_found=True,
                    proposed_fix="Pridať reset hesla",
                ),
                _U(6, 3, "m"),
            ),
            ("garbage", _U(9, 4, "m")),
            ("garbage", _U(9, 4, "m")),
            ("garbage", _U(9, 4, "m")),
        ]
    )
    monkeypatch.setattr(orchestrator, "invoke_claude", seq)
    monkeypatch.setattr(orchestrator, "verify_mechanical", lambda slug, block: None)
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _to_gate_e(db_session, version)
    state = await orchestrator.run_dispatch(db_session, version.id)

    assert state.status == "awaiting_director"  # non-blocking advisory UNCHANGED
    assert "medzeru" in state.next_action
    notes = [m for m in _msgs(db_session, version.id) if "Revízia navrhovanej opravy Koordinátorom" in m.content]
    assert len(notes) == 1
    assert notes[0].payload["usage"] == {"input_tokens": 27, "output_tokens": 12, "model": "m"}


async def test_baseline_unreadable_relay_parse_failure_visible_note(db_session, fake_claude, monkeypatch):
    """Site 3 — baseline-unreadable relay: unchecked → note + metrics; task stays todo + settled UNCHANGED."""
    monkeypatch.setattr(orchestrator, "_repo_head", lambda root: None)  # HEAD unreadable
    version, project = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _epic, _feat, (task,) = _seed_one_feat(db_session, version, project, ["T"])
    _to_build(db_session, version)
    fake_claude.response = lambda prompt: ("garbage", _U(9, 4, "m"))  # the coordinator relay can't parse

    state = await orchestrator.run_dispatch(db_session, version.id)

    assert state.status == "awaiting_director"
    assert "baseline nečitateľný" in state.next_action  # settled outcome UNCHANGED
    db_session.refresh(task)
    assert task.status == "todo"  # stays todo (precondition failure, not a failed attempt) UNCHANGED
    notes = [m for m in _msgs(db_session, version.id) if "Relay Koordinátora (baseline nečitateľný)" in m.content]
    assert len(notes) == 1
    assert notes[0].payload["usage"] == {"input_tokens": 27, "output_tokens": 12, "model": "m"}


async def test_halt_relay_parse_failure_visible_note(db_session, fake_claude, monkeypatch):
    """Site 4 — failed-task HALT relay: unchecked → note + metrics; HALT (failed + awaiting) UNCHANGED."""
    monkeypatch.setattr(orchestrator, "_repo_head", lambda root: "f" * 40)
    version, project = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _epic, _feat, (task,) = _seed_one_feat(db_session, version, project, ["T"])
    _to_build(db_session, version)
    fake_claude.response = lambda prompt: ("garbage", _U(9, 4, "m"))  # implementer + HALT relay all fail

    state = await orchestrator.run_dispatch(db_session, version.id)

    assert state.status == "awaiting_director"
    assert "zlyhala po" in state.next_action  # HALT next_action UNCHANGED
    db_session.refresh(task)
    assert task.status == "failed"  # UNCHANGED
    notes = [m for m in _msgs(db_session, version.id) if "Relay Koordinátora (úloha zlyhala)" in m.content]
    assert len(notes) == 1  # exactly one HALT-relay note (the auto-fix-returns are kind=return)
    assert notes[0].payload["usage"] == {"input_tokens": 27, "output_tokens": 12, "model": "m"}


async def test_verify_done_judge_parse_failure_visible_note(db_session, monkeypatch):
    """Site 5a — `verify_done` Coordinator judge exhausts → note + metrics; still returns a FAIL reason.

    v0.7.2 R-A: the judge now runs through `invoke_agent_with_parse_retry`, so it RETRIES (1 + _PARSE_RETRIES)
    before exhausting — the visible note accumulates all attempts' tokens and the FAIL is flagged as a
    Coordinator SYSTEM error (`is_coordinator_error=True`, the R-B escalation signal)."""
    seq = SequenceClaude([("garbage", _U(9, 4, "m"))])  # last repeats → every retry re-fails the same way
    monkeypatch.setattr(orchestrator, "invoke_claude", seq)
    monkeypatch.setattr(orchestrator, "verify_mechanical", lambda slug, block: None)  # pass mechanical → reach judge
    version, _ = _make_version(db_session)
    block = parse_status_block(_block(stage="gate_a", kind="gate_report", summary="hotovo", awaiting="director"))

    reason, _, is_coord_error = await orchestrator.verify_done(db_session, version.id, block)  # 3-tuple (R-B)

    assert reason is not None and "unparseable" in reason  # control flow UNCHANGED (non-None reason = FAIL)
    assert is_coord_error is True  # R-B: a Coordinator's own unparseable verify is a SYSTEM error
    notes = [m for m in _msgs(db_session, version.id) if "Overenie DONE reportu Koordinátorom" in m.content]
    assert len(notes) == 1
    # 1 + _PARSE_RETRIES attempts, each (9,4) → accumulated; parse_attempts reflects the now-real retries.
    expected = 1 + orchestrator._PARSE_RETRIES
    assert notes[0].payload["usage"] == {"input_tokens": 9 * expected, "output_tokens": 4 * expected, "model": "m"}
    assert notes[0].payload["timing"]["parse_attempts"] == expected


async def test_verify_retry_reemit_parse_failure_visible_note(db_session, monkeypatch):
    """Site 5b — `_verify_with_retries` worker re-emit can't parse → note + metrics; still returns reason.

    v0.7.2 R-A: the worker re-emit now also runs through `invoke_agent_with_parse_retry`, so it retries
    (1 + _PARSE_RETRIES) before exhausting — the note accumulates all attempts' tokens."""
    seq = SequenceClaude(
        [
            (_block(stage="gate_a", kind="blocked", summary="problém", question="treba viac dát"), _U(3, 1, "m")),
            ("garbage", _U(9, 4, "m")),  # the worker re-emit now retries (last repeats) → ParseFailure each time
        ]
    )
    monkeypatch.setattr(orchestrator, "invoke_claude", seq)
    monkeypatch.setattr(orchestrator, "verify_mechanical", lambda slug, block: None)
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    state = orchestrator._get_state(db_session, version.id)
    state.current_stage, state.current_actor = "gate_a", "designer"
    db_session.flush()
    block = parse_status_block(_block(stage="gate_a", kind="gate_report", summary="hotovo", awaiting="director"))

    reason, _ = await orchestrator._verify_with_retries(db_session, state, block)  # (reason, is_scope) — CR-NS-056

    assert reason is not None  # caller still blocks (control flow UNCHANGED)
    notes = [m for m in _msgs(db_session, version.id) if "Oprava po overení" in m.content]
    assert len(notes) == 1
    expected = 1 + orchestrator._PARSE_RETRIES  # the re-emit retries are now real
    assert notes[0].payload["usage"] == {"input_tokens": 9 * expected, "output_tokens": 4 * expected, "model": "m"}


async def test_internal_turn_failure_timing_only_when_usage_none(db_session, monkeypatch):
    """WS-E (CR-NS-037 review fix): a usage-less internal-turn failure (no envelope — bare-str /
    ClaudeAgentError) still records its TIMING. Timing counts independently of usage (the
    aggregate_pipeline_usage contract) and the visibility note is recorded regardless; usage is NOT
    fabricated."""
    seq = SequenceClaude(["garbage — bare str, no usage envelope"])  # bare str → usage None, timing present
    monkeypatch.setattr(orchestrator, "invoke_claude", seq)
    monkeypatch.setattr(orchestrator, "verify_mechanical", lambda slug, block: None)
    version, _ = _make_version(db_session)
    block = parse_status_block(_block(stage="gate_a", kind="gate_report", summary="hotovo", awaiting="director"))

    reason, _, _ = await orchestrator.verify_done(db_session, version.id, block)  # 3-tuple (v0.7.2 R-B)

    assert reason is not None  # control flow unchanged
    notes = [m for m in _msgs(db_session, version.id) if "Overenie DONE reportu Koordinátorom" in m.content]
    assert len(notes) == 1
    assert notes[0].payload is not None  # NOT a NULL payload — timing is carried (not skipped in aggregation)
    assert "usage" not in notes[0].payload  # no fabricated usage
    # v0.7.2 R-A: the judge now retries (1 + _PARSE_RETRIES) before exhausting — timing counts each attempt.
    assert notes[0].payload["timing"]["parse_attempts"] == 1 + orchestrator._PARSE_RETRIES


async def test_verify_task_audit_judge_parse_failure_visible_note(db_session, monkeypatch):
    """Site 6 (WS-E addendum, CR-NS-037) — `_verify_task` Auditor judge exhausts parse-retries → note +
    metrics; returns the IDENTICAL `audit nečitateľný:` reason so the auto-fix loop / ≤5 bound / HALT
    are byte-for-byte preserved (no control-flow change)."""
    monkeypatch.setattr(orchestrator, "verify_mechanical", lambda slug, block, baseline_sha=None: None)
    seq = SequenceClaude([("garbage", _U(9, 4, "m")), ("garbage", _U(9, 4, "m")), ("garbage", _U(9, 4, "m"))])
    monkeypatch.setattr(orchestrator, "invoke_claude", seq)
    version, project = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _epic, _feat, (task,) = _seed_one_feat(db_session, version, project, ["T"])
    state = _to_build(db_session, version)
    block = parse_status_block(_block(stage="build", kind="gate_report", summary="hotovo", awaiting="director"))

    reason = await orchestrator._verify_task(db_session, state, task, block)

    assert reason is not None and reason.startswith("audit nečitateľný:")  # IDENTICAL return UNCHANGED
    notes = [m for m in _msgs(db_session, version.id) if "Audítorov verdikt úlohy" in m.content]
    assert len(notes) == 1 and notes[0].author == "system" and notes[0].recipient == "director"
    assert notes[0].payload["usage"] == {"input_tokens": 27, "output_tokens": 12, "model": "m"}  # 3×(9,4)
    assert notes[0].payload["timing"]["parse_attempts"] == 3


# ── CR-NS-053 Pillar A: Coordinator synthesis turn (§A.1–§A.2) ──────────────────


async def _synthesis_verify_pass(*args, **kwargs):
    """Async stub for verify_done → PASS, so a gate_report settle reaches the synthesis turn without a real
    Coordinator judge invocation consuming the fake's sequence. Returns the (reason, directive,
    is_coordinator_error) 3-tuple (v0.7.2 R-B) — PASS = (None, None, False)."""
    return None, None, False


async def test_coordinator_synthesis_records_director_message(db_session, fake_claude):
    """§A.1: the helper records a coordinator→director synthesis (payload.is_synthesis=true) and
    returns its summary for the caller's next_action."""
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    state = orchestrator._get_state(db_session, version.id)
    state.current_actor = "designer"  # a WORKER decision point (the guard skips coordinator-authored ones)
    db_session.flush()
    fake_claude.response = _block(
        stage=state.current_stage, kind="done", summary="Zhrnutie: fáza prešla — schváľ.", awaiting="director"
    )

    summary = await orchestrator._coordinator_synthesis(db_session, state, trigger="fáza gate_a")

    assert summary == "Zhrnutie: fáza prešla — schváľ."
    syn = [m for m in _msgs(db_session, version.id) if m.payload.get("is_synthesis")]
    assert len(syn) == 1
    assert syn[0].author == "coordinator" and syn[0].recipient == "director"
    # the synthesis prompt asked the Coordinator to summarize for the Director (structured, plain Slovak)
    assert any("ZHRŇ" in c["prompt"] for c in fake_claude.calls)


async def test_coordinator_synthesis_parse_failure_keeps_settle(db_session, fake_claude):
    """§A.1 WS-E fallback (non-negotiable): a synthesis ParseFailure records a visible note, returns
    None, and leaves the caller's settled state (next_action) UNCHANGED — no control-flow change."""
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    state = orchestrator._get_state(db_session, version.id)
    state.current_actor = "designer"  # a WORKER decision point (else the guard short-circuits before invoke)
    state.next_action = "PÔVODNÝ next_action"
    db_session.flush()
    fake_claude.response = "no valid status block"  # ParseFailure on every (re)emit

    summary = await orchestrator._coordinator_synthesis(db_session, state, trigger="fáza gate_a")

    assert summary is None
    assert state.next_action == "PÔVODNÝ next_action"  # caller's settle preserved
    notes = [m for m in _msgs(db_session, version.id) if m.author == "system" and m.recipient == "director"]
    assert any("Zhrnutie Koordinátora" in n.content for n in notes)  # WS-E visibility note
    assert not any(m.payload.get("is_synthesis") for m in _msgs(db_session, version.id))


async def test_synthesis_at_gate_report_pass(db_session, monkeypatch):
    """§A.2 site 1: a gate_report PASS emits the Coordinator synthesis as the primary Director-facing
    message (next_action from it); the raw worker report stays recorded for drill-down."""
    monkeypatch.setattr(orchestrator, "verify_done", _synthesis_verify_pass)
    monkeypatch.setattr(orchestrator, "verify_mechanical", lambda slug, block, baseline_sha=None: None)
    seq = SequenceClaude(
        [
            _block(stage="gate_a", kind="gate_report", summary="14 endpoints", awaiting="director"),  # worker
            _block(
                stage="gate_a", kind="done", summary="gate_a prešla — schváľ alebo vráť.", awaiting="director"
            ),  # synthesis
        ]
    )
    monkeypatch.setattr(orchestrator, "invoke_claude", seq)
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    state = orchestrator._get_state(db_session, version.id)
    state.current_stage = "gate_a"
    state.current_actor = "designer"
    state.status = "agent_working"
    db_session.flush()

    state = await orchestrator.run_dispatch(db_session, version.id)

    assert state.status == "awaiting_director"
    assert state.next_action == "gate_a prešla — schváľ alebo vráť."
    msgs = _msgs(db_session, version.id)
    syn = [m for m in msgs if m.payload.get("is_synthesis")]
    assert len(syn) == 1 and syn[0].author == "coordinator" and syn[0].recipient == "director"
    # raw worker gate_report still present (audit trail / drill-down)
    assert any(m.kind == "gate_report" and not m.payload.get("is_synthesis") for m in msgs)


async def test_synthesis_at_build_completion(db_session, fake_claude):
    """§A.2 site 2: build completion (no todo task) settles with a Coordinator synthesis sign-off."""
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _to_build(db_session, version)  # no tasks → the first loop iteration hits the final sign-off
    fake_claude.response = _block(
        stage="build", kind="done", summary="Build dokončený — finálne schválenie.", awaiting="director"
    )

    state = await orchestrator.run_dispatch(db_session, version.id)

    assert state.status == "awaiting_director"
    assert state.next_action == "Build dokončený — finálne schválenie."
    syn = [m for m in _msgs(db_session, version.id) if m.payload.get("is_synthesis")]
    assert len(syn) == 1 and syn[0].author == "coordinator" and syn[0].recipient == "director"


async def test_no_synthesis_at_kickoff(db_session, fake_claude):
    """§A.2 site 4 guard (fix-round 1): kickoff is coordinator-authored → NO synthesis (the Coordinator
    never synthesizes its OWN output)."""
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    fake_claude.response = _block(
        stage="kickoff", kind="done", summary="Discovery hotová — pokračuj na gate_a.", awaiting="director"
    )

    state = await orchestrator.run_dispatch(db_session, version.id)

    assert state.status == "awaiting_director"
    assert not any(m.payload.get("is_synthesis") for m in _msgs(db_session, version.id))
    assert not any("ZHRŇ" in c["prompt"] for c in fake_claude.calls)  # no synthesis turn ran


async def test_synthesis_at_worker_fallback(db_session, fake_claude):
    """§A.2 site 4: a WORKER 'done'/answer output reaching the fallback (not gate_report/question) DOES
    synthesize (the actor is a worker, not the Coordinator)."""
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    state = orchestrator._get_state(db_session, version.id)
    state.current_stage = "gate_a"
    state.current_actor = "designer"
    state.status = "agent_working"
    db_session.flush()
    fake_claude.response = _block(
        stage="gate_a", kind="done", summary="Návrhárov výstup zhrnutý — rozhodni.", awaiting="director"
    )

    state = await orchestrator.run_dispatch(db_session, version.id)

    assert state.status == "awaiting_director"
    assert state.next_action == "Návrhárov výstup zhrnutý — rozhodni."
    syn = [m for m in _msgs(db_session, version.id) if m.payload.get("is_synthesis")]
    assert len(syn) == 1 and syn[0].author == "coordinator" and syn[0].recipient == "director"


async def test_synthesis_at_task_plan_pass(db_session, fake_claude, monkeypatch):
    """§A.2 site 1 (task_plan PASS, fix-round 1 — its own settle branch): after the Designer's plan is
    written, a Coordinator synthesis fires (is_synthesis) and drives next_action."""
    seq = SequenceClaude(
        [
            ("", None, _skeleton_dict([("E1", [("F1", [("T1", "backend")])])])),  # pass 1 — skeleton
            ("", None, _feat_tasks_dict([("T1", "backend")])),  # pass 2 — feat F1 tasks → plan written
            _block(
                stage="task_plan", kind="done", summary="Plán hotový — schváľ alebo vráť.", awaiting="director"
            ),  # post-write Coordinator synthesis
        ]
    )
    monkeypatch.setattr(orchestrator, "invoke_claude", seq)
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _to_task_plan(db_session, version)

    state = await orchestrator.run_dispatch(db_session, version.id)

    assert state.status == "awaiting_director" and state.current_stage == "task_plan"
    assert _epics_of(db_session, version)  # plan materialized (worker turn)
    assert state.next_action == "Plán hotový — schváľ alebo vráť."  # from the synthesis
    syn = [m for m in _msgs(db_session, version.id) if m.payload.get("is_synthesis")]
    assert len(syn) == 1 and syn[0].author == "coordinator" and syn[0].recipient == "director"


# ── CR-NS-054 Pillar C: per-task Director reporting (§C.1–§C.2) ──────────────────


async def test_task_summary_recorded_on_done(db_session, fake_claude, monkeypatch):
    """§C.1/§C.2: a passing build task records ONE per-task summary (is_task_summary) — done, 1 attempt,
    audit_verdict.task_pass=True, no last_error."""
    monkeypatch.setattr(orchestrator, "_repo_head", lambda root: "a" * 40)
    version, project = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _epic, _feat, (task,) = _seed_one_feat(db_session, version, project, ["Auth modul"])
    _to_build(db_session, version)
    fake_claude.response = _build_fake(audit_pass=True)

    await orchestrator.run_dispatch(db_session, version.id)

    summaries = [m for m in _msgs(db_session, version.id) if (m.payload or {}).get("is_task_summary")]
    assert len(summaries) == 1
    assert summaries[0].author == "system" and summaries[0].recipient == "director"
    ts = summaries[0].payload["task_summary"]
    assert ts["final_status"] == "done"
    assert ts["attempts"] == 1
    assert ts["audit_verdict"]["task_pass"] is True
    assert ts["last_error"] is None
    assert ts["task_number"] == task.number and ts["title"] == "Auth modul"


async def test_task_summary_recorded_on_failed(db_session, fake_claude, monkeypatch):
    """§C.1/§C.2: a failing build task (audit never passes) records ONE per-task summary — failed,
    _AUTO_FIX_RETRIES attempts, audit_verdict.task_pass=False, last_error = the verbatim audit reason."""
    monkeypatch.setattr(orchestrator, "_repo_head", lambda root: "b" * 40)
    version, project = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _epic, _feat, (task,) = _seed_one_feat(db_session, version, project, ["Broken modul"])
    _to_build(db_session, version)
    fake_claude.response = _build_fake(audit_pass=False, audit_findings=["chýba validácia DPH"])

    await orchestrator.run_dispatch(db_session, version.id)

    summaries = [m for m in _msgs(db_session, version.id) if (m.payload or {}).get("is_task_summary")]
    assert len(summaries) == 1
    ts = summaries[0].payload["task_summary"]
    assert ts["final_status"] == "failed"
    assert ts["attempts"] == orchestrator._AUTO_FIX_RETRIES
    assert ts["audit_verdict"]["task_pass"] is False
    assert "chýba validácia DPH" in (ts["last_error"] or "")
    assert ts["task_number"] == task.number


# ── CR-NS-055 Pillar B: Coordinator autonomous first-principles decision (§B.1–§B.4) ──


async def test_autonomous_recovery_executes_bounded_high_conf(db_session, fake_claude):
    """§B.1: an executable bounded-recovery directive (reset_task, conf 0.9, not director_decision) →
    AUTO-EXECUTE (task reset) + a VISIBLE is_autonomous coordinator→director note; returns True."""
    version, project = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _epic, _feat, (task,) = _seed_one_feat(db_session, version, project, ["T"])
    state = _to_build(db_session, version)
    directive = _coord_directive(
        triage_class="nex_studio_bug",
        proposed_action="coordinator_reset_task",
        confidence=0.9,
        target={"task_id": str(task.id)},
    )

    ok = await orchestrator._maybe_autonomous_recovery(db_session, state, task, directive)

    assert ok is True
    db_session.refresh(task)
    assert task.status == "todo"  # reset by the executor
    notes = [m for m in _msgs(db_session, version.id) if (m.payload or {}).get("is_autonomous")]
    assert len(notes) == 1
    assert notes[0].author == "coordinator" and notes[0].recipient == "director"
    assert notes[0].payload["action"] == "coordinator_reset_task"


async def test_autonomous_recovery_escalates_director_decision(db_session, fake_claude):
    """§B.1: triage_class=director_decision → NOT executable → escalate (returns False, no note)."""
    version, project = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _epic, _feat, (task,) = _seed_one_feat(db_session, version, project, ["T"])
    state = _to_build(db_session, version)
    directive = _coord_directive(
        triage_class="director_decision",
        proposed_action="coordinator_reset_task",
        confidence=0.95,
        target={"task_id": str(task.id)},
    )
    ok = await orchestrator._maybe_autonomous_recovery(db_session, state, task, directive)
    assert ok is False
    assert not any((m.payload or {}).get("is_autonomous") for m in _msgs(db_session, version.id))


async def test_autonomous_recovery_escalates_low_confidence(db_session, fake_claude):
    """§B.1: confidence < 0.80 → escalate (returns False)."""
    version, project = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _epic, _feat, (task,) = _seed_one_feat(db_session, version, project, ["T"])
    state = _to_build(db_session, version)
    directive = _coord_directive(
        triage_class="nex_studio_bug",
        proposed_action="coordinator_reset_task",
        confidence=0.5,
        target={"task_id": str(task.id)},
    )
    assert await orchestrator._maybe_autonomous_recovery(db_session, state, task, directive) is False


async def test_autonomous_recovery_escalates_route_to_designer(db_session, fake_claude):
    """§B.1: route_to_designer is executable but NOT in the bounded AUTO_SET (design-quality signal) →
    escalate (returns False)."""
    version, project = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _epic, _feat, (task,) = _seed_one_feat(db_session, version, project, ["T"])
    state = _to_build(db_session, version)
    directive = _coord_directive(
        triage_class="spec_problem",
        proposed_action="coordinator_route_to_designer",
        confidence=0.95,
        target={"task_id": str(task.id)},
    )
    assert await orchestrator._maybe_autonomous_recovery(db_session, state, task, directive) is False


async def test_autonomous_recovery_per_task_cap(db_session, fake_claude):
    """§B.4: the Coordinator auto-recovers at most ONCE per task — a 2nd attempt on the same task escalates."""
    version, project = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _epic, _feat, (task,) = _seed_one_feat(db_session, version, project, ["T"])
    state = _to_build(db_session, version)
    directive = _coord_directive(
        triage_class="nex_studio_bug",
        proposed_action="coordinator_reset_task",
        confidence=0.9,
        target={"task_id": str(task.id)},
    )
    first = await orchestrator._maybe_autonomous_recovery(db_session, state, task, directive)
    second = await orchestrator._maybe_autonomous_recovery(db_session, state, task, directive)
    assert first is True and second is False  # cap = 1
    notes = [m for m in _msgs(db_session, version.id) if (m.payload or {}).get("is_autonomous")]
    assert len(notes) == 1


async def test_build_autonomous_recovery_then_cap_integration(db_session, fake_claude, monkeypatch):
    """Integration §B.1+§B.4: a failed task → Coordinator AUTO-resets (is_autonomous, build CONTINUES) →
    re-run fails again → 2nd HALT hits the cap → escalate (awaiting_director). Exactly ONE autonomous note."""
    monkeypatch.setattr(orchestrator, "_repo_head", lambda root: "a" * 40)
    version, project = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _epic, _feat, (task,) = _seed_one_feat(db_session, version, project, ["T"])
    _to_build(db_session, version)

    def _resp(prompt: str) -> str:
        if prompt.startswith("Audítor"):
            return _audit(False, ["zlyhal"])  # audit always fails → the task fails
        if "zlyhala po" in prompt:  # the failed-HALT Coordinator relay → propose a bounded recovery
            return _block(
                stage="build",
                kind="gate_report",
                summary="reset",
                awaiting="director",
                coordinator_directive=_coord_directive(
                    triage_class="nex_studio_bug",
                    proposed_action="coordinator_reset_task",
                    confidence=0.9,
                    target={"task_id": str(task.id)},
                ),
            )
        return _build_report()

    fake_claude.response = _resp

    state = await orchestrator.run_dispatch(db_session, version.id)

    assert state.status == "awaiting_director"  # capped 2nd HALT escalated
    notes = [m for m in _msgs(db_session, version.id) if (m.payload or {}).get("is_autonomous")]
    assert len(notes) == 1  # exactly one autonomous recovery (the cap stopped a 2nd)
    assert notes[0].payload["action"] == "coordinator_reset_task"
    db_session.refresh(task)
    assert task.status == "failed"


# ── CR-NS-056 gate_g FAIL flow, Fix 1: verify-judge mechanical-vs-scope (§F1.8) ──


def test_verify_reason_is_scope_predicate():
    """§F1.2: scope iff triage_class=director_decision OR proposed_action=route_to_designer; else mechanical."""
    assert orchestrator._verify_reason_is_scope(None) is False
    assert orchestrator._verify_reason_is_scope(_coord_directive(triage_class="director_decision")) is True
    assert (
        orchestrator._verify_reason_is_scope(_coord_directive(proposed_action="coordinator_route_to_designer")) is True
    )
    assert (
        orchestrator._verify_reason_is_scope(
            _coord_directive(triage_class="spec_problem", proposed_action="coordinator_reset_task")
        )
        is False
    )
    assert orchestrator._verify_reason_is_scope(_coord_directive(triage_class="programmer_guidance")) is False


async def test_verify_done_returns_directive(db_session, fake_claude):
    """§F1.1 + v0.7.2 R-B: verify_done returns (reason, directive, is_coordinator_error) — directive on a
    blocked verdict, (None, None, False) on PASS; is_coordinator_error True only for the Coordinator's own
    unparseable verify (a "flagged" block is a real Coordinator verdict, not a system error → False)."""
    version, _ = _make_version(db_session)
    block = parse_status_block(_block(stage="gate_g", kind="gate_report", summary="audit", awaiting="director"))
    fake_claude.response = _block(
        stage="gate_g",
        kind="blocked",
        summary="otázka",
        awaiting="director",
        question="je to v rozsahu?",
        coordinator_directive=_coord_directive(triage_class="director_decision", proposed_action="relay"),
    )
    reason, directive, is_coord_error = await orchestrator.verify_done(db_session, version.id, block)
    assert reason is not None and "flagged" in reason
    assert directive is not None and directive["triage_class"] == "director_decision"
    assert is_coord_error is False  # v0.7.2 R-B: a real Coordinator "flagged" block is NOT a system error

    fake_claude.response = _block(stage="gate_g", kind="gate_report", summary="ok", awaiting="director")
    assert await orchestrator.verify_done(db_session, version.id, block) == (None, None, False)


def _to_gate_g(db_session, version):
    state = orchestrator._get_state(db_session, version.id)
    state.current_stage = "gate_g"
    state.current_actor = "auditor"
    state.status = "agent_working"
    db_session.flush()
    return state


async def test_gate_g_verify_scope_question_escalates_once(db_session, monkeypatch):
    """§F1.4: a gate_g scope question escalates ONCE — status=blocked, current_actor=auditor, NO auto-return
    loop, the synthesis fired, exactly one scope escalation."""
    monkeypatch.setattr(orchestrator, "verify_mechanical", lambda slug, block: None)
    seq = SequenceClaude(
        [
            _block(stage="gate_g", kind="gate_report", summary="audit hotový", awaiting="director"),  # auditor
            _block(
                stage="gate_g",
                kind="blocked",
                summary="otázka rozsahu",
                awaiting="director",
                question="je X v rozsahu?",
                coordinator_directive=_coord_directive(triage_class="director_decision", proposed_action="relay"),
            ),  # verify-judge: SCOPE
            _block(stage="gate_g", kind="done", summary="zhrnutie pre Directora", awaiting="director"),  # synthesis
        ]
    )
    monkeypatch.setattr(orchestrator, "invoke_claude", seq)
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _to_gate_g(db_session, version)

    state = await orchestrator.run_dispatch(db_session, version.id)

    assert state.status == "blocked"
    assert state.block_reason == "agent_question"  # R4 (D1, FIX 1): a gate_g scope escalation is a question
    assert state.current_actor == "auditor" and state.current_stage == "gate_g"
    returns = [m for m in _msgs(db_session, version.id) if m.author == "system" and m.kind == "return"]
    assert returns == []  # the loop was broken at the scope detection — no auto-return to the Auditor
    assert orchestrator._scope_escalations_this_iteration(db_session, version.id) == 1
    assert any((m.payload or {}).get("is_synthesis") for m in _msgs(db_session, version.id))


async def test_gate_g_scope_escalation_capped_second_time(db_session, monkeypatch):
    """§F1.5: a 2nd scope flag in the same iteration → awaiting_director (no loop, no new escalation)."""
    monkeypatch.setattr(orchestrator, "verify_mechanical", lambda slug, block: None)
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _to_gate_g(db_session, version)
    # pre-seed the FIRST scope escalation this iteration (already answered once)
    orchestrator._record_message(
        db_session,
        version_id=version.id,
        stage="gate_g",
        author="coordinator",
        recipient="director",
        kind="question",
        content="otázka 1",
        payload={"coordinator_directive": _coord_directive(triage_class="director_decision")},
    )
    db_session.flush()
    seq = SequenceClaude(
        [
            _block(stage="gate_g", kind="gate_report", summary="audit", awaiting="director"),  # auditor
            _block(
                stage="gate_g",
                kind="blocked",
                summary="otázka 2",
                awaiting="director",
                question="znova rozsah?",
                coordinator_directive=_coord_directive(triage_class="director_decision"),
            ),  # 2nd SCOPE flag
        ]
    )
    monkeypatch.setattr(orchestrator, "invoke_claude", seq)

    state = await orchestrator.run_dispatch(db_session, version.id)

    assert state.status == "awaiting_director"  # capped — no loop
    assert orchestrator._scope_escalations_this_iteration(db_session, version.id) == 2


async def test_gate_g_verify_mechanical_failure_auto_returns(db_session, fake_claude):
    """§F1.3/§F1.4: a mechanical (no-directive / P-2) blocked verdict → the auto-return loop fires
    _VERIFY_RETRIES, settles blocked — behaviorally today (NOT the scope branch). FakeClaude dispatches on
    the prompt: the verify-judge stays blocked-no-directive; the Auditor + re-emits return a gate_report."""

    def _resp(prompt: str) -> str:
        if prompt.startswith("Verifikuj DONE report"):  # the verify-judge → mechanical (NO directive)
            return _block(stage="gate_g", kind="blocked", summary="P-2", awaiting="director", question="chýba citácia")
        return _block(stage="gate_g", kind="gate_report", summary="audit", awaiting="director")  # auditor + re-emits

    fake_claude.response = _resp
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _to_gate_g(db_session, version)

    state = await orchestrator.run_dispatch(db_session, version.id)

    assert state.status == "blocked"
    returns = [m for m in _msgs(db_session, version.id) if m.author == "system" and m.kind == "return"]
    assert len(returns) == orchestrator._VERIFY_RETRIES  # mechanical auto-return fired the full bound


async def test_scope_escalations_this_iteration_counts_from_verdict_boundary(db_session):
    """§F1.5: the cap counter resets after a verdict (the iteration boundary)."""
    version, _ = _make_version(db_session)
    rec = orchestrator._record_message
    rec(
        db_session,
        version_id=version.id,
        stage="gate_g",
        author="coordinator",
        recipient="director",
        kind="question",
        content="old",
        payload={"coordinator_directive": _coord_directive(triage_class="director_decision")},
    )
    rec(
        db_session,
        version_id=version.id,
        stage="gate_g",
        author="director",
        recipient="auditor",
        kind="verdict",
        content="FAIL",
    )
    rec(
        db_session,
        version_id=version.id,
        stage="gate_g",
        author="coordinator",
        recipient="director",
        kind="question",
        content="new",
        payload={"coordinator_directive": _coord_directive(triage_class="director_decision")},
    )
    db_session.flush()
    assert orchestrator._scope_escalations_this_iteration(db_session, version.id) == 1  # only the post-verdict one


async def test_prior_scope_qa_pairs_any_director_channel(db_session):
    """§F1.6: a scope question pairs with the Director's response via ANY channel (answer / return)."""
    version, _ = _make_version(db_session)
    rec = orchestrator._record_message
    rec(
        db_session,
        version_id=version.id,
        stage="gate_g",
        author="coordinator",
        recipient="director",
        kind="question",
        content="Q1",
        payload={"coordinator_directive": _coord_directive(triage_class="director_decision")},
    )
    rec(
        db_session,
        version_id=version.id,
        stage="gate_g",
        author="director",
        recipient="auditor",
        kind="answer",
        content="A1",
    )
    rec(
        db_session,
        version_id=version.id,
        stage="gate_g",
        author="coordinator",
        recipient="director",
        kind="question",
        content="Q2",
        payload={"coordinator_directive": _coord_directive(proposed_action="coordinator_route_to_designer")},
    )
    rec(
        db_session,
        version_id=version.id,
        stage="gate_g",
        author="director",
        recipient="auditor",
        kind="return",
        content="A2",
    )
    db_session.flush()
    pairs = orchestrator._prior_scope_qa(db_session, version.id)
    assert ("Q1", "A1") in pairs and ("Q2", "A2") in pairs


async def test_verify_prompt_injects_prior_scope_block(db_session, monkeypatch):
    """§F1.6: when a prior scope Q&A exists, the verify prompt carries the Director's response + the
    do-not-re-raise line. (Empty ⇒ byte-identical to today, covered implicitly elsewhere.)"""
    monkeypatch.setattr(orchestrator, "verify_mechanical", lambda slug, block: None)
    version, _ = _make_version(db_session)
    rec = orchestrator._record_message
    rec(
        db_session,
        version_id=version.id,
        stage="gate_g",
        author="coordinator",
        recipient="director",
        kind="question",
        content="Q-rozsah",
        payload={"coordinator_directive": _coord_directive(triage_class="director_decision")},
    )
    rec(
        db_session,
        version_id=version.id,
        stage="gate_g",
        author="director",
        recipient="auditor",
        kind="answer",
        content="A-vysvetlenie",
    )
    db_session.flush()
    captured = {}

    async def _cap(db, *, version_id, role, stage, prompt, **kw):
        captured["prompt"] = prompt
        return PipelineStatusBlock(stage=stage, kind="gate_report", summary="ok", awaiting="director")

    monkeypatch.setattr(orchestrator, "invoke_agent", _cap)
    block = parse_status_block(_block(stage="gate_g", kind="gate_report", summary="audit", awaiting="director"))
    await orchestrator.verify_done(db_session, version.id, block)
    assert "A-vysvetlenie" in captured["prompt"]
    assert "NEoznačuj ich znova ako blocker" in captured["prompt"]


# ── CR-NS-057 gate_g FAIL flow, Fix 2: Coordinator-inferred targeted re-gate (§F2.5) ──


def _seed_gate_g_directive(db_session, version_id, **over):
    """Seed a gate_g classifying directive the PRODUCTION way: on a coordinator kind='question' gate_g
    message with the directive in payload (NOT a gate_report at build)."""
    orchestrator._record_message(
        db_session,
        version_id=version_id,
        stage="gate_g",
        author="coordinator",
        recipient="director",
        kind="question",
        content="otázka rozsahu",
        payload={"coordinator_directive": _coord_directive(**over)},
    )
    db_session.flush()


async def test_latest_gate_g_classifying_directive_reads_question_kind(db_session):
    """§F2.1: the directive on a coordinator kind='question' gate_g message IS returned; a LATER
    directive-less synthesis (gate_report, coordinator_directive JSON-null) does NOT shadow it."""
    version, _ = _make_version(db_session)
    _seed_gate_g_directive(db_session, version.id, triage_class="director_decision")
    orchestrator._record_message(
        db_session,
        version_id=version.id,
        stage="gate_g",
        author="coordinator",
        recipient="director",
        kind="gate_report",
        content="zhrnutie",
        payload={"is_synthesis": True, "coordinator_directive": None},
    )
    db_session.flush()
    d = orchestrator._latest_gate_g_classifying_directive(db_session, version.id)
    assert d is not None and d["triage_class"] == "director_decision"


async def test_infer_regate_entry_stage_design_class(db_session):
    """§F2.1: a design/scope directive (director_decision / spec_problem / route_to_designer) → gate_a."""
    for tc, pa in [
        ("director_decision", "relay"),
        ("spec_problem", "relay"),
        ("programmer_guidance", "coordinator_route_to_designer"),
    ]:
        version, _ = _make_version(db_session)
        _seed_gate_g_directive(db_session, version.id, triage_class=tc, proposed_action=pa)
        assert orchestrator._infer_regate_entry_stage(db_session, version.id) == "gate_a"


async def test_infer_regate_entry_stage_code_or_none_falls_to_build(db_session):
    """§F2.1: a code-fixable directive → build; NO gate_g directive → build."""
    version, _ = _make_version(db_session)
    _seed_gate_g_directive(
        db_session, version.id, triage_class="programmer_guidance", proposed_action="coordinator_reset_task"
    )
    assert orchestrator._infer_regate_entry_stage(db_session, version.id) == "build"
    version2, _ = _make_version(db_session)
    assert orchestrator._infer_regate_entry_stage(db_session, version2.id) == "build"


async def test_reset_done_tasks_for_regate(db_session):
    """§F2.2: done→todo (existing todo untouched, no failed left)."""
    version, project = _make_version(db_session)
    _epic, _feat, tasks = _seed_one_feat(db_session, version, project, ["A", "B"])
    tasks[0].status = "done"
    tasks[1].status = "todo"
    db_session.flush()
    orchestrator._reset_done_tasks_for_regate(db_session, version.id)
    for t in tasks:
        db_session.refresh(t)
    assert all(t.status == "todo" for t in tasks)


async def test_verdict_fail_infers_build_and_resets_done(db_session, fake_claude):
    """§F2.4: FAIL (no entry_stage) + a code-class gate_g directive → build, done tasks reset, is_regate, iter+1."""
    version, project = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _epic, _feat, (task,) = _seed_one_feat(db_session, version, project, ["T"])
    task.status = "done"
    _to_gate_g(db_session, version)
    orchestrator._get_state(db_session, version.id).status = "awaiting_director"
    db_session.flush()
    _seed_gate_g_directive(
        db_session, version.id, triage_class="programmer_guidance", proposed_action="coordinator_reset_task"
    )

    state = await orchestrator.apply_action(
        db_session, version_id=version.id, action="verdict", payload={"verdict": "FAIL"}
    )

    assert state.current_stage == "build" and state.is_regate is True and state.iteration == 1
    db_session.refresh(task)
    assert task.status == "todo"  # done reset for the re-run


async def test_verdict_fail_infers_gate_a_on_design_gap(db_session, fake_claude):
    """§F2.4: FAIL (no entry_stage) + a design-class gate_g directive → gate_a, done tasks NOT reset."""
    version, project = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _epic, _feat, (task,) = _seed_one_feat(db_session, version, project, ["T"])
    task.status = "done"
    _to_gate_g(db_session, version)
    orchestrator._get_state(db_session, version.id).status = "awaiting_director"
    db_session.flush()
    _seed_gate_g_directive(db_session, version.id, triage_class="director_decision", proposed_action="relay")

    state = await orchestrator.apply_action(
        db_session, version_id=version.id, action="verdict", payload={"verdict": "FAIL"}
    )

    assert state.current_stage == "gate_a" and state.is_regate is True
    db_session.refresh(task)
    assert task.status == "done"  # a gate_a re-gate rebuilds the epics via task_plan — no reset


async def test_verdict_fail_director_override_entry_stage(db_session, fake_claude):
    """§F2.4: an explicit Director entry_stage beats the inference; an invalid one still raises."""
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _to_gate_g(db_session, version)
    orchestrator._get_state(db_session, version.id).status = "awaiting_director"
    db_session.flush()
    _seed_gate_g_directive(db_session, version.id, triage_class="director_decision")  # would infer gate_a

    state = await orchestrator.apply_action(
        db_session, version_id=version.id, action="verdict", payload={"verdict": "FAIL", "entry_stage": "build"}
    )
    assert state.current_stage == "build"  # explicit override wins

    orchestrator._get_state(db_session, version.id).status = "awaiting_director"
    orchestrator._get_state(db_session, version.id).current_stage = "gate_g"
    db_session.flush()
    with pytest.raises(orchestrator.OrchestratorError):
        await orchestrator.apply_action(
            db_session, version_id=version.id, action="verdict", payload={"verdict": "FAIL", "entry_stage": "nonsense"}
        )


async def test_verdict_fail_from_pass_no_directive_infers_build(db_session, fake_claude):
    """§F2.4: a Director-initiated FAIL on a PASS-verified audit (no gate_g directive) → build."""
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _to_gate_g(db_session, version)
    orchestrator._get_state(db_session, version.id).status = "awaiting_director"
    db_session.flush()

    state = await orchestrator.apply_action(
        db_session, version_id=version.id, action="verdict", payload={"verdict": "FAIL"}
    )
    assert state.current_stage == "build"  # no gate_g directive → build (conservative default)


async def test_build_regate_brief_includes_gate_g_findings(db_session):
    """§F2.2: a direct FAIL→build re-run's findings block carries the gate_g audit findings."""
    version, _ = _make_version(db_session)
    orchestrator._record_message(
        db_session,
        version_id=version.id,
        stage="gate_g",
        author="auditor",
        recipient="director",
        kind="gate_report",
        content="audit",
        payload={"findings": ["chýba DPH validácia"]},
    )
    db_session.flush()
    block = orchestrator._latest_gate_g_findings(db_session, version.id)
    assert block is not None and "chýba DPH validácia" in block


async def test_gate_a_regate_build_excludes_stale_gate_g_findings(db_session):
    """§F2.2 sticky-is_regate guard: a task_plan message newer than the audit → findings superseded → None."""
    version, _ = _make_version(db_session)
    orchestrator._record_message(
        db_session,
        version_id=version.id,
        stage="gate_g",
        author="auditor",
        recipient="director",
        kind="gate_report",
        content="audit",
        payload={"findings": ["staré zistenie"]},
    )
    orchestrator._record_message(
        db_session,
        version_id=version.id,
        stage="task_plan",
        author="designer",
        recipient="director",
        kind="gate_report",
        content="nový plán",
        payload={},
    )
    db_session.flush()
    assert orchestrator._latest_gate_g_findings(db_session, version.id) is None


# ── Fast-Fix Lane (F-009, CR-NS-094) ────────────────────────────────────────────


def _fast_fix_kickoff_ok() -> str:
    """A trivial fast-fix kickoff triage → fast-lane-suitable, settles awaiting_director."""
    return _block(stage="kickoff", kind="kickoff", summary="malá oprava, vhodné pre rýchlu opravu", awaiting="director")


def _fast_fix_build_fake():
    """Role/prompt-aware fake for the fast-fix build loop (CR-NS-094): the Coordinator — NOT the Auditor —
    verifies the single task. The verify prompt starts 'Koordinátor, nezávisle over'; the Programmer brief
    starts 'Programátor'; the settle synthesis ('Fáza/udalosť…') gets a plain report it can summarize."""

    def _resp(prompt: str) -> str:
        if prompt.startswith("Koordinátor, nezávisle over"):
            return _block(
                stage="build", kind="gate_report", summary="overené", awaiting="director", task_pass=True, findings=[]
            )
        return _build_report()

    return _resp


def test_next_stage_flow_aware():
    # Fast-Fix takes the short path; new_version (default) is unchanged.
    assert orchestrator._next_stage("kickoff", "fast_fix") == "build"
    assert orchestrator._next_stage("build", "fast_fix") == "release"
    assert orchestrator._next_stage("release", "fast_fix") == "done"
    assert orchestrator._next_stage("done", "fast_fix") == "done"  # clamps at terminal
    assert orchestrator._next_stage("kickoff") == "gate_a"
    assert orchestrator._next_stage("build", "new_version") == "gate_g"
    assert orchestrator._next_stage("gate_e", "new_version") == "task_plan"


async def test_fast_fix_start_records_directive_in_kickoff(db_session, fake_claude):
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(
        db_session,
        version_id=version.id,
        action="start",
        payload={"flow_type": "fast_fix", "directive": "Oprav preklep v hlavičke faktúry"},
    )
    state = orchestrator._get_state(db_session, version.id)
    assert state.flow_type == "fast_fix" and state.current_stage == "kickoff"
    kickoff = [m for m in _msgs(db_session, version.id) if m.kind == "kickoff" and m.author == "director"][-1]
    assert kickoff.payload["flow_type"] == "fast_fix"
    assert kickoff.payload["directive"] == "Oprav preklep v hlavičke faktúry"
    # CR-NS-097 §1: the directive is ALSO the human-readable kickoff content (not the generic placeholder).
    assert kickoff.content == "Oprav preklep v hlavičke faktúry"


async def test_fast_fix_directive_reaches_kickoff_triage(db_session, fake_claude):
    # CR-NS-097 §1: the kickoff agent runs a fresh session — the Director directive must be IN the brief
    # (prompt) it triages, else the escalation guard is blind ("chýba samotný popis toho, čo mám opraviť").
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(
        db_session,
        version_id=version.id,
        action="start",
        payload={"flow_type": "fast_fix", "directive": "Premenuj 'Firmy' na 'Dodávatelia'"},
    )
    fake_claude.response = _fast_fix_kickoff_ok()
    await orchestrator.run_dispatch(db_session, version.id)
    # the FIRST (kickoff) claude call's prompt carries the directive verbatim
    assert "Premenuj 'Firmy' na 'Dodávatelia'" in fake_claude.calls[0]["prompt"]


async def test_fast_fix_kickoff_auto_advances_to_build_and_materializes_task(db_session, fake_claude):
    # CR-NS-097 §2: a trivial+clear triage AUTO-proceeds to build — NO awaiting_director gate at kickoff
    # (the Director's submission IS the authorization). The runner then continues the chain.
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(
        db_session,
        version_id=version.id,
        action="start",
        payload={"flow_type": "fast_fix", "directive": "Oprav preklep v hlavičke faktúry"},
    )
    fake_claude.response = _fast_fix_kickoff_ok()
    state = await orchestrator.run_dispatch(db_session, version.id)
    # auto-advanced to build, handed back agent_working for the runner — never settled at kickoff.
    assert state.current_stage == "build" and state.status == "agent_working"

    # the ONE minimal Task is materialized from the directive (no Director approve, no task_plan).
    task = task_service.get_next_todo_task(db_session, version.id)
    assert task is not None
    assert "preklep" in task.description and task.task_type == "backend"
    assert task_service.count_tasks(db_session, feat_id=task.feat_id) == 1


async def test_fast_fix_skips_gates_kickoff_to_build_to_release(db_session, fake_claude, monkeypatch):
    # CR-NS-097: the one-touch auto-chain. Each run_dispatch advances ONE stage and hands back
    # agent_working (the pipeline_runner drives the loop in production); here we drive it manually.
    monkeypatch.setattr(orchestrator, "_repo_head", lambda root: "a" * 40)
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(
        db_session,
        version_id=version.id,
        action="start",
        payload={"flow_type": "fast_fix", "directive": "Oprav zaokrúhľovanie DPH"},
    )
    # kickoff trivial triage → AUTO-advance to build (skipped gate_a-e + task_plan; no Director approve).
    fake_claude.response = _fast_fix_kickoff_ok()
    state = await orchestrator.run_dispatch(db_session, version.id)
    assert state.current_stage == "build" and state.status == "agent_working"

    # build the single task (coordinator verify) → clean build AUTO-advances to release (skips gate_g; no
    # approve). The Director never touches kickoff or build.
    fake_claude.response = _fast_fix_build_fake()
    state = await orchestrator.run_dispatch(db_session, version.id)
    assert state.current_stage == "release" and state.status == "agent_working"  # NOT gate_g, NOT awaiting
    assert task_service.get_next_todo_task(db_session, version.id) is None  # the task is done

    # release (coordinator) settles awaiting_director for the Director's SINGLE uat_accept touch.
    fake_claude.response = _block(stage="release", kind="done", summary="pripravené na akceptáciu", awaiting="director")
    state = await orchestrator.run_dispatch(db_session, version.id)
    assert state.current_stage == "release" and state.status == "awaiting_director"

    # uat_accept → done (the patch version is released) — the ONE Director touch.
    state = await orchestrator.apply_action(db_session, version_id=version.id, action="uat_accept")
    assert state.current_stage == "done" and state.status == "done"


async def test_fast_fix_build_verify_uses_coordinator_not_auditor(db_session, fake_claude, monkeypatch):
    monkeypatch.setattr(orchestrator, "_repo_head", lambda root: "a" * 40)
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(
        db_session,
        version_id=version.id,
        action="start",
        payload={"flow_type": "fast_fix", "directive": "Oprav VS sanitizáciu"},
    )
    fake_claude.response = _fast_fix_kickoff_ok()
    await orchestrator.run_dispatch(db_session, version.id)  # kickoff → AUTO build + task (CR-NS-097 §2)
    task = task_service.get_next_todo_task(db_session, version.id)

    fake_claude.response = _fast_fix_build_fake()
    await orchestrator.run_dispatch(db_session, version.id)

    db_session.refresh(task)
    assert task.status == "done"
    msgs = _msgs(db_session, version.id)
    # NO Auditor anywhere on a fast-fix; the verify is a Coordinator turn carrying task_pass for the task.
    assert not any(m.author == "auditor" for m in msgs)
    verify = [
        m
        for m in msgs
        if m.author == "coordinator"
        and m.stage == "build"
        and m.payload
        and m.payload.get("task_id") == str(task.id)
        and m.payload.get("task_pass") is True
    ]
    assert verify, "expected a Coordinator per-task verify message with task_pass for the fast-fix task"


async def test_fast_fix_escalation_blocks_and_proposes_convert(db_session, fake_claude):
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(
        db_session,
        version_id=version.id,
        action="start",
        payload={"flow_type": "fast_fix", "directive": "Prerob celý modul exportu na nový formát"},
    )
    # Non-trivial → the Coordinator STOPs and proposes converting to a full version (escalation guard §3).
    fake_claude.response = _block(
        stage="kickoff",
        kind="blocked",
        summary="netriviálne — multi-modul, treba návrh",
        awaiting="director",
        question="Toto je netriviálne; navrhujem konverziu na plnú verziu.",
        coordinator_directive={
            "triage_class": "director_decision",
            "proposed_action": "convert_to_full_version",
            "rationale": "multi-modul + zmena špecifikovaného správania — treba Návrhára",
            "confidence": 0.9,
        },
    )
    state = await orchestrator.run_dispatch(db_session, version.id)

    # STOP: settled blocked, never advanced past kickoff — no Designer / task_plan / gate dispatch.
    assert state.status == "blocked" and state.current_stage == "kickoff"
    assert state.current_stage not in ("gate_a", "gate_b", "gate_c", "gate_d", "gate_e", "task_plan", "gate_g")
    # the convert-to-full-version proposal is recorded on the Coordinator's message.
    coord = [m for m in _msgs(db_session, version.id) if m.author == "coordinator" and m.stage == "kickoff"][-1]
    assert coord.payload["coordinator_directive"]["proposed_action"] == "convert_to_full_version"
    # no build task was materialized (the escalation never reached build).
    assert task_service.get_next_todo_task(db_session, version.id) is None


def test_fast_fix_build_brief_marks_directive_authoritative():
    # CR-NS-097 §3: the fast_fix build brief tells the Programmer the directive is AUTHORITATIVE — execute
    # it, do NOT debate semantics. The new_version brief is UNCHANGED (studies the spec, no such note).
    from types import SimpleNamespace

    task = SimpleNamespace(number=1, title="Premenuj 'Firmy' na 'Dodávatelia'", description="Premenuj label v UI.")
    ff = orchestrator._directive_for_build_task(task, None, [], flow_type="fast_fix")
    assert "AUTORITATÍVNY" in ff and "NESPOCHYBŇUJ" in ff
    assert "docs/specs/" not in ff  # no spec section to study on a fast-fix

    nv = orchestrator._directive_for_build_task(task, None, [])  # default new_version
    assert "AUTORITATÍVNY" not in nv
    assert "docs/specs/" in nv  # regression: full-pipeline brief still points at the authoritative spec


async def test_new_version_build_still_uses_auditor_regression(db_session, fake_claude, monkeypatch):
    # Regression: new_version per-task verify is UNCHANGED — the Auditor (not the Coordinator) verifies.
    monkeypatch.setattr(orchestrator, "_repo_head", lambda root: "a" * 40)
    version, project = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _seed_cross_cutting(db_session, version, "## Invarianty")
    _seed_one_feat(db_session, version, project, ["T"])
    _to_build(db_session, version)
    fake_claude.response = _build_fake()
    state = await orchestrator.run_dispatch(db_session, version.id)
    assert state.status == "awaiting_director"
    msgs = _msgs(db_session, version.id)
    assert any(m.author == "auditor" and m.stage == "build" for m in msgs)  # auditor still verifies
    # new_version build → gate_g (NOT release) on final approve.
    state = await orchestrator.apply_action(db_session, version_id=version.id, action="approve")
    assert state.current_stage == "gate_g"


# ── Fast-Fix UAT auto-deploy (F-009, CR-NS-098) ────────────────────────────────


async def _drive_fast_fix_to_release(db_session, fake_claude, monkeypatch, *, directive="Oprav drobnosť"):
    """Drive a fresh fast_fix pipeline through kickoff + build so the NEXT run_dispatch is the release
    (Coordinator-verify + auto-deploy) turn. Returns ``(version, project)``."""
    monkeypatch.setattr(orchestrator, "_repo_head", lambda root: "a" * 40)
    version, project = _make_version(db_session)
    await orchestrator.apply_action(
        db_session,
        version_id=version.id,
        action="start",
        payload={"flow_type": "fast_fix", "directive": directive},
    )
    fake_claude.response = _fast_fix_kickoff_ok()
    await orchestrator.run_dispatch(db_session, version.id)  # kickoff → build (auto)
    fake_claude.response = _fast_fix_build_fake()
    state = await orchestrator.run_dispatch(db_session, version.id)  # build → release (auto, agent_working)
    assert state.current_stage == "release" and state.status == "agent_working"
    return version, project


def _uat_deploy_note(db_session, version_id):
    """The latest system→director ``uat_deploy`` outcome note, or None."""
    notes = [m for m in _msgs(db_session, version_id) if m.payload and m.payload.get("uat_deploy")]
    return notes[-1] if notes else None


async def test_fast_fix_release_auto_deploys_uat_when_slug_set(db_session, fake_claude, monkeypatch):
    # CR-NS-098: uat_slug set → the release-verify PASS auto-redeploys UAT via the existing tool, then
    # settles to the Director's single uat_accept ("Nasadené na UAT — over a akceptuj.").
    version, project = await _drive_fast_fix_to_release(db_session, fake_claude, monkeypatch)
    project.uat_slug = "ledger"
    db_session.flush()
    calls = []

    async def _fake_deploy(project_slug, uat_slug):
        calls.append((project_slug, uat_slug))
        return True, "OK"

    monkeypatch.setattr(orchestrator, "_uat_compose_exists", lambda slug: True)
    monkeypatch.setattr(orchestrator, "_run_uat_deploy", _fake_deploy)
    fake_claude.response = _block(stage="release", kind="done", summary="hotovo", awaiting="director")
    state = await orchestrator.run_dispatch(db_session, version.id)

    assert calls == [(project.slug, "ledger")]  # invoked with <uat_slug> mapped to the project slug
    assert state.current_stage == "release" and state.status == "awaiting_director"
    assert "Nasadené na UAT" in state.next_action
    note = _uat_deploy_note(db_session, version.id)
    assert note is not None and note.payload["uat_deploy"]["ok"] is True
    # the single Director touch still completes the lane.
    state = await orchestrator.apply_action(db_session, version_id=version.id, action="uat_accept")
    assert state.current_stage == "done" and state.status == "done"


async def test_fast_fix_release_gate_report_verify_then_deploys(db_session, fake_claude, monkeypatch):
    # CR-NS-098: a gate_report release turn runs the Coordinator-verify FIRST; on PASS it auto-deploys.
    version, project = await _drive_fast_fix_to_release(db_session, fake_claude, monkeypatch)
    project.uat_slug = "mager"
    db_session.flush()
    calls = []

    async def _fake_deploy(project_slug, uat_slug):
        calls.append((project_slug, uat_slug))
        return True, "OK"

    monkeypatch.setattr(orchestrator, "_uat_compose_exists", lambda slug: True)
    monkeypatch.setattr(orchestrator, "_run_uat_deploy", _fake_deploy)
    # release coordinator returns gate_report → verify judge (same fake, gate_report = PASS) → deploy.
    fake_claude.response = _block(stage="release", kind="gate_report", summary="overené", awaiting="director")
    state = await orchestrator.run_dispatch(db_session, version.id)

    assert calls == [(project.slug, "mager")]
    assert state.status == "awaiting_director" and "Nasadené na UAT" in state.next_action


async def test_fast_fix_release_skips_deploy_when_no_uat_slug(db_session, fake_claude, monkeypatch):
    # CR-NS-098: uat_slug NULL → skip the deploy gracefully with a system→director note, still settle to
    # await uat_accept (never silently blocked).
    version, project = await _drive_fast_fix_to_release(db_session, fake_claude, monkeypatch)
    assert project.uat_slug is None
    called = False

    async def _fake_deploy(project_slug, uat_slug):
        nonlocal called
        called = True
        return True, "OK"

    monkeypatch.setattr(orchestrator, "_run_uat_deploy", _fake_deploy)
    fake_claude.response = _block(stage="release", kind="done", summary="hotovo", awaiting="director")
    state = await orchestrator.run_dispatch(db_session, version.id)

    assert called is False  # no deploy attempted
    assert state.current_stage == "release" and state.status == "awaiting_director"
    note = _uat_deploy_note(db_session, version.id)
    assert note is not None and note.payload["uat_deploy"].get("skipped") is True
    assert "nakonfigurované" in note.content


async def test_fast_fix_release_skips_deploy_when_compose_missing(db_session, fake_claude, monkeypatch):
    # CR-NS-101: uat_slug set but /opt/uat/<slug>/docker-compose.yml absent → skip gracefully (note +
    # await uat_accept, never blocked) — a missing compose is not the fix's fault.
    version, project = await _drive_fast_fix_to_release(db_session, fake_claude, monkeypatch)
    project.uat_slug = "ledger"
    db_session.flush()
    called = False

    async def _fake_deploy(project_slug, uat_slug):
        nonlocal called
        called = True
        return True, "OK"

    monkeypatch.setattr(orchestrator, "_uat_compose_exists", lambda slug: False)
    monkeypatch.setattr(orchestrator, "_run_uat_deploy", _fake_deploy)
    fake_claude.response = _block(stage="release", kind="done", summary="hotovo", awaiting="director")
    state = await orchestrator.run_dispatch(db_session, version.id)

    assert called is False  # no redeploy attempted — compose missing
    assert state.current_stage == "release" and state.status == "awaiting_director"
    note = _uat_deploy_note(db_session, version.id)
    assert note is not None and note.payload["uat_deploy"].get("skipped") is True
    assert note.payload["uat_deploy"].get("reason") == "compose_missing"
    # the single Director touch still completes the lane.
    state = await orchestrator.apply_action(db_session, version_id=version.id, action="uat_accept")
    assert state.current_stage == "done" and state.status == "done"


async def test_fast_fix_release_deploy_failure_blocks(db_session, fake_claude, monkeypatch):
    # CR-NS-098: a non-zero / unhealthy deploy is SURFACED to the Director (blocked + the error in
    # next_action) — never hidden, never silently marked done.
    version, project = await _drive_fast_fix_to_release(db_session, fake_claude, monkeypatch)
    project.uat_slug = "ledger"
    db_session.flush()

    async def _fake_deploy(project_slug, uat_slug):
        return False, "exit 1: docker build zlyhal"

    monkeypatch.setattr(orchestrator, "_uat_compose_exists", lambda slug: True)
    monkeypatch.setattr(orchestrator, "_run_uat_deploy", _fake_deploy)
    fake_claude.response = _block(stage="release", kind="done", summary="hotovo", awaiting="director")
    state = await orchestrator.run_dispatch(db_session, version.id)

    assert state.current_stage == "release" and state.status == "blocked"
    assert state.block_reason == "system_error"  # R4 (D1): engine-side UAT deploy failure
    assert "UAT deploy zlyhal" in state.next_action and "docker build zlyhal" in state.next_action
    note = _uat_deploy_note(db_session, version.id)
    assert note is not None and note.payload["uat_deploy"]["ok"] is False


async def test_fast_fix_release_verify_fail_blocks_system_error(db_session, fake_claude, monkeypatch):
    # R4 (D1): a fast_fix release gate_report that FAILS the Coordinator-verify (BEFORE deploy) → blocked with
    # block_reason=system_error, and the auto-deploy is never reached.
    version, _project = await _drive_fast_fix_to_release(db_session, fake_claude, monkeypatch)
    deploy_called = False

    async def _fake_deploy(project_slug, uat_slug):
        nonlocal deploy_called
        deploy_called = True
        return True, "OK"

    async def _fail_verify(db, state, result, *, on_message=None):
        return "release neprešla overením", False  # (reason, is_scope)

    monkeypatch.setattr(orchestrator, "_run_uat_deploy", _fake_deploy)
    monkeypatch.setattr(orchestrator, "_verify_with_retries", _fail_verify)
    fake_claude.response = _block(stage="release", kind="gate_report", summary="overené", awaiting="director")
    state = await orchestrator.run_dispatch(db_session, version.id)

    assert state.current_stage == "release" and state.status == "blocked"
    assert state.block_reason == "system_error"
    assert deploy_called is False  # blocked at verify → never deployed


async def test_new_version_release_does_not_auto_deploy(db_session, fake_claude, monkeypatch):
    # Regression: the auto-deploy hook is fast_fix-ONLY. A new_version release (generic gate_report path)
    # must NOT invoke the deploy, even WITH a uat_slug set, and still offers uat_accept.
    called = False

    async def _spy(project_slug, uat_slug):
        nonlocal called
        called = True
        return True, "OK"

    monkeypatch.setattr(orchestrator, "_run_uat_deploy", _spy)
    version, project = _make_version(db_session)
    project.uat_slug = "ledger"
    db_session.flush()
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    _settle(db_session, version.id)
    await orchestrator.apply_action(db_session, version_id=version.id, action="verdict", payload={"verdict": "PASS"})
    fake_claude.response = _block(stage="release", kind="gate_report", summary="release ok", awaiting="director")
    state = await orchestrator.run_dispatch(db_session, version.id)

    assert state.current_stage == "release" and state.status == "awaiting_director"
    assert called is False  # fast_fix-only hook
    assert "uat_accept" in orchestrator.determine_available_actions(state)


class _FakeProc:
    """Minimal async-subprocess stand-in for :func:`orchestrator._run_uat_deploy` tests."""

    def __init__(self, returncode: int, output: bytes = b""):
        self.returncode = returncode
        self._output = output

    async def communicate(self):
        return self._output, b""

    def kill(self):
        pass


async def test_run_uat_deploy_redeploys_existing_compose_with_version(monkeypatch):
    # CR-NS-101: plain redeploy of the EXISTING compose (NOT uat-deploy.py) — exactly
    # `docker compose -f /opt/uat/<slug>/docker-compose.yml up -d --build --force-recreate`, with the FE
    # build-arg stamped via VITE_APP_VERSION. Exit 0 → (True, "OK").
    captured = {}

    async def _fake_exec(*cmd, stdout=None, stderr=None, env=None):
        captured["cmd"] = cmd
        captured["env"] = env
        return _FakeProc(0, b"deploy log tail")

    monkeypatch.setattr(orchestrator.asyncio, "create_subprocess_exec", _fake_exec)
    monkeypatch.setattr(orchestrator, "_fe_app_version", lambda slug: "0.1.42")
    ok, detail = await orchestrator._run_uat_deploy("nex-ledger", "ledger")

    assert ok is True and detail == "OK"
    assert list(captured["cmd"]) == [
        "docker",
        "compose",
        "-f",
        "/opt/uat/ledger/docker-compose.yml",
        "up",
        "-d",
        "--build",
        "--force-recreate",
    ]
    assert captured["env"]["VITE_APP_VERSION"] == "0.1.42"  # FE build-arg stamped
    assert "uat-deploy.py" not in " ".join(captured["cmd"])  # no provisioner invocation


async def test_run_uat_deploy_nonzero_exit_returns_failure(monkeypatch):
    async def _fake_exec(*cmd, stdout=None, stderr=None, env=None):
        return _FakeProc(2, b"boom: docker build failed")

    monkeypatch.setattr(orchestrator.asyncio, "create_subprocess_exec", _fake_exec)
    ok, detail = await orchestrator._run_uat_deploy("nex-ledger", "ledger")

    assert ok is False and "exit 2" in detail and "docker build failed" in detail


async def test_run_uat_deploy_spawn_failure_returns_failure(monkeypatch):
    async def _fake_exec(*cmd, stdout=None, stderr=None, env=None):
        raise OSError("docker not found")

    monkeypatch.setattr(orchestrator.asyncio, "create_subprocess_exec", _fake_exec)
    ok, detail = await orchestrator._run_uat_deploy("nex-ledger", "ledger")

    assert ok is False and "nepodarilo spustiť" in detail


def test_fe_app_version_from_git_count(monkeypatch):
    # CR-NS-101: VITE_APP_VERSION = 0.1.<git rev-list --count HEAD>.
    class _R:
        returncode = 0
        stdout = "123\n"

    monkeypatch.setattr(orchestrator.subprocess, "run", lambda *a, **k: _R())
    assert orchestrator._fe_app_version("nex-ledger") == "0.1.123"


def test_fe_app_version_falls_back_when_git_unavailable(monkeypatch):
    def _boom(*a, **k):
        raise OSError("git not found")

    monkeypatch.setattr(orchestrator.subprocess, "run", _boom)
    assert orchestrator._fe_app_version("nex-ledger") == "0.1.0"


def test_fe_app_version_falls_back_on_nonzero_git(monkeypatch):
    class _R:
        returncode = 128
        stdout = ""

    monkeypatch.setattr(orchestrator.subprocess, "run", lambda *a, **k: _R())
    assert orchestrator._fe_app_version("missing-repo") == "0.1.0"


# ── CR-NS-103: autonomous Coordinator for the fast-fix lane (F-009 §3 D5) ───────


def _answer_directive(confidence=0.9, rationale="Áno — slovo už je správne, pokračuj.", **over):
    """A Coordinator `coordinator_answer_question` directive (routine build question → autonomous answer)."""
    d = _coord_directive(
        triage_class="programmer_routine_question",
        proposed_action="coordinator_answer_question",
        confidence=confidence,
        rationale=rationale,
    )
    d.update(over)
    return d


async def _fast_fix_at_build_with_task(db_session, fake_claude, *, directive="Premenuj 'Firmy' na 'Dodávatelia'"):
    """Start a fast_fix pipeline and put it at build with ONE todo Task (loop entry)."""
    version, project = _make_version(db_session)
    await orchestrator.apply_action(
        db_session,
        version_id=version.id,
        action="start",
        payload={"flow_type": "fast_fix", "directive": directive},
    )
    _epic, _feat, (task,) = _seed_one_feat(db_session, version, project, ["Rýchla oprava"])
    state = _to_build(db_session, version)
    assert state.flow_type == "fast_fix"
    return version, project, state, task


# ── Part 2: _maybe_autonomous_answer unit gate ──────────────────────────────────


async def test_maybe_autonomous_answer_executes_high_conf(db_session, fake_claude):
    """D5: a coordinator_answer_question, conf 0.9, fast_fix → AUTO-ANSWER: task reset to todo, re-dispatched,
    a VISIBLE is_autonomous note recorded; returns the answer brief (mirrors the Director framed-return)."""
    version, _project, state, task = await _fast_fix_at_build_with_task(db_session, fake_claude)
    brief = await orchestrator._maybe_autonomous_answer(db_session, state, task, _answer_directive())

    assert brief is not None and "Koordinátor odpovedal" in brief
    db_session.refresh(task)
    assert task.status == "todo"  # resumed for re-dispatch
    notes = [m for m in _msgs(db_session, version.id) if (m.payload or {}).get("is_autonomous")]
    assert len(notes) == 1
    assert notes[0].author == "coordinator" and notes[0].recipient == "director"
    assert notes[0].payload["action"] == "coordinator_answer_question"


async def test_maybe_autonomous_answer_gated_to_fast_fix(db_session, fake_claude):
    """No autonomy leak: a perfect answer directive on a new_version flow → None (escalate path unchanged)."""
    version, project = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")  # new_version
    _epic, _feat, (task,) = _seed_one_feat(db_session, version, project, ["T"])
    state = _to_build(db_session, version)
    assert state.flow_type == "new_version"
    assert await orchestrator._maybe_autonomous_answer(db_session, state, task, _answer_directive()) is None
    assert not any((m.payload or {}).get("is_autonomous") for m in _msgs(db_session, version.id))


async def test_maybe_autonomous_answer_escalates_low_confidence(db_session, fake_claude):
    """D5: confidence < 0.85 (the answer floor, above the 0.80 recovery floor) → None (escalate)."""
    _v, _p, state, task = await _fast_fix_at_build_with_task(db_session, fake_claude)
    assert (
        await orchestrator._maybe_autonomous_answer(db_session, state, task, _answer_directive(confidence=0.82)) is None
    )


async def test_maybe_autonomous_answer_escalates_director_decision(db_session, fake_claude):
    """D5: triage_class=director_decision → None (genuine scope is never auto-answered)."""
    _v, _p, state, task = await _fast_fix_at_build_with_task(db_session, fake_claude)
    d = _answer_directive(triage_class="director_decision")
    assert await orchestrator._maybe_autonomous_answer(db_session, state, task, d) is None


async def test_maybe_autonomous_answer_ignores_non_answer_action(db_session, fake_claude):
    """Only coordinator_answer_question is auto-answered — a reset_task directive → None (recovery's job)."""
    _v, _p, state, task = await _fast_fix_at_build_with_task(db_session, fake_claude)
    d = _coord_directive(proposed_action="coordinator_reset_task", confidence=0.95)
    assert await orchestrator._maybe_autonomous_answer(db_session, state, task, d) is None


async def test_maybe_autonomous_answer_per_task_cap(db_session, fake_claude):
    """D5 cap: ≤2 answers per task — the 3rd routine question on the same task → None (escalate)."""
    _v, _p, state, task = await _fast_fix_at_build_with_task(db_session, fake_claude)
    first = await orchestrator._maybe_autonomous_answer(db_session, state, task, _answer_directive())
    # the helper resets task→todo; re-pick it as the loop would.
    db_session.refresh(task)
    second = await orchestrator._maybe_autonomous_answer(db_session, state, task, _answer_directive())
    db_session.refresh(task)
    third = await orchestrator._maybe_autonomous_answer(db_session, state, task, _answer_directive())
    assert first is not None and second is not None and third is None  # cap = 2
    notes = [m for m in _msgs(db_session, _v.id) if (m.payload or {}).get("is_autonomous")]
    assert len(notes) == 2


# ── CR-NS-103 follow-up FIX A: the recovery cap and the answer cap are orthogonal ──


async def test_answer_does_not_consume_recovery_cap(db_session, fake_claude):
    """FIX A: an autonomous ANSWER does NOT fill the Pillar B recovery cap — after one auto-answer the SAME
    task can STILL be auto-recovered (reset_task), instead of being escalated to the Director."""
    _v, _p, state, task = await _fast_fix_at_build_with_task(db_session, fake_claude)
    assert await orchestrator._maybe_autonomous_answer(db_session, state, task, _answer_directive()) is not None
    db_session.refresh(task)
    recovery = _coord_directive(
        triage_class="nex_studio_bug",
        proposed_action="coordinator_reset_task",
        confidence=0.9,
        target={"task_id": str(task.id)},
    )
    assert await orchestrator._maybe_autonomous_recovery(db_session, state, task, recovery) is True
    notes = [m for m in _msgs(db_session, _v.id) if (m.payload or {}).get("is_autonomous")]
    actions = sorted(n.payload["action"] for n in notes)
    assert actions == ["coordinator_answer_question", "coordinator_reset_task"]  # both fired, neither capped


async def test_recovery_does_not_consume_answer_cap(db_session, fake_claude):
    """FIX A (vice-versa): an autonomous RECOVERY does NOT fill the fast_fix answer cap — after one auto-reset
    the SAME task can STILL be auto-answered twice (the answer budget is untouched)."""
    _v, _p, state, task = await _fast_fix_at_build_with_task(db_session, fake_claude)
    recovery = _coord_directive(
        triage_class="nex_studio_bug",
        proposed_action="coordinator_reset_task",
        confidence=0.9,
        target={"task_id": str(task.id)},
    )
    assert await orchestrator._maybe_autonomous_recovery(db_session, state, task, recovery) is True
    db_session.refresh(task)
    # the answer cap (≤2) is still full-budget after the recovery: two answers both fire.
    assert await orchestrator._maybe_autonomous_answer(db_session, state, task, _answer_directive()) is not None
    db_session.refresh(task)
    assert await orchestrator._maybe_autonomous_answer(db_session, state, task, _answer_directive()) is not None
    db_session.refresh(task)
    assert await orchestrator._maybe_autonomous_answer(db_session, state, task, _answer_directive()) is None  # cap
    notes = [m for m in _msgs(db_session, _v.id) if (m.payload or {}).get("is_autonomous")]
    actions = sorted(n.payload["action"] for n in notes)
    assert actions == [
        "coordinator_answer_question",
        "coordinator_answer_question",
        "coordinator_reset_task",
    ]


# ── Part 2: build-loop integration (autonomous answer re-dispatches the SAME task) ──


def _fast_fix_answer_then_pass_fake():
    """Build-loop fake: first Programmer dispatch → a routine question; the Coordinator relay → an
    answer directive (conf 0.9); the resumed task brief (carries 'Koordinátor odpovedal') → a clean build;
    the Coordinator verify → task_pass."""

    def _resp(prompt: str) -> str:
        if prompt.startswith("Koordinátor, nezávisle over"):
            return _block(
                stage="build", kind="gate_report", summary="overené", awaiting="director", task_pass=True, findings=[]
            )
        if prompt.startswith("Worker '"):
            return _block(
                stage="build",
                kind="gate_report",
                summary="odpoveď na rutinnú otázku",
                awaiting="director",
                coordinator_directive=_answer_directive(),
            )
        if "Koordinátor odpovedal" in prompt:  # resumed task → now builds clean
            return _build_report()
        # first Programmer dispatch → a routine question
        return _block(
            stage="build",
            kind="question",
            summary="otázka",
            awaiting="director",
            question="Slovo už je 'Dodávatelia' — mám pokračovať?",
        )

    return _resp


async def test_build_autonomous_answer_redispatches_same_task(db_session, fake_claude, monkeypatch):
    """Integration D5: a routine build question → the Coordinator AUTO-ANSWERS (is_autonomous) and the SAME
    task is re-dispatched with the answer → it passes → fast_fix AUTO-advances to release. No Director gate."""
    monkeypatch.setattr(orchestrator, "_repo_head", lambda root: "a" * 40)
    version, _project, _state, task = await _fast_fix_at_build_with_task(db_session, fake_claude)
    fake_claude.response = _fast_fix_answer_then_pass_fake()

    state = await orchestrator.run_dispatch(db_session, version.id)

    assert state.current_stage == "release" and state.status == "agent_working"  # clean build → release
    db_session.refresh(task)
    assert task.status == "done"
    notes = [m for m in _msgs(db_session, version.id) if (m.payload or {}).get("is_autonomous")]
    assert len(notes) == 1 and notes[0].payload["action"] == "coordinator_answer_question"


async def test_build_autonomous_answer_cap_escalates_third_question(db_session, fake_claude, monkeypatch):
    """Integration D5 cap: the Programmer keeps asking → 2 autonomous answers, then the 3rd question escalates
    (status=blocked, the Programmer question on the board). Exactly TWO autonomous notes."""
    monkeypatch.setattr(orchestrator, "_repo_head", lambda root: "a" * 40)
    version, _project, _state, task = await _fast_fix_at_build_with_task(db_session, fake_claude)

    def _resp(prompt: str) -> str:
        if prompt.startswith("Worker '"):
            return _block(
                stage="build",
                kind="gate_report",
                summary="odpoveď",
                awaiting="director",
                coordinator_directive=_answer_directive(),
            )
        # the Programmer NEVER settles — always a routine question (even after answers)
        return _block(stage="build", kind="question", summary="otázka", awaiting="director", question="A čo toto pole?")

    fake_claude.response = _resp
    state = await orchestrator.run_dispatch(db_session, version.id)

    assert state.status == "blocked" and state.current_stage == "build"
    assert "sa pýta" in state.next_action  # escalated to the Director
    notes = [m for m in _msgs(db_session, version.id) if (m.payload or {}).get("is_autonomous")]
    assert len(notes) == 2  # capped at 2 answers, then escalate
    db_session.refresh(task)
    assert task.status == "in_progress"  # never settled done/failed


async def test_build_new_version_question_still_escalates_no_autonomy_leak(db_session, fake_claude, monkeypatch):
    """No autonomy leak: a new_version build question — even WITH an answer directive — escalates to the
    Director byte-for-byte (no auto-answer, no is_autonomous note)."""
    monkeypatch.setattr(orchestrator, "_repo_head", lambda root: "a" * 40)
    version, project = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")  # new_version
    _seed_one_feat(db_session, version, project, ["T"])
    _to_build(db_session, version)

    def _resp(prompt: str) -> str:
        if prompt.startswith("Worker '"):
            return _block(
                stage="build",
                kind="gate_report",
                summary="odpoveď",
                awaiting="director",
                coordinator_directive=_answer_directive(),
            )
        return _block(stage="build", kind="question", summary="otázka", awaiting="director", question="Ktorý helper?")

    fake_claude.response = _resp
    state = await orchestrator.run_dispatch(db_session, version.id)

    assert state.status == "blocked" and "sa pýta" in state.next_action
    assert not any((m.payload or {}).get("is_autonomous") for m in _msgs(db_session, version.id))


# ── Part 1: release-stage Coordinator-question carve-out (the PRIMARY live fix) ──


async def test_release_coordinator_question_carveout_deploys_no_director_gate(db_session, fake_claude, monkeypatch):
    """D5/Part 1: a routine Coordinator question at the fast_fix release turn does NOT escalate — control
    falls through to the engine-owned auto-deploy (no 'third approval'). The stuck nex-ledger v0.1.2 fix."""
    version, project = await _drive_fast_fix_to_release(db_session, fake_claude, monkeypatch)
    project.uat_slug = "ledger"
    db_session.flush()
    calls = []

    async def _fake_deploy(project_slug, uat_slug):
        calls.append((project_slug, uat_slug))
        return True, "OK"

    monkeypatch.setattr(orchestrator, "_uat_compose_exists", lambda slug: True)
    monkeypatch.setattr(orchestrator, "_run_uat_deploy", _fake_deploy)
    # The Coordinator's release turn is a routine QUESTION (no director_decision) — the live "third approval".
    fake_claude.response = _block(
        stage="release",
        kind="question",
        summary="otázka o nasadení",
        awaiting="director",
        question="Mám spustiť automatické nasadenie na UAT?",
    )
    state = await orchestrator.run_dispatch(db_session, version.id)

    assert calls == [(project.slug, "ledger")]  # the deploy fired despite the question
    assert state.current_stage == "release" and state.status == "awaiting_director"
    assert "Nasadené na UAT" in state.next_action
    assert "sa pýta" not in state.next_action  # NOT escalated as the third approval


async def test_release_coordinator_director_decision_still_escalates(db_session, fake_claude, monkeypatch):
    """D5/Part 1: a genuine director_decision scope at the fast_fix release turn DOES escalate (convert to a
    full version) — the deploy is NOT run. Distinguishes a routine question from real scope."""
    version, project = await _drive_fast_fix_to_release(db_session, fake_claude, monkeypatch)
    project.uat_slug = "ledger"
    db_session.flush()
    called = False

    async def _spy(project_slug, uat_slug):
        nonlocal called
        called = True
        return True, "OK"

    monkeypatch.setattr(orchestrator, "_uat_compose_exists", lambda slug: True)
    monkeypatch.setattr(orchestrator, "_run_uat_deploy", _spy)
    fake_claude.response = _block(
        stage="release",
        kind="question",
        summary="vlastne väčšia zmena",
        awaiting="director",
        question="Toto je vlastne väčšia zmena — navrhujem konverziu na plnú verziu.",
        coordinator_directive={
            "triage_class": "director_decision",
            "proposed_action": "convert_to_full_version",
            "rationale": "multi-modul rozsah — treba Návrhára",
            "confidence": 0.9,
        },
    )
    state = await orchestrator.run_dispatch(db_session, version.id)

    assert called is False  # no deploy on a genuine scope
    assert state.current_stage == "release" and state.status == "blocked"
    assert "sa pýta" in state.next_action  # escalated to the Director


# ── Part 3: engine-owned deploy locked — no-op build → release → deploy ──────────


async def test_fast_fix_noop_build_still_releases_and_deploys(db_session, fake_claude, monkeypatch):
    """D5/Part 3: a NO-OP build (empty diff — the word was already correct) still advances build → release and
    the engine-owned deploy fires (--build --force-recreate is idempotent, so the Director SEES the UAT)."""
    monkeypatch.setattr(orchestrator, "_repo_head", lambda root: "a" * 40)
    version, project, _state, _task = await _fast_fix_at_build_with_task(db_session, fake_claude)
    project.uat_slug = "ledger"
    db_session.flush()
    calls = []

    async def _fake_deploy(project_slug, uat_slug):
        calls.append((project_slug, uat_slug))
        return True, "OK"

    monkeypatch.setattr(orchestrator, "_uat_compose_exists", lambda slug: True)
    monkeypatch.setattr(orchestrator, "_run_uat_deploy", _fake_deploy)

    def _noop_build(prompt: str) -> str:
        if prompt.startswith("Koordinátor, nezávisle over"):
            return _block(
                stage="build",
                kind="gate_report",
                summary="žiadna zmena — slovo už je správne",
                awaiting="director",
                task_pass=True,
                findings=[],
            )
        # the Programmer reports a no-op (empty commits/deliverables)
        return _block(
            stage="build",
            kind="gate_report",
            summary="žiadna zmena potrebná",
            awaiting="director",
            commits=[],
            deliverables=[],
        )

    # build (no-op) → AUTO-advances to release (agent_working)
    fake_claude.response = _noop_build
    state = await orchestrator.run_dispatch(db_session, version.id)
    assert state.current_stage == "release" and state.status == "agent_working"

    # release turn → engine-owned deploy fires even on the no-op build
    fake_claude.response = _block(stage="release", kind="done", summary="pripravené", awaiting="director")
    state = await orchestrator.run_dispatch(db_session, version.id)
    assert calls == [(project.slug, "ledger")]
    assert state.status == "awaiting_director" and "Nasadené na UAT" in state.next_action


# ── Part 2: Director-approved answer execution (executable action handler) ───────


async def test_apply_coordinator_recommendation_executes_answer_question(db_session, fake_claude):
    """The new coordinator_answer_question is executable: a Director-approved answer resets the held task to
    todo + re-dispatches (no OrchestratorError from the executor's else-branch)."""
    version, project = _make_version(db_session)
    await orchestrator.apply_action(
        db_session, version_id=version.id, action="start", payload={"flow_type": "fast_fix", "directive": "x"}
    )
    _epic, _feat, (task,) = _seed_one_feat(db_session, version, project, ["T"])
    task.status = "in_progress"
    db_session.flush()
    state = _to_build(db_session, version)
    state.status = "awaiting_director"
    db_session.flush()
    _seed_coordinator_directive(db_session, version.id, _answer_directive(target={"task_id": str(task.id)}))
    state = await orchestrator.apply_action(
        db_session, version_id=version.id, action="apply_coordinator_recommendation"
    )
    db_session.refresh(task)
    assert task.status == "todo"  # executed (reset for the answered retry), not relayed
    assert state.status == "agent_working"  # re-dispatched
    assert any(m.kind == "approval" and "odpoveď na otázku" in m.content for m in _msgs(db_session, version.id))


# ── R1 dispatch resilience (v0.7.0) ─────────────────────────────────────────────
# Baseline capture + durable single-flight (R1-b), lost-work detection (R1-c), all-stage
# orphan recovery + session TTL (R1-d). The cockpit's own dispatch path must never silently
# lose agent work and must serialize dispatch durably.


async def test_begin_dispatch_captures_baseline_and_arms_flag(db_session, fake_claude, monkeypatch):
    # R1-b UNIT: _begin_dispatch captures dispatch_baseline_sha (repo HEAD) + arms dispatch_in_flight.
    monkeypatch.setattr(orchestrator, "_repo_head", lambda root: "b" * 40)
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")  # → _begin_dispatch
    state = orchestrator._get_state(db_session, version.id)
    assert state.dispatch_baseline_sha == "b" * 40
    assert state.dispatch_in_flight is True
    assert state.status == "agent_working"
    # Seam #4: a re-entry (parse-retry) does NOT overwrite the frozen baseline.
    monkeypatch.setattr(orchestrator, "_repo_head", lambda root: "c" * 40)
    orchestrator._begin_dispatch(db_session, state)
    assert state.dispatch_baseline_sha == "b" * 40  # frozen across the dispatch


async def test_settle_clears_dispatch_flag_and_baseline(db_session, fake_claude, monkeypatch):
    # R1-b UNIT: the status set listener clears the flag + baseline on every ORM settle ("settle paths").
    monkeypatch.setattr(orchestrator, "_repo_head", lambda root: "b" * 40)
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    state = orchestrator._get_state(db_session, version.id)
    assert state.dispatch_in_flight is True and state.dispatch_baseline_sha == "b" * 40
    state.status = "awaiting_director"  # settle
    assert state.dispatch_in_flight is False
    assert state.dispatch_baseline_sha is None


async def test_apply_action_durable_single_flight_guard(db_session, fake_claude):
    # R1-b UNIT: a dispatching action while dispatch_in_flight=True raises (the durable guard that survives
    # a restart, beyond the in-memory _ACTIVE_DISPATCH). Set the flag AFTER the settle so the listener
    # doesn't clear it (simulates a stale in-flight flag a restart left before orphan recovery).
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    state = orchestrator._get_state(db_session, version.id)
    state.status = "awaiting_director"
    state.dispatch_in_flight = True
    db_session.flush()
    with pytest.raises(orchestrator.OrchestratorError, match="Dispečer už beží"):
        await orchestrator.apply_action(db_session, version_id=version.id, action="approve")
    # not mutated past the guard
    assert orchestrator._get_state(db_session, version.id).current_stage == "kickoff"


def _arm_dispatch_state(db_session, version, stage="kickoff", actor="coordinator", baseline="b" * 40):
    """Seed a PipelineState as a live dispatch (agent_working + a frozen baseline)."""
    state = PipelineState(
        version_id=version.id,
        flow_type="new_version",
        current_stage=stage,
        current_actor=actor,
        status="agent_working",
        next_action="working",
    )
    db_session.add(state)
    db_session.flush()
    state.dispatch_baseline_sha = baseline  # set AFTER construction so the listener keeps it
    state.dispatch_in_flight = True
    db_session.flush()
    return state


def _lost_work_notifs(db_session, version_id):
    return [
        m
        for m in _msgs(db_session, version_id)
        if m.author == "system" and m.kind == "notification" and (m.payload or {}).get("lost_work_audit")
    ]


async def test_invoke_agent_timeout_records_lost_work_audit_with_commits(db_session, monkeypatch):
    # R1-c UNIT: the timeout catch audits baseline..HEAD and records ONE commit-audit notification
    # (count >= 1 branch) while still returning a ParseFailure (escalation intact).
    async def _boom(**kwargs):
        raise orchestrator.ClaudeAgentError("claude invocation timed out after 900s")

    monkeypatch.setattr(orchestrator, "invoke_claude", _boom)
    monkeypatch.setattr(orchestrator, "_repo_head", lambda root: "h" * 40)
    monkeypatch.setattr(orchestrator, "_rev_list_count", lambda root, baseline: 3)
    version, _ = _make_version(db_session)
    _arm_dispatch_state(db_session, version)

    result = await orchestrator.invoke_agent(
        db_session, version_id=version.id, role="coordinator", stage="kickoff", prompt="x"
    )

    assert isinstance(result, ParseFailure)
    assert result.lost_work is not None and result.lost_work["detected_commit_count"] == 3
    notifs = _lost_work_notifs(db_session, version.id)
    assert len(notifs) == 1
    assert notifs[0].payload["detected_commit_count"] == 3
    assert notifs[0].payload["dispatch_baseline_sha"] == "b" * 40
    assert notifs[0].payload["post_timeout_head_sha"] == "h" * 40
    assert "3 commitov" in notifs[0].content


async def test_invoke_agent_timeout_records_lost_work_audit_no_commits(db_session, monkeypatch):
    # R1-c UNIT: the count == 0 branch reads "žiadna zmena nezistená".
    async def _boom(**kwargs):
        raise orchestrator.ClaudeAgentError("claude invocation timed out after 900s")

    monkeypatch.setattr(orchestrator, "invoke_claude", _boom)
    monkeypatch.setattr(orchestrator, "_repo_head", lambda root: "h" * 40)
    monkeypatch.setattr(orchestrator, "_rev_list_count", lambda root, baseline: 0)
    version, _ = _make_version(db_session)
    _arm_dispatch_state(db_session, version)

    result = await orchestrator.invoke_agent(
        db_session, version_id=version.id, role="coordinator", stage="kickoff", prompt="x"
    )

    assert isinstance(result, ParseFailure)
    assert result.lost_work["detected_commit_count"] == 0
    notifs = _lost_work_notifs(db_session, version.id)
    assert len(notifs) == 1 and "žiadna zmena" in notifs[0].content


async def test_invoke_agent_timeout_no_baseline_no_audit(db_session, monkeypatch):
    # R1-c UNIT: no dispatch baseline armed → no audit, plain ParseFailure (unchanged escalation).
    async def _boom(**kwargs):
        raise orchestrator.ClaudeAgentError("claude invocation timed out after 900s")

    monkeypatch.setattr(orchestrator, "invoke_claude", _boom)
    version, _ = _make_version(db_session)
    state = PipelineState(
        version_id=version.id,
        flow_type="new_version",
        current_stage="kickoff",
        current_actor="coordinator",
        status="agent_working",
        next_action="working",
    )
    db_session.add(state)
    db_session.flush()  # dispatch_baseline_sha stays NULL

    result = await orchestrator.invoke_agent(
        db_session, version_id=version.id, role="coordinator", stage="kickoff", prompt="x"
    )
    assert isinstance(result, ParseFailure)
    assert result.lost_work is None
    assert _lost_work_notifs(db_session, version.id) == []


async def test_run_dispatch_timeout_with_commits_surfaces_lost_work(db_session, monkeypatch):
    # R1-c INTEGRATION: a timeout during a Coordinator turn with commits → audit recorded, awaiting_director,
    # next_action names the commit count; the audit is recorded ONCE despite the parse-retries.
    async def _boom(**kwargs):
        raise orchestrator.ClaudeAgentError("claude invocation timed out after 900s")

    monkeypatch.setattr(orchestrator, "_repo_head", lambda root: "h" * 40)
    monkeypatch.setattr(orchestrator, "_rev_list_count", lambda root, baseline: 2)
    monkeypatch.setattr(orchestrator, "invoke_claude", _boom)
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")  # arms baseline=h*40

    state = await orchestrator.run_dispatch(db_session, version.id)

    assert state.status == "awaiting_director"  # never a bare blocked, never auto-proceeds
    assert "2 commitov" in state.next_action
    assert len(_lost_work_notifs(db_session, version.id)) == 1  # idempotent across parse-retries


async def test_run_dispatch_timeout_no_commits_surfaces_no_change(db_session, monkeypatch):
    # R1-c INTEGRATION: a timeout with no commits → "žiadna zmena", still awaiting_director.
    async def _boom(**kwargs):
        raise orchestrator.ClaudeAgentError("claude invocation timed out after 900s")

    monkeypatch.setattr(orchestrator, "_repo_head", lambda root: "h" * 40)
    monkeypatch.setattr(orchestrator, "_rev_list_count", lambda root, baseline: 0)
    monkeypatch.setattr(orchestrator, "invoke_claude", _boom)
    version, _ = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")

    state = await orchestrator.run_dispatch(db_session, version.id)

    assert state.status == "awaiting_director"
    assert "žiadna zmena" in state.next_action


async def test_recover_orphaned_release_with_commits(db_session, monkeypatch):
    # R1-d INTEGRATION: a restart at release/agent_working → recovery flips to awaiting_director, records the
    # commit audit (generic stage message), and clears the durable flag.
    monkeypatch.setattr(orchestrator, "_repo_head", lambda root: "h" * 40)
    monkeypatch.setattr(orchestrator, "_rev_list_count", lambda root, baseline: 4)
    version, _ = _make_version(db_session)
    _arm_dispatch_state(db_session, version, stage="release", actor="coordinator", baseline="h" * 40)

    assert orchestrator.recover_orphaned_builds_on_startup(db_session) == 1

    state = orchestrator._get_state(db_session, version.id)
    assert state.current_stage == "release"
    assert state.status == "awaiting_director"
    assert state.dispatch_in_flight is False
    assert state.dispatch_baseline_sha is None
    assert "release" in state.next_action and "4 commitov" in state.next_action
    notif = [m for m in _msgs(db_session, version.id) if (m.payload or {}).get("recovery_audit")]
    assert notif and notif[-1].payload["detected_commit_count"] == 4


async def test_dispatch_baseline_independent_of_task_baseline(db_session, fake_claude, monkeypatch):
    # R1 REGRESSION (Seam #7): the dispatch-level baseline (PipelineState) and the per-task Task.baseline_sha
    # are independent — settling clears the dispatch baseline but never touches the task baseline.
    monkeypatch.setattr(orchestrator, "_repo_head", lambda root: "d" * 40)
    version, project = _make_version(db_session)
    await orchestrator.apply_action(db_session, version_id=version.id, action="start")
    state = orchestrator._get_state(db_session, version.id)
    assert state.dispatch_baseline_sha == "d" * 40
    _epic, _feat, (task,) = _seed_one_feat(db_session, version, project, ["T"])
    task.baseline_sha = "t" * 40
    db_session.flush()

    state.status = "awaiting_director"  # settle → dispatch baseline reset
    db_session.flush()
    db_session.refresh(task)
    assert state.dispatch_baseline_sha is None  # dispatch baseline cleared
    assert task.baseline_sha == "t" * 40  # per-task verify anchor untouched


def test_cleanup_old_orchestrator_sessions_prunes_idle(db_session, monkeypatch):
    # R1-d UNIT (D3): rows untouched > 7d on last_input_at are pruned; fresh rows survive.
    from datetime import datetime, timedelta, timezone

    old = OrchestratorSession(project_slug="p-old", role="designer", claude_session_id=uuid.uuid4())
    fresh = OrchestratorSession(project_slug="p-fresh", role="designer", claude_session_id=uuid.uuid4())
    db_session.add_all([old, fresh])
    db_session.flush()
    db_session.execute(
        update(OrchestratorSession)
        .where(OrchestratorSession.project_slug == "p-old")
        .values(last_input_at=datetime.now(timezone.utc) - timedelta(days=8))
    )
    db_session.flush()
    monkeypatch.setattr(db_session, "commit", db_session.flush)  # SAVEPOINT-safe

    n = orchestrator.cleanup_old_orchestrator_sessions(db_session)

    assert n == 1
    remaining = db_session.execute(select(OrchestratorSession.project_slug)).scalars().all()
    assert "p-old" not in remaining and "p-fresh" in remaining


async def test_invoke_agent_bumps_last_input_at(db_session, fake_claude):
    # R1-d UNIT (D3): every invoke_agent stamps the session's last_input_at (drives the TTL).
    from datetime import datetime, timedelta, timezone

    version, project = _make_version(db_session)
    orchestrator._resolve_orch_session(db_session, project.slug, "designer")  # create the row
    stale = datetime.now(timezone.utc) - timedelta(days=10)
    db_session.execute(
        update(OrchestratorSession)
        .where(OrchestratorSession.project_slug == project.slug, OrchestratorSession.role == "designer")
        .values(last_input_at=stale)
    )
    db_session.flush()

    await orchestrator.invoke_agent(db_session, version_id=version.id, role="designer", stage="gate_a", prompt="x")

    row = db_session.execute(
        select(OrchestratorSession).where(
            OrchestratorSession.project_slug == project.slug, OrchestratorSession.role == "designer"
        )
    ).scalar_one()
    assert row.last_input_at > stale  # bumped on the turn
