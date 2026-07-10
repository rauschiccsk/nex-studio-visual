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
        allowed_tools=None,
        sandbox=False,
        log_dir=None,
        log_label=None,
    ):
        # konzultacia-followup.md Fix 1: ``invoke_agent`` now forwards ``allowed_tools=`` on EVERY turn (the
        # read-only consult profile, or ``None`` for a build). konzultacia-sidecar-sandbox.md Part 2 adds
        # ``sandbox=`` (``True`` only for a consult turn). build-robustness-crash-handling.md Fix 1 adds
        # ``log_dir=``/``log_label=`` (the per-turn diagnostic log). Accept all so this fake mirrors the real
        # ``invoke_claude`` signature (else a TypeError — the regression this fixes).
        self.calls.append(
            {
                "prompt": prompt,
                "model": model,
                "effort": effort,
                "json_schema": json_schema,
                "allowed_tools": allowed_tools,
                "sandbox": sandbox,
            }
        )
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
    # CR-V2-028: the model defaults to DEFAULT_AGENT_MODEL (Opus) when there is no per-user pick.
    version, _ = _make_version_with_owner_config(db_session, [])
    assert orchestrator._resolve_dispatch_overrides(db_session, version.id, "auditor") == (
        orchestrator.DEFAULT_AGENT_MODEL,
        "max",
    )


def test_resolve_overrides_auditor_explicit_overrides_default(db_session):
    version, _ = _make_version_with_owner_config(db_session, [("auditor", "claude-opus-4-8", "low")])
    assert orchestrator._resolve_dispatch_overrides(db_session, version.id, "auditor") == (
        "claude-opus-4-8",
        "low",
    )


def test_resolve_overrides_no_owner_falls_back(db_session):
    # _make_version leaves owner_id NULL → no per-user config. CR-V2-028: the model still defaults to
    # DEFAULT_AGENT_MODEL for BOTH roles (never the CLI's small default); the Auditor effort stays max,
    # the AI Agent effort stays unset (CLI default).
    version, _ = _make_version(db_session)
    assert orchestrator._resolve_dispatch_overrides(db_session, version.id, "ai_agent") == (
        orchestrator.DEFAULT_AGENT_MODEL,
        None,
    )
    assert orchestrator._resolve_dispatch_overrides(db_session, version.id, "auditor") == (
        orchestrator.DEFAULT_AGENT_MODEL,
        "max",
    )


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


# ── CR-V2-029: runaway containment + visible failure ────────────────────────────


async def test_kill_process_tree_kills_the_whole_group(monkeypatch):
    """Timeout must SIGKILL the process GROUP (parent + the helper sub-agents the CLI spawns), not just
    the parent PID — orphaned helpers kept a Príprava turn alive at ~1200% CPU."""
    seen: dict = {}
    monkeypatch.setattr(claude_agent.os, "getpgid", lambda pid: 4242)
    monkeypatch.setattr(claude_agent.os, "killpg", lambda pgid, sig: seen.update(pgid=pgid, sig=sig))

    class _Proc:
        pid = 999

        async def wait(self):
            return 0

    await claude_agent._kill_process_tree(_Proc())
    assert seen == {"pgid": 4242, "sig": claude_agent.signal.SIGKILL}


async def test_kill_process_tree_falls_back_when_group_already_gone(monkeypatch):
    """If the group is already gone, fall back to a plain proc.kill() — cleanup must never raise."""
    killed = {"plain": False}

    def _gone(*_a):
        raise ProcessLookupError

    monkeypatch.setattr(claude_agent.os, "getpgid", lambda pid: 4242)
    monkeypatch.setattr(claude_agent.os, "killpg", _gone)

    class _Proc:
        pid = 999

        def kill(self):
            killed["plain"] = True

        async def wait(self):
            return 0

    await claude_agent._kill_process_tree(_Proc())
    assert killed["plain"] is True


async def test_parse_retry_stops_when_wall_clock_budget_exhausted(db_session, monkeypatch):
    """The whole turn shares ONE wall-clock budget — with too little left, no re-emit fires (previously
    each of 1+_PARSE_RETRIES attempts got a fresh full timeout → up to 3×900s)."""
    calls = {"n": 0}

    async def _fake(*, prompt, **kwargs):
        calls["n"] += 1
        return ("garbage — not a valid status block", claude_agent.UsageMetadata(10, 5, "m"))

    monkeypatch.setattr(orchestrator, "invoke_claude", _fake)
    version, _ = _make_version(db_session)
    # budget (10s) < _MIN_RETRY_BUDGET_S (60s) → after the primary, no re-emit is launched.
    result = await orchestrator.invoke_agent_with_parse_retry(
        db_session,
        version_id=version.id,
        role=orchestrator.AI_AGENT_ROLE,
        stage="navrh",
        prompt="go",
        timeout=10,
    )
    assert isinstance(result, ParseFailure)
    assert calls["n"] == 1  # primary only — the budget gate blocked the re-emits


async def test_parse_exhaustion_records_readable_notification(db_session):
    """A parse-exhausted turn records a system→manazer notification (parser reason + raw-output excerpt) so
    the AI Agent tab is never left empty (it previously showed a blank 'awaiting' screen)."""
    version, _ = _make_version(db_session)
    state = _arm_dispatch_state(db_session, version, stage="priprava")
    pf = ParseFailure("status block schema invalid — kind: Field required", raw="…surový výstup agenta…")

    await orchestrator._record_parse_exhaustion(
        db_session,
        state,
        stage="priprava",
        result=pf,
        human_hint="Skús znova (Uprav).",
        on_message=None,
    )

    note = [m for m in _msgs(db_session, version.id) if m.kind == "notification"][-1]
    assert note.author == "system" and note.recipient == "manazer"
    assert "nevrátil platný" in note.content
    assert note.payload["raw_excerpt"] == "…surový výstup agenta…"
    assert note.payload["parse_failure_reason"].startswith("status block schema invalid")


# ── CR-V2-031: inject the exact status-block enum values ─────────────────────────


def test_status_block_instruction_names_exact_stage_and_enums():
    """The per-turn instruction names the EXACT slovak ``stage`` literal (never the English translation)
    plus the valid kind/awaiting values — so the agent emits them verbatim."""
    msg = orchestrator._status_block_instruction("priprava")
    assert "priprava" in msg
    assert "preparation" not in msg.lower()  # the exact enum, not Opus's English translation
    assert "gate_report" in msg and "question" in msg  # kind enum listed
    assert "manazer" in msg  # awaiting enum listed
    # CR-V2-034: also mandates formatted Markdown (no wall of text) — newlines are fine in JSON.
    assert "Markdown" in msg
    assert "PRÁZDNYM riadkom" in msg


async def test_invoke_agent_appends_exact_stage_to_prompt(db_session, monkeypatch):
    """CR-V2-031: every dispatch's prompt carries the exact ``stage`` value (here ``priprava``) so the agent
    cannot guess/translate it — injected at the single chokepoint, so retries inherit it too."""
    captured: dict = {}

    async def _fake(*, prompt, **kwargs):
        captured["prompt"] = prompt
        return (_navrh_block(), claude_agent.UsageMetadata(1, 1, "m"))

    monkeypatch.setattr(orchestrator, "invoke_claude", _fake)
    version, _ = _make_version(db_session)
    await orchestrator.invoke_agent(
        db_session, version_id=version.id, role="ai_agent", stage="priprava", prompt="BRIEF-XYZ"
    )
    assert "BRIEF-XYZ" in captured["prompt"]  # the original directive is preserved
    assert "priprava" in captured["prompt"]  # the exact stage value is appended
    assert "PEVNÉ KÓDOVÉ" in captured["prompt"]  # the enum-contract instruction is present


# ── CR-V2-038: AI-Agent helper model ─────────────────────────────────────────


def test_resolve_helper_model_defaults_to_haiku_when_unset(db_session):
    # No per-owner helper_model → the dispatch default (Haiku): the AI Agent does the hard core itself and
    # delegates only bulk to cheap helpers.
    version, _ = _make_version_with_owner_config(db_session, [("ai_agent", "claude-opus-4-8", "max")])
    assert orchestrator._resolve_helper_model(db_session, version.id) == orchestrator.DEFAULT_HELPER_MODEL


def test_resolve_helper_model_honours_explicit_owner_choice(db_session):
    # The Manažér can raise the AI Agent's helpers to Opus for a high-stakes build ("identically to Dedo").
    version, owner = _make_version_with_owner_config(db_session, [])
    db_session.add(UserAgentSettings(user_id=owner.id, agent_role="ai_agent", helper_model="claude-opus-4-8"))
    db_session.flush()
    assert orchestrator._resolve_helper_model(db_session, version.id) == "claude-opus-4-8"


async def test_invoke_agent_injects_helper_directive_for_ai_agent_only(db_session, monkeypatch):
    # The helper-model directive is appended to the AI Agent's turn (the helper model can't be a CLI flag),
    # carrying the resolved model — but the Auditor (which is not the helper-spawner) never gets it.
    fake = _fake_claude(monkeypatch)
    version, owner = _make_version_with_owner_config(db_session, [])
    db_session.add(UserAgentSettings(user_id=owner.id, agent_role="ai_agent", helper_model="claude-opus-4-8"))
    db_session.flush()

    await orchestrator.invoke_agent(
        db_session, version_id=version.id, role="ai_agent", stage="programovanie", prompt="go"
    )
    ai_prompt = fake.calls[-1]["prompt"]
    assert "pomocné agenty" in ai_prompt.lower() and "claude-opus-4-8" in ai_prompt

    await orchestrator.invoke_agent(db_session, version_id=version.id, role="auditor", stage="verifikacia", prompt="go")
    assert "pomocné agenty" not in fake.calls[-1]["prompt"].lower()
