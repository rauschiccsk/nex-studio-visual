"""DEV-47 — a change of the agent's rules reaches the next new agent session, in every project.

The charter reaches the agent once per session: the first turn appends it (``--append-system-prompt``), the
following turns resume. The project's copy of the rules was refreshed only when a build started and never in an
adopted project, so a cockpit release that changed the rules reached a running build only with its next version
and NEX Inbox / NEX Manager not at all — measured 10.10.2026: all seven projects carried the 02.09.2026 charter,
none of the rules released on 09.10.2026.

Director 10.10.2026: „Áno, založ tiket do DEV“ → „Áno, začni DEV-47“.

DEV-48: when Claude Code has lost a session, the cockpit starts a replacement under the same id (ICCINT-110). That
replacement is a new session too, but the resumed turn that started it carried no charter, so the agent ran the
rest of its session without any rules. Director 10.10.2026: „Áno, založ tiket do DEV“ → „Áno, začni DEV-48“.

These tests drive the real :func:`claude_agent._invoke_once` up to the CLI: only the process that would run
``claude`` is replaced, so what is asserted is the argv the agent would get.
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest

from backend.services import claude_agent, orchestrator
from backend.services import create_project_postscaffold as cps

SLUG = "projekt"


def _current_charter(role: str) -> str:
    base = (cps.NEX_STUDIO_TEMPLATES / "agent-shared-base.md").read_text(encoding="utf-8").rstrip()
    return base + "\n\n---\n\n" + (cps.NEX_STUDIO_TEMPLATES / cps._V2_AGENTS[role][0]).read_text(encoding="utf-8")


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A project the cockpit provisioned, whose rules then went stale (an older cockpit wrote them)."""

    def make(*, adopted: bool) -> Path:
        monkeypatch.setattr(claude_agent, "PROJECTS_ROOT", tmp_path)
        root = tmp_path / SLUG
        (root / ".claude" / "agents" / "ai-agent").mkdir(parents=True)
        (root / ".claude" / "agents" / "ai-agent" / "CLAUDE.md").write_text("vlastné pravidlá projektu", "utf-8")
        cps.provision_v2_agent_charters(root, SLUG, "Projekt", adopted=adopted)
        for role in cps._V2_AGENTS:
            (root / ".claude" / "agents" / role / "CLAUDE.md").write_text(f"stará charta {role}", encoding="utf-8")
        stale_skill = sorted(cps.cockpit_skills())[0]
        (root / ".claude" / "skills" / stale_skill / cps.SKILL_FILE).write_text("stará zručnosť", encoding="utf-8")
        return root

    return make


@pytest.fixture
def turns(monkeypatch):
    """The argv of every turn that would have run; nothing runs."""
    seen: list[list[str]] = []

    async def _no_cli(args, **_kwargs):
        seen.append(args)
        return "hotovo", None, None

    monkeypatch.setattr(claude_agent, "_run_turn", _no_cli)
    return seen


def _charter_given(argv: list[str]) -> str | None:
    return argv[argv.index("--append-system-prompt") + 1] if "--append-system-prompt" in argv else None


async def _turn(root: Path, *, new_session: bool, role: str = "ai-agent") -> None:
    """A turn the way the orchestrator sends it: always with its role's charter, ``resume`` on a later turn."""
    await claude_agent._invoke_once(
        project_slug=SLUG,
        claude_session_id=uuid4(),
        prompt="pokračuj",
        charter_path=root / ".claude" / "agents" / role / "CLAUDE.md",
        resume=not new_session,
    )


async def test_a_new_session_gets_the_current_rules(project, turns):
    root = project(adopted=False)

    await _turn(root, new_session=True)

    assert _charter_given(turns[-1]) == _current_charter("ai-agent")
    skills = {p.parent.name: p.read_text(encoding="utf-8") for p in (root / ".claude" / "skills").glob("*/SKILL.md")}
    assert skills == cps.cockpit_skills(), "zručnosti v projekte ostali staré"


async def test_an_adopted_project_gets_the_current_rules_and_keeps_its_own_aside(project, turns):
    root = project(adopted=True)

    await _turn(root, new_session=True, role="auditor")

    assert _charter_given(turns[-1]) == _current_charter("auditor")
    assert (root / ".claude" / "agents" / "ai-agent" / "CLAUDE.md").read_text(encoding="utf-8") == _current_charter(
        "ai-agent"
    )
    kept = root / ".claude" / "agents" / "ai-agent" / "CLAUDE.md.pre-nex-studio"
    assert kept.read_text(encoding="utf-8") == "vlastné pravidlá projektu", "pôvodné pravidlá z prevzatia zmizli"


async def test_a_resumed_session_is_left_alone(project, turns):
    root = project(adopted=False)

    await _turn(root, new_session=False)

    assert "--resume" in turns[-1] and _charter_given(turns[-1]) is None
    charter = (root / ".claude" / "agents" / "ai-agent" / "CLAUDE.md").read_text(encoding="utf-8")
    assert charter == "stará charta ai-agent", "pokračujúce sedenie prepisovalo pravidlá, ktoré už nečíta"


async def test_a_replacement_for_a_lost_session_gets_the_current_rules(project, monkeypatch):
    root = project(adopted=False)
    seen: list[list[str]] = []

    async def _session_lost_once(args, **_kwargs):
        seen.append(args)
        if len(seen) == 1:
            raise claude_agent.ClaudeAgentError("No conversation found with session ID: 7d1e…")
        return "hotovo", None, None

    monkeypatch.setattr(claude_agent, "_run_turn", _session_lost_once)
    await claude_agent.invoke_claude(
        project_slug=SLUG,
        claude_session_id=uuid4(),
        prompt="pokračuj",
        charter_path=root / ".claude" / "agents" / "ai-agent" / "CLAUDE.md",
        resume=True,
    )

    resumed, replacement = seen
    assert "--resume" in resumed and _charter_given(resumed) is None
    assert "--session-id" in replacement and "--resume" not in replacement
    assert _charter_given(replacement) == _current_charter("ai-agent"), "náhradné sedenie beží bez pravidiel"


async def test_a_turn_without_a_known_charter_still_resumes(turns, tmp_path, monkeypatch):
    monkeypatch.setattr(claude_agent, "PROJECTS_ROOT", tmp_path)
    (tmp_path / SLUG).mkdir()

    await claude_agent._invoke_once(project_slug=SLUG, claude_session_id=uuid4(), prompt="x", charter_path=None)

    assert "--resume" in turns[-1] and _charter_given(turns[-1]) is None


async def test_a_failed_refresh_does_not_cost_the_turn(project, turns, monkeypatch):
    root = project(adopted=False)

    def _broken(*_args):
        raise RuntimeError("šablóna sa nedá prečítať")

    with monkeypatch.context() as m:
        m.setattr(cps, "refresh_v2_agent_charters", _broken)
        await _turn(root, new_session=True)

    assert _charter_given(turns[-1]) == "stará charta ai-agent", "ťah mal bežať na pravidlách, ktoré projekt má"


async def test_a_refresh_never_moves_the_project_head(project, turns):
    root = project(adopted=False)
    git = ["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-q", "-m", "overená verzia"], check=True)
    head = subprocess.run([*git, "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout

    await _turn(root, new_session=True)

    assert subprocess.run([*git, "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout == head
    changed = subprocess.run([*git, "status", "--porcelain"], capture_output=True, text=True, check=True).stdout
    assert ".claude/agents/ai-agent/CLAUDE.md" in changed, "obnova sa neudiala — skúška by nič nedokazovala"


def test_every_orchestrator_turn_names_its_charter_and_says_whether_it_resumes():
    """DEV-48, as a rule rather than a list: every ``invoke_claude`` call in the orchestrator names the charter of
    its role, and one in a function that resumes sessions (``_resolve_orch_session``) says whether it does — a call
    without them is exactly the turn whose lost session would be replaced without any rules."""
    tree = ast.parse(Path(orchestrator.__file__).read_text(encoding="utf-8"))
    calls = []
    for function in ast.walk(tree):
        if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        names = [n.func.id for n in ast.walk(function) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
        resumes = "_resolve_orch_session" in names
        for node in ast.walk(function):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "invoke_claude":
                given = {kw.arg for kw in node.keywords}
                calls.append((node.lineno, "charter_path" in given and (not resumes or "resume" in given)))

    assert calls, "v orchestrátore sa nenašlo ani jedno spustenie agenta — stráž by nič nestrážila"
    offenders = sorted({line for line, ok in calls if not ok})
    assert offenders == [], f"spustenie agenta bez charty roly alebo bez resume= v orchestrator.py, riadky {offenders}"
