"""Milestone-I — live coverage of ``invoke_agent`` + its dispatch-lifecycle helpers (v2).

These are the engine PRIMITIVES every v2 phase relies on but never asserts directly — the phase tests
(navrh/programovanie/verifikacia/…) all STUB ``invoke_agent`` / ``invoke_agent_with_parse_retry`` /
``_begin_dispatch`` / ``_resolve_dispatch_overrides`` / ``_resolve_orch_session`` to a no-op, so the
contracts those primitives themselves carry had no live test once the v1 ``test_orchestrator.py`` module
was deferred. This file re-expresses the still-relevant v1 invoke_agent / dispatch-lifecycle assertions
in the v2 4-phase vocabulary (roles ``ai_agent`` / ``auditor``; recipient ``manazer``; a Návrh
``gate_report`` carries a non-empty ``plan``), run against the real v2 branch DB.

Covered (one focused test per v1-source contract):
  * message recording (author=role, kind, payload.commits)
  * silent parse failure (no Manažér-facing leak) — fence path AND structured path
  * R3 structured/fence ladder: prefer structured → fall back to fence → silent ParseFailure
  * the constrained-grammar schema (PIPELINE_STATUS_JSON_SCHEMA) is always threaded to claude
  * WS-D metrics: payload.usage / payload.timing; bare-text → usage None; parse-retry SUMS usage+attempts
  * owner-config overrides (model+effort) resolved per dispatch incl. Auditor-defaults-max + parse-retry
  * last_input_at bumped on every turn (TTL driver); resolve_orch_session creates-then-reuses (one row)
  * timeout → lost-work audit (commits / no-commits / no-baseline)
"""

import json
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update

from backend.db.models.foundation import User, UserAgentSettings
from backend.db.models.orchestrator import OrchestratorSession
from backend.db.models.pipeline import PipelineMessage, PipelineState
from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.services import claude_agent, orchestrator
from backend.services.pipeline_status import ParseFailure, PipelineStatusBlock

# (pytest ``asyncio_mode = auto`` — async tests run without an explicit mark.)

# A minimal valid Návrh task plan — the v2 content contract requires a non-empty EPIC→FEAT→TASK ``plan``
# on a ``stage=navrh`` + ``kind=gate_report`` block (CR-V2-011), so every AI-Agent gate_report below uses it.
_NAVRH_PLAN = {
    "epics": [{"title": "E1", "feats": [{"title": "F1", "tasks": [{"title": "T1", "task_type": "backend"}]}]}]
}


def _block(stage="navrh", kind="gate_report", summary="ok", awaiting="manazer", **extra) -> str:
    """A valid ``<<<PIPELINE_STATUS>>>`` fence (the rollout-safe fallback transport)."""
    body = {"stage": stage, "kind": kind, "summary": summary, "awaiting": awaiting}
    body.update(extra)
    return f"<<<PIPELINE_STATUS>>>\n{json.dumps(body)}\n<<<END_PIPELINE_STATUS>>>"


def _block_dict(stage="navrh", kind="gate_report", summary="ok", awaiting="manazer", **extra) -> dict:
    """The status block as a plain dict — the shape claude returns in ``structured_output`` (R3)."""
    body = {"stage": stage, "kind": kind, "summary": summary, "awaiting": awaiting}
    body.update(extra)
    return body


def _navrh_block() -> str:
    return _block(summary="14 endpoints", commits=["abc123"], plan=_NAVRH_PLAN)


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
    )
    db_session.add(project)
    db_session.flush()
    version = Version(project_id=project.id, version_number=f"1.{uuid.uuid4().hex[:4]}.0")
    db_session.add(version)
    db_session.flush()
    return version, project


def _make_version_with_owner_config(db_session, configs):
    """version+project whose OWNER has the given ``user_agent_settings`` rows.

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
        type="standard",
        auth_mode="password",
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


class FakeClaude:
    """Controllable async stand-in for ``invoke_claude`` (the headless CLI seam). ``response`` is a fixed
    value OR a ``callable(prompt) -> value``; the value may be a bare ``str`` (fence text, no usage), a
    ``(text, usage)`` 2-tuple, or a ``(text, usage, structured_output)`` 3-tuple (R3)."""

    def __init__(self):
        self.response = _navrh_block()
        self.calls: list[dict] = []

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
        self.calls.append({"prompt": prompt, "model": model, "effort": effort, "json_schema": json_schema})
        return self.response(prompt) if callable(self.response) else self.response


def _fake_claude(monkeypatch):
    fake = FakeClaude()
    monkeypatch.setattr(orchestrator, "invoke_claude", fake)
    return fake


def _msgs(db_session, version_id):
    return (
        db_session.execute(
            select(PipelineMessage).where(PipelineMessage.version_id == version_id).order_by(PipelineMessage.seq.asc())
        )
        .scalars()
        .all()
    )


def _arm_dispatch_state(db_session, version, stage="programovanie", actor="ai_agent", baseline="b" * 40):
    """Seed a PipelineState as a live dispatch (agent_working + a frozen dispatch baseline)."""
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
    state.dispatch_baseline_sha = baseline  # set AFTER construction so the settle listener keeps it
    state.dispatch_in_flight = True
    db_session.flush()
    return state


def _lost_work_notifs(db_session, version_id):
    return [
        m
        for m in _msgs(db_session, version_id)
        if m.author == "system" and m.kind == "notification" and (m.payload or {}).get("lost_work_audit")
    ]


# ── session resolution (live: creates-then-reuses, one row per project/role) ────


def test_resolve_orch_session_creates_then_reuses(db_session):
    version, project = _make_version(db_session)
    sid1, first1 = orchestrator._resolve_orch_session(db_session, project.slug, orchestrator.AI_AGENT_ROLE)
    assert first1 is True
    sid2, first2 = orchestrator._resolve_orch_session(db_session, project.slug, orchestrator.AI_AGENT_ROLE)
    assert first2 is False
    assert sid1 == sid2
    rows = (
        db_session.execute(select(OrchestratorSession).where(OrchestratorSession.project_slug == project.slug))
        .scalars()
        .all()
    )
    assert len(rows) == 1


# ── invoke_agent records exactly one message with the turn contract ─────────────


async def test_invoke_agent_records_message(db_session, monkeypatch):
    fake = _fake_claude(monkeypatch)
    version, _ = _make_version(db_session)
    fake.response = _navrh_block()
    result = await orchestrator.invoke_agent(
        db_session, version_id=version.id, role=orchestrator.AI_AGENT_ROLE, stage="navrh", prompt="go"
    )
    assert isinstance(result, PipelineStatusBlock)
    msgs = _msgs(db_session, version.id)
    assert len(msgs) == 1
    assert msgs[0].author == "ai_agent"  # author = role
    assert msgs[0].kind == "gate_report"
    assert msgs[0].recipient == "manazer"
    assert msgs[0].payload["commits"] == ["abc123"]


async def test_invoke_agent_parse_failure_is_silent(db_session, monkeypatch):
    # CR-NS-022 §2: a parse failure records NO Manažér-facing dump — it returns the ParseFailure for the
    # bounded retry / caller to handle; a single invoke leaks nothing (no raw-dump message).
    fake = _fake_claude(monkeypatch)
    version, _ = _make_version(db_session)
    fake.response = "no status block here"
    result = await orchestrator.invoke_agent(
        db_session, version_id=version.id, role=orchestrator.AI_AGENT_ROLE, stage="navrh", prompt="go"
    )
    assert isinstance(result, ParseFailure)
    assert _msgs(db_session, version.id) == []  # silent — nothing leaked to the Manažér


# ── R3: native structured output preferred; the fence is the fallback ──────────


async def test_invoke_agent_prefers_structured_output(db_session, monkeypatch):
    """R3 D1/D2: a grammar-constrained ``structured_output`` object is validated + recorded — no fence in
    the result text is needed."""
    fake = _fake_claude(monkeypatch)
    version, _ = _make_version(db_session)
    fake.response = (
        "no fence here — just prose",
        None,
        _block_dict(summary="from structured", commits=["s1"], plan=_NAVRH_PLAN),
    )
    result = await orchestrator.invoke_agent(
        db_session, version_id=version.id, role=orchestrator.AI_AGENT_ROLE, stage="navrh", prompt="go"
    )
    assert isinstance(result, PipelineStatusBlock)
    assert result.summary == "from structured"
    msgs = _msgs(db_session, version.id)
    assert len(msgs) == 1
    assert msgs[0].content == "from structured"
    assert msgs[0].payload["commits"] == ["s1"]


async def test_invoke_agent_structured_invalid_falls_back_to_fence(db_session, monkeypatch):
    """R3 D2: a ``structured_output`` that fails the content contract degrades to the fence parse of the
    result text (the fence parser STAYS as the rollout-safe fallback)."""
    fake = _fake_claude(monkeypatch)
    version, _ = _make_version(db_session)
    fake.response = (
        _block(summary="from fence", plan=_NAVRH_PLAN),  # valid fence in the text
        None,
        _block_dict(stage="not_a_real_stage", summary="bogus"),  # structured fails (unknown stage)
    )
    result = await orchestrator.invoke_agent(
        db_session, version_id=version.id, role=orchestrator.AI_AGENT_ROLE, stage="navrh", prompt="go"
    )
    assert isinstance(result, PipelineStatusBlock)
    assert result.summary == "from fence"  # fence fallback won


async def test_invoke_agent_structured_invalid_no_fence_is_parsefailure(db_session, monkeypatch):
    """R3 D2/D3: structured invalid AND no fence in the text → the SAME silent ParseFailure the fence path
    returns (which the bounded parse-retry then feeds back)."""
    fake = _fake_claude(monkeypatch)
    version, _ = _make_version(db_session)
    fake.response = (
        "prose with no fence",
        None,
        _block_dict(stage="bogus_stage"),  # structured invalid; nothing to fall back to
    )
    result = await orchestrator.invoke_agent(
        db_session, version_id=version.id, role=orchestrator.AI_AGENT_ROLE, stage="navrh", prompt="go"
    )
    assert isinstance(result, ParseFailure)
    assert _msgs(db_session, version.id) == []  # silent, like the fence path


async def test_invoke_agent_passes_status_schema_to_claude(db_session, monkeypatch):
    """R3: invoke_agent ALWAYS invokes claude with PIPELINE_STATUS_JSON_SCHEMA (the constrained-grammar
    contract is threaded through every turn)."""
    fake = _fake_claude(monkeypatch)
    version, _ = _make_version(db_session)
    fake.response = _navrh_block()
    await orchestrator.invoke_agent(
        db_session, version_id=version.id, role=orchestrator.AI_AGENT_ROLE, stage="navrh", prompt="go"
    )
    assert fake.calls
    assert fake.calls[-1]["json_schema"] == orchestrator.PIPELINE_STATUS_JSON_SCHEMA


# ── WS-D metrics: usage + timing capture ───────────────────────────────────────


async def test_invoke_agent_records_usage_and_timing(db_session, monkeypatch):
    """A turn's token usage + dispatch wall-clock land in payload.usage / payload.timing (WS-D)."""
    fake = _fake_claude(monkeypatch)
    version, _ = _make_version(db_session)
    fake.response = (_navrh_block(), claude_agent.UsageMetadata(input_tokens=100, output_tokens=40, model="claude-z"))
    await orchestrator.invoke_agent(
        db_session, version_id=version.id, role=orchestrator.AI_AGENT_ROLE, stage="navrh", prompt="go"
    )
    msg = _msgs(db_session, version.id)[0]
    assert msg.payload["usage"] == {"input_tokens": 100, "output_tokens": 40, "model": "claude-z"}
    assert msg.payload["timing"]["parse_attempts"] == 1
    assert msg.payload["timing"]["duration_seconds"] >= 0.0


async def test_invoke_agent_no_usage_records_none_not_zeros(db_session, monkeypatch):
    """A bare-text response (no usage envelope) → payload.usage is None, never fabricated zeros (WS-D)."""
    fake = _fake_claude(monkeypatch)
    version, _ = _make_version(db_session)
    fake.response = _navrh_block()  # bare str → usage None
    await orchestrator.invoke_agent(
        db_session, version_id=version.id, role=orchestrator.AI_AGENT_ROLE, stage="navrh", prompt="go"
    )
    msg = _msgs(db_session, version.id)[0]
    assert msg.payload["usage"] is None
    assert msg.payload["timing"]["parse_attempts"] == 1


async def test_parse_retry_accumulates_usage_and_attempts(db_session, monkeypatch):
    """Failed parse re-emits burn tokens too — the surviving message SUMS across the primary + every retry,
    and timing.parse_attempts counts them (WS-D)."""
    seq = [
        ("garbage — not a valid status block", claude_agent.UsageMetadata(10, 5, "m")),  # ParseFailure
        (_navrh_block(), claude_agent.UsageMetadata(20, 8, "m")),  # recovery re-emit
    ]
    calls = {"n": 0}

    async def _fake(*, prompt, **kwargs):
        i = min(calls["n"], len(seq) - 1)
        calls["n"] += 1
        return seq[i]

    monkeypatch.setattr(orchestrator, "invoke_claude", _fake)
    version, _ = _make_version(db_session)
    result = await orchestrator.invoke_agent_with_parse_retry(
        db_session, version_id=version.id, role=orchestrator.AI_AGENT_ROLE, stage="navrh", prompt="go"
    )
    assert isinstance(result, PipelineStatusBlock)
    msg = [m for m in _msgs(db_session, version.id) if m.payload and "usage" in m.payload][-1]
    assert msg.payload["usage"]["input_tokens"] == 30  # 10 (failed re-emit) + 20 (success)
    assert msg.payload["usage"]["output_tokens"] == 13  # 5 + 8
    assert msg.payload["timing"]["parse_attempts"] == 2  # primary + one recovery re-emit


# ── owner-config model/effort overrides (CR-NS-040 / CR-V2-008) ─────────────────


def test_resolve_overrides_owner_config_applies(db_session):
    version, _ = _make_version_with_owner_config(db_session, [("ai_agent", "claude-sonnet-4-6", "high")])
    assert orchestrator._resolve_dispatch_overrides(db_session, version.id, "ai_agent") == (
        "claude-sonnet-4-6",
        "high",
    )


def test_resolve_overrides_auditor_defaults_max(db_session):
    # CR-V2-008 / AUTON-5: the Auditor (independent verifier) effort defaults to max when unset.
    version, _ = _make_version_with_owner_config(db_session, [])
    assert orchestrator._resolve_dispatch_overrides(db_session, version.id, "auditor") == (None, "max")


def test_resolve_overrides_auditor_explicit_overrides_default(db_session):
    version, _ = _make_version_with_owner_config(db_session, [("auditor", "claude-opus-4-8", "low")])
    assert orchestrator._resolve_dispatch_overrides(db_session, version.id, "auditor") == (
        "claude-opus-4-8",
        "low",
    )


def test_resolve_overrides_no_owner_falls_back(db_session):
    # _make_version leaves owner_id NULL → no config; the AI Agent gets no flags, the Auditor still max.
    version, _ = _make_version(db_session)
    assert orchestrator._resolve_dispatch_overrides(db_session, version.id, "ai_agent") == (None, None)
    assert orchestrator._resolve_dispatch_overrides(db_session, version.id, "auditor") == (None, "max")


async def test_invoke_agent_threads_owner_model_effort(db_session, monkeypatch):
    fake = _fake_claude(monkeypatch)
    version, _ = _make_version_with_owner_config(db_session, [("ai_agent", "claude-sonnet-4-6", "high")])
    await orchestrator.invoke_agent(db_session, version_id=version.id, role="ai_agent", stage="navrh", prompt="go")
    assert fake.calls[-1]["model"] == "claude-sonnet-4-6"
    assert fake.calls[-1]["effort"] == "high"


async def test_parse_retry_keeps_model_effort(db_session, monkeypatch):
    """Each parse-retry re-enters invoke_agent → re-resolves + re-applies the owner config (no loss)."""
    fake = _fake_claude(monkeypatch)
    version, _ = _make_version_with_owner_config(db_session, [("ai_agent", "claude-sonnet-4-6", "high")])
    # Primary (prompt "go") fails to parse; the retry (re-prompt starting "Tvoj…") emits a valid block.
    fake.response = lambda prompt: (_navrh_block() if prompt.startswith("Tvoj") else "no status block")
    result = await orchestrator.invoke_agent_with_parse_retry(
        db_session, version_id=version.id, role="ai_agent", stage="navrh", prompt="go"
    )
    assert isinstance(result, PipelineStatusBlock)
    assert len(fake.calls) >= 2  # primary + at least one retry
    assert all(c["model"] == "claude-sonnet-4-6" and c["effort"] == "high" for c in fake.calls)


# ── session TTL driver: every turn bumps last_input_at ─────────────────────────


async def test_invoke_agent_bumps_last_input_at(db_session, monkeypatch):
    _fake_claude(monkeypatch)
    version, project = _make_version(db_session)
    orchestrator._resolve_orch_session(db_session, project.slug, "ai_agent")  # create the row
    stale = datetime.now(timezone.utc) - timedelta(days=10)
    db_session.execute(
        update(OrchestratorSession)
        .where(OrchestratorSession.project_slug == project.slug, OrchestratorSession.role == "ai_agent")
        .values(last_input_at=stale)
    )
    db_session.flush()

    await orchestrator.invoke_agent(db_session, version_id=version.id, role="ai_agent", stage="navrh", prompt="x")

    row = db_session.execute(
        select(OrchestratorSession).where(
            OrchestratorSession.project_slug == project.slug, OrchestratorSession.role == "ai_agent"
        )
    ).scalar_one()
    assert row.last_input_at > stale  # re-stamped to ≈now


# ── timeout → lost-work audit (committed-but-lost work surfaced, never dropped) ─


async def test_invoke_agent_timeout_records_lost_work_audit_with_commits(db_session, monkeypatch):
    # The timeout catch audits baseline..HEAD and records ONE commit-audit notification (count>=1 branch)
    # while still returning a ParseFailure (the escalation is intact).
    async def _boom(**kwargs):
        raise orchestrator.ClaudeAgentError("claude invocation timed out after 900s")

    monkeypatch.setattr(orchestrator, "invoke_claude", _boom)
    monkeypatch.setattr(orchestrator, "_repo_head", lambda root: "h" * 40)
    monkeypatch.setattr(orchestrator, "_rev_list_count", lambda root, baseline: 3)
    version, _ = _make_version(db_session)
    _arm_dispatch_state(db_session, version)

    result = await orchestrator.invoke_agent(
        db_session, version_id=version.id, role="ai_agent", stage="programovanie", prompt="x"
    )

    assert isinstance(result, ParseFailure)
    assert result.lost_work is not None and result.lost_work["detected_commit_count"] == 3
    notifs = _lost_work_notifs(db_session, version.id)
    assert len(notifs) == 1
    assert notifs[0].payload["detected_commit_count"] == 3
    assert notifs[0].payload["dispatch_baseline_sha"] == "b" * 40
    assert notifs[0].payload["post_timeout_head_sha"] == "h" * 40
    assert notifs[0].recipient == "manazer"  # CR-V2-009: lost-work audit re-pointed to the Manažér
    assert "3 commitov" in notifs[0].content


async def test_invoke_agent_timeout_records_lost_work_audit_no_commits(db_session, monkeypatch):
    # The count == 0 branch reads "žiadna zmena nezistená".
    async def _boom(**kwargs):
        raise orchestrator.ClaudeAgentError("claude invocation timed out after 900s")

    monkeypatch.setattr(orchestrator, "invoke_claude", _boom)
    monkeypatch.setattr(orchestrator, "_repo_head", lambda root: "h" * 40)
    monkeypatch.setattr(orchestrator, "_rev_list_count", lambda root, baseline: 0)
    version, _ = _make_version(db_session)
    _arm_dispatch_state(db_session, version)

    result = await orchestrator.invoke_agent(
        db_session, version_id=version.id, role="ai_agent", stage="programovanie", prompt="x"
    )

    assert isinstance(result, ParseFailure)
    assert result.lost_work["detected_commit_count"] == 0
    notifs = _lost_work_notifs(db_session, version.id)
    assert len(notifs) == 1 and "žiadna zmena" in notifs[0].content


async def test_invoke_agent_timeout_no_baseline_no_audit(db_session, monkeypatch):
    # No dispatch baseline armed → no audit, plain ParseFailure (the escalation is unchanged).
    async def _boom(**kwargs):
        raise orchestrator.ClaudeAgentError("claude invocation timed out after 900s")

    monkeypatch.setattr(orchestrator, "invoke_claude", _boom)
    version, _ = _make_version(db_session)
    state = PipelineState(
        version_id=version.id,
        flow_type="new_version",
        current_stage="programovanie",
        current_actor="ai_agent",
        status="agent_working",
        next_action="working",
    )
    db_session.add(state)
    db_session.flush()  # dispatch_baseline_sha stays NULL

    result = await orchestrator.invoke_agent(
        db_session, version_id=version.id, role="ai_agent", stage="programovanie", prompt="x"
    )
    assert isinstance(result, ParseFailure)
    assert result.lost_work is None
    assert _lost_work_notifs(db_session, version.id) == []
