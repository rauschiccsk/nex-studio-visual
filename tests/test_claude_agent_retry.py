"""Bounded transient-error retry in invoke_claude (CR-NS-018 robustness).

A 529 / overload must not kill a run: invoke_claude retries _invoke_once with
bounded backoff on a transient signature, fails fast on a non-transient error.
The subprocess (_invoke_once) is faked; asyncio.sleep is neutralised.
"""

from uuid import uuid4

import pytest

from backend.services import claude_agent
from backend.services.claude_agent import ClaudeAgentError


@pytest.fixture
def no_sleep(monkeypatch):
    async def _sleep(_seconds):
        return None

    monkeypatch.setattr(claude_agent.asyncio, "sleep", _sleep)


def _fake_once(monkeypatch, outcomes):
    """Patch _invoke_once to yield `outcomes` in order (last repeats); count calls.

    Each outcome is ("raise", message) or ("ok", text). _invoke_once returns ``(text, usage)`` since
    WS-D (CR-NS-036), so the "ok" stand-in mirrors that — usage None (retry logic doesn't touch it)."""
    calls = {"n": 0}

    async def _once(**kwargs):
        idx = min(calls["n"], len(outcomes) - 1)
        calls["n"] += 1
        kind, value = outcomes[idx]
        if kind == "raise":
            raise ClaudeAgentError(value)
        return (value, None)

    monkeypatch.setattr(claude_agent, "_invoke_once", _once)
    return calls


async def _invoke():
    return await claude_agent.invoke_claude(project_slug="p", claude_session_id=uuid4(), prompt="x")


async def test_retries_transient_then_succeeds(monkeypatch, no_sleep):
    calls = _fake_once(monkeypatch, [("raise", "API Error 529 Overloaded"), ("ok", "done")])
    assert (await _invoke())[0] == "done"  # invoke_claude returns (text, usage)
    assert calls["n"] == 2  # one retry after the transient


async def test_persistent_transient_raises_after_bounded_attempts(monkeypatch, no_sleep):
    calls = _fake_once(monkeypatch, [("raise", "529 overloaded")])  # always transient
    with pytest.raises(ClaudeAgentError):
        await _invoke()
    assert calls["n"] == len(claude_agent._TRANSIENT_BACKOFF) + 1  # bounded (4)


async def test_non_transient_fails_fast(monkeypatch, no_sleep):
    calls = _fake_once(monkeypatch, [("raise", "claude exited with code 1: spec error")])
    with pytest.raises(ClaudeAgentError):
        await _invoke()
    assert calls["n"] == 1  # no retry on a non-transient error


async def test_rate_limit_is_transient(monkeypatch, no_sleep):
    calls = _fake_once(monkeypatch, [("raise", "429 Too Many Requests: rate limit"), ("ok", "ok")])
    assert (await _invoke())[0] == "ok"  # invoke_claude returns (text, usage)
    assert calls["n"] == 2
