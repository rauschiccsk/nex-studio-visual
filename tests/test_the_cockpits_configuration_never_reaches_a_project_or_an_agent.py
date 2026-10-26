"""DEV-227 — nothing of the cockpit's configuration reaches a project's processes or an agent's.

26.10.2026, Verifikácia of Author Asistent 0.1.0: the launch check's ``migrate`` failed with „password authentication
failed for user "nexstudiovisual"“ — the cockpit's own database user. The project's compose takes
``DATABASE_URL: ${DATABASE_URL:-}`` and the cockpit ran ``docker compose`` with its own environment, which
:func:`agent_env` passed on minus four tokens only. Measured in the running backend: ``DATABASE_URL``,
``TEST_DATABASE_URL`` and ``SECRET_KEY`` (it signs the cockpit's logins) went to the agent-written
``release_smoke_test.sh``, the launch check, founding, deploys and the agent's terminal — processes that run in the
backend, from where the cockpit's database is reachable. Director: „Súhlasím“.

Pinned: every variable of the cockpit's settings (read from the ``Settings`` model, not listed here) stays out of a
project's environment, and out of an agent's except its own tools; the sandboxes still get what they pass by name;
the spawn sites use these builders and no module that drives a project's Docker copies the backend's environment.
"""

from __future__ import annotations

import asyncio
import inspect
import os
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.config.settings import Settings
from backend.core.agent_env import agent_env, project_env
from backend.services import (
    agent_terminal,
    build_sandbox,
    claude_agent,
    create_project_postscaffold,
    instance_adoption,
    orchestrator,
    remote_instance,
)
from backend.services.poradca import sandbox as poradca_sandbox

#: Every variable of the cockpit's settings — a field added later is guarded without touching this file.
SETTINGS_NAMES = sorted(name.upper() for name in Settings.model_fields)
#: What an agent may carry from them: its own tools, no secret of the cockpit's.
AGENT_MAY = {"CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CLI_PATH", "CLAUDE_CONFIG_DIR", "QDRANT_URL", "OLLAMA_URL"}
#: The ones found leaving on 26.10.2026, named so the failure reads as the incident.
FOUND = {"DATABASE_URL", "TEST_DATABASE_URL", "SECRET_KEY", "PLANE_API_TOKEN", "APP_VERSION"}
SENTINEL = "sentinel-of-the-cockpit-"


@pytest.fixture
def cockpit_environment(monkeypatch):
    """The backend's environment with every setting set, plus a variable nobody has listed yet."""
    for name in SETTINGS_NAMES:
        monkeypatch.setenv(name, SENTINEL + name)
    monkeypatch.setenv("SOMETHING_THE_COCKPIT_ADDS_LATER", SENTINEL + "later")
    monkeypatch.setenv("AGENT_GITHUB_TOKEN", "agent-reads")
    for name in ("LANG", "IS_SANDBOX", "DISABLE_AUTOUPDATER"):
        monkeypatch.setenv(name, "1")


def _cockpits(env: dict[str, str], *, allowed: set[str] = frozenset()) -> list[str]:
    return sorted(name for name, value in env.items() if value.startswith(SENTINEL) and name not in allowed)


def test_a_project_process_carries_nothing_of_the_cockpits_configuration(cockpit_environment):
    env = project_env({"APP_VERSION": "0.1.0", "COMPOSE_PROJECT_NAME": "smoke"})

    assert _cockpits(env) == []
    assert (env["APP_VERSION"], env["COMPOSE_PROJECT_NAME"]) == ("0.1.0", "smoke")  # what the cockpit hands over
    assert env["PATH"] == os.environ["PATH"] and env["HOME"] == os.environ["HOME"]


def test_an_agent_carries_only_its_own_tools(cockpit_environment):
    env = agent_env({"TERM": "xterm-256color"})

    assert _cockpits(env, allowed=AGENT_MAY) == []
    assert AGENT_MAY <= env.keys()
    assert (env["GITHUB_TOKEN"], env["TERM"]) == ("agent-reads", "xterm-256color")


def test_neither_carries_what_left_on_26_10(cockpit_environment):
    assert FOUND & project_env().keys() == set()
    assert FOUND & agent_env().keys() == set()


def test_the_sandboxes_still_get_what_they_pass_by_name(cockpit_environment):
    """The build sandbox and Poradca's copy these names from the agent's environment — none may go missing."""
    env = agent_env()

    passed = {*build_sandbox._PASSTHROUGH_ENV, *poradca_sandbox._PASSTHROUGH_ENV}
    assert sorted(passed - env.keys()) == []


def test_the_agent_written_smoke_script_runs_with_a_projects_environment(cockpit_environment, monkeypatch, tmp_path):
    proc = MagicMock()
    proc.returncode = 0
    proc.communicate = AsyncMock(return_value=(b"ok", b""))
    mock_exec = AsyncMock(return_value=proc)
    monkeypatch.setattr(orchestrator.asyncio, "create_subprocess_exec", mock_exec)

    asyncio.run(orchestrator._run_acceptance_script(tmp_path / "release_smoke_test.sh", {"COMPOSE_PROJECT_NAME": "x"}))

    env = mock_exec.call_args.kwargs["env"]
    assert _cockpits(env) == []
    assert env["COMPOSE_PROJECT_NAME"] == "x"


def test_the_build_turn_and_the_agents_terminal_get_only_the_agents_tools(cockpit_environment, monkeypatch, tmp_path):
    proc = MagicMock()
    proc.returncode = 0
    proc.communicate = AsyncMock(return_value=(b'{"result": "ok"}', b""))
    mock_exec = AsyncMock(return_value=proc)
    monkeypatch.setattr(claude_agent.asyncio, "create_subprocess_exec", mock_exec)
    asyncio.run(claude_agent.invoke_claude(project_slug="p", claude_session_id=uuid.uuid4(), prompt="x"))
    captured: dict = {}
    monkeypatch.setattr(
        agent_terminal.ptyprocess.PtyProcess,
        "spawn",
        staticmethod(lambda argv, **kwargs: captured.update(kwargs) or MagicMock()),
    )
    spec = tmp_path / "CLAUDE.md"
    spec.write_text("charter", encoding="utf-8")
    agent_terminal._spawn_pty(spec_path=spec, project_root=tmp_path, claude_session_id=uuid.uuid4(), resume=False)

    assert _cockpits(mock_exec.call_args.kwargs["env"], allowed=AGENT_MAY) == []
    assert _cockpits(captured["env"], allowed=AGENT_MAY) == []


@pytest.mark.parametrize(
    "module", [orchestrator, create_project_postscaffold, instance_adoption, remote_instance], ids=lambda m: m.__name__
)
def test_no_module_that_drives_a_projects_docker_copies_the_backends_environment(module):
    """A sweep, so a site added later cannot bring the hole back: these modules start a project's containers and
    scripts, so they build environments with :func:`project_env` — never from ``os.environ``, never the agent's.
    Comments are stripped: the explanation quotes the patterns it forbids."""
    code = "\n".join(line.split("#", 1)[0] for line in inspect.getsource(module).splitlines())

    found = [p for p in ("dict(os.environ", "{**os.environ", "os.environ.copy(", "agent_env(") if p in code]
    assert found == [], f"{module.__name__}: {found} — use project_env()"
