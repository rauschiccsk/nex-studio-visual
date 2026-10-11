"""What a build turn cost — on every way out (ICCINT-168).

Until v4.42 spend was taken from the run result: with ``--resume`` it holds the price and per-model breakdown FOR THE
WHOLE SESSION, and a turn that timed out or crashed had nothing — ``record(None, …)``, as if it were free. Here the real
``_invoke_once``/``invoke_claude``/``invoke_agent`` run; only the Claude Code process is imitated, which — like the real
one — writes the session log: the finished message, and the session total (``cost-state``) only when the run closes.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from backend.db.models.pipeline import PipelineState
from backend.services import build_sandbox, claude_agent, orchestrator
from backend.services.claude_agent import ClaudeAgentError, ClaudeAgentTimeout, UsageMetadata
from backend.services.pipeline_status import ParseFailure
from backend.services.usage_ledger import UsagePart

SESSION_TOTAL_BEFORE = {"inputTokens": 10, "outputTokens": 900, "cacheReadInputTokens": 50_000, "costUSD": 0.5}


def _cost_state(total: dict) -> dict:
    usage = {"cacheCreationInputTokens": 0, **total}
    return {"type": "cost-state", "totalCostUSD": usage["costUSD"], "modelUsage": {"claude-opus-5-5": usage}}


def _message(output: int) -> dict:
    return {
        "type": "assistant",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "message": {
            "id": f"msg-{uuid4().hex}",
            "model": "claude-opus-5-5",
            "usage": {
                "input_tokens": 2,
                "output_tokens": output,
                "cache_read_input_tokens": 20_000,
                "cache_creation_input_tokens": 300,
            },
        },
    }


@dataclass
class _Session:
    path: Path
    session: UUID


@pytest.fixture()
def transcript(tmp_path, monkeypatch) -> _Session:
    """Session log of project ``p`` with one earlier finished turn (the session total before this turn)."""
    monkeypatch.setattr(build_sandbox, "_CLAUDE_HOME_DIR", str(tmp_path / "claude"))
    session = uuid4()
    path = Path(build_sandbox._host_session_dir(str(claude_agent.PROJECTS_ROOT / "p"))) / f"{session}.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(_cost_state(SESSION_TOTAL_BEFORE)) + "\n")
    return _Session(path, session)


def _append(path: Path, *rows: dict) -> None:
    with open(path, "a") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


class _Claude:
    """Claude Code process: during the run it writes a finished message, on closing the session total and the result."""

    def __init__(self, path: Path, *, closes: bool, returncode: int = 0, hang: bool = False):
        self.path, self.closes, self.returncode, self.hang, self.pid = path, closes, returncode, hang, 4242

    async def communicate(self):
        _append(self.path, _message(output=700))
        if self.closes:
            after = {**SESSION_TOTAL_BEFORE, "inputTokens": 12, "outputTokens": 1600, "cacheReadInputTokens": 70_000}
            _append(self.path, _cost_state({**after, "cacheCreationInputTokens": 300, "costUSD": 0.5 + 0.0262}))
        if self.hang:
            await asyncio.sleep(30)
        envelope = {
            "result": "hotovo",
            # run result: ``usage`` per run, ``modelUsage``/``total_cost_usd`` FOR THE SESSION — not used for the price
            "usage": {"input_tokens": 2, "output_tokens": 700, "cache_read_input_tokens": 20_000},
            "modelUsage": {"claude-opus-5-5": {"outputTokens": 1600, "costUSD": 0.5262}},
            "total_cost_usd": 0.5262,
        }
        return json.dumps(envelope).encode(), b""

    def kill(self):
        pass

    async def wait(self):
        return self.returncode


def _run(monkeypatch, proc) -> None:
    async def _exec(*args, **kwargs):
        return proc

    async def _no_kill(_proc):
        return None

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _exec)
    monkeypatch.setattr(claude_agent, "_kill_process_tree", _no_kill)


THIS_TURN = UsagePart("claude-opus-5-5", 2, 700, 20_000, 300, 0.0262)


async def test_a_finished_turn_costs_the_difference_of_session_totals(monkeypatch, transcript):
    """The run result reports the session total ($0.5262); the turn costs only the totals' difference ($0.0262)."""
    _run(monkeypatch, _Claude(transcript.path, closes=True))
    _text, usage, _s = await claude_agent._invoke_once(
        project_slug="p", claude_session_id=transcript.session, prompt="go", timeout=5
    )
    [part] = usage.parts
    assert part == UsagePart("claude-opus-5-5", 2, 700, 20_000, 300, pytest.approx(0.0262))
    assert (usage.input_tokens, usage.output_tokens, usage.model) == (2, 700, "claude-opus-5-5")


async def test_a_timed_out_turn_carries_its_spend_on_the_exception(monkeypatch, transcript):
    """A timed-out turn is not free: its finished messages from the log, without price (Claude Code did not bill it)."""
    _run(monkeypatch, _Claude(transcript.path, closes=False, hang=True))
    with pytest.raises(ClaudeAgentTimeout) as ei:
        await claude_agent._invoke_once(
            project_slug="p", claude_session_id=transcript.session, prompt="go", timeout=0.2
        )
    assert ei.value.usage.parts == (UsagePart("claude-opus-5-5", 2, 700, 20_000, 300, None),)


async def test_a_crashed_turn_that_claude_code_closed_keeps_its_price(monkeypatch, transcript):
    """The run ended with an error, but Claude Code closed it (the total grew) — the price is its, not an estimate."""
    _run(monkeypatch, _Claude(transcript.path, closes=True, returncode=1))
    with pytest.raises(ClaudeAgentError) as ei:
        await claude_agent._invoke_once(project_slug="p", claude_session_id=transcript.session, prompt="go", timeout=5)
    assert ei.value.usage.parts[0].cost_usd == pytest.approx(0.0262)


async def test_without_a_session_record_the_turns_own_usage_stands(monkeypatch, tmp_path):
    """The log cannot be read → the spend from the run result remains (``usage`` is per run), without a price — nothing
    is invented and the session total is not passed off as the turn price."""
    monkeypatch.setattr(build_sandbox, "_CLAUDE_HOME_DIR", str(tmp_path / "nothing-here"))

    class _NoRecord(_Claude):
        async def communicate(self):
            body = {"result": "ok", "usage": {"input_tokens": 2, "output_tokens": 700, "cache_read_input_tokens": 9}}
            body["modelUsage"] = {"claude-opus-5-5": {"outputTokens": 1600}}
            body["total_cost_usd"] = 0.5262
            return json.dumps(body).encode(), b""

    _run(monkeypatch, _NoRecord(tmp_path / "x.jsonl", closes=False))
    _t, usage, _s = await claude_agent._invoke_once(project_slug="p", claude_session_id=uuid4(), prompt="go", timeout=5)
    assert usage.parts == (UsagePart("claude-opus-5-5", 2, 700, 9, 0, None),)


async def test_a_failed_attempt_before_a_transient_retry_is_paid_too(monkeypatch):
    """529 and retry: the first attempt spent something — it is added to the attempt that succeeded."""
    first = UsageMetadata.from_parts([UsagePart("claude-opus-5-5", 1, 50, 9_000, 0, None)])
    second = UsageMetadata.from_parts([THIS_TURN])
    calls = []

    async def _once(**kwargs):
        calls.append(1)
        if len(calls) == 1:
            err = ClaudeAgentError("claude exited with code 1: 529 overloaded")
            err.usage = first
            raise err
        return "ok", second, None

    async def _no_sleep(_s):
        return None

    monkeypatch.setattr(claude_agent, "_invoke_once", _once)
    monkeypatch.setattr(claude_agent.asyncio, "sleep", _no_sleep)
    _t, usage, _s = await claude_agent.invoke_claude(project_slug="p", claude_session_id=uuid4(), prompt="go")
    assert usage.parts == (*first.parts, THIS_TURN)
    assert usage.output_tokens == 750


async def test_the_engine_records_what_a_failed_turn_spent(db_session, monkeypatch):
    """``invoke_agent`` wrote ``record(None)`` for a crashed turn; now it records the spend from the exception."""
    from tests.test_orchestrator_v2_invoke_agent import _make_version

    spent = UsageMetadata.from_parts([UsagePart("claude-opus-5-5", 2, 700, 20_000, 300, None)])

    async def _boom(**kwargs):
        err = ClaudeAgentTimeout("claude invocation timed out: agent mlčal")
        err.usage = spent
        raise err

    monkeypatch.setattr(orchestrator, "invoke_claude", _boom)
    version, _ = _make_version(db_session)
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
    db_session.flush()
    result = await orchestrator.invoke_agent(
        db_session, version_id=version.id, role="ai_agent", stage="programovanie", prompt="x"
    )
    assert isinstance(result, ParseFailure)
    assert result.usage["parts"] == [p.to_payload() for p in spent.parts]
    assert result.usage["output_tokens"] == 700


async def test_a_lost_work_turn_leaves_its_spend_on_the_lost_work_notification(db_session, monkeypatch):
    """A turn with work in progress (timed out after writing changes) writes no message but the unfinished-work notice —
    so the spend goes into it, or it would vanish from Náklady (finding of the independent review ICCINT-168). The same
    turn again (retry after invalid output) replaces its write; another turn of the same build run (retry after a crash)
    is added alongside — the sum is always right."""
    from tests.test_orchestrator_v2_invoke_agent import _arm_dispatch_state, _lost_work_notifs, _make_version

    def _spent(output: int) -> UsageMetadata:
        return UsageMetadata.from_parts([UsagePart("claude-opus-5-5", 2, output, 20_000, 300, None)])

    outputs = iter([700, 900])

    async def _boom(**kwargs):
        err = ClaudeAgentTimeout("claude invocation timed out: agent mlčal")
        err.usage = _spent(next(outputs))
        raise err

    monkeypatch.setattr(orchestrator, "invoke_claude", _boom)
    monkeypatch.setattr(orchestrator, "_repo_head", lambda root: "h" * 40)
    monkeypatch.setattr(orchestrator, "_rev_list_count", lambda root, baseline: 2)
    version, _ = _make_version(db_session)
    _arm_dispatch_state(db_session, version)

    for _turn in range(2):  # two turns of the same build run (retry after a crash) — each with its own spend
        result = await orchestrator.invoke_agent(
            db_session, version_id=version.id, role="ai_agent", stage="programovanie", prompt="x"
        )
        assert result.lost_work is not None
    [notif] = _lost_work_notifs(db_session, version.id)
    assert [p["output_tokens"] for p in notif.payload["usage"]["parts"]] == [700, 900]
    assert notif.payload["usage"]["output_tokens"] == 1600
    assert notif.payload["timing"]["parse_attempts"] == 2


async def test_a_failure_whose_record_shows_no_spend_is_a_known_zero(monkeypatch, transcript):
    """The log was read and the turn spent nothing (crashed at once) — zero, not "unknown"."""
    _run(monkeypatch, _Claude(transcript.path, closes=False, returncode=1))

    class _Nothing(_Claude):
        async def communicate(self):
            return b"", b"boom"

    _run(monkeypatch, _Nothing(transcript.path, closes=False, returncode=1))
    with pytest.raises(ClaudeAgentError) as ei:
        await claude_agent._invoke_once(project_slug="p", claude_session_id=transcript.session, prompt="go", timeout=5)
    assert ei.value.usage is not None and ei.value.usage.parts == ()


async def test_the_same_turn_reentering_the_notification_replaces_its_entry(db_session, monkeypatch):
    """A retry after invalid output carries THE SAME running spend of the turn (it grows with each attempt) — its write
    in the notice is replaced, otherwise earlier attempts would be counted twice."""
    from tests.test_orchestrator_v2_invoke_agent import _arm_dispatch_state, _lost_work_notifs, _make_version

    monkeypatch.setattr(orchestrator, "_repo_head", lambda root: "h" * 40)
    monkeypatch.setattr(orchestrator, "_rev_list_count", lambda root, baseline: 1)
    version, project = _make_version(db_session)
    _arm_dispatch_state(db_session, version)
    turn = orchestrator._DispatchMetrics()
    for output in (100, 250):  # second attempt: the turn total is already 100 + 150
        turn.record(UsageMetadata.from_parts([UsagePart("claude-opus-5-5", 1, output - turn.output_tokens)]), 1.0)
        await orchestrator._audit_lost_work(
            db_session, version_id=version.id, slug=project.slug, stage="programovanie", timeout_seconds=1, metrics=turn
        )
    [notif] = _lost_work_notifs(db_session, version.id)
    assert notif.payload["usage"]["output_tokens"] == 250
    assert notif.payload["timing"]["parse_attempts"] == 2
