"""Shared headless ``claude`` CLI invocation primitive (CR-NS-018 Phase 2).

Extracted verbatim from ``dialogue._invoke_agent`` so both Gate E (dialogue)
and the F-007 orchestrator drive agents the same way — ``claude -p --resume``
against a per-agent disk-persisted session UUID. Behaviour is identical to the
original; ``dialogue.py`` now delegates here.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Optional
from uuid import UUID

from backend.config.settings import settings

logger = logging.getLogger(__name__)

PROJECTS_ROOT = Path("/opt/projects")

#: Default timeout per ``claude --print`` invocation (seconds). Agent dispatch
#: is asynchronous (CR-NS-018 fix-round), so this only backstops a *hung* agent
#: — hence generous and env-tunable via ``CLAUDE_INVOKE_TIMEOUT``. The
#: orchestrator passes a per-stage ``timeout`` that overrides this default.
CLAUDE_INVOKE_TIMEOUT = settings.claude_invoke_timeout

#: StreamReader line-buffer limit for stream-json mode (bytes). One NDJSON event
#: can be a whole spec file on a single line, so the 64 KB default is far too
#: small (CR-NS-018). 64 MB is generous and bounded.
_STREAM_LINE_LIMIT = 64 * 1024 * 1024


class ClaudeAgentError(RuntimeError):
    """claude CLI invocation failed (non-zero exit, timeout, decode failure)."""


#: Per-event callback type for streaming mode. Receives each parsed stream-json
#: event (a dict); must never raise (the caller guards it anyway).
EventCallback = Callable[[dict], Awaitable[None]]


async def invoke_claude(
    *,
    project_slug: str,
    claude_session_id: UUID,
    prompt: str,
    charter_path: Optional[Path] = None,
    timeout: int = CLAUDE_INVOKE_TIMEOUT,
    on_event: Optional[EventCallback] = None,
) -> str:
    """Invoke ``claude -p`` with the agent's session UUID + prompt.

    Args:
        project_slug: cwd will be ``/opt/projects/<slug>/`` so claude picks up
            project-level settings (CLAUDE.md, .claude/settings).
        claude_session_id: claude CLI session UUID (disk-persisted by claude).
        prompt: user message to send.
        charter_path: only on the **first** call for this session —
            ``--session-id <uuid>`` + ``--append-system-prompt <charter>``
            create the session and load the agent's charter. For subsequent
            calls pass ``None`` and we ``--resume <uuid>``.
        timeout: per-invocation subprocess timeout (seconds).
        on_event: opt-in streaming (CR-NS-018). When given, run with
            ``--output-format stream-json --verbose`` and ``await on_event(evt)``
            for each NDJSON event as it arrives; the final text is taken from the
            ``result`` event. When ``None`` (default) the behavior is byte-for-byte
            the legacy ``--output-format text`` path (Gate E relies on this).

    Returns:
        Plain text response from claude (stripped of trailing newline).

    Raises:
        ClaudeAgentError: subprocess non-zero exit, timeout, or decode failure.
    """
    project_root = PROJECTS_ROOT / project_slug

    if on_event is not None:
        args = ["claude", "-p", "--output-format", "stream-json", "--verbose"]
    else:
        args = ["claude", "-p", "--output-format", "text"]
    if charter_path is not None:
        # First invocation for this claude session — create it.
        charter_text = charter_path.read_text(encoding="utf-8")
        args += [
            "--session-id",
            str(claude_session_id),
            "--append-system-prompt",
            charter_text,
        ]
    else:
        # Subsequent invocation — resume existing session.
        args += ["--resume", str(claude_session_id)]
    args.append(prompt)

    logger.info(
        "Invoking claude agent: project=%s session=%s charter=%s prompt_len=%d",
        project_slug,
        claude_session_id,
        "yes" if charter_path else "no",
        len(prompt),
    )

    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(project_root),
        # Generous StreamReader buffer: a single stream-json NDJSON event (e.g. a
        # gate's full openapi.yaml in one `result` line) can far exceed the 64 KB
        # default and would raise LimitOverrunError on readline (CR-NS-018).
        limit=_STREAM_LINE_LIMIT,
    )

    if on_event is not None:
        return await _invoke_streaming(proc, timeout=timeout, on_event=on_event)

    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(),
            timeout=timeout,
        )
    except asyncio.TimeoutError as exc:
        proc.kill()
        await proc.wait()
        raise ClaudeAgentError(
            f"claude invocation timed out after {timeout}s",
        ) from exc

    if proc.returncode != 0:
        stderr_text = stderr.decode("utf-8", errors="replace").strip()
        raise ClaudeAgentError(
            f"claude exited with code {proc.returncode}: {stderr_text[:500]}",
        )

    return stdout.decode("utf-8", errors="replace").strip()


async def _invoke_streaming(proc, *, timeout: int, on_event: EventCallback) -> str:
    """Read ``--output-format stream-json`` NDJSON, emit events, return final text.

    The complete response is the ``result`` event's ``result`` field — the status
    block is parsed from it downstream, exactly as in text mode. A callback that
    raises is logged and swallowed (a broken UI feed must never kill an agent run).
    """

    async def _consume() -> Optional[str]:
        result_text: Optional[str] = None
        assert proc.stdout is not None
        async for raw in proc.stdout:
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                evt = json.loads(line)
            except json.JSONDecodeError:
                continue  # tolerate non-JSON noise
            try:
                await on_event(evt)
            except Exception:  # noqa: BLE001 — a feed callback must never break the run
                logger.exception("on_event callback failed; continuing")
            if isinstance(evt, dict) and evt.get("type") == "result":
                result_text = evt.get("result")
        return result_text

    try:
        result_text = await asyncio.wait_for(_consume(), timeout=timeout)
    except asyncio.TimeoutError as exc:
        proc.kill()
        await proc.wait()
        raise ClaudeAgentError(f"claude invocation timed out after {timeout}s") from exc

    await proc.wait()
    if proc.returncode != 0:
        stderr_text = ""
        if proc.stderr is not None:
            stderr_text = (await proc.stderr.read()).decode("utf-8", errors="replace").strip()
        raise ClaudeAgentError(f"claude exited with code {proc.returncode}: {stderr_text[:500]}")
    if result_text is None:
        raise ClaudeAgentError("claude stream ended without a result event")
    return result_text.strip()
