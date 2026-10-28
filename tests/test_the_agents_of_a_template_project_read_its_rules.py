"""DEV-253 — the agents of a project on an app template read that its frame is the template's.

Both build agents and Poradca get the template's addendum to their charter; the project gets the skill
`sablona-aplikacie` and not `nex-manager-launch` (the template signs people in and takes the launch from NEX Manager —
a procedure for building it would send the agent to write what it must not). The rules follow the cockpit's record of
the project (``projects.template``) at every new session; a project without a template gets exactly what it got before.
Director 28.10.2026: „Áno, súhlasím, zverejni ho a pokračuj treťou fázou“.
"""

from __future__ import annotations

from pathlib import Path

from backend.db.models.projects import Project
from backend.services import app_template, claude_agent
from backend.services import create_project_postscaffold as cps
from backend.services.poradca import runner as poradca_runner
from tests.test_poradca_runner import _ask, _capture_calls, world  # noqa: F401 — the fixture is used by name

ADDENDUM = (cps.NEX_STUDIO_TEMPLATES / "app-template-charter.md").read_text(encoding="utf-8")
#: The real charter — the runner tests' world replaces it with a stub.
REAL_CHARTER = poradca_runner._charter_text
WEB = (cps.NEX_STUDIO_TEMPLATES / "web-project-charter.md").read_text(encoding="utf-8")


def _provision(tmp_path: Path, template: str | None) -> Path:
    root = tmp_path / "projekt"
    (root / ".claude").mkdir(parents=True)
    cps.provision_v2_agent_charters(root, "projekt", "Projekt", adopted=False, template=template)
    return root


def _charter(root: Path, role: str) -> str:
    return (root / ".claude" / "agents" / role / "CLAUDE.md").read_text(encoding="utf-8")


def _skills(root: Path) -> set[str]:
    return {entry.name for entry in (root / ".claude" / "skills").iterdir() if entry.is_dir()}


def test_both_agents_of_a_template_project_read_the_template_s_rules(tmp_path):
    root = _provision(tmp_path, "nsv-standard")
    plain = _provision(tmp_path / "bez", None)

    for role in cps._V2_AGENTS:
        assert _charter(root, role) == _charter(plain, role).rstrip() + "\n\n---\n\n" + ADDENDUM


def test_a_template_project_gets_the_template_s_skill_instead_of_the_launch_procedure(tmp_path):
    root = _provision(tmp_path, "nsv-standard")
    plain = _provision(tmp_path / "bez", None)

    assert _skills(root) == _skills(plain) - {"nex-manager-launch"} | {"sablona-aplikacie"}
    assert "sablona-aplikacie" not in _skills(plain) and "nex-manager-launch" in _skills(plain)


def test_the_rules_say_what_the_director_decided():
    """The frame is the template's; the app adds only its own screens, entries, settings and tables."""
    for decided in (
        "**Rám dodáva šablóna — nemeníš ho, nekopíruješ ani nestaviaš vedľa neho:**",
        "**Pravidlá\na zručnosť `nex-manager-launch` tu neplatia**",
        "`<Route>` vnútri `<TemplateApp>` vo `frontend/src/App.tsx`",
        "`frontend/app.manifest.json`",
        "`SettingDefinition` v `backend/app/kit.py`",
        "`kit_users.id`",
        "`...previewHandlers({ manifest })`",
        "zmena rámu je **FAIL**",
    ):
        assert decided in ADDENDUM, decided


def test_a_web_project_on_a_template_would_read_both_addenda_in_order():
    assert cps.project_type_rules("web", "nsv-standard") == WEB.rstrip() + "\n" + "\n\n---\n\n" + ADDENDUM
    assert cps.project_type_rules("standard", None) == ""


def test_a_new_session_reads_the_rules_by_the_cockpit_s_record(tmp_path, monkeypatch):
    root = _provision(tmp_path, None)  # written before the record said „template“
    monkeypatch.setattr(claude_agent, "PROJECTS_ROOT", tmp_path)
    monkeypatch.setattr(claude_agent, "_project_type", lambda slug: "standard")
    monkeypatch.setattr(app_template, "template_of_slug", lambda slug: "nsv-standard" if slug == "projekt" else None)

    claude_agent._refresh_rules("projekt")

    for role in cps._V2_AGENTS:
        assert _charter(root, role).endswith(ADDENDUM), role
    assert "sablona-aplikacie" in _skills(root) and "nex-manager-launch" not in _skills(root)

    monkeypatch.setattr(app_template, "template_of_slug", lambda slug: None)
    claude_agent._refresh_rules("projekt")

    assert ADDENDUM not in _charter(root, "ai-agent")
    assert "sablona-aplikacie" not in _skills(root) and "nex-manager-launch" in _skills(root)


def test_poradca_reads_the_rules_its_build_agents_read():
    assert poradca_runner._charter_text("standard", "nsv-standard").endswith(ADDENDUM)
    assert ADDENDUM not in poradca_runner._charter_text("standard", None)


async def test_poradca_of_a_project_put_on_a_template_reads_its_rules_at_the_next_question(world, monkeypatch):  # noqa: F811
    monkeypatch.setattr(poradca_runner, "_charter_text", REAL_CHARTER)
    calls = _capture_calls(monkeypatch)
    db = world["db"]
    project = db.get(Project, world["conversation"].project_id)

    await _ask(world, "Prvá otázka")
    project.template = "nsv-standard"
    db.commit()
    await _ask(world, "Druhá otázka")

    assert ADDENDUM.rstrip() not in calls[0].charter_text
    assert ADDENDUM.rstrip() in calls[1].prompt
