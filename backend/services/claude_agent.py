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
import os
import re
import signal
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


class ClaudeAgentTimeout(ClaudeAgentError):
    """The ``claude`` invocation exceeded its wall-clock timeout (CR-V2-037).

    A SUBCLASS of :class:`ClaudeAgentError`, so every existing ``except ClaudeAgentError`` still catches a
    timeout unchanged. It exists only to let callers distinguish a genuine TIMEOUT (the turn burned its
    whole budget — re-invoking just risks another long wait) from a FAST crash (non-zero exit / decode /
    stream-end — produced nothing but cost almost no wall-clock and is usually transient, so worth a
    bounded re-invoke). The task-plan per-feat passes use this to retry a crash but not a timeout."""


def _load_charter(charter_path: Path) -> str:
    """Read a role's ``Pravidlá agenta`` charter for ``--append-system-prompt``.

    The charter is a HARD requirement on the first session invocation. If it is missing we raise a
    descriptive :class:`ClaudeAgentError` (NOT a raw ``FileNotFoundError``): a missing charter means the
    project was never provisioned with this role's v2 charter (see
    ``create_project_postscaffold._provision_v2_agent_charters``), and the actionable hint is to re-create
    the project through NEX Studio v2 — not a CLI/runtime fault. ``pipeline_runner`` surfaces this message
    verbatim ("Agent dispatch failed: … — pipeline blocked")."""
    if not charter_path.is_file():
        raise ClaudeAgentError(
            f"Charter (Pravidlá agenta) missing at {charter_path} — this project was not provisioned "
            f"with this role's v2 charter. Re-create the project through NEX Studio v2."
        )
    return charter_path.read_text(encoding="utf-8")


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


def _structured_from(envelope: dict) -> Optional[dict]:
    """Extract the grammar-constrained ``structured_output`` object from a claude json / stream-json
    ``result`` envelope (R3, v0.7.0). The ``claude`` CLI sets this field only when invoked with
    ``--json-schema`` — the model's output is forced to conform, so a malformed status block is
    impossible at the source. Returns ``None`` when absent or not an object (no schema passed, or an
    older CLI) — never fabricated; the caller falls back to parsing the ``<<<PIPELINE_STATUS>>>`` fence
    out of the ``result`` text (D2 defense-in-depth)."""
    obj = envelope.get("structured_output")
    return obj if isinstance(obj, dict) else None


async def invoke_claude(
    *,
    project_slug: str,
    claude_session_id: UUID,
    prompt: str,
    charter_path: Optional[Path] = None,
    timeout: int = CLAUDE_INVOKE_TIMEOUT,
    on_event: Optional[EventCallback] = None,
    model: Optional[str] = None,
    effort: Optional[str] = None,
    json_schema: Optional[dict] = None,
) -> tuple[str, Optional["UsageMetadata"], Optional[dict]]:
    """Invoke ``claude -p`` with bounded transient-error retry (CR-NS-018 robustness).

    Returns ``(text, usage, structured_output)`` (WS-D, CR-NS-036; R3, v0.7.0): the result text +
    token usage + the grammar-constrained structured object from the json / stream-json envelope.
    ``usage`` is ``None`` when the envelope carries none; ``structured_output`` is ``None`` when no
    ``json_schema`` was passed (e.g. Gate E) or the CLI emitted none (D2 fence fallback applies).

    ``json_schema`` (R3): when given, the agent is invoked with ``--json-schema`` so the runtime
    grammar-constrains its output to the schema and returns the validated object in the envelope's
    ``structured_output`` field — making a malformed status block impossible at the source. Unset →
    today's behavior (no flag, ``structured_output`` ``None``).

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
                model=model,
                effort=effort,
                json_schema=json_schema,
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
    model: Optional[str] = None,
    effort: Optional[str] = None,
    json_schema: Optional[dict] = None,
) -> tuple[str, Optional["UsageMetadata"], Optional[dict]]:
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
        model: optional ``--model <id>`` (CR-NS-040); ``None`` → no flag (CLI default).
        effort: optional ``--effort <level>`` (CR-NS-040); ``None`` → no flag (CLI default).
        json_schema: optional ``--json-schema <schema>`` (R3, v0.7.0). When given, the runtime
            grammar-constrains the agent's output to this JSON Schema and returns the validated
            object in the envelope's ``structured_output`` field; ``None`` → no flag (no structured
            output, fence fallback applies).

    Returns:
        ``(text, usage, structured_output)`` — the result text (stripped) + token usage + the
        grammar-constrained object from the json / stream-json envelope; ``usage`` is ``None`` when
        the envelope carried none and ``structured_output`` is ``None`` when no schema was passed
        (or the CLI emitted none).

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
        # First invocation for this claude session — create it. A missing charter raises a descriptive
        # ClaudeAgentError (not a raw FileNotFoundError) → clear "re-create through NEX Studio v2" hint.
        charter_text = _load_charter(charter_path)
        args += [
            "--session-id",
            str(claude_session_id),
            "--append-system-prompt",
            charter_text,
        ]
    else:
        # Subsequent invocation — resume existing session.
        args += ["--resume", str(claude_session_id)]
    # CR-NS-040 (E3(b/c)): per-dispatch model/effort from the project owner's user_agent_settings.
    # Stateless per-invoke directives — they do NOT conflict with --resume/--session-id/--output-format
    # and may vary per turn on a shared session (the session UUID is flag-agnostic). Unset → no flag
    # (the CLI uses .claude/agents/<role>/settings.json — today's exact behavior).
    if model:
        args += ["--model", model]
    if effort:
        args += ["--effort", effort]
    # R3 (v0.7.0): grammar-constrain the output to the status-block schema. Stateless per-invoke flag
    # (like --model/--effort) — added BEFORE the positional prompt; unset → no flag (today's behavior).
    if json_schema is not None:
        args += ["--json-schema", json.dumps(json_schema)]
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
        # CR-V2-029: make the agent its own session/process-group leader so a timeout can SIGKILL the
        # WHOLE tree (parent + the helper sub-agents the claude CLI spawns via its Task tool). Killing
        # only ``proc.pid`` orphaned those helpers — they kept a Príprava turn alive at ~1200% CPU.
        start_new_session=True,
    )

    if on_event is not None:
        return await _invoke_streaming(proc, timeout=timeout, on_event=on_event)

    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(),
            timeout=timeout,
        )
    except asyncio.TimeoutError as exc:
        await _kill_process_tree(proc)
        raise ClaudeAgentTimeout(
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
    return str(envelope["result"]).strip(), _usage_from(envelope), _structured_from(envelope)


async def _kill_process_tree(proc) -> None:
    """SIGKILL the agent process AND its children (CR-V2-029). The claude CLI spawns helper sub-agents
    (Task tool) as child processes; killing only ``proc.pid`` orphans them. The process is a session
    leader (``start_new_session=True``), so its PID is the process-group id — one ``killpg`` reaps the
    whole tree. Falls back to a plain ``proc.kill()`` if the group is already gone."""
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        try:
            proc.kill()
        except ProcessLookupError:
            pass
    try:
        await asyncio.wait_for(proc.wait(), timeout=5)
    except asyncio.TimeoutError:
        pass  # the OS will reap it; never hang the dispatch on cleanup


async def _invoke_streaming(
    proc, *, timeout: int, on_event: EventCallback
) -> tuple[str, Optional["UsageMetadata"], Optional[dict]]:
    """Read ``--output-format stream-json`` NDJSON, emit events, return ``(text, usage, structured_output)``.

    The complete response is the ``result`` event's ``result`` field — the status block is parsed
    from it downstream, exactly as in json mode — and that same event carries the token ``usage``
    (WS-D, CR-NS-036) and, when ``--json-schema`` was passed, the grammar-constrained
    ``structured_output`` object (R3). A callback that raises is logged and swallowed (a broken UI
    feed must never kill an agent run).
    """

    async def _consume() -> tuple[Optional[str], Optional[UsageMetadata], Optional[dict]]:
        result_text: Optional[str] = None
        result_usage: Optional[UsageMetadata] = None
        result_structured: Optional[dict] = None
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
                result_structured = _structured_from(evt)
        return result_text, result_usage, result_structured

    try:
        result_text, result_usage, result_structured = await asyncio.wait_for(_consume(), timeout=timeout)
    except asyncio.TimeoutError as exc:
        await _kill_process_tree(proc)
        raise ClaudeAgentTimeout(f"claude invocation timed out after {timeout}s") from exc

    await proc.wait()
    if proc.returncode != 0:
        stderr_text = ""
        if proc.stderr is not None:
            stderr_text = (await proc.stderr.read()).decode("utf-8", errors="replace").strip()
        raise ClaudeAgentError(f"claude exited with code {proc.returncode}: {stderr_text[:500]}")
    if result_text is None:
        raise ClaudeAgentError("claude stream ended without a result event")
    return result_text.strip(), result_usage, result_structured
