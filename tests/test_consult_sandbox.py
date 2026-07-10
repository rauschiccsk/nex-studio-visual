"""OS-level read-only consult sidecar — the mounts ARE the guarantee (konzultacia-sidecar-sandbox.md Part 3).

The CONSULT turn (read-only) must run inside an isolated ``docker run --rm`` sibling where the project is
KERNEL-enforced ``:ro`` and the host is unreachable. These unit tests assert the EXACT ``docker`` argv
composition (never running a real container — ``asyncio.create_subprocess_exec`` is mocked and we inspect
the argv it built), that a BUILD turn never touches the sidecar, and that the sidecar's json envelope parses
to the SAME ``(text, usage, structured_output)`` tuple as the in-process turn.

Live sandbox acceptance (a real container: answers, kernel-refuses a raw write, no docker.sock/customers/
uat inside, container reaped) is Dedo's before the v3 deploy — NOT the Implementer's.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from backend.services import claude_agent, consult_sandbox

_READ_ONLY = ["Read", "Grep", "Glob"]


@pytest.fixture(autouse=True)
def _pin_projects_root(monkeypatch):
    """These tests assert the FIXED in-container ``/opt/projects`` → host ``/opt/projects-v3`` sidecar path
    translation, so ``PROJECTS_ROOT`` must be the real ``/opt/projects`` here. The suite-wide
    ``_isolate_projects_root`` default (a temp dir, so scaffold WRITES never pollute the real workspace) would
    otherwise break the translation — but the consult sidecar tests do NO scaffolding (docker is mocked), so
    pinning the real prefix is correct and leak-free."""
    monkeypatch.setattr(claude_agent, "PROJECTS_ROOT", Path("/opt/projects"))


def _ok_proc(envelope: dict | None = None) -> MagicMock:
    """A subprocess mock whose ``communicate()`` returns a valid ``--output-format json`` envelope, exit 0."""
    proc = MagicMock()
    proc.returncode = 0
    body = envelope if envelope is not None else {"result": "ok", "usage": {"input_tokens": 1, "output_tokens": 1}}
    proc.communicate = AsyncMock(return_value=(json.dumps(body).encode("utf-8"), b""))
    return proc


async def _sidecar_argv(monkeypatch, **kwargs) -> list[str]:
    """Run ``run_consult_in_sandbox`` (docker subprocess mocked) and return the argv it launched docker with."""
    mock_exec = AsyncMock(return_value=_ok_proc())
    monkeypatch.setattr(consult_sandbox.asyncio, "create_subprocess_exec", mock_exec)
    await consult_sandbox.run_consult_in_sandbox(
        project_slug=kwargs.pop("project_slug", "p"),
        claude_session_id=kwargs.pop("claude_session_id", uuid4()),
        prompt=kwargs.pop("prompt", "otázka"),
        allowed_tools=kwargs.pop("allowed_tools", _READ_ONLY),
        **kwargs,
    )
    return list(mock_exec.call_args.args)


def _value_after(argv: list[str], flag: str) -> str:
    return argv[argv.index(flag) + 1]


# ---------------------------------------------------------------------------
# The docker argv — the POSITIVE half of the guarantee (mounts that MUST be present)
# ---------------------------------------------------------------------------


async def test_sidecar_launches_ephemeral_unprivileged_container(monkeypatch) -> None:
    argv = await _sidecar_argv(monkeypatch)
    assert argv[:3] == ["docker", "run", "--rm"]  # ephemeral — never leaks after exit
    assert _value_after(argv, "--user") == "andros"  # unprivileged, never root
    assert _value_after(argv, "--entrypoint") == "claude"
    assert _value_after(argv, "--name").startswith("nex-consult-")  # named so a hung one can be reaped
    # image = the running backend image tag (dedicated setting; default v3 backend image)
    assert consult_sandbox.settings.consult_sandbox_image in argv


async def test_sidecar_mounts_project_read_only_at_host_path(monkeypatch) -> None:
    argv = await _sidecar_argv(monkeypatch, project_slug="acme")
    # -v <HOST_PROJECT_PATH>:/opt/projects/<slug>:ro — the HARD guarantee. Host path is translated from the
    # backend's in-container /opt/projects view to the daemon-resolvable /opt/projects-v3 host path.
    binds = [argv[i + 1] for i, a in enumerate(argv) if a == "-v"]
    assert "/opt/projects-v3/acme:/opt/projects/acme:ro" in binds
    assert _value_after(argv, "-w") == "/opt/projects/acme"  # cwd = project, as in-process


async def test_sidecar_mounts_auth_writable_no_tmpfs(monkeypatch) -> None:
    argv = await _sidecar_argv(monkeypatch)
    binds = [argv[i + 1] for i, a in enumerate(argv) if a == "-v"]
    # MAX-subscription OAuth auth/config dir mounted READ-WRITE so claude can persist + --resume its own
    # session state (exactly as the in-container build turns already do). CLAUDE_CONFIG_DIR points at it.
    assert "/home/andros/.claude:/home/andros/.claude" in binds
    assert "/home/andros/.claude:/home/andros/.claude:ro" not in binds  # NOT read-only anymore
    # The dead --tmpfs scratch (only existed for the :ro scenario) is gone — claude writes ~/.claude directly.
    assert "--tmpfs" not in argv
    assert "/home/andros/.claude-scratch" not in " ".join(argv)
    assert _value_after(argv, "-e") == "CLAUDE_CONFIG_DIR=/home/andros/.claude"


async def test_auth_dir_writable_so_resume_can_persist(monkeypatch) -> None:
    # Regression guard for the LIVE bug (v3 2026-07-08): the real consult runs `claude --resume`, which
    # WRITES session state into CLAUDE_CONFIG_DIR = the mounted ~/.claude. A :ro auth mount kernel-refused
    # that write (EROFS) → the turn failed. The auth bind MUST be writable (no :ro suffix).
    argv = await _sidecar_argv(monkeypatch)
    binds = [argv[i + 1] for i, a in enumerate(argv) if a == "-v"]
    auth_bind = next(b for b in binds if b.startswith("/home/andros/.claude:"))
    assert not auth_bind.endswith(":ro"), f"auth bind must be writable for --resume, got {auth_bind!r}"


async def test_sidecar_reuses_readonly_tool_flags(monkeypatch) -> None:
    # The per-turn claude flags come from the SAME build_claude_argv the in-process turn uses — so the sidecar
    # carries the exclusive deny-by-default read-only profile verbatim, and non-streaming json (never stream).
    argv = await _sidecar_argv(monkeypatch)
    assert _value_after(argv, "--output-format") == "json"
    assert "--verbose" not in argv  # sidecar is non-streaming
    assert _value_after(argv, "--allowedTools") == "Read,Grep,Glob"
    assert _value_after(argv, "--permission-mode") == "default"
    deny = _value_after(argv, "--disallowedTools").split(",")
    for tool in ("Bash", "Write", "Edit", "Agent", "Task", "Workflow", "Skill", "ToolSearch"):
        assert tool in deny
    assert argv[-1] == "otázka"  # positional prompt last


# ---------------------------------------------------------------------------
# The docker argv — the NEGATIVE half of the guarantee (what must NEVER be mounted)
# ---------------------------------------------------------------------------


async def test_sidecar_omits_all_forbidden_mounts(monkeypatch) -> None:
    joined = " ".join(await _sidecar_argv(monkeypatch))
    # NO docker.sock (no sibling-launch power), NO customers / uat / credentials / infra / knowledge — the
    # sidecar sees ONLY the one project (:ro) + auth (:ro).
    for forbidden in (
        "docker.sock",
        "/opt/customers",
        "/opt/uat",
        "/opt/data/nex-studio/credentials",
        "/opt/infra",
        "/home/icc/knowledge",
    ):
        assert forbidden not in joined


# ---------------------------------------------------------------------------
# Host-path translation
# ---------------------------------------------------------------------------


def test_host_path_translation() -> None:
    # backend /opt/projects view → daemon-resolvable /opt/projects-v3 host path; customer projects identity.
    assert consult_sandbox._host_project_path("/opt/projects/p") == "/opt/projects-v3/p"
    assert consult_sandbox._host_project_path("/opt/customers/acme") == "/opt/customers/acme"
    with pytest.raises(consult_sandbox.SidecarUnavailable):
        consult_sandbox._host_project_path("/somewhere/else")


# ---------------------------------------------------------------------------
# Routing: a BUILD turn NEVER touches the sidecar; a CONSULT turn does
# ---------------------------------------------------------------------------


async def test_build_turn_never_calls_sandbox(monkeypatch) -> None:
    # allowed_tools=None is a build turn — even with sandbox=True it must run in-process (claude argv), never
    # the docker sidecar. The sandbox requires an active read-only profile.
    sandbox_spy = AsyncMock()
    monkeypatch.setattr(consult_sandbox, "run_consult_in_sandbox", sandbox_spy)
    mock_exec = AsyncMock(return_value=_ok_proc())
    monkeypatch.setattr(claude_agent.asyncio, "create_subprocess_exec", mock_exec)
    await claude_agent.invoke_claude(
        project_slug="p", claude_session_id=uuid4(), prompt="build", allowed_tools=None, sandbox=True
    )
    sandbox_spy.assert_not_called()
    assert mock_exec.call_args.args[0] == "claude"  # in-process, not "docker"


async def test_consult_turn_routes_to_sandbox(monkeypatch) -> None:
    # A consult turn (allowed_tools set + sandbox=True, sandbox enabled) runs THROUGH the sidecar — the
    # in-process subprocess is never launched.
    sandbox_spy = AsyncMock(return_value=("answer", None, None))
    monkeypatch.setattr(consult_sandbox, "run_consult_in_sandbox", sandbox_spy)
    monkeypatch.setattr(consult_sandbox, "sandbox_enabled", lambda: True)
    mock_exec = AsyncMock(return_value=_ok_proc())
    monkeypatch.setattr(claude_agent.asyncio, "create_subprocess_exec", mock_exec)
    text, _usage, _structured = await claude_agent.invoke_claude(
        project_slug="p", claude_session_id=uuid4(), prompt="otázka", allowed_tools=_READ_ONLY, sandbox=True
    )
    sandbox_spy.assert_awaited_once()
    mock_exec.assert_not_called()  # the in-process subprocess is bypassed
    assert text == "answer"


async def test_consult_falls_back_in_process_when_sidecar_unavailable(monkeypatch) -> None:
    # Sidecar unavailable (no docker) → DEGRADE to the in-process read-only turn (still tool-profile
    # read-only) — an honest, logged fallback, never a hard consult failure.
    monkeypatch.setattr(consult_sandbox, "sandbox_enabled", lambda: True)
    monkeypatch.setattr(
        consult_sandbox,
        "run_consult_in_sandbox",
        AsyncMock(side_effect=consult_sandbox.SidecarUnavailable("no docker")),
    )
    mock_exec = AsyncMock(return_value=_ok_proc())
    monkeypatch.setattr(claude_agent.asyncio, "create_subprocess_exec", mock_exec)
    text, _usage, _structured = await claude_agent.invoke_claude(
        project_slug="p", claude_session_id=uuid4(), prompt="otázka", allowed_tools=_READ_ONLY, sandbox=True
    )
    # Fell back to the in-process claude subprocess, still carrying the read-only tool flags.
    in_process_argv = list(mock_exec.call_args.args)
    assert in_process_argv[0] == "claude"
    assert "--allowedTools" in in_process_argv
    assert text == "ok"


async def test_sandbox_disabled_runs_in_process(monkeypatch) -> None:
    # CONSULT_SANDBOX off → consult runs in-process (still read-only) without ever invoking the sidecar.
    monkeypatch.setattr(consult_sandbox, "sandbox_enabled", lambda: False)
    sandbox_spy = AsyncMock()
    monkeypatch.setattr(consult_sandbox, "run_consult_in_sandbox", sandbox_spy)
    mock_exec = AsyncMock(return_value=_ok_proc())
    monkeypatch.setattr(claude_agent.asyncio, "create_subprocess_exec", mock_exec)
    await claude_agent.invoke_claude(
        project_slug="p", claude_session_id=uuid4(), prompt="otázka", allowed_tools=_READ_ONLY, sandbox=True
    )
    sandbox_spy.assert_not_called()
    assert mock_exec.call_args.args[0] == "claude"


def test_sandbox_enabled_env(monkeypatch) -> None:
    monkeypatch.delenv("CONSULT_SANDBOX", raising=False)
    assert consult_sandbox.sandbox_enabled() is True  # default ON in prod
    for off in ("0", "false", "no", "off"):
        monkeypatch.setenv("CONSULT_SANDBOX", off)
        assert consult_sandbox.sandbox_enabled() is False
    monkeypatch.setenv("CONSULT_SANDBOX", "1")
    assert consult_sandbox.sandbox_enabled() is True


# ---------------------------------------------------------------------------
# Envelope parse → the SAME (text, usage, structured_output) tuple as the in-process turn
# ---------------------------------------------------------------------------


async def test_sidecar_envelope_parse_matches_in_process(monkeypatch) -> None:
    envelope = {
        "result": "  odpoveď  ",
        "usage": {"input_tokens": 42, "output_tokens": 7},
        "model": "claude-opus-4-8",
        "structured_output": {"stage": "done", "next_action": "rest"},
    }
    proc = _ok_proc(envelope)
    monkeypatch.setattr(consult_sandbox.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc))
    text, usage, structured = await consult_sandbox.run_consult_in_sandbox(
        project_slug="p", claude_session_id=uuid4(), prompt="otázka", allowed_tools=_READ_ONLY
    )
    # Identical to what the in-process json path returns for the same envelope (claude_agent helpers).
    assert text == "odpoveď"  # result stripped
    assert usage == claude_agent._usage_from(envelope)
    assert usage == claude_agent.UsageMetadata(input_tokens=42, output_tokens=7, model="claude-opus-4-8")
    assert structured == {"stage": "done", "next_action": "rest"}


async def test_sidecar_claude_failure_is_claude_error_not_unavailable(monkeypatch) -> None:
    # claude ran inside a HEALTHY sidecar and exited non-zero → a plain ClaudeAgentError (retried/handled
    # like the in-process turn), NOT SidecarUnavailable (which would silently degrade the guarantee).
    proc = MagicMock()
    proc.returncode = 1
    proc.communicate = AsyncMock(return_value=(b"", b"claude: some model error"))
    monkeypatch.setattr(consult_sandbox.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc))
    with pytest.raises(claude_agent.ClaudeAgentError) as exc:
        await consult_sandbox.run_consult_in_sandbox(
            project_slug="p", claude_session_id=uuid4(), prompt="otázka", allowed_tools=_READ_ONLY
        )
    assert not isinstance(exc.value, consult_sandbox.SidecarUnavailable)


async def test_sidecar_daemon_error_is_unavailable(monkeypatch) -> None:
    # A docker-daemon/infra failure (claude never ran) → SidecarUnavailable so the caller degrades honestly.
    proc = MagicMock()
    proc.returncode = 125
    proc.communicate = AsyncMock(
        return_value=(b"", b"Cannot connect to the Docker daemon at unix:///var/run/docker.sock")
    )
    monkeypatch.setattr(consult_sandbox.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc))
    with pytest.raises(consult_sandbox.SidecarUnavailable):
        await consult_sandbox.run_consult_in_sandbox(
            project_slug="p", claude_session_id=uuid4(), prompt="otázka", allowed_tools=_READ_ONLY
        )


# ---------------------------------------------------------------------------
# Fix 1 — a traversal slug must RAISE at the mount-composition boundary (never broaden the :ro mount)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_slug",
    ["..", "../other", "../../etc", "/", "", ".", "a/b", "a/../../etc", "foo/..", "/opt", "-leading"],
)
def test_traversal_slug_raises_before_composing_bind(bad_slug) -> None:
    # An unvalidated `..` slug composes the -v SOURCE /opt/projects-v3/.. → docker would mount ALL of /opt
    # :ro (every customer/uat/infra/project) into the sidecar. The slug MUST be rejected at the
    # mount-composition boundary, BEFORE any -v is composed.
    with pytest.raises(consult_sandbox.SidecarUnavailable):
        consult_sandbox.build_sidecar_argv(
            project_slug=bad_slug,
            container_name="nex-consult-test",
            claude_argv=["claude", "-p", "otázka"],
        )


async def test_traversal_slug_raises_through_run_entrypoint(monkeypatch) -> None:
    # The guard holds through the public run entrypoint too — docker is never even launched for a bad slug.
    mock_exec = AsyncMock(return_value=_ok_proc())
    monkeypatch.setattr(consult_sandbox.asyncio, "create_subprocess_exec", mock_exec)
    with pytest.raises(consult_sandbox.SidecarUnavailable):
        await consult_sandbox.run_consult_in_sandbox(
            project_slug="..", claude_session_id=uuid4(), prompt="otázka", allowed_tools=_READ_ONLY
        )
    mock_exec.assert_not_called()  # never launched docker for a traversal slug


def test_good_slug_bind_source_realpath_contained() -> None:
    # The composed project -v SOURCE must RESOLVE (realpath, symlinks followed) strictly UNDER the intended
    # host prefix — assert on realpath containment, not the literal-string absence a `..` slug masks.
    argv = consult_sandbox.build_sidecar_argv(
        project_slug="acme", container_name="nex-consult-test", claude_argv=["claude", "-p", "q"]
    )
    binds = [argv[i + 1] for i, a in enumerate(argv) if a == "-v"]
    # bind format is SOURCE:DEST:ro (paths carry no colon) → SOURCE is the first field
    project_source = next(b.split(":")[0] for b in binds if b.endswith(":ro") and "/projects" in b)
    real = os.path.realpath(project_source)
    assert real == os.path.realpath("/opt/projects-v3/acme")
    assert real.startswith(os.path.realpath("/opt/projects-v3") + os.sep)


def test_containment_assert_rejects_out_of_prefix_source() -> None:
    # Belt-and-suspenders: even if a future prefix change / symlink composed a source that escaped the
    # project roots, the realpath containment assertion refuses it (rather than silently broadening the mount).
    with pytest.raises(consult_sandbox.SidecarUnavailable):
        consult_sandbox._assert_host_source_contained("/opt/projects-v3/../secret")  # resolves to /opt/secret
    with pytest.raises(consult_sandbox.SidecarUnavailable):
        consult_sandbox._assert_host_source_contained("/opt")  # the bare parent, not a project dir
    # a legitimately-contained source passes
    consult_sandbox._assert_host_source_contained("/opt/projects-v3/acme")
    consult_sandbox._assert_host_source_contained("/opt/customers/acme")


# ---------------------------------------------------------------------------
# Fix 3 — the container is reaped on ANY error path, without double-reaping the clean exit
# ---------------------------------------------------------------------------


async def test_unexpected_error_mid_run_reaps_container(monkeypatch) -> None:
    # An UNEXPECTED exception (not Timeout/Cancelled) mid-run must STILL reap the container — --rm only
    # covers a clean exit; without this the sidecar leaks until claude self-exits.
    proc = MagicMock()
    proc.communicate = AsyncMock(side_effect=RuntimeError("boom"))
    monkeypatch.setattr(consult_sandbox.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc))
    reap_spy = AsyncMock()
    kill_spy = AsyncMock()
    monkeypatch.setattr(consult_sandbox, "_reap_container", reap_spy)
    monkeypatch.setattr(consult_sandbox, "_kill_process_tree", kill_spy)
    with pytest.raises(RuntimeError):
        await consult_sandbox.run_consult_in_sandbox(
            project_slug="p", claude_session_id=uuid4(), prompt="otázka", allowed_tools=_READ_ONLY
        )
    reap_spy.assert_awaited_once()
    kill_spy.assert_awaited_once()


async def test_clean_exit_does_not_double_reap(monkeypatch) -> None:
    # --rm reaps a clean exit; we must NOT double-reap it — only abnormal paths call _reap_container.
    reap_spy = AsyncMock()
    monkeypatch.setattr(consult_sandbox, "_reap_container", reap_spy)
    monkeypatch.setattr(consult_sandbox.asyncio, "create_subprocess_exec", AsyncMock(return_value=_ok_proc()))
    await consult_sandbox.run_consult_in_sandbox(
        project_slug="p", claude_session_id=uuid4(), prompt="otázka", allowed_tools=_READ_ONLY
    )
    reap_spy.assert_not_awaited()
