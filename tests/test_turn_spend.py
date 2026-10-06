"""Čo ťah stavby stál — na každej ceste von (ICCINT-168).

Do v4.42 sa spotreba brala z výsledku behu: pri ``--resume`` je v ňom cena a rozpis po modeloch ZA CELÉ
SEDENIE, a ťah, ktorý vypršal alebo spadol, nemal nič — ``record(None, …)``, akoby bol zadarmo. Tu beží
skutočné ``_invoke_once``/``invoke_claude``/``invoke_agent``; napodobnený je len proces Claude Code, ktorý
— ako skutočný — píše záznam sedenia: dokončenú správu, a súčet sedenia (``cost-state``) len keď beh uzavrie.
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
    """Záznam sedenia projektu ``p`` s jedným skôr dokončeným ťahom (súčet sedenia pred týmto ťahom)."""
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
    """Proces Claude Code: počas behu zapíše dokončenú správu, pri uzavretí súčet sedenia a výsledok."""

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
            # výsledok behu: ``usage`` za beh, ``modelUsage``/``total_cost_usd`` ZA SEDENIE — na cenu sa nepoužijú
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
    """Výsledok behu hlási súčet sedenia (0,5262 $); ťah stojí len rozdiel súčtov (0,0262 $)."""
    _run(monkeypatch, _Claude(transcript.path, closes=True))
    _text, usage, _s = await claude_agent._invoke_once(
        project_slug="p", claude_session_id=transcript.session, prompt="go", timeout=5
    )
    [part] = usage.parts
    assert part == UsagePart("claude-opus-5-5", 2, 700, 20_000, 300, pytest.approx(0.0262))
    assert (usage.input_tokens, usage.output_tokens, usage.model) == (2, 700, "claude-opus-5-5")


async def test_a_timed_out_turn_carries_its_spend_on_the_exception(monkeypatch, transcript):
    """Vypršaný ťah nie je zadarmo: jeho dokončené správy zo záznamu, bez ceny (Claude Code ho nenaúčtoval)."""
    _run(monkeypatch, _Claude(transcript.path, closes=False, hang=True))
    with pytest.raises(ClaudeAgentTimeout) as ei:
        await claude_agent._invoke_once(
            project_slug="p", claude_session_id=transcript.session, prompt="go", timeout=0.2
        )
    assert ei.value.usage.parts == (UsagePart("claude-opus-5-5", 2, 700, 20_000, 300, None),)


async def test_a_crashed_turn_that_claude_code_closed_keeps_its_price(monkeypatch, transcript):
    """Beh skončil chybou, ale Claude Code ho uzavrel (súčet pribudol) — cena je jeho, nie odhad."""
    _run(monkeypatch, _Claude(transcript.path, closes=True, returncode=1))
    with pytest.raises(ClaudeAgentError) as ei:
        await claude_agent._invoke_once(project_slug="p", claude_session_id=transcript.session, prompt="go", timeout=5)
    assert ei.value.usage.parts[0].cost_usd == pytest.approx(0.0262)


async def test_without_a_session_record_the_turns_own_usage_stands(monkeypatch, tmp_path):
    """Záznam sa nedá prečítať → ostane spotreba z výsledku behu (``usage`` je za beh), bez ceny — nič sa
    nevymýšľa a súčet sedenia sa za cenu ťahu nevydáva."""
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
    """529 a opakovanie: prvý pokus niečo minul — pripočíta sa k pokusu, ktorý uspel."""
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
    """``invoke_agent`` pri spadnutom ťahu zapisovalo ``record(None)``; teraz spotrebu z výnimky."""
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
    """Ťah s rozrobenou prácou (vypršal po zápise zmien) nezapíše inú správu než upozornenie o rozrobenej práci —
    spotreba preto ide doň, inak by z Nákladov zmizla (nález nezávislej kontroly ICCINT-168). Ten istý ťah znova
    (opakovanie po neplatnom výstupe) svoj zápis nahradí; ďalší ťah toho istého behu stavby (opakovanie po páde)
    pribudne vedľa — súčet je vždy správny."""
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

    for _turn in range(2):  # dva ťahy toho istého behu stavby (opakovanie po páde) — každý s vlastnou spotrebou
        result = await orchestrator.invoke_agent(
            db_session, version_id=version.id, role="ai_agent", stage="programovanie", prompt="x"
        )
        assert result.lost_work is not None
    [notif] = _lost_work_notifs(db_session, version.id)
    assert [p["output_tokens"] for p in notif.payload["usage"]["parts"]] == [700, 900]
    assert notif.payload["usage"]["output_tokens"] == 1600
    assert notif.payload["timing"]["parse_attempts"] == 2


async def test_a_failure_whose_record_shows_no_spend_is_a_known_zero(monkeypatch, transcript):
    """Záznam sa prečítal a ťah nič neminul (spadol hneď) — nula, nie „nevieme"."""
    _run(monkeypatch, _Claude(transcript.path, closes=False, returncode=1))

    class _Nothing(_Claude):
        async def communicate(self):
            return b"", b"boom"

    _run(monkeypatch, _Nothing(transcript.path, closes=False, returncode=1))
    with pytest.raises(ClaudeAgentError) as ei:
        await claude_agent._invoke_once(project_slug="p", claude_session_id=transcript.session, prompt="go", timeout=5)
    assert ei.value.usage is not None and ei.value.usage.parts == ()


async def test_the_same_turn_reentering_the_notification_replaces_its_entry(db_session, monkeypatch):
    """Opakovanie po neplatnom výstupe nesie TÚ ISTÚ priebežnú spotrebu ťahu (rastie s každým pokusom) — jej zápis
    v upozornení sa nahradí, inak by sa skoršie pokusy zarátali dvakrát."""
    from tests.test_orchestrator_v2_invoke_agent import _arm_dispatch_state, _lost_work_notifs, _make_version

    monkeypatch.setattr(orchestrator, "_repo_head", lambda root: "h" * 40)
    monkeypatch.setattr(orchestrator, "_rev_list_count", lambda root, baseline: 1)
    version, project = _make_version(db_session)
    _arm_dispatch_state(db_session, version)
    turn = orchestrator._DispatchMetrics()
    for output in (100, 250):  # druhý pokus: súčet ťahu je už 100 + 150
        turn.record(UsageMetadata.from_parts([UsagePart("claude-opus-5-5", 1, output - turn.output_tokens)]), 1.0)
        await orchestrator._audit_lost_work(
            db_session, version_id=version.id, slug=project.slug, stage="programovanie", timeout_seconds=1, metrics=turn
        )
    [notif] = _lost_work_notifs(db_session, version.id)
    assert notif.payload["usage"]["output_tokens"] == 250
    assert notif.payload["timing"]["parse_attempts"] == 2
