"""DEV-45 — what a build agent reads at every turn must stay under the Director's ceiling.

Every turn the AI Agent of a build gets its role charter (the shared base + the role's template, written into the
project by :func:`create_project_postscaffold.provision_v2_agent_charters` and appended to the session) and the
project's root ``CLAUDE.md`` (the ``claude`` CLI loads it from the project directory). Long instructions are followed
worse, and the charter only ever grew: every fix added a paragraph and nothing watched the sum. Measured 09.10.2026:
the AI Agent would get 37 111 characters. Dedo's own workspace has the same guard for the same reason (ICCINT-173).

Director 09.10.2026: „Áno, súhlasím s hranicou 37 000, začni DEV-45“. The ceiling is his; when the text outgrows it,
the text is shortened — the ceiling is not moved to make this pass.

What is measured is what the cockpit itself writes — the files come from the real provisioning code on a scratch
project, not from adding up template sizes here, so a change in how the charter is assembled is measured too.

DEV-46 added skills: at start Claude Code shows the agent each project skill's name and description, and loads the
body only when the agent uses it. So the names and descriptions count for every agent; the bodies do not. Poradca
runs with a restricted tool set and may not be shown them at all — counted anyway, the ceiling is an upper bound.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from backend.services import create_project_postscaffold
from backend.services.poradca import runner as poradca_runner

#: Director 09.10.2026 (DEV-45). Changed only by the Director.
CEILING = 37_000


def _scaffold(tmp_path: Path) -> Path:
    root = tmp_path / "projekt"
    (root / ".claude").mkdir(parents=True)
    create_project_postscaffold.provision_v2_agent_charters(root, "projekt", "Projekt", adopted=False)
    return root


def _skill_listing(root: Path) -> str:
    """What Claude Code shows of the project's skills at start: each skill's name and description."""
    lines = []
    for skill in sorted((root / ".claude" / "skills").glob(f"*/{create_project_postscaffold.SKILL_FILE}")):
        front = yaml.safe_load(skill.read_text(encoding="utf-8").split("\n---\n", 1)[0].removeprefix("---\n"))
        lines.append(f"{front['name']}: {front['description']}")
    return "\n".join(lines)


def instruction_texts(tmp_path: Path) -> dict[str, list[str]]:
    """Per agent, the texts it reads at every turn: its charter, the project's root ``CLAUDE.md`` and the listing
    of the project's skills."""
    root = _scaffold(tmp_path)
    project_md = (root / "CLAUDE.md").read_text(encoding="utf-8")
    skills = _skill_listing(root)
    texts = {
        role: [(root / ".claude" / "agents" / role / "CLAUDE.md").read_text(encoding="utf-8"), project_md, skills]
        for role in create_project_postscaffold._V2_AGENTS
    }
    texts["poradca"] = [poradca_runner._charter_text(), project_md, skills]
    return texts


def test_every_agent_reads_less_than_the_ceiling(tmp_path):
    sizes = {agent: sum(len(t) for t in parts) for agent, parts in instruction_texts(tmp_path).items()}

    over = {agent: size for agent, size in sizes.items() if size > CEILING}
    assert over == {}, (
        f"pokyny agenta prerástli hranicu {CEILING} znakov: {over} — skráť text (Director 09.10.2026, DEV-45), "
        "hranicu neposúvaj"
    )


def test_the_measure_covers_every_part_an_agent_reads(tmp_path):
    texts = instruction_texts(tmp_path)
    base = (create_project_postscaffold.NEX_STUDIO_TEMPLATES / "agent-shared-base.md").read_text(encoding="utf-8")
    first_line = base.strip().splitlines()[0]

    assert set(texts) == {"ai-agent", "auditor", "poradca"}
    for role in create_project_postscaffold._V2_AGENTS:
        charter, project_md, skills = texts[role]
        assert first_line in charter, f"{role}: spoločný základ chýba v meranej charte"
        role_template, _settings = create_project_postscaffold._V2_AGENTS[role]
        role_first = (create_project_postscaffold.NEX_STUDIO_TEMPLATES / role_template).read_text(encoding="utf-8")
        assert role_first.strip().splitlines()[0] in charter, f"{role}: charta roly chýba v meranej charte"
        assert "Projekt" in project_md and len(project_md) > 1000, "koreňový CLAUDE.md projektu chýba v meraní"
        shipped = create_project_postscaffold.cockpit_skills()
        assert shipped and all(f"{name}: " in skills for name in shipped), "opisy zručností chýbajú v meraní"
