"""DEV-255 — the launch check judges only its own run: the stack is taken down with every profile, and what an earlier
run left is cleared before ``up``.

29.10.2026 NEX Knowledge 0.1.0 stopped on „Kontrola po oprave neprešla — zlyhal kontajner nex-manager-fake (exit 137)“.
The check never started that service: it is in profile ``smoke``, started only by the release acceptance. Its container
from 28.10.2026 20:26 — stopped by the acceptance itself — had stayed in ``nex-knowledge-smoke``, because the stack was
taken down with a bare ``down -v``, and every later check read it as its own failure. The agent could not fix it.
"""

from __future__ import annotations

import pytest

from backend.services import orchestrator
from backend.tests.test_acceptance_smoke import _make_project, _StepRecorder

DOWN = ["--profile", "*", "down", "-v", "--remove-orphans"]


@pytest.mark.asyncio
async def test_the_stack_is_cleared_before_up_and_taken_down_after_with_every_profile(monkeypatch, tmp_path):
    monkeypatch.setattr(orchestrator.claude_agent, "PROJECTS_ROOT", tmp_path)
    _make_project(tmp_path, "nk")
    rec = _StepRecorder({"up": (1, "build error")})
    monkeypatch.setattr(orchestrator, "_compose_smoke_step", rec)

    await orchestrator._run_release_smoke("nk", "v0.1.0", (0, 0))

    compose = [cmd for cmd in rec.calls if cmd[:2] == ["docker", "compose"]]
    downs = [cmd for cmd in compose if cmd[-len(DOWN) :] == DOWN]
    assert compose[0][-len(DOWN) :] == DOWN and "up" in compose[1] and compose[-1][-len(DOWN) :] == DOWN
    assert len(downs) == 2 and downs[0] == downs[1]  # the same project, files and every profile — before and after
    assert not [cmd for cmd in compose if "down" in cmd and cmd not in downs]  # no bare ``down`` left anywhere


@pytest.mark.asyncio
async def test_the_launch_check_after_a_fix_clears_too(monkeypatch, tmp_path):
    monkeypatch.setattr(orchestrator.claude_agent, "PROJECTS_ROOT", tmp_path)
    _make_project(tmp_path, "nk")
    rec = _StepRecorder({"up": (1, "build error")})
    monkeypatch.setattr(orchestrator, "_compose_smoke_step", rec)

    await orchestrator._app_starts_after_fix("nk")

    compose = [cmd for cmd in rec.calls if cmd[:2] == ["docker", "compose"]]
    assert compose[0][-len(DOWN) :] == DOWN and "up" in compose[1] and compose[-1][-len(DOWN) :] == DOWN
