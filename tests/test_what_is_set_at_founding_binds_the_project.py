"""DEV-522 — what the Manažér sets when founding a project binds it: its type, sign-in and frontends do not change while
it is made. The one exception is „Pridať webový frontend“: only the admin account, only before the project's first PROD
deploy, and with a written reason that is kept.

26.11.2026, orthodox-register: the project page offered „Prepnúť na Web“ in a box titled „Projekt s webom“ that listed a
web frontend with its port — the Director read it as the project's setting, though the project is Štandardný since its
founding. The Director: „Je neprípustné, aby typ projektu a ďaľšie jeho parametre, ktoré určíme pri zakladaní projektu
boli zmenené počas tvorby projektu.“ On the proposal (admin only, before PROD, with a reason): „Áno toto považujem za
dobré riešenie. Spravme to tak, aby kopit aj do budúcna mal takú funkciu - pridať webový frontend.“
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from sqlalchemy import select

from backend.db.models.customers import Customer
from backend.db.models.deploy import DeployEvent
from backend.db.models.foundation import User
from backend.db.models.project_type_change import ProjectTypeChange
from backend.services import port_registry, web_workspace
from backend.services import project_surfaces as ps
from tests.test_web_project_surfaces import BASE, _as, _project, _user

BACKEND = Path(__file__).resolve().parents[1] / "backend"
REASON = "Pri založení mal byť projekt webový — kňazi sa prihlasujú do webu e-mailom."


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(port_registry, "PORT_REGISTRY_FILE", tmp_path / "port-registry.yaml")
    monkeypatch.setattr(web_workspace, "unsaved_work", lambda _project: None)
    monkeypatch.setattr(web_workspace, "add_web", lambda _db, _project: [])
    monkeypatch.setattr("backend.api.routes.project_surfaces.chown_workspace", lambda _path: None)
    monkeypatch.setattr(ps, "WEB_PROJECTS_AVAILABLE", True)


def _admin(db) -> User:
    found = db.execute(select(User).where(User.username == "admin")).scalar_one_or_none()
    if found:
        return found
    admin = User(username="admin", email="admin@t.sk", password_hash="x", role="ri")
    db.add(admin)
    db.flush()
    return admin


def _changes(db, project) -> list[ProjectTypeChange]:
    return list(db.execute(select(ProjectTypeChange).where(ProjectTypeChange.project_id == project.id)).scalars())


def _frontends(found: list[dict]) -> list[tuple]:
    return [(s["name"], s["directory"], s["login"], s["port"]) for s in found]


def test_the_page_shows_the_frontends_the_project_has_and_the_admin_what_adding_the_web_would_give(client, db_session):
    project = _project(db_session, _user(db_session))
    _as(db_session, _admin(db_session))

    preview = client.get(f"/api/v1/projects/{project.id}/web-switch").json()

    assert preview["allowed"] is True
    assert _frontends(preview["surfaces"]) == [("app", "frontend", "username", BASE + 1)]
    assert _frontends(preview["after"]) == [
        ("admin", "frontend", "username", BASE + 1),
        ("web", "web", "email", BASE + 5),
    ]


def test_only_the_admin_account_adds_a_web_frontend(client, db_session):
    owner = _user(db_session)
    project = _project(db_session, owner)
    _as(db_session, owner)

    preview = client.get(f"/api/v1/projects/{project.id}/web-switch").json()
    answer = client.post(f"/api/v1/projects/{project.id}/web-switch", json={"reason": REASON})

    assert (preview["allowed"], preview["refusal"], preview["after"]) == (False, ps.ONLY_ADMIN, [])
    assert _frontends(preview["surfaces"]) == [("app", "frontend", "username", BASE + 1)]
    assert (answer.status_code, answer.json()["detail"]) == (409, ps.ONLY_ADMIN)
    db_session.refresh(project)
    assert (project.type, project.web_port, _changes(db_session, project)) == ("standard", None, [])


def test_a_project_once_in_prod_keeps_its_frontends(client, db_session):
    project = _project(db_session, _user(db_session))
    customer = Customer(project_id=project.id, name="MÁGERSTAV", slug=f"m-{project.slug}")
    db_session.add(customer)
    db_session.flush()
    db_session.add(
        DeployEvent(
            customer_id=customer.id,
            project_id=project.id,
            version_number="1.0.0",
            environment="prod",
            event_type="deploy",
            status="ok",
        )
    )
    db_session.flush()
    _as(db_session, _admin(db_session))

    preview = client.get(f"/api/v1/projects/{project.id}/web-switch").json()
    answer = client.post(f"/api/v1/projects/{project.id}/web-switch", json={"reason": REASON})

    assert (preview["allowed"], preview["refusal"]) == (False, ps.AFTER_PROD)
    assert (answer.status_code, answer.json()["detail"]) == (409, ps.AFTER_PROD)


def test_a_project_only_on_uat_may_still_get_it(client, db_session):
    project = _project(db_session, _user(db_session))
    customer = Customer(project_id=project.id, name="MÁGERSTAV", slug=f"m-{project.slug}")
    db_session.add(customer)
    db_session.flush()
    for environment, status in (("uat", "ok"), ("prod", "failed")):
        db_session.add(
            DeployEvent(
                customer_id=customer.id,
                project_id=project.id,
                version_number="0.1.0",
                environment=environment,
                event_type="deploy",
                status=status,
            )
        )
    db_session.flush()
    _as(db_session, _admin(db_session))

    assert client.get(f"/api/v1/projects/{project.id}/web-switch").json()["allowed"] is True


def test_the_reason_is_required_and_kept_with_who(client, db_session):
    project = _project(db_session, _user(db_session))
    admin = _admin(db_session)
    _as(db_session, admin)

    for empty in (None, {}, {"reason": "   "}):
        refused = client.post(f"/api/v1/projects/{project.id}/web-switch", json=empty)
        assert (refused.status_code, refused.json()["detail"]) == (409, ps.NO_REASON), empty
    assert _changes(db_session, project) == []

    answer = client.post(f"/api/v1/projects/{project.id}/web-switch", json={"reason": f"  {REASON}  "})

    assert answer.status_code == 200, answer.text
    db_session.refresh(project)
    assert (project.type, project.web_port) == ("web", BASE + 5)
    [change] = _changes(db_session, project)
    assert (change.previous_type, change.type, change.changed_by, change.reason) == (
        "standard",
        "web",
        admin.id,
        REASON,
    )


def test_a_project_on_the_app_template_may_get_the_templates_web(client, db_session):
    """DEV-523 — the template has a web now: a project on it is offered the web like any other."""
    project = _project(db_session, _user(db_session))
    project.template = "nsv-standard"
    db_session.flush()
    _as(db_session, _admin(db_session))

    preview = client.get(f"/api/v1/projects/{project.id}/web-switch").json()

    assert (preview["allowed"], preview["refusal"]) == (True, None)


def test_nothing_else_changes_a_projects_type_or_sign_in_after_founding() -> None:
    """The rule, read from the whole backend: the one place that sets a project's type or sign-in on an existing project
    is adding the web frontend."""
    setters = re.compile(r"\b\w*project\w*\.(type|auth_mode)\s*=(?!=)")
    found = sorted(
        f"{path.relative_to(BACKEND)}:{number}"
        for path in BACKEND.rglob("*.py")
        if "tests" not in path.parts
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if setters.search(line)
    )
    assert [place.rsplit(":", 1)[0] for place in found] == ["services/project_surfaces.py"], found
    assert sum(1 for _ in BACKEND.rglob("*.py")) > 100  # it read the backend, not an empty folder
