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
from collections import deque
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

#: The known WRITE / EXECUTE / spawn tools a read-only turn must NOT reach (konzultacia-mode.md Part 1).
#: When ``invoke_claude`` is given an explicit ``allowed_tools`` set, every one of these NOT in that set is
#: passed to ``--disallowedTools`` — a CLI DENY, which ALWAYS wins over the project ``settings.json`` allow
#: list (the ai-agent profile allows Edit/Write/Bash). So the hard guarantee is the ABSENCE of any write
#: tool from the turn (per the Bash-permission lesson), not a "read-only Bash". Sub-agent spawn is denied
#: under BOTH names: ``Agent`` (Claude Code 2.x) AND ``Task`` (historical/SDK) — the CLI spawns helpers via
#: ``Task`` (see ``_kill_process_tree``) and the sibling ``pipeline_activity._HELPER_SPAWN_TOOLS`` keys on
#: both, so a rename can't silently reopen the hole; a helper would run with its OWN write-capable profile
#: and could mutate the project (konzultacia-followup.md Fix 2a). Also denied: the orchestration / skill /
#: tool-loading meta-tools ``Workflow`` / ``Skill`` / ``ToolSearch`` — a live read-only smoke showed these
#: remain in a headless session and could indirectly spawn a write-capable sub-agent or load a mutating
#: deferred/MCP tool; a read-only consult needs none of them (Read/Grep/Glob suffice to read the project).
_MUTATING_TOOLS: tuple[str, ...] = (
    "Bash",
    "Write",
    "Edit",
    "MultiEdit",
    "NotebookEdit",
    "Agent",
    "Task",
    "Workflow",
    "Skill",
    "ToolSearch",
)


class ClaudeAgentError(RuntimeError):
    """claude CLI invocation failed (non-zero exit, timeout, decode failure).

    ``log_path`` (build-robustness-crash-handling.md Fix 1): the per-turn diagnostic log written for this
    failing turn (redacted stderr / stdout tail / stream-event tail), or ``None`` when no ``log_dir`` was
    passed to :func:`invoke_claude`. The honest crash/timeout notification (Fix 3) references it so the
    operator/Dedo can read the cause of the next crash instead of an empty terminal-logs volume."""

    #: Class-level default so ``exc.log_path`` is always safe to read (raisers set the instance attr).
    log_path: Optional[str] = None


class ClaudeAgentTimeout(ClaudeAgentError):
    """The ``claude`` invocation exceeded its wall-clock timeout (CR-V2-037).

    A SUBCLASS of :class:`ClaudeAgentError`, so every existing ``except ClaudeAgentError`` still catches a
    timeout unchanged. It exists only to let callers distinguish a genuine TIMEOUT (the turn burned its
    whole budget — re-invoking just risks another long wait) from a FAST crash (non-zero exit / decode /
    stream-end — produced nothing but cost almost no wall-clock and is usually transient, so worth a
    bounded re-invoke). The task-plan per-feat passes use this to retry a crash but not a timeout."""


# --------------------------------------------------------------------------------------------------------
# Per-turn diagnostic logging (build-robustness-crash-handling.md Fix 1)
# --------------------------------------------------------------------------------------------------------

#: Per-turn diagnostic log root. Same DURABLE volume as the PTY logs (docker-compose ``terminal_logs`` →
#: ``/var/lib/nex-studio/terminal-logs``), so a crash/timeout leaves a trace on disk (the volume was empty
#: → a crash was undiagnosable). Env-overridable (``NEX_TURN_LOG_DIR``) for a non-container run / tests.
TURN_LOG_DIR = Path(os.environ.get("NEX_TURN_LOG_DIR", "/var/lib/nex-studio/terminal-logs"))

#: Bounded tail (bytes) of stdout / stream-events kept in a turn log — a single ``result`` line can be a
#: whole spec file, so only the TAIL is durable. The stderr (where a crash cause lives) is kept up to the
#: same bound but is normally tiny.
_LOG_TAIL_BYTES = 64 * 1024
#: How many trailing stream-json event lines to retain for a streaming turn's log.
_LOG_EVENT_TAIL = 50

#: §4 SECURITY (Fix 1): credential / OAuth-token patterns scrubbed from a turn log BEFORE it hits disk. The
#: ``claude`` CLI should never emit a token, but a durable log is a leak surface, so redact defensively:
#: an ``Authorization: …`` / ``…=…`` header (whole value to line-end), a bare ``Bearer <tok>``, ``token=`` /
#: ``api_key=`` / ``access_token=`` k=v pairs, and any bare ``sk-…`` secret (Anthropic OAuth ``sk-ant-oat…``
#: / API ``sk-ant-api…`` keys). Ordered so the header rule runs first, then the standalone-token rules mop
#: up anything it left (defense in depth — a leaked token must not survive under ANY of these shapes).
_REDACTIONS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?im)^(.*\bauthorization)\b\s*[:=].*$"), r"\1: [REDACTED]"),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]+"), "Bearer [REDACTED]"),
    (
        re.compile(r"(?i)\b(access[_-]?token|refresh[_-]?token|api[_-]?key|token|secret|password)\s*[:=]\s*\S+"),
        r"\1=[REDACTED]",
    ),
    (re.compile(r"\bsk-[A-Za-z0-9._\-]{6,}"), "[REDACTED]"),
)


def _redact_secrets(text: str) -> str:
    """Scrub credential / OAuth-token patterns from ``text`` (Fix 1, §4). Idempotent; never raises."""
    if not text:
        return text
    for pattern, replacement in _REDACTIONS:
        text = pattern.sub(replacement, text)
    return text


def _write_turn_log(
    log_dir: Optional[Path],
    log_label: Optional[str],
    *,
    outcome: str,
    detail: str = "",
    stdout_tail: str = "",
    stderr: str = "",
    events_tail: str = "",
) -> Optional[str]:
    """Persist ONE agent turn's output to ``<log_dir>/<log_label>.log`` (Fix 1) — REDACTED (§4) + bounded.

    ``outcome`` is ``ok`` / ``crash`` / ``timeout``. A no-op returning ``None`` when ``log_dir`` /
    ``log_label`` is unset (today's byte-identical behaviour) OR on any ``OSError`` — a diagnostic log must
    NEVER break a run. Returns the written path (str) so the caller can reference it in the honest
    crash/timeout message (Fix 3)."""
    if not log_dir or not log_label:
        return None
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", log_label)
        path = log_dir / f"{safe}.log"
        sections = [f"=== agent turn — {outcome} ==="]
        if detail:
            sections.append(detail)
        if stderr:
            sections.append("--- stderr ---\n" + stderr[-_LOG_TAIL_BYTES:])
        if events_tail:
            sections.append("--- last stream events ---\n" + events_tail[-_LOG_TAIL_BYTES:])
        if stdout_tail:
            sections.append("--- stdout (tail) ---\n" + stdout_tail[-_LOG_TAIL_BYTES:])
        body = _redact_secrets("\n\n".join(sections))
        path.write_text(body + "\n", encoding="utf-8")
        return str(path)
    except OSError as exc:  # never let a diagnostic write break a run (Fix 1)
        logger.warning("failed to persist agent turn log %s: %s", log_label, exc)
        return None


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
            # CR-V2-038: pick the DOMINANT model (most output tokens), NOT the first dict key. The
            # ``modelUsage`` key order is arbitrary, so a turn that ran on Opus (the main agent) but spawned
            # a Haiku helper would otherwise be mislabeled Haiku purely because it was listed first — which
            # mis-attributed the Auditor's turn and would skew the role-based cost metrics. The model that
            # produced the most output is the turn's primary model. (modelUsage entries use camelCase
            # ``outputTokens``; tolerate snake_case too.)
            def _model_output(name: str) -> int:
                entry = model_usage.get(name)
                if not isinstance(entry, dict):
                    return 0
                return int(entry.get("outputTokens") or entry.get("output_tokens") or 0)

            model = max(model_usage, key=_model_output)
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


def build_claude_argv(
    *,
    streaming: bool,
    claude_session_id: UUID,
    prompt: str,
    charter_text: Optional[str],
    model: Optional[str] = None,
    effort: Optional[str] = None,
    json_schema: Optional[dict] = None,
    allowed_tools: Optional[list[str]] = None,
) -> list[str]:
    """Compose the ``claude -p`` argv shared by the in-process turn AND the OS-isolated consult sidecar.

    The SINGLE source of the per-turn ``claude`` flags (konzultacia-sidecar-sandbox.md Part 1): both
    :func:`_invoke_once` (in-process subprocess) and :func:`consult_sandbox.run_consult_in_sandbox` (the
    ``docker run --entrypoint claude`` sidecar) call this so the two transports stay byte-identical except
    for the container wrapper. Returns the full argv beginning with the literal ``"claude"``; the sidecar
    drops that leading element (the entrypoint provides it) and appends the rest after the image.

    Flags, in order:
      * ``--output-format`` — ``stream-json`` (+ ``--verbose``) when ``streaming`` else ``json`` (WS-D,
        CR-NS-036: json carries the usage/cost envelope; the sidecar is always non-streaming → json).
      * ``charter_text`` given (first turn for this session — already read by the caller via
        :func:`_load_charter`, whose descriptive error is preserved) → ``--session-id`` +
        ``--append-system-prompt``; else ``--resume`` the existing session.
      * ``--model`` / ``--effort`` (CR-NS-040) when set; unset → no flag (CLI default).
      * ``--json-schema`` (R3, v0.7.0) when set → grammar-constrain the status block at the source.
      * ``allowed_tools`` given (konzultacia-mode.md Part 1 + konzultacia-followup.md Fix 2) → the
        EXCLUSIVE, deny-by-default read-only profile: ``--allowedTools`` auto-approves exactly those,
        ``--disallowedTools`` hard-denies every :data:`_MUTATING_TOOLS` member NOT in the set (a CLI deny
        wins over the project ``settings.json`` allow), and ``--permission-mode default`` makes the allow
        list exclusive (every other/MCP/future tool denied in headless). Unset → no tool flags (build
        turns, byte-identical).
    The positional ``prompt`` is always last.
    """
    if streaming:
        args = ["claude", "-p", "--output-format", "stream-json", "--verbose"]
    else:
        args = ["claude", "-p", "--output-format", "json"]
    if charter_text is not None:
        args += ["--session-id", str(claude_session_id), "--append-system-prompt", charter_text]
    else:
        args += ["--resume", str(claude_session_id)]
    if model:
        args += ["--model", model]
    if effort:
        args += ["--effort", effort]
    if json_schema is not None:
        args += ["--json-schema", json.dumps(json_schema)]
    if allowed_tools is not None:
        args += ["--allowedTools", ",".join(allowed_tools)]
        deny = [t for t in _MUTATING_TOOLS if t not in allowed_tools]
        if deny:
            args += ["--disallowedTools", ",".join(deny)]
        args += ["--permission-mode", "default"]
    args.append(prompt)
    return args


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
    allowed_tools: Optional[list[str]] = None,
    sandbox: bool = False,
    log_dir: Optional[Path] = None,
    log_label: Optional[str] = None,
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

    ``allowed_tools`` (konzultacia-mode.md Part 1): an explicit read-only tool profile. When given, the
    turn is auto-approved for exactly those tools (``--allowedTools``) AND every mutating/exec/spawn tool
    NOT in the set is HARD-denied (``--disallowedTools`` — a CLI deny wins over the project settings.json
    allow list), so a read-only Konzultácia turn provably cannot touch the project. Unset (default) →
    today's full-auto build profile, byte-identical (no tool flags — the project settings.json governs).

    ``sandbox`` (konzultacia-sidecar-sandbox.md Part 2): when ``True`` AND ``allowed_tools`` is set (a
    CONSULT turn), the turn runs inside an OS-isolated sidecar container where the project is
    KERNEL-enforced ``:ro`` and the host is unreachable — not the in-process subprocess. Build turns
    (``allowed_tools is None``) never take the sidecar path regardless of this flag. If the sidecar is
    unavailable it degrades to the in-process read-only turn with an honest WARNING (see
    :func:`_invoke_once`). Default ``False`` → today's in-process behavior, byte-identical.

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
                allowed_tools=allowed_tools,
                sandbox=sandbox,
                log_dir=log_dir,
                log_label=log_label,
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
    allowed_tools: Optional[list[str]] = None,
    sandbox: bool = False,
    log_dir: Optional[Path] = None,
    log_label: Optional[str] = None,
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
        allowed_tools: optional read-only tool profile (konzultacia-mode.md Part 1). When given,
            ``--allowedTools`` auto-approves exactly these tools AND ``--disallowedTools`` hard-denies
            every :data:`_MUTATING_TOOLS` member NOT in the set (a CLI deny wins over settings.json
            allow), so the turn cannot mutate the project. ``None`` → no tool flags (build profile).
        sandbox: konzultacia-sidecar-sandbox.md Part 2. When ``True`` and ``allowed_tools`` is set (a
            CONSULT turn), run inside an OS-isolated sidecar container (project KERNEL-``:ro``, host
            unreachable) instead of this in-process subprocess; the sidecar produces the same
            ``--output-format json`` envelope so the return contract is unchanged. Build turns
            (``allowed_tools is None``) never take the sidecar path. ``None``/``False`` → in-process.

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

    # konzultacia-sidecar-sandbox.md Part 2: a CONSULT turn (read-only tool profile active) requested to run
    # OS-isolated executes inside an ephemeral sidecar container where the project is KERNEL-enforced ``:ro``
    # and the host is unreachable — NOT this in-process subprocess. Build turns (``allowed_tools is None``)
    # never take this path. If the sidecar is UNAVAILABLE (no docker CLI / daemon), degrade to the in-process
    # read-only turn below (still tool-profile read-only, just not kernel-isolated) and LOG the weaker
    # guarantee HONESTLY — never a silent downgrade (Part 2).
    if sandbox and allowed_tools is not None:
        from backend.services import consult_sandbox  # local import — avoids a claude_agent↔consult_sandbox cycle

        if consult_sandbox.sandbox_enabled():
            try:
                return await consult_sandbox.run_consult_in_sandbox(
                    project_slug=project_slug,
                    claude_session_id=claude_session_id,
                    prompt=prompt,
                    charter_path=charter_path,
                    timeout=timeout,
                    model=model,
                    effort=effort,
                    json_schema=json_schema,
                    allowed_tools=allowed_tools,
                )
            except consult_sandbox.SidecarUnavailable as exc:
                logger.warning(
                    "consult sidecar unavailable (%s) — DEGRADED to in-process read-only turn: the project is "
                    "tool-profile read-only but NOT kernel-isolated this turn (konzultacia-sidecar-sandbox.md)",
                    exc,
                )
        else:
            logger.info(
                "CONSULT_SANDBOX disabled — running the consult turn in-process (tool-profile read-only, "
                "not kernel-isolated)",
            )

    # First invocation for this claude session loads the charter (a missing one raises a descriptive
    # ClaudeAgentError — the "re-create through NEX Studio v2" hint — not a raw FileNotFoundError); a
    # subsequent turn passes None and the argv builder emits ``--resume`` instead.
    charter_text = _load_charter(charter_path) if charter_path is not None else None
    args = build_claude_argv(
        streaming=on_event is not None,
        claude_session_id=claude_session_id,
        prompt=prompt,
        charter_text=charter_text,
        model=model,
        effort=effort,
        json_schema=json_schema,
        allowed_tools=allowed_tools,
    )

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
        return await _invoke_streaming(proc, timeout=timeout, on_event=on_event, log_dir=log_dir, log_label=log_label)

    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(),
            timeout=timeout,
        )
    except asyncio.TimeoutError as exc:
        await _kill_process_tree(proc)
        # Fix 1: a real TIMEOUT returns no envelope — persist a marker log so the wall-clock exhaustion is
        # diagnosable (and so Fix 3 can reference the path), then raise the DISTINCT timeout type.
        log_path = _write_turn_log(
            log_dir,
            log_label,
            outcome="timeout",
            detail=f"claude invocation timed out after {timeout}s (no envelope returned)",
        )
        err = ClaudeAgentTimeout(f"claude invocation timed out after {timeout}s")
        err.log_path = log_path
        raise err from exc

    stderr_text = stderr.decode("utf-8", errors="replace").strip()
    # WS-D (CR-NS-036): --output-format json → parse the envelope for the result text + usage.
    raw = stdout.decode("utf-8", errors="replace").strip()

    if proc.returncode != 0:
        log_path = _write_turn_log(
            log_dir,
            log_label,
            outcome="crash",
            detail=f"claude exited with code {proc.returncode}",
            stdout_tail=raw,
            stderr=stderr_text,
        )
        err = ClaudeAgentError(f"claude exited with code {proc.returncode}: {stderr_text[:500]}")
        err.log_path = log_path
        raise err

    try:
        envelope = json.loads(raw)
    except json.JSONDecodeError as exc:
        log_path = _write_turn_log(
            log_dir,
            log_label,
            outcome="crash",
            detail=f"claude json output not parseable: {exc}",
            stdout_tail=raw,
            stderr=stderr_text,
        )
        err = ClaudeAgentError(f"claude json output not parseable: {exc}")
        err.log_path = log_path
        raise err from exc
    if not isinstance(envelope, dict) or "result" not in envelope:
        log_path = _write_turn_log(
            log_dir,
            log_label,
            outcome="crash",
            detail="claude json output has no 'result' field",
            stdout_tail=raw,
            stderr=stderr_text,
        )
        err = ClaudeAgentError("claude json output has no 'result' field")
        err.log_path = log_path
        raise err
    # Fix 1: persist a normal completion too, so the NEXT crash has a healthy prior-turn baseline to diff.
    _write_turn_log(log_dir, log_label, outcome="ok", stdout_tail=raw, stderr=stderr_text)
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
    proc, *, timeout: int, on_event: EventCallback, log_dir: Optional[Path] = None, log_label: Optional[str] = None
) -> tuple[str, Optional["UsageMetadata"], Optional[dict]]:
    """Read ``--output-format stream-json`` NDJSON, emit events, return ``(text, usage, structured_output)``.

    The complete response is the ``result`` event's ``result`` field — the status block is parsed
    from it downstream, exactly as in json mode — and that same event carries the token ``usage``
    (WS-D, CR-NS-036) and, when ``--json-schema`` was passed, the grammar-constrained
    ``structured_output`` object (R3). A callback that raises is logged and swallowed (a broken UI
    feed must never kill an agent run).

    Fix 1: the last :data:`_LOG_EVENT_TAIL` raw event lines are retained in an outer-scope ring buffer, so
    on a timeout (``_consume`` cancelled) OR a crash the tail is still persisted to the per-turn log.
    """
    event_tail: deque[str] = deque(maxlen=_LOG_EVENT_TAIL)

    async def _consume() -> tuple[Optional[str], Optional[UsageMetadata], Optional[dict]]:
        result_text: Optional[str] = None
        result_usage: Optional[UsageMetadata] = None
        result_structured: Optional[dict] = None
        assert proc.stdout is not None
        async for raw in proc.stdout:
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            event_tail.append(line)
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
        log_path = _write_turn_log(
            log_dir,
            log_label,
            outcome="timeout",
            detail=f"claude invocation timed out after {timeout}s (stream did not complete)",
            events_tail="\n".join(event_tail),
        )
        err = ClaudeAgentTimeout(f"claude invocation timed out after {timeout}s")
        err.log_path = log_path
        raise err from exc

    await proc.wait()
    if proc.returncode != 0:
        stderr_text = ""
        if proc.stderr is not None:
            stderr_text = (await proc.stderr.read()).decode("utf-8", errors="replace").strip()
        log_path = _write_turn_log(
            log_dir,
            log_label,
            outcome="crash",
            detail=f"claude exited with code {proc.returncode}",
            stderr=stderr_text,
            events_tail="\n".join(event_tail),
        )
        err = ClaudeAgentError(f"claude exited with code {proc.returncode}: {stderr_text[:500]}")
        err.log_path = log_path
        raise err
    if result_text is None:
        log_path = _write_turn_log(
            log_dir,
            log_label,
            outcome="crash",
            detail="claude stream ended without a result event",
            events_tail="\n".join(event_tail),
        )
        err = ClaudeAgentError("claude stream ended without a result event")
        err.log_path = log_path
        raise err
    _write_turn_log(log_dir, log_label, outcome="ok", events_tail="\n".join(event_tail))
    return result_text.strip(), result_usage, result_structured
