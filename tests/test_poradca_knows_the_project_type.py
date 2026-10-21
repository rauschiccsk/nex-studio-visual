"""DEV-171 — Poradca knows what kind of project it advises on: its type, frontends and ports, and the rules of its type.

21.10.2026 Poradca agreed to approve Career Asistent 1.1.0's Specification although it had no ``web`` service in
``docker-compose.yml`` and no release check that a web token never opens the administration — both rules of a Web
project. It could not know: its context line named only the project, version, phase and state, and its rules were one
text for every project, while the build agents have had the Web rules since DEV-165. Director: „chcel by som vedieť …
čo treba robiť, aby som mohol spoľahnúť na Poradcu“ — „Áno založ a hneď implementuj.“
"""

from __future__ import annotations

import uuid

from backend.db.models.customers import Customer
from backend.db.models.foundation import User
from backend.db.models.projects import Project
from backend.services import create_project_postscaffold as postscaffold
from backend.services.poradca import runner
from tests.test_poradca_runner import _ask, _capture_calls, world  # noqa: F401 — the fixture is used by name

#: The real charter — the runner tests' world replaces it with a stub.
REAL_CHARTER = runner._charter_text
WEB_RULES = (postscaffold.NEX_STUDIO_TEMPLATES / "web-project-charter.md").read_text(encoding="utf-8")


def _project(db, project_type: str, auth_mode: str = "password", customer: str | None = "ICC") -> Project:
    name = f"u-{uuid.uuid4().hex[:8]}"
    owner = User(username=name, email=f"{name}@example.com", password_hash="x", role="ri", is_active=True)
    db.add(owner)
    db.flush()
    project = Project(
        name="Career Asistent",
        slug=f"cas-{uuid.uuid4().hex[:6]}",
        type=project_type,
        auth_mode=auth_mode,
        description="",
        created_by=owner.id,
        backend_port=10250,
        frontend_port=10251,
        db_port=10252,
        web_port=10255 if project_type == "web" else None,
        private_network=True,
    )
    db.add(project)
    db.flush()
    if customer:
        db.add(Customer(project_id=project.id, name=customer, slug=customer.lower()))
        db.flush()
    return project


PORTS_NOTE = (
    " Porty platia pri spustení projektu priamo na ANDROSe (jeho docker-compose.yml); nasadené inštalácie (UAT, "
    "PROD) porty nepublikujú — kokpit ich sprístupní na adresách nižšie."
)


# --- the context line ---------------------------------------------------------------------------------------------


def test_the_context_names_a_web_projects_type_frontends_and_ports(db_session):
    project = _project(db_session, "web")

    line = runner.scope_line(db_session, project, None)
    app = project.slug  # no "nex-" to strip

    assert line == (
        f"Projekt Career Asistent ({project.slug}) — otázka sa týka celého projektu.\n"
        "Typ projektu: Web (administrácia a verejný web). Frontendy — Administrácia: priečinok frontend/, služba "
        "frontend, prihlásenie menom a heslom, port 10251; Web: priečinok web/, služba web, prihlásenie e-mailom a "
        "heslom, registrácia, port 10255. Backend port 10250, databáza 10252." + PORTS_NOTE + "\n"
        "Adresy inštalácií (kokpit ich dá pri nasadení):\n"
        f"- UAT ICC: Administrácia https://uat-icc-{app}-admin.int.isnex.eu, Web https://uat-icc-{app}.int.isnex.eu\n"
        f"- PROD ICC: Administrácia https://icc-{app}-admin.int.isnex.eu, Web https://icc-{app}.int.isnex.eu"
    )


def test_the_context_names_a_standard_projects_one_frontend(db_session):
    project = _project(db_session, "standard", auth_mode="token")

    line = runner.scope_line(db_session, project, None)

    assert line.splitlines()[1:] == [
        "Typ projektu: Štandardný. Frontendy — Aplikácia: priečinok frontend/, služba frontend, spúšťa ho NEX "
        "Manager tokenom, port 10251. Backend port 10250, databáza 10252." + PORTS_NOTE,
        "Adresy inštalácií (kokpit ich dá pri nasadení):",
        f"- UAT ICC: Aplikácia https://uat-icc-{project.slug}.int.isnex.eu",
        f"- PROD ICC: Aplikácia https://icc-{project.slug}.int.isnex.eu",
    ]


def test_a_project_without_a_customer_says_its_installations_have_no_address_yet(db_session):
    project = _project(db_session, "web", customer=None)

    line = runner.scope_line(db_session, project, None)

    assert line.splitlines()[-1] == (
        "Adresy inštalácií: projekt zatiaľ nemá zákazníka — adresy vzniknú, keď ho kokpitu pridáš."
    )


# --- the rules of the type ----------------------------------------------------------------------------------------


def test_a_web_projects_poradca_reads_the_rules_its_build_agents_read():
    base = runner._CHARTER.read_text(encoding="utf-8")
    agents = postscaffold.role_charter_text("", postscaffold.NEX_STUDIO_TEMPLATES / "ai-agent-charter.md", "web")

    assert runner._charter_text("standard") == base
    assert runner._charter_text("web") == base.rstrip() + "\n\n---\n\n" + runner.TYPE_RULES_INTRO + "\n\n" + WEB_RULES
    assert agents.endswith(WEB_RULES), "the build agents and Poradca no longer read the same Web rules"


def test_poradca_has_a_procedure_for_judging_a_phase_before_it_is_approved():
    base = runner._CHARTER.read_text(encoding="utf-8")

    assert "## 6. Keď posudzuješ výstup fázy pred schválením" in base
    assert "Porty sú len na spustenie projektu priamo na ANDROSe" in base


async def test_a_project_switched_to_web_gives_its_open_conversation_the_web_rules(world, monkeypatch):  # noqa: F811
    monkeypatch.setattr(runner, "_charter_text", REAL_CHARTER)
    calls = _capture_calls(monkeypatch)
    db = world["db"]
    project = db.get(Project, world["conversation"].project_id)

    await _ask(world, "Prvá otázka")
    project.type, project.web_port = "web", 10255
    db.commit()
    await _ask(world, "Druhá otázka")

    assert WEB_RULES not in calls[0].charter_text
    assert "Typ projektu: Štandardný" in calls[0].prompt
    assert WEB_RULES in calls[1].prompt and "Typ projektu: Web" in calls[1].prompt
