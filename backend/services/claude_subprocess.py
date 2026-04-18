"""Claude CLI subprocess executor for streaming AI responses.

Spawns ``claude -p`` as a subprocess with stdin/stdout piping, reads
NDJSON ``stream_event`` lines from stdout (requires ``--verbose
--include-partial-messages``), and yields text chunks as an async
generator.

Design (DESIGN.md D-11 — Claude MAX via CLI Subprocess):
- ICC uses Claude MAX subscription — no Anthropic API key needed.
- System prompt goes to a temp file (--system-prompt-file) to avoid
  OS ARG_MAX limits for large templates.
- User prompt goes via stdin (also avoids ARG_MAX).
- ``--include-partial-messages`` enables real-time ``content_block_delta``
  streaming events so the frontend sees text arriving incrementally.
- ``--no-session-persistence`` ensures each call is stateless.

Ported from the proven NEX Command ``ClaudeStreamingExecutor`` pattern
(``backend/core/claude_streaming.py``) — root cause of the original
non-streaming implementation was that ``_extract_content`` was reading
``data["content"]`` instead of ``stream_event → content_block_delta →
text_delta`` which is the actual Claude CLI NDJSON format.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
from collections.abc import AsyncGenerator

from backend.config.settings import settings

logger = logging.getLogger(__name__)


async def run_claude_stream(
    prompt: str,
    context: str | None = None,
    timeout: int | None = None,
) -> AsyncGenerator[str, None]:
    """Spawn ``claude`` CLI and yield text chunks as they arrive.

    Args:
        prompt: The user message sent via stdin.
        context: Optional system prompt — written to a temp file and
            passed via ``--system-prompt-file`` to avoid ARG_MAX limits.
        timeout: Maximum wall-clock seconds before killing the process.
            Defaults to ``Settings.claude_stream_timeout`` (300 s).

    Yields:
        Text strings from ``content_block_delta`` streaming events.

    Raises:
        RuntimeError: If the subprocess exits with a non-zero code.
        TimeoutError: If the subprocess exceeds *timeout* seconds.
    """
    effective_timeout = timeout if timeout is not None else settings.claude_stream_timeout

    # Write system prompt to a temp file — avoids OS ARG_MAX for large templates.
    tmp_path: str | None = None
    if context:
        tmp = tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".txt",
            prefix="nex_sysprompt_",
            delete=False,
            encoding="utf-8",
        )
        tmp.write(context)
        tmp.flush()
        tmp.close()
        tmp_path = tmp.name

    cmd = [settings.claude_cli_path, "-p"]
    if tmp_path:
        cmd.extend(["--system-prompt-file", tmp_path])
    cmd.extend([
        "--output-format", "stream-json",
        "--verbose",
        "--include-partial-messages",
        "--no-session-persistence",
    ])

    env = os.environ.copy()
    env["CLAUDE_CONFIG_DIR"] = settings.claude_config_dir

    logger.info(
        "Spawning Claude CLI (timeout=%ds, context=%s)",
        effective_timeout,
        "yes" if tmp_path else "no",
    )

    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
        limit=1024 * 1024,  # 1 MB per readline — NDJSON lines can be large
    )

    assert process.stdin is not None  # noqa: S101
    assert process.stdout is not None  # noqa: S101

    # Send user prompt via stdin and close it so Claude knows input is done.
    process.stdin.write(prompt.encode("utf-8"))
    await process.stdin.drain()
    process.stdin.close()

    stderr_chunks: list[bytes] = []

    try:
        async with asyncio.timeout(effective_timeout):
            while True:
                line_bytes = await process.stdout.readline()
                if not line_bytes:
                    break  # EOF — process finished

                line = line_bytes.decode("utf-8", errors="replace").strip()
                if not line:
                    continue

                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    logger.debug("Skipping non-JSON line: %.120s", line)
                    continue

                # Real-time streaming: content_block_delta carries individual text chunks.
                if (
                    data.get("type") == "stream_event"
                    and isinstance(data.get("event"), dict)
                    and data["event"].get("type") == "content_block_delta"
                    and isinstance(data["event"].get("delta"), dict)
                    and data["event"]["delta"].get("type") == "text_delta"
                ):
                    text = data["event"]["delta"].get("text", "")
                    if text:
                        yield text

            # Collect any stderr emitted after stdout EOF.
            assert process.stderr is not None  # noqa: S101
            stderr_chunks.append(await process.stderr.read())

    except TimeoutError:
        logger.error("Claude CLI timed out after %ds — killing process", effective_timeout)
        try:
            process.kill()
        except ProcessLookupError:
            pass
        await process.wait()
        raise TimeoutError(f"Claude CLI subprocess exceeded {effective_timeout}s timeout")
    finally:
        if process.returncode is None:
            try:
                process.terminate()
                await asyncio.wait_for(process.wait(), timeout=5)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
        if tmp_path is not None:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    await process.wait()

    stderr_text = b"".join(stderr_chunks).decode("utf-8", errors="replace").strip()
    if stderr_text:
        logger.warning("Claude CLI stderr: %.500s", stderr_text)

    if process.returncode != 0:
        raise RuntimeError(
            f"Claude CLI exited with code {process.returncode}: {stderr_text[:500]}"
        )
