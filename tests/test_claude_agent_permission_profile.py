"""The role permission profile reaches the CLI; an in-process build turn carries no tool flags.

(Until ICCINT-167 this file also guarded the read-only tool profile of the Konzultácia turn — that turn is
gone, replaced by Poradca, whose container builds its own restricted argv in ``poradca/sandbox.py`` and is
guarded by ``tests/test_poradca_sandbox.py``.)

``asyncio.create_subprocess_exec`` is mocked so no real ``claude`` binary runs; we inspect the argv it built.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from backend.services import claude_agent


def _ok_proc() -> MagicMock:
    """A subprocess mock whose ``communicate()`` returns a valid ``--output-format json`` envelope, exit 0."""
    proc = MagicMock()
    proc.returncode = 0
    envelope = json.dumps({"result": "ok", "usage": {"input_tokens": 1, "output_tokens": 1}}).encode("utf-8")
    proc.communicate = AsyncMock(return_value=(envelope, b""))
    return proc


async def _argv_for(monkeypatch) -> list[str]:
    """Invoke ``claude`` (subprocess mocked) and return the argv ``create_subprocess_exec`` was called with."""
    mock_exec = AsyncMock(return_value=_ok_proc())
    monkeypatch.setattr(claude_agent.asyncio, "create_subprocess_exec", mock_exec)
    await claude_agent.invoke_claude(project_slug="p", claude_session_id=uuid4(), prompt="otázka")
    return list(mock_exec.call_args.args)


def _value_after(argv: list[str], flag: str) -> str:
    return argv[argv.index(flag) + 1]


async def test_in_process_build_turn_passes_no_tool_or_permission_flags(monkeypatch) -> None:
    argv = await _argv_for(monkeypatch)
    assert "--permission-mode" not in argv
    assert "--allowedTools" not in argv
    assert "--disallowedTools" not in argv


# ---------------------------------------------------------------------------
# The role permission profile actually reaches the CLI
# ---------------------------------------------------------------------------


async def test_build_turn_passes_the_role_permission_profile(monkeypatch, tmp_path) -> None:
    """``--settings`` is what makes the profile real; without it every deny rule is decoration.

    ``--setting-sources`` loads only ``user`` / ``project`` / ``local``, and Create Project writes the
    profile to ``.claude/agents/<role>/settings.json`` — none of those. The dispatch passed no
    ``--settings``, so build turns ran under the mounted user config's ``defaultMode:
    bypassPermissions``: fully auto-approved, free to ``git push --force``, ``git reset --hard`` and
    rewrite the very charter that forbids it.
    """
    profile = tmp_path / "settings.json"
    profile.write_text('{"permissions":{"deny":["Bash(git push:*)"]}}', encoding="utf-8")

    mock_exec = AsyncMock(return_value=_ok_proc())
    monkeypatch.setattr(claude_agent.asyncio, "create_subprocess_exec", mock_exec)
    await claude_agent.invoke_claude(
        project_slug="p",
        claude_session_id=uuid4(),
        prompt="postav",
        settings_path=profile,
    )
    argv = list(mock_exec.call_args.args)

    assert "--settings" in argv
    assert _value_after(argv, "--settings") == str(profile)


async def test_no_profile_means_no_flag(monkeypatch) -> None:
    """A project founded before CR-V2-018 has no profile on disk — pass no flag rather than a dead path
    (``--settings <missing>`` fails the whole turn). The caller logs the unconstrained dispatch."""
    argv = await _argv_for(monkeypatch)
    assert "--settings" not in argv
