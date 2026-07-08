"""Read-only tool profile → the EXCLUSIVE, deny-by-default consult CLI args (konzultacia-followup.md Fix 2).

``invoke_claude(allowed_tools=[...])`` (the read-only Konzultácia turn) must build a headless ``claude``
invocation that is read-only by CONSTRUCTION, not by the project's ``bypassPermissions``:

  * **2a** — the deny-list (`--disallowedTools`) carries every mutating/exec/spawn tool, INCLUDING ``Task``
    (the CLI's sub-agent spawn name) alongside ``Agent`` — else a consult could spawn a write-capable helper.
  * **2b** — the turn passes ``--permission-mode default`` so the allow-list is EXCLUSIVE (only the read tools
    are auto-approved; every other/MCP/future tool is denied by default in headless), overriding the project
    ``bypassPermissions``.
  * a BUILD turn (``allowed_tools=None``) passes NONE of these flags — today's full-auto behavior, unchanged.

``asyncio.create_subprocess_exec`` is mocked so no real ``claude`` binary runs; we inspect the argv it built.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from backend.services import claude_agent

_READ_ONLY = ["Read", "Grep", "Glob"]


def _ok_proc() -> MagicMock:
    """A subprocess mock whose ``communicate()`` returns a valid ``--output-format json`` envelope, exit 0."""
    proc = MagicMock()
    proc.returncode = 0
    envelope = json.dumps({"result": "ok", "usage": {"input_tokens": 1, "output_tokens": 1}}).encode("utf-8")
    proc.communicate = AsyncMock(return_value=(envelope, b""))
    return proc


async def _argv_for(monkeypatch, *, allowed_tools) -> list[str]:
    """Invoke ``claude`` (subprocess mocked) and return the argv ``create_subprocess_exec`` was called with."""
    mock_exec = AsyncMock(return_value=_ok_proc())
    monkeypatch.setattr(claude_agent.asyncio, "create_subprocess_exec", mock_exec)
    await claude_agent.invoke_claude(
        project_slug="p",
        claude_session_id=uuid4(),
        prompt="otázka",
        allowed_tools=allowed_tools,
    )
    return list(mock_exec.call_args.args)


def _value_after(argv: list[str], flag: str) -> str:
    return argv[argv.index(flag) + 1]


# ---------------------------------------------------------------------------
# Fix 2a — the deny-list keys on BOTH Agent AND Task
# ---------------------------------------------------------------------------


def test_mutating_tools_includes_task() -> None:
    # A consult must be unable to spawn a sub-agent under EITHER spelling (Agent = Claude Code 2.x, Task = SDK),
    # nor reach the orchestration/skill/tool-loading meta-tools a live smoke found still available (Workflow /
    # Skill / ToolSearch — indirect spawn / mutating-tool-load vectors).
    for tool in (
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
    ):
        assert tool in claude_agent._MUTATING_TOOLS


async def test_consult_deny_list_carries_task(monkeypatch) -> None:
    argv = await _argv_for(monkeypatch, allowed_tools=_READ_ONLY)
    deny = _value_after(argv, "--disallowedTools").split(",")
    # Every mutating/exec/spawn tool is denied (none are in the read-only allow-set), incl. the Task spawn
    # and the Workflow/Skill/ToolSearch meta-tools the live smoke found still reachable.
    for tool in (
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
    ):
        assert tool in deny


# ---------------------------------------------------------------------------
# Fix 2b — the consult turn passes --permission-mode default (allow-list EXCLUSIVE); a build turn does NOT
# ---------------------------------------------------------------------------


async def test_consult_passes_permission_mode_default_and_readonly_allow(monkeypatch) -> None:
    argv = await _argv_for(monkeypatch, allowed_tools=_READ_ONLY)
    assert _value_after(argv, "--allowedTools") == "Read,Grep,Glob"
    assert "--permission-mode" in argv
    assert _value_after(argv, "--permission-mode") == "default"


async def test_build_turn_passes_no_tool_or_permission_flags(monkeypatch) -> None:
    argv = await _argv_for(monkeypatch, allowed_tools=None)
    # A build turn (allowed_tools None) keeps today's full-auto bypassPermissions behavior — byte-identical.
    assert "--permission-mode" not in argv
    assert "--allowedTools" not in argv
    assert "--disallowedTools" not in argv
