"""DEV-179 — the agent gets a GitHub token that can only read; the cockpit's own token never reaches it.

The build sandbox and every other process the agent controls inherited ``GITHUB_TOKEN``/``GH_TOKEN`` — the cockpit's
token, which reaches the account's 28 repositories and administers the cockpit's own. The ban on ``git push`` in the
agent's permissions caught one shape of the command only: 9.–13.10.2026 the NEX Inbox agent pushed 38 times with
``git -c credential.helper=… push``. Since DEV-173 the cockpit sends everything, so the agent needs to read only.
Director 21.10.2026: „Súhlasím“. Under the same two names the agent now gets ``AGENT_GITHUB_TOKEN`` — a token the
Director created to read — or no GitHub token at all.
"""

from __future__ import annotations

import pytest

from backend.core import agent_env as agent_env_module
from backend.core.agent_env import agent_env
from backend.services import build_sandbox

COCKPIT = "cockpit-token-that-writes"
READER = "agent-token-that-reads"


@pytest.fixture
def tokens(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", COCKPIT)
    monkeypatch.setenv("GH_TOKEN", COCKPIT)


def test_the_agent_reads_github_with_the_token_made_for_reading(tokens, monkeypatch):
    monkeypatch.setenv("AGENT_GITHUB_TOKEN", READER)

    env = agent_env()

    assert (env["GITHUB_TOKEN"], env["GH_TOKEN"]) == (READER, READER)
    assert "AGENT_GITHUB_TOKEN" not in env
    assert COCKPIT not in env.values()


def test_without_a_token_for_reading_the_agent_gets_no_github_token_at_all(tokens, monkeypatch):
    monkeypatch.delenv("AGENT_GITHUB_TOKEN", raising=False)

    env = agent_env()

    assert {"GITHUB_TOKEN", "GH_TOKEN", "AGENT_GITHUB_TOKEN"} & set(env) == set()
    assert COCKPIT not in env.values()


@pytest.mark.parametrize("reading", [READER, None], ids=["with-a-reading-token", "without-one"])
def test_a_caller_cannot_hand_the_agent_the_cockpits_token_by_the_back_door(tokens, monkeypatch, reading):
    if reading:
        monkeypatch.setenv("AGENT_GITHUB_TOKEN", reading)
    else:
        monkeypatch.delenv("AGENT_GITHUB_TOKEN", raising=False)

    env = agent_env({"APP_VERSION": "1.1.0", "GITHUB_TOKEN": COCKPIT, "GH_TOKEN": COCKPIT})

    assert env["APP_VERSION"] == "1.1.0"
    assert (env.get("GITHUB_TOKEN"), env.get("GH_TOKEN")) == (reading, reading)


def test_the_sandbox_takes_the_github_names_from_the_agents_environment_not_the_cockpits():
    """The build sandbox passes GitHub tokens BY NAME (``-e GITHUB_TOKEN``): docker copies the value from the
    environment of the process that runs it — the agent's (:func:`agent_env`), so the reading token."""
    assert {"GITHUB_TOKEN", "GH_TOKEN"} <= set(build_sandbox._PASSTHROUGH_ENV)
    assert agent_env_module.AGENT_GITHUB_TOKEN not in build_sandbox._PASSTHROUGH_ENV


def test_a_sandbox_without_a_token_for_reading_says_so_in_its_readiness(monkeypatch):
    monkeypatch.delenv("AGENT_GITHUB_TOKEN", raising=False)
    monkeypatch.setattr(build_sandbox, "sandbox_enabled", lambda: True)
    monkeypatch.setattr(build_sandbox.shutil, "which", lambda name: "/usr/bin/docker")
    monkeypatch.setattr(
        build_sandbox.subprocess,
        "run",
        lambda *a, **k: build_sandbox.subprocess.CompletedProcess(a, 0, "sha256:x", ""),
    )

    problems = build_sandbox.preflight().problems

    assert build_sandbox.NO_READING_TOKEN in problems
