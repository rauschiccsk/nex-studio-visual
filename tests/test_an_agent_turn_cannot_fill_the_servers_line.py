"""DEV-70 — an agent turn that sees this server's Docker cannot fill the server's internet line.

11.10.2026 the Auditor of career-asistent (verification keeps its Docker so it can boot the app it judges; ICCINT-20)
pulled ``mcr.microsoft.com/playwright:v1.50.1-noble`` (2.56 GB) five times and removed it at the end of every round.
Each pull filled ANDROS's line for about twenty minutes and every customer site answered in 4-5 s. Now the cockpit
keeps that image under its own tag and puts the public name back before each round, and a turn outside the sandbox
is started with a profile that forbids pulling and pruning.
"""

from __future__ import annotations

import ast
import inspect
import json
import subprocess
import textwrap
from pathlib import Path

import pytest

from backend.config.settings import settings
from backend.services import host_docker_guard, orchestrator
from tests.test_orchestrator_v2_verifikacia import (
    _ban_deploy_calls,
    _make_version,
    _seed_verifikacia,
    _stub_auditor,
    _stub_smoke,
    _verdict_pass,
)

#: The real function — the suite's ``_isolate_audit_browser`` replaces the module attribute during every test.
REAL_ENSURE = host_docker_guard.ensure_audit_browser

PUBLIC, PIN = settings.audit_browser_image, settings.audit_browser_pin


class _FakeDocker:
    """Image names → ids, and every command a test's Docker was asked to run."""

    def __init__(self, tags: dict[str, str], *, pull_ok: bool = True):
        self.tags, self.pull_ok, self.calls = dict(tags), pull_ok, []

    def __call__(self, *args: str, timeout: int = 60) -> subprocess.CompletedProcess:
        self.calls.append((args, timeout))
        if args[:2] == ("image", "inspect"):
            image_id = self.tags.get(args[-1])
            return subprocess.CompletedProcess(args, 0 if image_id else 1, f"{image_id}\n" if image_id else "", "")
        if args[0] == "tag":
            self.tags[args[2]] = self.tags[args[1]]
            return subprocess.CompletedProcess(args, 0, "", "")
        if args[0] == "pull":
            if self.pull_ok:
                self.tags[args[1]] = "sha256:pulled"
            return subprocess.CompletedProcess(args, 0 if self.pull_ok else 1, "", "")
        raise AssertionError(f"unexpected docker command {args}")

    def rmi(self, name: str) -> None:
        """What ``docker rmi <name>`` does to an image that has another name: only the name goes."""
        self.tags.pop(name)

    @property
    def pulls(self) -> list[tuple]:
        return [args for args, _timeout in self.calls if args[0] == "pull"]


def _docker(monkeypatch, tags, **kw) -> _FakeDocker:
    fake = _FakeDocker(tags, **kw)
    monkeypatch.setattr(host_docker_guard, "_docker", fake)
    return fake


# --- the browser image stays on the server ---------------------------------------------------------------------------


def test_the_auditor_removing_the_public_name_costs_no_download(monkeypatch):
    """The 11.10.2026 round trip — prepared, used, removed by name, prepared again — without a second pull."""
    docker = _docker(monkeypatch, {})

    assert REAL_ENSURE() == host_docker_guard.PULLED
    docker.rmi(PUBLIC)  # the Auditor's cleanup at the end of its round
    assert REAL_ENSURE() == host_docker_guard.RESTORED
    assert REAL_ENSURE() == host_docker_guard.READY

    assert len(docker.pulls) == 1, "the image was downloaded again"
    assert docker.tags[PUBLIC] == docker.tags[PIN]


def test_an_image_pulled_before_this_check_gets_the_cockpits_tag(monkeypatch):
    docker = _docker(monkeypatch, {PUBLIC: "sha256:old"})

    assert REAL_ENSURE() == host_docker_guard.PINNED
    assert docker.tags[PIN] == "sha256:old" and docker.pulls == []


def test_the_one_pull_may_take_its_time_and_a_failure_never_stops_verifikacia(monkeypatch):
    docker = _docker(monkeypatch, {}, pull_ok=False)

    assert REAL_ENSURE() == host_docker_guard.UNAVAILABLE
    assert [timeout for args, timeout in docker.calls if args[0] == "pull"] == [settings.audit_browser_pull_timeout]

    def _broken(*_a, **_k):
        raise OSError("docker: not found")

    monkeypatch.setattr(host_docker_guard, "_docker", _broken)
    assert REAL_ENSURE() == host_docker_guard.UNAVAILABLE


# --- the profile a turn outside the sandbox is started with ----------------------------------------------------------


def test_the_profile_adds_the_docker_denies_outside_the_project(tmp_path):
    project_profile = tmp_path / "proj" / ".claude" / "agents" / "auditor" / "settings.json"
    project_profile.parent.mkdir(parents=True)
    original = {"permissions": {"allow": ["Read"], "deny": ["Bash(git push:*)", "Bash(docker pull:*)"]}}
    project_profile.write_text(json.dumps(original), encoding="utf-8")

    written = host_docker_guard.turn_profile(project_profile, "career-asistent")

    assert written == Path(settings.agent_profile_dir) / "career-asistent-auditor.json"
    profile = json.loads(written.read_text(encoding="utf-8"))
    assert profile["permissions"]["allow"] == ["Read"]
    assert profile["permissions"]["deny"] == [
        "Bash(git push:*)",
        "Bash(docker pull:*)",
        *[rule for rule in host_docker_guard.HOST_DOCKER_DENY if rule != "Bash(docker pull:*)"],
    ]
    assert json.loads(project_profile.read_text(encoding="utf-8")) == original, "the project's own file changed"


def test_the_denies_name_pulling_and_pruning_but_leave_removing_by_name():
    deny = host_docker_guard.HOST_DOCKER_DENY
    for rule in ("Bash(docker pull:*)", "Bash(docker image pull:*)", "Bash(docker system prune:*)"):
        assert rule in deny
    assert "Bash(docker rmi:*)" not in deny, "the Auditor cleans up the images it built"
    assert "Bash(docker rmi -f:*)" in deny, "a forced removal deletes the image under the cockpit's tag too"


# --- verification ----------------------------------------------------------------------------------------------------


async def test_the_image_is_ready_before_the_auditor_and_the_brief_names_it(db_session, monkeypatch):
    version, _project = _make_version(db_session)
    _seed_verifikacia(db_session, version.id)
    order: list[str] = []
    monkeypatch.setattr(host_docker_guard, "ensure_audit_browser", lambda: order.append("ensure") or "ready")
    captured = _stub_auditor(monkeypatch, _verdict_pass())
    stubbed = orchestrator.invoke_agent_with_parse_retry

    async def _auditor(*a, **k):
        order.append("auditor")
        return await stubbed(*a, **k)

    monkeypatch.setattr(orchestrator, "invoke_agent_with_parse_retry", _auditor)
    monkeypatch.setattr(orchestrator, "_commit_release_note", lambda *a, **k: None)
    monkeypatch.setattr(orchestrator, "_git_tag_version", lambda *a, **k: None)
    monkeypatch.setattr(orchestrator, "_push_release_artifacts", lambda *a, **k: None)
    _stub_smoke(monkeypatch)
    _ban_deploy_calls(monkeypatch)

    await orchestrator.run_dispatch(db_session, version.id)

    assert order == ["ensure", "auditor"]
    assert orchestrator._audit_docker_rule("ready") in captured["prompt"]
    assert f"`{PUBLIC}` — na serveri UŽ JE" in captured["prompt"]


@pytest.mark.parametrize("flow", ["new_version", "fast_fix"])
def test_both_briefs_carry_the_rule(db_session, flow):
    version, _project = _make_version(db_session)
    _seed_verifikacia(db_session, version.id, flow_type=flow)

    brief = orchestrator._verifikacia_directive(db_session, version.id, flow_type=flow, browser="ready")
    missing = orchestrator._verifikacia_directive(db_session, version.id, flow_type=flow, browser="unavailable")

    assert "Obrazy NESŤAHUJ (`docker pull`)" in brief and PUBLIC in brief
    assert "na serveri NIE JE pripravený" in missing and PUBLIC not in missing


def test_the_preparation_runs_off_the_main_loop():
    """Docker waits — up to an hour for the one pull — so the preparation runs in a thread (ICCINT-74)."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(orchestrator._prepare_audit_browser)))
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)]
    offloaded = [
        c
        for c in calls
        if getattr(c.func, "id", None) == "run_blocking"
        and c.args
        and getattr(c.args[0], "attr", None) == "ensure_audit_browser"
    ]
    direct = [c for c in calls if getattr(c.func, "attr", None) == "ensure_audit_browser"]
    assert len(offloaded) == 1 and direct == []


def test_the_charter_says_it():
    charter = Path(orchestrator.__file__).resolve().parents[2] / "templates" / "auditor-charter.md"
    assert "**Docker servera (DEV-70)**" in charter.read_text(encoding="utf-8")


def test_the_suite_never_prepares_the_image_on_a_live_docker():
    """The test job runs on ANDROS; ``_isolate_audit_browser`` keeps every test from a real pull."""
    assert host_docker_guard.ensure_audit_browser is not REAL_ENSURE
    assert host_docker_guard.ensure_audit_browser() == host_docker_guard.READY
