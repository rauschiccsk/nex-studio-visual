"""DEV-165 — the agents of a Web project know it has two frontends, two kinds of users and how each signs in.

The agents never learned the project's type: the charters spoke of one „BE+FE“ and of ``frontend/`` only, the first
administrator was founded from an e-mail (which collides with an administration signed into by name), the Vizuál brief
said „VÝHRADNE vo FRONTENDE (adresár `frontend/`)“ and the Auditor compared only ``frontend/`` with the approved
Vizuál. Director 20.10.2026: „Do web FE bude sa prihlasovať pomocou emailu a hesla, a do admin FE len meno a heslo.“
21.10.2026: the public registers itself (Director: „Áno platia, súhlasím“). A Štandardný project gets exactly what it
got before — byte for byte.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.db.models.versions import Version
from backend.services import claude_agent, orchestrator
from backend.services import create_project_postscaffold as cps
from tests.test_orchestrator_v2_vizual import _make_version

ADDENDUM = (cps.NEX_STUDIO_TEMPLATES / "web-project-charter.md").read_text(encoding="utf-8")
TABLE = (cps.NEX_STUDIO_TEMPLATES / "web-project-claude-md.md").read_text(encoding="utf-8")


def _provision(tmp_path: Path, project_type: str) -> Path:
    root = tmp_path / "projekt"
    (root / ".claude").mkdir(parents=True)
    cps.provision_v2_agent_charters(root, "projekt", "Projekt", adopted=False, project_type=project_type)
    return root


def _before(role: str) -> str:
    """What every project's charter was before DEV-165 — the shared base and the role's template."""
    base = (cps.NEX_STUDIO_TEMPLATES / "agent-shared-base.md").read_text(encoding="utf-8").rstrip()
    return base + "\n\n---\n\n" + (cps.NEX_STUDIO_TEMPLATES / cps._V2_AGENTS[role][0]).read_text(encoding="utf-8")


def test_a_standard_projects_rules_are_byte_for_byte_what_they_were(tmp_path):
    root = _provision(tmp_path, "standard")

    for role in cps._V2_AGENTS:
        assert (root / ".claude" / "agents" / role / "CLAUDE.md").read_text(encoding="utf-8") == _before(role)
    universal = (cps.NEX_STUDIO_TEMPLATES / "project-claude-md.md").read_text(encoding="utf-8")
    assert (root / "CLAUDE.md").read_text(encoding="utf-8") == universal.replace("{{PROJECT_NAME}}", "Projekt")


def test_both_agents_of_a_web_project_get_the_web_contract(tmp_path):
    root = _provision(tmp_path, "web")

    for role in cps._V2_AGENTS:
        charter = (root / ".claude" / "agents" / role / "CLAUDE.md").read_text(encoding="utf-8")
        assert charter.startswith(_before(role).rstrip()), f"{role}: the common rules are no longer the base"
        assert charter.endswith(ADDENDUM), f"{role}: the Web contract is missing"
    assert (root / "CLAUDE.md").read_text(encoding="utf-8").endswith(TABLE)


def test_the_web_contract_says_what_the_director_decided():
    """The decisions themselves, not their wording: name for the administration, e-mail and registration for the web,
    separate accounts, the first administrator by name."""
    assert "| Administrácia | `frontend/` | zamestnanci | meno + heslo |" in TABLE
    assert "| Web | `web/` | verejnosť | e-mail + heslo, registrácia |" in TABLE
    for decided in (
        "POST /api/v1/auth/login {username, password}",
        "POST /api/v1/web/auth/register {email, password}",
        "**Prvý správca sa\n  zakladá menom**",
        "**Token webu neotvorí volanie administrácie a naopak**",
        "`git diff <Vizuál> -- frontend/ web/`",
    ):
        assert decided in ADDENDUM


def test_a_new_session_of_a_web_project_reads_the_contract_from_the_cockpits_type(tmp_path, monkeypatch):
    root = _provision(tmp_path, "standard")  # rules written before the switch to Web
    monkeypatch.setattr(claude_agent, "PROJECTS_ROOT", tmp_path)
    monkeypatch.setattr(claude_agent, "_project_type", lambda slug: "web" if slug == "projekt" else None)

    claude_agent._refresh_rules("projekt")

    for role in cps._V2_AGENTS:
        assert (root / ".claude" / "agents" / role / "CLAUDE.md").read_text(encoding="utf-8").endswith(ADDENDUM)


def test_a_project_the_cockpit_does_not_know_keeps_the_standard_rules(tmp_path, monkeypatch):
    root = _provision(tmp_path, "standard")
    monkeypatch.setattr(claude_agent, "PROJECTS_ROOT", tmp_path)
    monkeypatch.setattr(claude_agent, "_project_type", lambda slug: None)

    claude_agent._refresh_rules("projekt")

    for role in cps._V2_AGENTS:
        assert (root / ".claude" / "agents" / role / "CLAUDE.md").read_text(encoding="utf-8") == _before(role)


def test_the_cockpits_type_is_read_from_its_table(db_session, monkeypatch):
    """The lookup asks the projects table by slug — and says None for a slug it does not have."""

    class _Session:
        def __init__(self, db):
            self.db = db

        def __enter__(self):
            return self.db

        def __exit__(self, *exc):
            return False

    from backend.db import session as session_module

    _version, project = _make_version(db_session)
    project.type, project.backend_port, project.frontend_port, project.db_port, project.web_port = (
        "web",
        14970,
        14971,
        14972,
        14975,
    )
    db_session.flush()
    monkeypatch.setattr(session_module, "SessionLocal", lambda: _Session(db_session))

    assert claude_agent._project_type(project.slug) == "web"
    assert claude_agent._project_type("niet-takeho") is None


# --- the briefs name every frontend ---------------------------------------------------------------------------------


def _web(db_session):
    version, project = _make_version(db_session)
    project.type, project.backend_port, project.frontend_port, project.db_port, project.web_port = (
        "web",
        14960,
        14961,
        14962,
        14965,
    )
    db_session.flush()
    return version, project


def test_the_vizual_brief_of_a_standard_project_is_as_it_was(db_session):
    version, _project = _make_version(db_session)

    brief = orchestrator._vizual_directive(db_session, version.id, None)

    assert "Pracuješ VÝHRADNE vo FRONTENDE (adresár `frontend/`): reálne obrazovky" in brief
    assert "`web/`" not in brief


def test_the_vizual_brief_of_a_web_project_names_both_frontends_and_their_preview(db_session):
    version, _project = _web(db_session)

    brief = orchestrator._vizual_directive(db_session, version.id, None)

    assert (
        "Pracuješ VÝHRADNE vo FRONTENDOCH (`frontend/` (Administrácia), `web/` (Web)) — každú obrazovku v tom, "
        "ktorému patrí: reálne obrazovky"
    ) in brief
    assert "Web `web/` má ten istý harness (`web/src/preview/`)" in brief
    assert "`web/src/preview/handlers.ts`" in brief


def test_the_vizual_writeback_reads_the_live_state_of_every_frontend(db_session):
    version, _project = _web(db_session)

    brief = orchestrator._vizual_writeback_directive(db_session, version.id)

    assert "živý stav `frontend/` (Administrácia), `web/` (Web)" in brief


@pytest.mark.parametrize(("web", "paths"), [(False, "frontend/"), (True, "frontend/ web/")])
def test_the_auditor_compares_every_frontend_with_the_approved_vizual(db_session, web, paths):
    version, _project = _web(db_session) if web else _make_version(db_session)
    db_session.get(Version, version.id).vizual_approved_sha = "abc1234"
    db_session.flush()

    brief = orchestrator._verifikacia_directive(db_session, version.id)

    assert f"Sprav `git diff abc1234 -- {paths}` a over" in brief
