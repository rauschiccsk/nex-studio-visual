"""A turn ends when the work is DONE or STUCK — not when a clock runs out (ICCINT-47).

31.08.2026. The acceptance suite for nex-productcatalogs needed 9 feature assertions and 21 safety
assertions — one coherent deliverable. It does not fit in 40 minutes, so Dedo proposed splitting it into four
batches, each sized to the tool rather than to the work. The Director refused:

    "Ak sa testy nezmestia, potom to netreba rozbíjať, ale zvýšiť časový limit. Toto pre mňa nie je dlhodobé
    a nie je akceptovateľné riešenie."

He was right, and the cost of the workaround fell on him: four clicks and four waits for one job. Behind it
sat ``STAGE_TIMEOUT`` — a hard-coded table capping the WHOLE turn regardless of what the turn was for, so a
big-but-honest task could not finish and the same 40 minutes were spent by a one-line fix and by thirty
spec-derived assertions.

A stuck agent stops emitting; a working one does not. So the budget is spent on SILENCE, and the wall-clock
ceiling stays only as a runaway guard.
"""

from __future__ import annotations

import asyncio

import pytest

from backend.services import claude_agent


class _Stdout:
    """A stdout that yields lines on a schedule, so a test can BE slow without SLEEPING for real."""

    def __init__(self, gaps: list[float], payload: str) -> None:
        self._gaps = list(gaps)
        self._payload = payload

    def __aiter__(self):
        return self

    async def __anext__(self) -> bytes:
        if not self._gaps:
            raise StopAsyncIteration
        await asyncio.sleep(self._gaps.pop(0))
        line = self._payload if not self._gaps else '{"type":"progress"}'
        return (line + "\n").encode()


class _Proc:
    def __init__(self, stdout) -> None:
        self.stdout = stdout
        self.returncode = 0
        self.pid = 4242

    async def wait(self):
        return 0


RESULT = '{"type":"result","result":"hotovo"}'


async def _noop_event(_evt) -> None:
    return None


@pytest.fixture(autouse=True)
def _fast_watchdog(monkeypatch):
    """Poll often so a test second is a test second, not a wall-clock five."""
    monkeypatch.setattr(claude_agent, "_SILENCE_POLL_SECONDS", 0.02)
    monkeypatch.setattr(claude_agent, "_kill_process_tree", lambda *_a, **_k: asyncio.sleep(0))
    monkeypatch.setattr(claude_agent, "_reap_sandbox", lambda *_a, **_k: asyncio.sleep(0))


@pytest.mark.asyncio
async def test_a_long_but_working_turn_is_not_cut_off() -> None:
    """THE defect. The turn runs well past its per-stage budget, but never goes quiet — it must finish.

    Before ICCINT-47 this raised a timeout for the sin of taking a while."""
    stdout = _Stdout([0.05] * 8, RESULT)  # 8 steps of work, budget below is 0.15s
    text, _usage, _structured = await claude_agent._invoke_streaming(_Proc(stdout), timeout=0.15, on_event=_noop_event)
    assert text == "hotovo", "a working turn was cut off for being long"


@pytest.mark.asyncio
async def test_a_silent_turn_still_ends() -> None:
    """The guard that must survive: a hung agent is exactly what the clock is FOR."""
    stdout = _Stdout([0.05, 5.0], RESULT)  # one line, then silence far past the budget
    with pytest.raises(claude_agent.ClaudeAgentTimeout) as exc:
        await claude_agent._invoke_streaming(_Proc(stdout), timeout=0.15, on_event=_noop_event)
    assert "mlčal" in str(exc.value), "the reason did not say the agent had gone quiet"


@pytest.mark.asyncio
async def test_the_runaway_ceiling_still_exists(monkeypatch) -> None:
    """Chatty-forever must not mean forever. The ceiling is a runaway guard, not a size limit."""
    monkeypatch.setattr(claude_agent, "TURN_CEILING_MULTIPLIER", 2)
    stdout = _Stdout([0.02] * 500, RESULT)  # never silent, never finishing
    with pytest.raises(claude_agent.ClaudeAgentTimeout) as exc:
        await claude_agent._invoke_streaming(_Proc(stdout), timeout=0.1, on_event=_noop_event)
    assert "strop" in str(exc.value)


@pytest.mark.asyncio
async def test_the_ceiling_is_generous_enough_to_never_size_the_work() -> None:
    """A turn that keeps working gets many times its silence budget before the guard bites — otherwise the
    ceiling would quietly become the new 40 minutes."""
    assert claude_agent.TURN_CEILING_MULTIPLIER >= 4


@pytest.mark.asyncio
async def test_a_turn_that_finishes_at_once_is_unaffected() -> None:
    """The control: the ordinary fast turn behaves exactly as before."""
    text, _u, _s = await claude_agent._invoke_streaming(_Proc(_Stdout([0.0], RESULT)), timeout=30, on_event=_noop_event)
    assert text == "hotovo"
