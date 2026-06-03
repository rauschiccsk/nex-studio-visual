"""Shared headless ``claude`` CLI invocation primitive (CR-NS-018 Phase 2).

Extracted verbatim from ``dialogue._invoke_agent`` so both Gate E (dialogue)
and the F-007 orchestrator drive agents the same way — ``claude -p --resume``
against a per-agent disk-persisted session UUID. Behaviour is identical to the
original; ``dialogue.py`` now delegates here.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Optional
from uuid import UUID

logger = logging.getLogger(__name__)

PROJECTS_ROOT = Path("/opt/projects")

#: Timeout per ``claude --print`` invocation (seconds). Tools-heavy turns can
#: run long, so this is generous.
CLAUDE_INVOKE_TIMEOUT = 180


class ClaudeAgentError(RuntimeError):
    """claude CLI invocation failed (non-zero exit, timeout, decode failure)."""


async def invoke_claude(
    *,
    project_slug: str,
    claude_session_id: UUID,
    prompt: str,
    charter_path: Optional[Path] = None,
    timeout: int = CLAUDE_INVOKE_TIMEOUT,
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

    Returns:
        Plain text response from claude (stripped of trailing newline).

    Raises:
        ClaudeAgentError: subprocess non-zero exit, timeout, or decode failure.
    """
    project_root = PROJECTS_ROOT / project_slug

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
    )
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
