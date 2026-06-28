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
from backend.services.claude_agent import ClaudeAgentError, ClaudeAgentTimeout, invoke_claude
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
    body = {"stage": "priprava", "kind": "gate_report", "summary": "14 endpoints", "awaiting": "manazer"}
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

    out, usage, _structured = await invoke_claude(
        project_slug="x", claude_session_id=uuid.uuid4(), prompt="go", on_event=on_event
    )

    assert len(events) == 3  # every NDJSON line surfaced
    assert out == block  # final text == result event
    assert usage is None  # this result event carries no usage block (WS-D: not fabricated)
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

    out, _usage, _structured = await invoke_claude(
        project_slug="x", claude_session_id=uuid.uuid4(), prompt="go", on_event=on_event
    )
    assert out == block  # a broken callback must not break the agent run


async def test_json_mode_returns_result_text_and_usage_no_callback(monkeypatch):
    """No callback → non-streaming ``--output-format json`` (WS-D, CR-NS-036): the text comes from
    the envelope's ``result`` field (so downstream parsing is unchanged) and the token usage is
    parsed from the envelope's ``usage`` block (``modelUsage`` key → model name)."""
    envelope = json.dumps(
        {"result": "hello world", "usage": {"input_tokens": 12, "output_tokens": 34}, "modelUsage": {"claude-x": {}}}
    ).encode()

    class _P:
        returncode = 0

        async def communicate(self):
            return (envelope + b"\n", b"")

    async def _fake_exec(*args, **kwargs):
        assert "stream-json" not in args  # no callback → not the streaming path
        assert "json" in args  # WS-D: json envelope (was the legacy 'text')
        assert "text" not in args
        return _P()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)

    out, usage, _structured = await invoke_claude(project_slug="x", claude_session_id=uuid.uuid4(), prompt="go")
    assert out == "hello world"
    assert usage is not None
    assert (usage.input_tokens, usage.output_tokens) == (12, 34)
    assert usage.model == "claude-x"  # derived from the modelUsage map key


async def test_json_mode_no_usage_returns_none(monkeypatch):
    """An envelope without a ``usage`` block → usage is ``None`` (WS-D: never fabricated zeros)."""

    class _P:
        returncode = 0

        async def communicate(self):
            return (json.dumps({"result": "hi"}).encode() + b"\n", b"")

    async def _fake_exec(*args, **kwargs):
        return _P()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)

    out, usage, _structured = await invoke_claude(project_slug="x", claude_session_id=uuid.uuid4(), prompt="go")
    assert out == "hi"
    assert usage is None


async def test_json_mode_unparseable_envelope_raises(monkeypatch):
    """Non-JSON stdout on the json path is a hard error (WS-D) — never a fabricated result."""

    class _P:
        returncode = 0

        async def communicate(self):
            return (b"not a json envelope at all", b"")

    async def _fake_exec(*args, **kwargs):
        return _P()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)

    with pytest.raises(ClaudeAgentError):
        await invoke_claude(project_slug="x", claude_session_id=uuid.uuid4(), prompt="go")


async def test_json_mode_timeout_raises_claude_agent_timeout(monkeypatch):
    """CR-V2-037: a json-path wall-clock timeout raises ``ClaudeAgentTimeout`` (a ``ClaudeAgentError``
    subclass) — so callers can tell a real timeout (don't re-invoke) from a fast crash (worth a re-invoke).
    A non-zero exit / decode failure stays a plain ``ClaudeAgentError`` (the crash case, tested above)."""

    class _P:
        returncode = None

        async def communicate(self):
            await asyncio.sleep(10)  # never completes within the timeout
            return (b"", b"")

        async def wait(self):
            return 0

    async def _fake_exec(*_a, **_k):
        return _P()

    async def _noop_kill(_proc):
        return None

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)
    monkeypatch.setattr(claude_agent, "_kill_process_tree", _noop_kill)  # avoid os.killpg on the fake proc

    with pytest.raises(ClaudeAgentTimeout):
        await invoke_claude(project_slug="x", claude_session_id=uuid.uuid4(), prompt="go", timeout=0)


async def test_streaming_parses_usage_from_result_event(monkeypatch):
    """The stream-json ``result`` event carries the same ``usage`` block as json mode (WS-D)."""
    lines = [
        json.dumps(
            {"type": "result", "result": "ok", "usage": {"input_tokens": 7, "output_tokens": 9}, "model": "claude-y"}
        ).encode()
        + b"\n"
    ]
    _patch_exec(monkeypatch, _FakeProc(lines))

    async def on_event(evt):
        pass

    out, usage, _structured = await invoke_claude(
        project_slug="x", claude_session_id=uuid.uuid4(), prompt="go", on_event=on_event
    )
    assert out == "ok"
    assert usage is not None
    assert (usage.input_tokens, usage.output_tokens, usage.model) == (7, 9, "claude-y")


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

    out, _usage, _structured = await invoke_claude(
        project_slug="x", claude_session_id=uuid.uuid4(), prompt="go", on_event=on_event
    )
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


# ── R3 (v0.7.0): native structured output via --json-schema ────────────────────


async def test_json_mode_extracts_structured_output(monkeypatch):
    """R3: with --json-schema, claude returns the grammar-constrained object in the envelope's
    ``structured_output`` field — invoke_claude surfaces it as the 3rd tuple element."""
    so = {"stage": "gate_b", "kind": "gate_report", "summary": "ok", "awaiting": "director"}
    envelope = json.dumps({"result": "fence-or-prose text", "structured_output": so}).encode()

    class _P:
        returncode = 0

        async def communicate(self):
            return (envelope + b"\n", b"")

    async def _fake_exec(*args, **kwargs):
        return _P()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)
    out, _usage, structured = await invoke_claude(
        project_slug="x", claude_session_id=uuid.uuid4(), prompt="go", json_schema={"type": "object"}
    )
    assert out == "fence-or-prose text"
    assert structured == so


async def test_json_mode_structured_output_none_when_absent(monkeypatch):
    """No --json-schema (or an older CLI) → structured_output is None; downstream the fence fallback
    parses the result text exactly as today (D2 defense-in-depth)."""

    class _P:
        returncode = 0

        async def communicate(self):
            return (json.dumps({"result": "hi"}).encode() + b"\n", b"")

    async def _fake_exec(*args, **kwargs):
        return _P()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)
    out, _usage, structured = await invoke_claude(project_slug="x", claude_session_id=uuid.uuid4(), prompt="go")
    assert out == "hi"
    assert structured is None


async def test_streaming_extracts_structured_output(monkeypatch):
    """R3: the stream-json ``result`` event carries ``structured_output`` when --json-schema was passed."""
    so = {"stage": "gate_a", "kind": "done", "summary": "ok", "awaiting": "none"}
    lines = [json.dumps({"type": "result", "result": "txt", "structured_output": so}).encode() + b"\n"]
    _patch_exec(monkeypatch, _FakeProc(lines))

    async def on_event(evt):
        pass

    out, _usage, structured = await invoke_claude(
        project_slug="x",
        claude_session_id=uuid.uuid4(),
        prompt="go",
        on_event=on_event,
        json_schema={"type": "object"},
    )
    assert out == "txt"
    assert structured == so


async def test_json_schema_arg_appended_before_prompt_when_given(monkeypatch):
    """R3: --json-schema <json> is injected BEFORE the positional prompt; the arg exists only when a
    schema is passed."""
    captured = {}

    class _P:
        returncode = 0

        async def communicate(self):
            return (json.dumps({"result": "ok"}).encode() + b"\n", b"")

    async def _fake_exec(*args, **kwargs):
        captured["args"] = args
        return _P()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)
    schema = {"type": "object", "properties": {"stage": {"type": "string"}}}
    await invoke_claude(project_slug="x", claude_session_id=uuid.uuid4(), prompt="THE_PROMPT", json_schema=schema)
    args = captured["args"]
    assert "--json-schema" in args
    i = args.index("--json-schema")
    assert args[i + 1] == json.dumps(schema)  # the schema json immediately follows the flag
    assert args[-1] == "THE_PROMPT"  # positional prompt is last → the schema flag precedes it


async def test_no_json_schema_arg_when_schema_none(monkeypatch):
    """Gate E passes no schema → no --json-schema flag (today's behavior preserved)."""
    captured = {}

    class _P:
        returncode = 0

        async def communicate(self):
            return (json.dumps({"result": "ok"}).encode() + b"\n", b"")

    async def _fake_exec(*args, **kwargs):
        captured["args"] = args
        return _P()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)
    await invoke_claude(project_slug="x", claude_session_id=uuid.uuid4(), prompt="go")
    assert "--json-schema" not in captured["args"]
