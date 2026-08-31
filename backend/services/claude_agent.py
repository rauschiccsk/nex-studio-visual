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
from typing import Any, Optional
from uuid import UUID

from backend.config.settings import settings
from backend.constants.paths import TERMINAL_LOG_DIR as DURABLE_TERMINAL_LOG_DIR
from backend.core.agent_env import agent_env

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

#: ICCINT-47: the per-stage budget is now the SILENCE budget — how long the agent may produce nothing before
#: the turn is judged stuck. A working agent streams events continuously, so a big-but-honest task is no
#: longer cut off for being big; only a hung one is. The hard ceiling below is the runaway guard that used to
#: be the only limit.
#: The multiplier is deliberately generous: it exists so a turn cannot run forever, NOT to size the work.
TURN_CEILING_MULTIPLIER = 6
#: How often the silence watchdog wakes. Short enough to be responsive, long enough to cost nothing.
_SILENCE_POLL_SECONDS = 5

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
#: :data:`backend.constants.paths.TERMINAL_LOG_DIR`), so a crash/timeout leaves a trace on disk. It did NOT:
#: this literal used to read ``/var/lib/nex-studio/terminal-logs``, a path no compose file mounts, so every
#: turn log went into the container's writable layer and died with the container while the volume stayed
#: empty — the exact undiagnosable-crash failure this log was added to prevent. Derived from the shared
#: constant now; env-overridable (``NEX_TURN_LOG_DIR``) for a non-container run / tests.
TURN_LOG_DIR = Path(os.environ.get("NEX_TURN_LOG_DIR", str(DURABLE_TERMINAL_LOG_DIR)))

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


#: The setting sources EVERY turn loads — deliberately NOT the CLI default (``user,project,local``).
#:
#: ``local`` is ``<project>/.claude/settings.local.json``: a file Create Project never writes, that no ICC
#: project has, and that a build turn can CREATE inside its own read-write project tree. Hooks declared
#: there are shell commands executed by whatever runs next — and since ICCINT-16 STEP 2 that means Vizuál and
#: Verifikácia, which still run as ROOT inside the backend container with the docker socket and
#: ``/opt/customers`` (Príprava, Návrh and Programovanie are isolated; see
#: :data:`build_sandbox.SANDBOXED_PHASES`). The project's own ``settings.json`` is re-mounted read-only for a
#: sandboxed turn
#: (:func:`build_sandbox.frozen_project_paths`), but a file that does not yet exist cannot be frozen by a
#: mount — so the second half of that guarantee is here, on the LOADING side: the source is never consulted.
_SETTING_SOURCES = "user,project"

#: ``--strict-mcp-config`` — same reasoning, for the other executable project config. ``.mcp.json`` declares
#: MCP servers that the CLI SPAWNS AS PROCESSES; it is absent from every ICC project, lives in the writable
#: project root, and could therefore be created by a build turn for a later privileged turn to launch.
#: With this flag (and no ``--mcp-config``) the CLI uses none of it, whatever appears on disk.
_STRICT_MCP_CONFIG = "--strict-mcp-config"


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
    settings_path: Optional[Path] = None,
    permission_mode: Optional[str] = None,
) -> list[str]:
    """Compose the ``claude -p`` argv shared by the in-process turn AND the OS-isolated consult sidecar.

    The SINGLE source of the per-turn ``claude`` flags (konzultacia-sidecar-sandbox.md Part 1): both
    :func:`_invoke_once` (in-process subprocess) and :func:`consult_sandbox.run_consult_in_sandbox` (the
    ``docker run --entrypoint claude`` sidecar) call this so the two transports stay byte-identical except
    for the container wrapper. Returns the full argv beginning with the CLI binary
    (``settings.claude_cli_path``, default ``"claude"``); the sidecar drops that leading element (the
    entrypoint provides it) and appends the rest after the image.

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
      * ``--setting-sources user,project`` + ``--strict-mcp-config`` — on EVERY turn, sandboxed or not.
        Both exist to make a file a build turn could CREATE in its own project tree un-loadable by the
        turns that still run as root: see :data:`_SETTING_SOURCES` / :data:`_STRICT_MCP_CONFIG`. Freezing
        the files that EXIST is the sandbox's job (a read-only re-mount); a file that does not exist yet
        can only be neutralised where it would be read.
      * ``permission_mode`` — an explicit ``--permission-mode``. A read-only consult passes ``default``
        (the allow-list must be exclusive); a SANDBOXED build turn passes ``bypassPermissions``, which is
        exactly what it used to inherit from the mounted user ``settings.json`` — except that the mounted
        file was writable BY the turn and the flag is not (ICCINT-16). An in-process build turn passes
        nothing, byte-identical to before.
      * ``--settings`` — the dispatched role's permission profile
        (``.claude/agents/<role>/settings.json``). WITHOUT THIS FLAG THE PROFILE IS INERT. Create
        Project writes one per role and this argv used to pass nothing, while the docstring claimed
        "the project settings.json governs"; it does not. ``--setting-sources`` accepts only ``user``,
        ``project`` and ``local`` — i.e. ``~/.claude/settings.json``, ``.claude/settings.json`` and
        ``.claude/settings.local.json`` — and ``.claude/agents/<role>/settings.json`` is none of them,
        so every ``deny`` in it (``git push --force``, ``git reset --hard``, rewriting its own charter)
        was decoration. The mounted user config sets ``defaultMode: bypassPermissions``, so what the
        build turn actually ran with was: everything auto-approved.
        Verified against the real CLI inside this backend's own container, because the merge semantics
        are not obvious: with ``bypassPermissions`` active, ``--disallowedTools`` alone does NOT block
        (the mode wins), but a ``deny`` rule arriving via ``--settings`` DOES. Deny is what we need and
        deny is what survives. Two roles share one project root, so the profile cannot live at
        ``.claude/settings.json`` — the flag is the only mechanism that can select per dispatch.
    The positional ``prompt`` is always last.
    """
    # ``settings.claude_cli_path`` decides WHICH binary runs — it used to decide only whether
    # ``/health`` reported ``claude_cli_available`` (health.py asks ``shutil.which`` about it), while the
    # live dispatch hardcoded the bare name. Point the setting at a specific build and the health check
    # would follow it while every actual turn kept running whatever ``claude`` resolved to on PATH: the
    # verdict and the reality could disagree without a word.
    cli = settings.claude_cli_path
    if streaming:
        args = [cli, "-p", "--output-format", "stream-json", "--verbose"]
    else:
        args = [cli, "-p", "--output-format", "json"]
    args += ["--setting-sources", _SETTING_SOURCES, _STRICT_MCP_CONFIG]
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
    if settings_path is not None:
        args += ["--settings", str(settings_path)]
    if allowed_tools is not None:
        args += ["--allowedTools", ",".join(allowed_tools)]
        deny = [t for t in _MUTATING_TOOLS if t not in allowed_tools]
        if deny:
            args += ["--disallowedTools", ",".join(deny)]
        args += ["--permission-mode", "default"]
    elif permission_mode:
        args += ["--permission-mode", permission_mode]
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
    settings_path: Optional[Path] = None,
    sandbox: bool = False,
    stage: Optional[str] = None,
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

    ``stage`` (ICCINT-16 STEP 2): the pipeline phase this turn belongs to. A BUILD turn in one of
    :data:`build_sandbox.SANDBOXED_PHASES` (``priprava`` / ``navrh`` / ``programovanie``) runs inside an
    OS-isolated container whose FILESYSTEM is its own project and nothing else — see :func:`_invoke_once`.
    Routing keys on the PHASE, not on a per-call flag, so it cannot be forgotten at a call site — but a call
    site that omits the argument gets ``None``, which routes IN-PROCESS, so ``tests/test_build_sandbox.py``
    walks the orchestrator's AST and fails on any ``invoke_claude`` call without ``stage=`` (one had it
    missing and every task-plan pass ran unisolated). ``Vizuál`` and ``Verifikácia`` are the two phases still
    running as backend subprocesses — they bring the whole app up through ``docker compose``, which is what
    the socket is for — and say so explicitly rather than by omission.

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
                settings_path=settings_path,
                sandbox=sandbox,
                stage=stage,
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
    settings_path: Optional[Path] = None,
    sandbox: bool = False,
    stage: Optional[str] = None,
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
        stage: ICCINT-16 STEP 2. The pipeline phase this turn belongs to. A BUILD turn
            (``allowed_tools is None``) whose phase is in :data:`build_sandbox.SANDBOXED_PHASES` has its
            argv WRAPPED in a ``docker run`` with an ephemeral HOME that mounts only the project (rw, with
            its executable config re-mounted read-only), that project's own claude transcript, the shared
            knowledge base (read-only) and the claude binary — the claude flags, streaming, timeout,
            per-turn logging and return contract are untouched, because only the transport and the
            permission-mode FLAG change. ``programovanie`` additionally gets a throwaway PostgreSQL on a
            per-build network and its ``DATABASE_URL`` (:mod:`build_db`) INSTEAD of the docker socket it
            used to need. ``vizual`` joined in ICCINT-20 with nothing extra: its preview container is the
            ENGINE's (``vizual_sandbox.spin_up``), bind-mounting the same host directory, so the agent's
            turn is file editing and HMR carries it. Verifikácia — and an unknown/``None`` phase — runs
            the in-process subprocess exactly as before. The FILESYSTEM is what is isolated;
            :data:`build_sandbox.NETWORK_RESIDUAL` states what is not.

    Returns:
        ``(text, usage, structured_output)`` — the result text (stripped) + token usage + the
        grammar-constrained object from the json / stream-json envelope; ``usage`` is ``None`` when
        the envelope carried none and ``structured_output`` is ``None`` when no schema was passed
        (or the CLI emitted none).

    Raises:
        ClaudeAgentError: subprocess non-zero exit, timeout, decode/JSON failure, or a
            json envelope with no ``result`` field.
    """
    # local import — avoids a claude_agent↔build_sandbox cycle (build_sandbox raises a ClaudeAgentError
    # subclass, so it imports this module at its own module scope).
    from backend.services import build_sandbox

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
                    settings_path=settings_path,
                )
            except consult_sandbox.SidecarUnavailable as exc:
                # LOUD, not a warning buried in a log: a promised kernel boundary that is not in effect gets
                # counted + published on ``GET /health`` (consult_sandbox.degraded_turns) and logged at ERROR
                # with the failing precondition. The audited deployment degraded on EVERY consult — the
                # configured image did not exist — and nothing outside the log file ever said so.
                consult_sandbox.record_degradation(str(exc))
        else:
            logger.info(
                "CONSULT_SANDBOX disabled — running the consult turn in-process (tool-profile read-only, "
                "not kernel-isolated)",
            )

    # ICCINT-16 STEP 2: a BUILD turn in the Príprava/Návrh/Programovanie phases is WRAPPED in a ``docker
    # run`` that mounts only its own project (rw), its own session transcript, the shared knowledge base
    # (read-only) and the claude binary — no docker socket, no /opt/customers, no /opt/uat, no /opt/infra, no
    # credential store, no shared ~/.claude. Only the TRANSPORT changes: the claude flags are the same argv,
    # and streaming, the timeout, the per-turn log and the return contract are untouched.
    #
    # The switch is the PHASE the engine is already in — not a per-call flag somebody has to remember.
    # Programovanie is in the list since STEP 2 because the engine hands it a throwaway PostgreSQL
    # (:mod:`build_db`) instead of the docker socket its charter used to need. Vizuál and Verifikácia stay
    # OUT: they build and run the whole app through ``docker compose``, which is what the socket is for — so
    # this covers three phases out of five, and no more. ``allowed_tools is None`` keeps a consult turn
    # (handled above) out.
    #
    # Decided BEFORE the argv is composed, because the isolation changes one claude flag as well as the
    # transport: a sandboxed turn carries ``--permission-mode bypassPermissions`` in its ARGV instead of
    # inheriting it from a user ``settings.json`` it could itself rewrite.
    use_sandbox = allowed_tools is None and build_sandbox.phase_uses_sandbox(stage) and build_sandbox.sandbox_enabled()
    if allowed_tools is None and build_sandbox.phase_uses_sandbox(stage) and not use_sandbox:
        # The kill-switch was thrown deliberately; say so at WARNING every turn, naming what is back in
        # reach. A boundary that is not in effect must never be inferable only from its absence.
        logger.warning(
            "BUILD_SANDBOX is off — the %s turn for %s runs as a backend subprocess, with "
            "/opt/customers, /opt/uat, /opt/infra and the docker socket in reach (ICCINT-16)",
            stage,
            project_slug,
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
        settings_path=settings_path,
        permission_mode=build_sandbox.SANDBOX_PERMISSION_MODE if use_sandbox else None,
    )

    sandbox_container: Optional[str] = None
    turn_database = None
    turn_network: Optional[str] = None
    # THE ``try`` OPENS BEFORE THE DATABASE IS CREATED, and that placement is the whole point. It used to
    # open after ``build_run_argv``, i.e. the composing of the argv sat BETWEEN ``build_db.start`` and the
    # only ``finally`` that reaps it — and ``build_run_argv`` declares ``Raises: BuildSandboxUnavailable``
    # (empty claude argv, unreachable session dir, a mount it cannot compose). On that path a PostgreSQL
    # container and a docker network were already running and nothing removed them: ``--rm`` cannot, because
    # the container must OUTLIVE the docker CLI that created it. "The database dies with the turn on every
    # way out" is only true if every way out is inside this block.
    try:
        if use_sandbox:
            # The sandbox is uid 1000; every turn so far ran as root, so the project tree and the session
            # transcript are littered with root-owned files that uid 1000 can neither ``--resume`` nor
            # rewrite. Re-own them (and create the transcript dir a brand-new project does not have yet)
            # BEFORE the container starts — a failure raises BuildSandboxUnavailable, never a silent
            # downgrade.
            await build_sandbox.prepare_turn(project_slug)
            build_sandbox.log_network_residual_once()
            token = build_sandbox.turn_token()
            sandbox_container = build_sandbox.container_name(project_slug, token)
            # ICCINT-16 STEP 2: a Programovanie turn gets a throwaway PostgreSQL on a per-build network
            # INSTEAD of the docker socket it used to need — the project's conftest asks for a reachable
            # Postgres and nothing more. Planned first (it can refuse a project needing a service we do not
            # supply, and that refusal must happen before anything is created), then started, then wired
            # into the argv. Every phase that is NOT Programovanie plans ``None`` and this is a no-op.
            turn_database = build_sandbox.plan_turn_database(project_slug=project_slug, stage=stage, token=token)
            from backend.services import build_db  # local import — cycle (see above)

            if turn_database is not None:
                await build_db.start(turn_database)
                turn_network = turn_database.network
            else:
                # ICCINT-21: a turn WITHOUT a database still needs a fenced network of its own. These three
                # phases used to sit on docker's default bridge, which cannot be fenced as a whole — it also
                # carries CI runners and other stacks' containers. Created here and reaped in the same
                # ``finally`` as the database, for the same reason: every way out must clean up.
                turn_network = build_db.network_name(project_slug, token)
                await build_db.create_fenced_network(turn_network, project_slug, turn_network)
            args = build_sandbox.build_run_argv(
                project_slug=project_slug,
                container_name=sandbox_container,
                claude_argv=args,
                database=turn_database,
                network=turn_network,
            )

        return await _run_turn(
            args,
            project_root=project_root,
            project_slug=project_slug,
            claude_session_id=claude_session_id,
            charter_path=charter_path,
            prompt=prompt,
            timeout=timeout,
            on_event=on_event,
            log_dir=log_dir,
            log_label=log_label,
            sandbox_container=sandbox_container,
        )
    finally:
        # THE DATABASE MUST DIE WITH THE TURN — on the clean return, the crash, the timeout and the cancel
        # alike. ``--rm`` cannot do it (the container has to OUTLIVE the docker CLI that created it), and a
        # forgotten PostgreSQL is the quietest leak there is: nobody notices until the host's disk is full.
        if turn_database is not None:
            from backend.services import build_db  # local import — cycle (see above)

            await build_db.release(turn_database)
        elif turn_network is not None:
            from backend.services import build_db  # local import — cycle (see above)

            await build_db.remove_network(turn_network)


async def _run_turn(
    args: list[str],
    *,
    project_root: Path,
    project_slug: str,
    claude_session_id: UUID,
    charter_path: Optional[Path],
    prompt: str,
    timeout: int,
    on_event: Optional[EventCallback],
    log_dir: Optional[Path],
    log_label: Optional[str],
    sandbox_container: Optional[str],
) -> tuple[str, Optional["UsageMetadata"], Optional[dict]]:
    """Launch the composed argv and turn its output into ``(text, usage, structured_output)``.

    Split out of :func:`_invoke_once` for ONE reason: the per-turn database has to be released on every
    exit path, and a ``finally`` around a body with this many ``return``/``raise`` sites is only readable
    if the body is a call. The behaviour is unchanged — this is the same code, at the same indentation,
    with its inputs named.
    """
    from backend.services import build_sandbox  # local import — cycle (see :func:`_invoke_once`)

    logger.info(
        "Invoking claude agent: project=%s session=%s charter=%s prompt_len=%d transport=%s",
        project_slug,
        claude_session_id,
        "yes" if charter_path else "no",
        len(prompt),
        f"sandbox:{sandbox_container}" if sandbox_container else "in-process",
    )

    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(project_root),
            # EXPLICIT env, never the inherited default: a build turn runs with an unrestricted Bash tool, so
            # every variable this process holds is a variable the agent can print. Dedo's machine token
            # (ICCINT-14) must not be among them — the agent is the party that RAISES the ``framework_issue``
            # blocks that token clears, and holding it would let a stuck build unstick itself as ``dedo``.
            # In the sandboxed case this is the env of the ``docker run`` CLIENT: the sandbox itself receives
            # only ``build_sandbox``'s explicit allow-list, passed by NAME so no value reaches the argv.
            env=agent_env(),
            # Generous StreamReader buffer: a single stream-json NDJSON event (e.g. a
            # gate's full openapi.yaml in one `result` line) can far exceed the 64 KB
            # default and would raise LimitOverrunError on readline (CR-NS-018).
            limit=_STREAM_LINE_LIMIT,
            # CR-V2-029: make the agent its own session/process-group leader so a timeout can SIGKILL the
            # WHOLE tree (parent + the helper sub-agents the claude CLI spawns via its Task tool). Killing
            # only ``proc.pid`` orphaned those helpers — they kept a Príprava turn alive at ~1200% CPU.
            start_new_session=True,
        )
    except FileNotFoundError as exc:
        # No ``docker`` CLI at all. For a sandboxed turn this is the sandbox being unavailable, and it is
        # reported as such — never retried in-process, which would be the silent downgrade to the very
        # exposure the sandbox removes. For an in-process turn the missing binary is ``claude`` itself and
        # the error propagates exactly as it did before.
        if sandbox_container is not None:
            raise build_sandbox.unavailable(str(exc)) from exc
        raise

    if on_event is not None:
        return await _invoke_streaming(
            proc,
            timeout=timeout,
            on_event=on_event,
            log_dir=log_dir,
            log_label=log_label,
            sandbox_container=sandbox_container,
        )

    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(),
            timeout=timeout,
        )
    except asyncio.TimeoutError as exc:
        await _kill_process_tree(proc)
        # Killing the docker-run CLIENT leaves the CONTAINER running — ``--rm`` only reaps a clean exit — so
        # a timed-out sandbox has to be removed explicitly or it leaks (and keeps writing to the project).
        await _reap_sandbox(sandbox_container)
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
    except asyncio.CancelledError:
        # Only for a sandboxed turn: a cancelled dispatch must not leave a container running against the
        # project. The in-process path is left byte-identical (it does not clean up on cancel today, and
        # changing that is not this task's business).
        if sandbox_container is not None:
            await _kill_process_tree(proc)
            await _reap_sandbox(sandbox_container)
        raise
    except Exception:  # noqa: BLE001 — cleanup, then re-raise unchanged
        # ANY OTHER unexpected error between launch and the process finishing (an OSError out of
        # ``communicate``, a decode blowing up) must NOT leave the container running: ``--rm`` reaps a CLEAN
        # exit only, and this container holds WRITE access to the project, so a leaked one keeps writing
        # after the turn is over from the backend's point of view. :mod:`consult_sandbox` already paid for
        # this lesson (``test_unexpected_error_mid_run_reaps_container``); the copy that did not carry it
        # over is the copy that would leak. Timeout/Cancelled are handled above and a clean exit never
        # enters here, so there is no double reap.
        if sandbox_container is not None:
            await _kill_process_tree(proc)
            await _reap_sandbox(sandbox_container)
        raise

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
        err = _turn_failure(proc.returncode, stderr_text, sandbox_container)
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


async def _reap_sandbox(container_name: Optional[str]) -> None:
    """``docker rm -f`` a sandbox container left running by a timeout/cancel. No-op for in-process turns."""
    if container_name is None:
        return
    from backend.services import build_sandbox  # local import — cycle (see :func:`_invoke_once`)

    await build_sandbox.reap_container(container_name)


def _turn_failure(returncode: Optional[int], stderr_text: str, container_name: Optional[str]) -> ClaudeAgentError:
    """Classify a non-zero exit: did the SANDBOX fail to start, or did ``claude`` fail inside a healthy one?

    The distinction is what the operator is told. "claude exited with code 125" for a missing image sends
    somebody hunting through agent logs; :class:`build_sandbox.BuildSandboxUnavailable` names the unmet
    precondition and the two ways out. For an in-process turn nothing changes — the same
    :class:`ClaudeAgentError`, with the same message, as before.
    """
    if container_name is not None:
        from backend.services import build_sandbox  # local import — cycle (see :func:`_invoke_once`)

        if build_sandbox.looks_unavailable(stderr_text):
            return build_sandbox.unavailable(stderr_text[:500])
    return ClaudeAgentError(f"claude exited with code {returncode}: {stderr_text[:500]}")


async def _invoke_streaming(
    proc,
    *,
    timeout: int,
    on_event: EventCallback,
    log_dir: Optional[Path] = None,
    log_label: Optional[str] = None,
    sandbox_container: Optional[str] = None,
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

    # ICCINT-47: the turn's clock measures SILENCE, not size. Every line the agent emits is proof it is still
    # working; ``_watch_for_silence`` below reads this.
    loop = asyncio.get_running_loop()
    last_activity = loop.time()

    async def _consume() -> tuple[Optional[str], Optional[UsageMetadata], Optional[dict]]:
        nonlocal last_activity
        result_text: Optional[str] = None
        result_usage: Optional[UsageMetadata] = None
        result_structured: Optional[dict] = None
        assert proc.stdout is not None
        async for raw in proc.stdout:
            last_activity = loop.time()
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

    async def _watch_for_silence(task: "asyncio.Task[Any]", idle_limit: float, ceiling: float) -> Optional[str]:
        """Cancel *task* when the agent goes SILENT for *idle_limit*, or when *ceiling* is reached.

        ICCINT-47. Before this, one ``wait_for`` capped the WHOLE turn — 40 minutes for Programovanie,
        whether the task was a one-line fix or thirty spec-derived acceptance assertions. A big-but-honest
        task could not finish, so the work got sharded to fit the tool: Dedo split one deliverable into four
        batches and the Manažér paid for it in four clicks and four waits. Director, 31.08.2026: *"Ak sa testy
        nezmestia, potom to netreba rozbíjať, ale zvýšiť časový limit. Toto pre mňa nie je dlhodobé a nie je
        akceptovateľné riešenie."*

        A turn should end when the work is DONE or STUCK — not when a clock runs out. A stuck agent stops
        emitting; a working one does not. So the budget is spent on silence, and the ceiling stays only as a
        runaway guard. Returns the reason, or ``None`` if the task finished on its own."""
        started = loop.time()
        while not task.done():
            await asyncio.sleep(min(_SILENCE_POLL_SECONDS, idle_limit))
            if task.done():
                return None
            now = loop.time()
            if now - last_activity >= idle_limit:
                task.cancel()
                return f"agent mlčal {int(now - last_activity)}s (limit {int(idle_limit)}s)"
            if now - started >= ceiling:
                task.cancel()
                return f"prekročený tvrdý strop ťahu ({int(ceiling)}s)"
        return None

    idle_limit = float(timeout)
    ceiling = max(idle_limit, idle_limit * TURN_CEILING_MULTIPLIER)
    consume_task: "asyncio.Task[Any]" = asyncio.ensure_future(_consume())
    watchdog = asyncio.ensure_future(_watch_for_silence(consume_task, idle_limit, ceiling))
    try:
        try:
            result_text, result_usage, result_structured = await consume_task
        except asyncio.CancelledError:
            # Cancelled BY the watchdog (silence / ceiling) → the turn timed out. A cancellation from anywhere
            # else re-raises below, unchanged.
            reason = watchdog.result() if watchdog.done() else None
            if reason is None:
                raise
            raise asyncio.TimeoutError(reason) from None
        finally:
            watchdog.cancel()
    except asyncio.TimeoutError as exc:
        await _kill_process_tree(proc)
        # A killed docker-run client leaves the CONTAINER alive (``--rm`` only reaps a clean exit) — remove
        # it, or a timed-out sandbox keeps running against the project. No-op for an in-process turn.
        await _reap_sandbox(sandbox_container)
        # ICCINT-47: WHY the turn ended — silence or the runaway ceiling. "Timed out after 40 min" says only
        # that a clock ran; the two have different answers (steer the agent vs. look at what it is stuck on).
        why = str(exc) or f"vypršalo po {timeout}s"
        log_path = _write_turn_log(
            log_dir,
            log_label,
            outcome="timeout",
            detail=f"claude invocation timed out: {why} (stream did not complete)",
            events_tail="\n".join(event_tail),
        )
        err = ClaudeAgentTimeout(f"claude invocation timed out: {why}")
        err.log_path = log_path
        raise err from exc
    except asyncio.CancelledError:
        # Sandboxed turns only — see the matching handler in :func:`_invoke_once`.
        if sandbox_container is not None:
            await _kill_process_tree(proc)
            await _reap_sandbox(sandbox_container)
        raise
    except Exception:  # noqa: BLE001 — cleanup, then re-raise unchanged
        # THE PRODUCTION PATH. Every live Príprava/Návrh dispatch streams (``invoke_agent`` always passes
        # ``on_event``), and the realistic mid-run failure is right here: ``async for raw in proc.stdout``
        # raises ``ValueError``/``LimitOverrunError`` when one NDJSON event exceeds
        # :data:`_STREAM_LINE_LIMIT` — the very case CR-NS-018 raised that limit for — and that is neither a
        # Timeout nor a Cancel. Without this the docker-run client dies with the coroutine and the CONTAINER
        # keeps running with write access to the project.
        if sandbox_container is not None:
            await _kill_process_tree(proc)
            await _reap_sandbox(sandbox_container)
        raise

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
        err = _turn_failure(proc.returncode, stderr_text, sandbox_container)
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
