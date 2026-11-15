"""DEV-340 — the UAT guide shows the full address of the customer being tried, as a link.

10.11.2026: Author Asistent 1.0.1's guide told the Manažér „Otvor v prehliadači adresu inštalácie a za ňu dopíš
/api/v1/health“ with the example https://<adresa-inštalácie>/api/v1/health — he had to put the address together,
while the cockpit knew TATA's (https://uat-tata-author-asistent.int.isnex.eu). Career Asistent 1.1.0 wrote ICC's
address out, wrong for any other customer. The guide is one for every customer: it writes a placeholder, the brief
says which, and the UAT tab gets each customer's address for each placeholder.
"""

from __future__ import annotations

from backend.db.models.customers import Customer
from backend.db.models.projects import Project
from backend.services import deploy, project_surfaces, uat_guide
from tests.test_delivered_tokens_statement import _as, _git, _write, project  # noqa: F401 — the fixture is used by name
from tests.test_uat_shows_what_to_try import _verified
from tests.test_web_project_surfaces import _project, _user


def test_tata_gets_the_address_of_its_own_installation(db_session):
    owner = _user(db_session)
    app = _project(db_session, owner, base=14910)
    app.slug, app.private_network = "author-asistent", True  # as Author Asistent is: reached only inside
    tata = Customer(project_id=app.id, name="TATA s.r.o.", slug="tata")
    db_session.add(tata)
    db_session.flush()

    assert uat_guide.addresses(deploy.uat_addresses(tata, app)) == {
        "<adresa-inštalácie>": "https://uat-tata-author-asistent.int.isnex.eu"
    }


def test_a_web_project_gets_the_address_of_each_frontend(db_session):
    owner = _user(db_session)
    web = _project(db_session, owner, project_type="web", web_port=14905)
    web.slug = "career-asistent"
    icc = Customer(project_id=web.id, name="ICC s.r.o.", slug="icc")
    db_session.add(icc)
    db_session.flush()

    assert uat_guide.addresses(deploy.uat_addresses(icc, web)) == {
        "<adresa-inštalácie>": "https://uat-icc-career-asistent.isnex.eu",
        "<adresa-administrácie>": "https://uat-icc-career-asistent-admin.isnex.eu",
        "<adresa-webu>": "https://uat-icc-career-asistent.isnex.eu",
    }


def test_every_frontend_of_a_project_with_more_than_one_has_its_placeholder():
    """A rule, not a list: whatever frontends a project type brings, the guide can name each of them."""
    for project_type in (project_surfaces.STANDARD, project_surfaces.WEB):
        surfaces = project_surfaces._surfaces(project_type, "password", 1, 2)
        if len(surfaces) > 1:
            assert {s.name for s in surfaces} <= set(uat_guide.PLACEHOLDERS), project_type
    assert "" in uat_guide.PLACEHOLDERS  # the installation's main address


def test_the_brief_tells_the_agent_every_placeholder_and_not_to_write_an_address_out():
    rule = uat_guide.BRIEF_RULE.format(path="docs/specs/versions/v1.0.1/UAT_SKUSKA.md")

    for placeholder in uat_guide.PLACEHOLDERS.values():
        assert f"`https://{placeholder}" in rule, placeholder
    assert "nepíš natvrdo" in rule


def test_the_uat_tab_gets_each_customers_address_for_the_guide(client, project):  # noqa: F811
    db, root, owner, v010, _v011, _d010, _d011 = project
    _write(
        root,
        uat_guide.relative_path("0.2.0"),
        "## Stav aplikácie\nObrazovka: prehliadač, `https://<adresa-inštalácie>/api/v1/health`\n"
        "1. Otvor adresu.\nOčakávané: ukáže sa stav.\n",
    )
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "feat: 0.2.0")
    _verified(db, v010.project_id, "0.2.0", _git(root, "rev-parse", "HEAD").strip())
    tata = Customer(project_id=v010.project_id, name="TATA s.r.o.", slug="tata")
    db.add(tata)
    db.flush()
    _as(db, owner)

    guide = client.get(f"/api/v1/projects/{root.name}/uat-guide", params={"version": "0.2.0"}).json()

    assert guide["addresses"] == [
        {
            "customer_id": str(tata.id),
            "placeholder": "<adresa-inštalácie>",
            "url": deploy.uat_addresses(tata, db.get(Project, v010.project_id))[""],
        }
    ]
    assert guide["addresses"][0]["url"].startswith("https://uat-tata-")
