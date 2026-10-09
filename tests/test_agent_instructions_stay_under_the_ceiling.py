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
"""

from __future__ import annotations

from pathlib import Path

from backend.services import create_project_postscaffold
from backend.services.poradca import runner as poradca_runner

#: Director 09.10.2026 (DEV-45). Changed only by the Director.
CEILING = 37_000


def _scaffold(tmp_path: Path) -> Path:
    root = tmp_path / "projekt"
    (root / ".claude").mkdir(parents=True)
    create_project_postscaffold.provision_v2_agent_charters(root, "projekt", "Projekt", adopted=False)
    return root


def instruction_texts(tmp_path: Path) -> dict[str, list[str]]:
    """Per agent, the texts it reads at every turn: its charter and the project's root ``CLAUDE.md``."""
    root = _scaffold(tmp_path)
    project_md = (root / "CLAUDE.md").read_text(encoding="utf-8")
    texts = {
        role: [(root / ".claude" / "agents" / role / "CLAUDE.md").read_text(encoding="utf-8"), project_md]
        for role in create_project_postscaffold._V2_AGENTS
    }
    texts["poradca"] = [poradca_runner._charter_text(), project_md]
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
        charter, project_md = texts[role]
        assert first_line in charter, f"{role}: spoločný základ chýba v meranej charte"
        role_template, _settings = create_project_postscaffold._V2_AGENTS[role]
        role_first = (create_project_postscaffold.NEX_STUDIO_TEMPLATES / role_template).read_text(encoding="utf-8")
        assert role_first.strip().splitlines()[0] in charter, f"{role}: charta roly chýba v meranej charte"
        assert "Projekt" in project_md and len(project_md) > 1000, "koreňový CLAUDE.md projektu chýba v meraní"
