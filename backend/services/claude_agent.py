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
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from uuid import UUID

from backend.config.settings import settings

logger = logging.getLogger(__name__)

PROJECTS_ROOT = Path("/opt/projects")

#: Transient (retryable) API failure signatures matched against the claude stderr
#: (CR-NS-018 robustness). A 529/overload must not kill a run — retry with backoff.
_TRANSIENT_RE = re.compile(r"(529|overloaded|429|rate.?limit)", re.IGNORECASE)
#: Backoff (seconds) slept BEFORE each retry on a transient error → up to
#: len()+1 = 4 bounded attempts. Bounded so a persistent overload terminates the
#: dispatch (settled blocked upstream) instead of an un-backed-off hammer loop.
_TRANSIENT_BACKOFF: tuple[int, ...] = (2, 8, 20)

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


@dataclass(frozen=True)
class UsageMetadata:
    """Token usage for one ``claude -p`` invocation (WS-D, CR-NS-036). Extracted from the json /
    stream-json result envelope — never fabricated (``None`` when the envelope carries no usage)."""

    input_tokens: int
    output_tokens: int
    model: Optional[str] = None


def _usage_from(envelope: dict) -> Optional[UsageMetadata]:
    """Extract :class:`UsageMetadata` from a claude json / stream-json ``result`` envelope. The
    envelope carries top-level ``usage`` ({input_tokens, output_tokens, …}) + ``modelUsage`` (a map
    keyed by model name) — verified against the live ``--output-format json`` envelope. Returns
    ``None`` (never zeros/guesses) when there is no ``usage`` block."""
    usage = envelope.get("usage")
    if not isinstance(usage, dict):
        return None
    model = envelope.get("model")
    if not model:
        model_usage = envelope.get("modelUsage")
        if isinstance(model_usage, dict) and model_usage:
            model = next(iter(model_usage))  # the model name is the key
    return UsageMetadata(
        input_tokens=int(usage.get("input_tokens") or 0),
        output_tokens=int(usage.get("output_tokens") or 0),
        model=model if isinstance(model, str) else None,
    )


async def invoke_claude(
    *,
    project_slug: str,
    claude_session_id: UUID,
    prompt: str,
    charter_path: Optional[Path] = None,
    timeout: int = CLAUDE_INVOKE_TIMEOUT,
    on_event: Optional[EventCallback] = None,
) -> tuple[str, Optional["UsageMetadata"]]:
    """Invoke ``claude -p`` with bounded transient-error retry (CR-NS-018 robustness).

    Returns ``(text, usage)`` (WS-D, CR-NS-036): the result text + token usage from the json /
    stream-json envelope (``usage`` is ``None`` when the envelope carries none).

    Delegates to :func:`_invoke_once`; on a **transient** ``ClaudeAgentError``
    (529 / overloaded / 429 / rate limit in stderr) retries with bounded backoff
    (:data:`_TRANSIENT_BACKOFF` → up to 4 attempts) so a transient overload doesn't
    kill a run. **Non-transient** errors fail fast (no retry). Distinct from
    ``invoke_agent_with_parse_retry`` (which retries parse failures). See
    :func:`_invoke_once` for the args/return contract.
    """
    attempts = len(_TRANSIENT_BACKOFF) + 1
    for attempt in range(attempts):
        try:
            return await _invoke_once(
                project_slug=project_slug,
                claude_session_id=claude_session_id,
                prompt=prompt,
                charter_path=charter_path,
                timeout=timeout,
                on_event=on_event,
            )
        except ClaudeAgentError as exc:
            if attempt < len(_TRANSIENT_BACKOFF) and _TRANSIENT_RE.search(str(exc)):
                delay = _TRANSIENT_BACKOFF[attempt]
                logger.warning(
                    "claude transient error (attempt %d/%d) — backoff %ds: %s",
                    attempt + 1,
                    attempts,
                    delay,
                    exc,
                )
                await asyncio.sleep(delay)
                continue
            raise
    raise AssertionError("unreachable")  # the loop always returns or raises


async def _invoke_once(
    *,
    project_slug: str,
    claude_session_id: UUID,
    prompt: str,
    charter_path: Optional[Path] = None,
    timeout: int = CLAUDE_INVOKE_TIMEOUT,
    on_event: Optional[EventCallback] = None,
) -> tuple[str, Optional["UsageMetadata"]]:
    """One ``claude -p`` subprocess invocation (no retry — see :func:`invoke_claude`).

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
            for each NDJSON event as it arrives; the final text + usage are taken
            from the ``result`` event. When ``None`` (default) run non-streaming
            with ``--output-format json`` and parse the same fields from its single
            envelope (WS-D, CR-NS-036) — the ``result`` text is what the legacy text
            path returned, so downstream status-block parsing is unaffected.

    Returns:
        ``(text, usage)`` — the result text (stripped) + token usage from the json /
        stream-json envelope; usage is ``None`` when the envelope carried none.

    Raises:
        ClaudeAgentError: subprocess non-zero exit, timeout, decode/JSON failure, or a
            json envelope with no ``result`` field.
    """
    project_root = PROJECTS_ROOT / project_slug

    if on_event is not None:
        args = ["claude", "-p", "--output-format", "stream-json", "--verbose"]
    else:
        # WS-D (CR-NS-036): json (not text) so the envelope carries usage/cost; we return the same
        # `result` text the text path returned, so downstream parsing is unaffected.
        args = ["claude", "-p", "--output-format", "json"]
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

    # WS-D (CR-NS-036): --output-format json → parse the envelope for the result text + usage.
    raw = stdout.decode("utf-8", errors="replace").strip()
    try:
        envelope = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ClaudeAgentError(f"claude json output not parseable: {exc}") from exc
    if not isinstance(envelope, dict) or "result" not in envelope:
        raise ClaudeAgentError("claude json output has no 'result' field")
    return str(envelope["result"]).strip(), _usage_from(envelope)


async def _invoke_streaming(proc, *, timeout: int, on_event: EventCallback) -> tuple[str, Optional["UsageMetadata"]]:
    """Read ``--output-format stream-json`` NDJSON, emit events, return ``(text, usage)``.

    The complete response is the ``result`` event's ``result`` field — the status block is parsed
    from it downstream, exactly as in json mode — and that same event carries the token ``usage``
    (WS-D, CR-NS-036). A callback that raises is logged and swallowed (a broken UI feed must never
    kill an agent run).
    """

    async def _consume() -> tuple[Optional[str], Optional[UsageMetadata]]:
        result_text: Optional[str] = None
        result_usage: Optional[UsageMetadata] = None
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
                result_usage = _usage_from(evt)
        return result_text, result_usage

    try:
        result_text, result_usage = await asyncio.wait_for(_consume(), timeout=timeout)
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
    return result_text.strip(), result_usage
