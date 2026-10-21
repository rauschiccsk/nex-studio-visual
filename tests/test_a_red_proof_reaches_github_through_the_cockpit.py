"""DEV-173 — the cockpit sends the agent's red proof to GitHub and starts its CI; the agent sends nothing.

The brief told the agent „Bočnú vetvu pošli na GitHub len pre červený dôkaz“ while its permissions forbid ``git push``.
On 13., 17. and 20.10.2026 the NEX Inbox agent was refused six times; on 12.–13.10. its proofs reached GitHub only by
``git -c credential.helper=… push``, a form the ban does not catch. Director 21.10.2026: „Súhlasím“ — the cockpit
takes the sending over, like the main branch since DEV-96, and the agent will keep a read-only token (DEV-179).

These run against REAL git repositories — a bare repository plays GitHub — so every assertion is about a ref that
moved; only the start of CI on GitHub is recorded instead of made.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from backend.services import ci_runs, git_push, orchestrator
from backend.services import create_project_postscaffold as postscaffold

CI = "on:\n  push:\n    branches: [main]\n  workflow_dispatch:\njobs: {}\n"
PUSH_ONLY = "on:\n  push:\n    branches: [main]\njobs: {}\n"


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True).stdout.strip()


def _commit(work: Path, name: str, subject: str) -> str:
    (work / name).write_text(f"{name}\n", encoding="utf-8")
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", subject)
    return _git(work, "rev-parse", "HEAD")


@pytest.fixture
def project(tmp_path, monkeypatch) -> tuple[Path, Path, list]:
    """A project on ``main`` with CI (``ci.yml`` can be started by hand, ``web.yml`` cannot), its origin, and a record
    of every CI the cockpit starts."""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-q", "-b", "main", str(origin)], check=True)
    work = tmp_path / "work"
    work.mkdir()
    _git(work, "init", "-q", "-b", "main")
    _git(work, "config", "user.email", "studio@isnex.eu")
    _git(work, "config", "user.name", "NEX Studio")
    (work / ".github" / "workflows").mkdir(parents=True)
    (work / ".github" / "workflows" / "ci.yml").write_text(CI)
    (work / ".github" / "workflows" / "web.yml").write_text(PUSH_ONLY)
    _commit(work, "README.md", "chore: start")
    _git(work, "remote", "add", "origin", str(origin))
    _git(work, "push", "-q", "origin", "main")
    started: list = []
    monkeypatch.setattr(git_push, "wire_credentials", lambda *, timeout: None)
    monkeypatch.setattr(
        orchestrator, "_start_ci", lambda root, workflow, branch: started.append((workflow, branch)) or None
    )
    return work, origin, started


def _proof(work: Path, branch: str, subject: str = "proof(red): the gate without its check (do not merge)") -> str:
    _git(work, "checkout", "-q", "-b", branch)
    sha = _commit(work, f"{branch.replace('/', '-')}.txt", subject)
    _git(work, "checkout", "-q", "main")
    return sha


async def test_the_cockpit_sends_a_red_proof_and_starts_its_ci(project):
    work, origin, started = project
    sha = _proof(work, "ci/gate-red")

    assert await orchestrator._push_after_task(work) is None

    assert _git(origin, "rev-parse", "refs/heads/ci/gate-red") == sha
    assert started == [("ci.yml", "ci/gate-red")]


async def test_a_side_branch_without_a_proof_stays_on_andros(project):
    work, origin, started = project
    _proof(work, "experiment", subject="feat: trying something")

    assert await orchestrator._push_after_task(work) is None

    assert _git(origin, "branch", "--list", "experiment") == ""
    assert started == []


async def test_a_proof_already_on_github_is_neither_sent_nor_started_again(project):
    work, origin, started = project
    _proof(work, "ci/gate-red")
    await orchestrator._push_after_task(work)

    await orchestrator._push_after_task(work)

    assert started == [("ci.yml", "ci/gate-red")]


async def test_a_proof_that_moved_on_is_sent_and_started_again(project):
    work, origin, started = project
    _proof(work, "ci/gate-red")
    await orchestrator._push_after_task(work)
    _git(work, "checkout", "-q", "ci/gate-red")
    sha = _commit(work, "second.txt", f"{ci_runs.PROOF_MARK} the second variant (do not merge)")
    _git(work, "checkout", "-q", "main")

    await orchestrator._push_after_task(work)

    assert _git(origin, "rev-parse", "refs/heads/ci/gate-red") == sha
    assert started == [("ci.yml", "ci/gate-red"), ("ci.yml", "ci/gate-red")]


async def test_a_proof_that_cannot_be_sent_is_told_to_the_manager(project, monkeypatch):
    work, _origin, started = project
    _proof(work, "ci/gate-red")
    real_push = git_push.push

    def _refused(root, *args, timeout):
        if any("ci/gate-red" in arg for arg in args):
            return subprocess.CompletedProcess(["git", "push", *args], 1, "", "remote: Permission denied")
        return real_push(root, *args, timeout=timeout)

    monkeypatch.setattr(git_push, "push", _refused)

    why = await orchestrator._push_after_task(work)

    assert why == "červený dôkaz na vetve ci/gate-red sa na GitHub nedostal: remote: Permission denied"
    assert started == []


# --- the agent is told what its permissions allow ------------------------------------------------------------


def test_the_brief_no_longer_asks_the_agent_to_send_anything():
    assert orchestrator.PUSH_RULE == (
        "Na GitHub nič neodosielaj — hlavnú vetvu odošle kokpit hneď po tejto úlohe. Červený dôkaz commitni na "
        "bočnú vetvu s predmetom, ktorý začína `proof(red):`, a vráť sa na hlavnú vetvu; po úlohe ju na GitHub "
        "odošle a kontroly na nej spustí kokpit. Jej výsledok nie je výsledok verzie a kokpit ho ukáže ako dôkaz, "
        "nie ako chybu (DEV-105)."
    )


def test_the_charter_says_the_same_as_the_brief():
    text = (postscaffold.NEX_STUDIO_TEMPLATES / "ai-agent-charter.md").read_text(encoding="utf-8")
    flat = " ".join(text.split())

    assert "Bočnú vetvu pošli" not in flat
    assert (
        "Na GitHub nič neodosielaj — hlavnú vetvu po každej hotovej úlohe odošle kokpit a CI na nej beží; tento "
        "výsledok vidí Manažér aj Auditor. Červený dôkaz commitni na bočnú vetvu s predmetom, ktorý začína "
        "`proof(red):`, a vráť sa na hlavnú vetvu — po úlohe ju na GitHub odošle a kontroly na nej spustí kokpit."
    ) in flat


def test_the_ci_is_started_on_the_proof_branch_of_the_projects_own_repository(tmp_path, monkeypatch):
    log = tmp_path / "gh.log"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(f'#!/usr/bin/env bash\necho "$*" >> {log}\n')
    gh.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    work = tmp_path / "work"
    work.mkdir()
    _git(work, "init", "-q", "-b", "main")
    _git(work, "remote", "add", "origin", "https://github.com/rauschiccsk/career-asistent.git")

    assert orchestrator._start_ci(work, "ci.yml", "ci/gate-red") is None
    assert log.read_text().splitlines() == ["workflow run ci.yml --ref ci/gate-red -R rauschiccsk/career-asistent"]
