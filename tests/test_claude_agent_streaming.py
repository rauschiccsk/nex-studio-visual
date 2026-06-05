"""Tests for opt-in stream-json mode in ``invoke_claude`` (CR-NS-018).

The subprocess is faked: we feed synthetic NDJSON and assert that each event is
emitted to the callback, the returned text comes from the ``result`` event (so
the status block parses exactly as in text mode), and the legacy text path is
unchanged when no callback is given.
"""

import asyncio
import json
import uuid

import pytest

from backend.services import claude_agent
from backend.services.claude_agent import ClaudeAgentError, invoke_claude
from backend.services.pipeline_status import PipelineStatusBlock, parse_status_block


class _FakeStdout:
    def __init__(self, lines):
        self._lines = list(lines)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._lines:
            raise StopAsyncIteration
        return self._lines.pop(0)


class _FakeStderr:
    def __init__(self, data=b""):
        self._data = data

    async def read(self):
        return self._data


class _FakeProc:
    def __init__(self, lines, returncode=0, stderr=b""):
        self.stdout = _FakeStdout(lines)
        self.stderr = _FakeStderr(stderr)
        self.returncode = returncode

    async def wait(self):
        return self.returncode

    def kill(self):
        pass


def _patch_exec(monkeypatch, proc):
    async def _fake_exec(*args, **kwargs):
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)


def _status_block() -> str:
    body = {"stage": "gate_a", "kind": "gate_report", "summary": "14 endpoints", "awaiting": "director"}
    return f"<<<PIPELINE_STATUS>>>\n{json.dumps(body)}\n<<<END_PIPELINE_STATUS>>>"


async def test_streaming_emits_events_and_returns_result_text(monkeypatch):
    block = _status_block()
    lines = [
        b'{"type":"system","subtype":"init"}\n',
        b'{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Read","input":{"file_path":"a/b.md"}}]}}\n',
        json.dumps({"type": "result", "subtype": "success", "result": block}).encode() + b"\n",
    ]
    _patch_exec(monkeypatch, _FakeProc(lines))

    events = []

    async def on_event(evt):
        events.append(evt)

    out = await invoke_claude(project_slug="x", claude_session_id=uuid.uuid4(), prompt="go", on_event=on_event)

    assert len(events) == 3  # every NDJSON line surfaced
    assert out == block  # final text == result event
    assert isinstance(parse_status_block(out), PipelineStatusBlock)  # parses as today


async def test_streaming_uses_stream_json_args(monkeypatch):
    captured = {}

    async def _fake_exec(*args, **kwargs):
        captured["args"] = args
        return _FakeProc([json.dumps({"type": "result", "result": "ok"}).encode() + b"\n"])

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)

    async def on_event(evt):
        pass

    await invoke_claude(project_slug="x", claude_session_id=uuid.uuid4(), prompt="go", on_event=on_event)
    assert "stream-json" in captured["args"]
    assert "--verbose" in captured["args"]


async def test_streaming_missing_result_raises(monkeypatch):
    _patch_exec(monkeypatch, _FakeProc([b'{"type":"system","subtype":"init"}\n']))

    async def on_event(evt):
        pass

    with pytest.raises(ClaudeAgentError):
        await invoke_claude(project_slug="x", claude_session_id=uuid.uuid4(), prompt="go", on_event=on_event)


async def test_callback_exception_does_not_kill_run(monkeypatch):
    block = _status_block()
    lines = [json.dumps({"type": "result", "result": block}).encode() + b"\n"]
    _patch_exec(monkeypatch, _FakeProc(lines))

    async def on_event(evt):
        raise RuntimeError("broken feed")

    out = await invoke_claude(project_slug="x", claude_session_id=uuid.uuid4(), prompt="go", on_event=on_event)
    assert out == block  # a broken callback must not break the agent run


async def test_text_mode_unchanged_when_no_callback(monkeypatch):
    class _P:
        returncode = 0

        async def communicate(self):
            return (b"hello world\n", b"")

    async def _fake_exec(*args, **kwargs):
        assert "stream-json" not in args  # text mode keeps the legacy args
        assert "text" in args
        return _P()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)

    out = await invoke_claude(project_slug="x", claude_session_id=uuid.uuid4(), prompt="go")
    assert out == "hello world"


# Ensure the module reference is used (guard against accidental import removal).
def test_event_callback_type_exported():
    assert hasattr(claude_agent, "EventCallback")


# ── >64 KB line regression (CR-NS-018 LimitOverrunError) ──────────────────────


async def test_streaming_handles_large_result_line(monkeypatch):
    """A single ``result`` NDJSON line far larger than the 64 KB default must
    parse intact (e.g. a gate's full openapi.yaml in one JSON line)."""
    big_summary = "x" * (200 * 1024)  # 200 KB — well over the 64 KB default
    block = (
        "<<<PIPELINE_STATUS>>>\n"
        + json.dumps({"stage": "gate_b", "kind": "gate_report", "summary": big_summary, "awaiting": "director"})
        + "\n<<<END_PIPELINE_STATUS>>>"
    )
    lines = [json.dumps({"type": "result", "result": block}).encode() + b"\n"]
    _patch_exec(monkeypatch, _FakeProc(lines))

    async def on_event(evt):
        pass

    out = await invoke_claude(project_slug="x", claude_session_id=uuid.uuid4(), prompt="go", on_event=on_event)
    assert out == block
    assert len(out) > 64 * 1024


async def test_subprocess_exec_uses_large_stream_limit(monkeypatch):
    captured = {}

    async def _fake_exec(*args, **kwargs):
        captured.update(kwargs)
        return _FakeProc([json.dumps({"type": "result", "result": "ok"}).encode() + b"\n"])

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)

    async def on_event(evt):
        pass

    await invoke_claude(project_slug="x", claude_session_id=uuid.uuid4(), prompt="go", on_event=on_event)
    assert captured["limit"] == claude_agent._STREAM_LINE_LIMIT
    assert captured["limit"] > 64 * 1024


async def test_default_streamreader_limit_overflows_but_ours_survives():
    """Direct mechanism lock: the 64 KB default raises on a 200 KB line; the
    configured limit reads it fine."""
    big = b"x" * (200 * 1024)

    default_reader = asyncio.StreamReader(limit=64 * 1024)
    default_reader.feed_data(big + b"\n")
    default_reader.feed_eof()
    # readline() surfaces the overrun as ValueError (LimitOverrunError internally).
    with pytest.raises((asyncio.LimitOverrunError, ValueError)):
        await default_reader.readline()

    ours = asyncio.StreamReader(limit=claude_agent._STREAM_LINE_LIMIT)
    ours.feed_data(big + b"\n")
    ours.feed_eof()
    line = await ours.readline()
    assert len(line) == len(big) + 1
