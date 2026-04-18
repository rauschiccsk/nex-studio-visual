"""Tests for :mod:`backend.services.claude_subprocess`.

Mocks ``asyncio.create_subprocess_exec`` so no real ``claude`` CLI binary
is required.  The new implementation:
- Sends user prompt via stdin (not argv)
- Writes optional system prompt to a temp file (``--system-prompt-file``)
- Extracts text from ``stream_event → content_block_delta → text_delta``

Private helpers (_build_claude_command, _build_env, _extract_content) were
removed in the rewrite — only the public ``run_claude_stream`` is tested.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.services.claude_subprocess import run_claude_stream

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _stream_event(text: str) -> bytes:
    """Build a content_block_delta stream_event NDJSON line."""
    obj = {
        "type": "stream_event",
        "event": {
            "type": "content_block_delta",
            "delta": {"type": "text_delta", "text": text},
        },
    }
    return json.dumps(obj).encode("utf-8") + b"\n"


def _other_event(event_type: str) -> bytes:
    """Build a non-text stream_event line (should be ignored)."""
    return json.dumps({"type": "stream_event", "event": {"type": event_type}}).encode() + b"\n"


def _make_mock_process(
    stdout_lines: list[bytes],
    stderr: bytes = b"",
    returncode: int = 0,
) -> MagicMock:
    """Create a mock subprocess compatible with the new stdin-based implementation."""
    process = MagicMock()
    process.returncode = returncode

    # stdin — write/drain/close are called by run_claude_stream
    stdin_mock = MagicMock()
    stdin_mock.write = MagicMock()
    stdin_mock.drain = AsyncMock()
    stdin_mock.close = MagicMock()
    process.stdin = stdin_mock

    # stdout — readline yields pre-configured lines then EOF (b"")
    line_iter = iter(stdout_lines + [b""])

    async def _readline() -> bytes:
        return next(line_iter)

    stdout_mock = MagicMock()
    stdout_mock.readline = _readline
    process.stdout = stdout_mock

    # stderr
    async def _read_stderr() -> bytes:
        return stderr

    stderr_mock = MagicMock()
    stderr_mock.read = _read_stderr
    process.stderr = stderr_mock

    async def _wait() -> int:
        return returncode

    process.wait = _wait
    process.kill = MagicMock()
    process.terminate = MagicMock()

    return process


async def _collect(
    prompt: str,
    context: str | None = None,
    timeout: int | None = None,
) -> list[str]:
    """Consume the async generator into a plain list."""
    chunks: list[str] = []
    async for chunk in run_claude_stream(prompt, context=context, timeout=timeout):
        chunks.append(chunk)
    return chunks


# ---------------------------------------------------------------------------
# run_claude_stream — content extraction
# ---------------------------------------------------------------------------


class TestContentExtraction:
    def test_yields_text_chunks(self) -> None:
        lines = [
            _other_event("message_start"),
            _stream_event("Hello"),
            _stream_event(" world"),
            _other_event("message_stop"),
        ]
        process = _make_mock_process(lines)

        with patch(
            "backend.services.claude_subprocess.asyncio.create_subprocess_exec",
            AsyncMock(return_value=process),
        ):
            chunks = asyncio.run(_collect("test"))

        assert chunks == ["Hello", " world"]

    def test_non_text_events_ignored(self) -> None:
        lines = [
            _other_event("message_start"),
            _stream_event("content"),
            # result-type event should be ignored (not stream_event)
            json.dumps({"type": "result", "result": "full text"}).encode() + b"\n",
        ]
        process = _make_mock_process(lines)

        with patch(
            "backend.services.claude_subprocess.asyncio.create_subprocess_exec",
            AsyncMock(return_value=process),
        ):
            chunks = asyncio.run(_collect("test"))

        assert chunks == ["content"]

    def test_invalid_json_lines_skipped(self) -> None:
        lines = [b"not-json\n", _stream_event("valid"), b"{{broken}}\n"]
        process = _make_mock_process(lines)

        with patch(
            "backend.services.claude_subprocess.asyncio.create_subprocess_exec",
            AsyncMock(return_value=process),
        ):
            chunks = asyncio.run(_collect("test"))

        assert chunks == ["valid"]

    def test_empty_stream_yields_nothing(self) -> None:
        process = _make_mock_process([_other_event("message_start")])

        with patch(
            "backend.services.claude_subprocess.asyncio.create_subprocess_exec",
            AsyncMock(return_value=process),
        ):
            chunks = asyncio.run(_collect("test"))

        assert chunks == []

    def test_blank_text_delta_skipped(self) -> None:
        lines = [_stream_event(""), _stream_event("real")]
        process = _make_mock_process(lines)

        with patch(
            "backend.services.claude_subprocess.asyncio.create_subprocess_exec",
            AsyncMock(return_value=process),
        ):
            chunks = asyncio.run(_collect("test"))

        assert chunks == ["real"]


# ---------------------------------------------------------------------------
# run_claude_stream — stdin / argv
# ---------------------------------------------------------------------------


class TestStdinAndCommand:
    def test_prompt_sent_via_stdin(self) -> None:
        process = _make_mock_process([_stream_event("ok")])

        with patch(
            "backend.services.claude_subprocess.asyncio.create_subprocess_exec",
            AsyncMock(return_value=process),
        ):
            asyncio.run(_collect("my prompt"))

        process.stdin.write.assert_called_once_with(b"my prompt")

    def test_no_context_no_system_prompt_file(self) -> None:
        process = _make_mock_process([_stream_event("ok")])

        with patch(
            "backend.services.claude_subprocess.asyncio.create_subprocess_exec",
            AsyncMock(return_value=process),
        ) as mock_exec:
            asyncio.run(_collect("direct prompt"))

        assert "--system-prompt-file" not in mock_exec.call_args.args

    def test_context_adds_system_prompt_file_flag(self) -> None:
        process = _make_mock_process([_stream_event("ok")])

        with patch(
            "backend.services.claude_subprocess.asyncio.create_subprocess_exec",
            AsyncMock(return_value=process),
        ) as mock_exec:
            asyncio.run(_collect("question", context="system instructions"))

        assert "--system-prompt-file" in mock_exec.call_args.args

    def test_include_partial_messages_flag_present(self) -> None:
        process = _make_mock_process([_stream_event("ok")])

        with patch(
            "backend.services.claude_subprocess.asyncio.create_subprocess_exec",
            AsyncMock(return_value=process),
        ) as mock_exec:
            asyncio.run(_collect("test"))

        assert "--include-partial-messages" in mock_exec.call_args.args
        assert "--verbose" in mock_exec.call_args.args

    def test_uses_settings_cli_path(self) -> None:
        process = _make_mock_process([_stream_event("ok")])

        with (
            patch(
                "backend.services.claude_subprocess.asyncio.create_subprocess_exec",
                AsyncMock(return_value=process),
            ) as mock_exec,
            patch("backend.services.claude_subprocess.settings") as mock_s,
        ):
            mock_s.claude_cli_path = "/custom/claude"
            mock_s.claude_config_dir = "/test/.claude"
            mock_s.claude_stream_timeout = 300
            asyncio.run(_collect("test"))

        assert mock_exec.call_args.args[0] == "/custom/claude"


# ---------------------------------------------------------------------------
# run_claude_stream — error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    def test_nonzero_exit_raises_runtime_error(self) -> None:
        process = _make_mock_process([], stderr=b"fatal error", returncode=1)

        with patch(
            "backend.services.claude_subprocess.asyncio.create_subprocess_exec",
            AsyncMock(return_value=process),
        ):
            with pytest.raises(RuntimeError, match="exited with code 1"):
                asyncio.run(_collect("test"))

    def test_stderr_on_success_does_not_raise(self) -> None:
        process = _make_mock_process(
            [_stream_event("ok")],
            stderr=b"some warning",
            returncode=0,
        )

        with patch(
            "backend.services.claude_subprocess.asyncio.create_subprocess_exec",
            AsyncMock(return_value=process),
        ):
            chunks = asyncio.run(_collect("test"))

        assert chunks == ["ok"]

    def test_timeout_kills_process(self) -> None:
        process = MagicMock()
        process.returncode = -9

        stdin_mock = MagicMock()
        stdin_mock.write = MagicMock()
        stdin_mock.drain = AsyncMock()
        stdin_mock.close = MagicMock()
        process.stdin = stdin_mock

        call_count = 0

        async def _slow_readline() -> bytes:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _stream_event("partial")
            await asyncio.sleep(999)
            return b""

        stdout_mock = MagicMock()
        stdout_mock.readline = _slow_readline
        process.stdout = stdout_mock

        async def _read_stderr() -> bytes:
            return b""

        stderr_mock = MagicMock()
        stderr_mock.read = _read_stderr
        process.stderr = stderr_mock

        async def _wait() -> int:
            return -9

        process.wait = _wait
        process.kill = MagicMock()
        process.terminate = MagicMock()

        with patch(
            "backend.services.claude_subprocess.asyncio.create_subprocess_exec",
            AsyncMock(return_value=process),
        ):
            with pytest.raises(TimeoutError, match="exceeded"):
                asyncio.run(_collect("test", timeout=1))

        process.kill.assert_called_once()
