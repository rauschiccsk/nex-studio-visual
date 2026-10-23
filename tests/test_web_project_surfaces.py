"""DEV-160 — the frontends of a project live in one place, and a Štandardný project can be switched to Web.

Project type „web“ (backend + administration + public web) was only a plan in ``.nex-archetype.json`` nothing read;
everywhere else a project had one frontend. Director 20.10.2026: „Do web FE bude sa prihlasovať pomocou emailu a hesla,
a do admin FE len meno a heslo. Ideme kokpit naučiť stavať projekt s dvoma frontendmi.“ Until every phase knows the
second frontend, Web is closed to the Manažér (Director: kroky po jednom, „Súhlasím“) — the tests that switch a project
open it for themselves.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.exc import DatabaseError

from backend.core.security import get_current_user
from backend.db.models.foundation import User
from backend.db.models.project_type_change import ProjectTypeChange
from backend.db.models.projects import Project
from backend.main import app
from backend.services import port_registry, web_workspace
from backend.services import project_surfaces as ps

BASE = 14900


@pytest.fixture(autouse=True)
def _no_registry_file(monkeypatch, tmp_path):
    """The KB port registry of this machine stays out of the tests — its blocks would answer for our ports."""
    monkeypatch.setattr(port_registry, "PORT_REGISTRY_FILE", tmp_path / "port-registry.yaml")


@pytest.fixture(autouse=True)
def _a_clean_workspace(monkeypatch):
    """These projects have no workspace; what the switch finds in one is tested with real git repositories in
    ``test_a_web_project_has_its_public_web.py``."""
    monkeypatch.setattr(web_workspace, "unsaved_work", lambda _project: None)


def _user(db, role="ri") -> User:
    user = User(
        username=f"u_{uuid.uuid4().hex[:6]}", email=f"{uuid.uuid4().hex[:6]}@t.sk", password_hash="x", role=role
    )
    db.add(user)
    db.flush()
    return user


def _project(db, owner, *, base=BASE, auth_mode="password", project_type="standard", web_port=None) -> Project:
    project = Project(
        name=f"CAS {uuid.uuid4().hex[:6]}",
        slug=f"cas-{uuid.uuid4().hex[:6]}",
        type=project_type,
        auth_mode=auth_mode,
        description="",
        created_by=owner.id,
        backend_port=base,
        frontend_port=base + 1,
        db_port=base + 2,
        web_port=web_port,
    )
    db.add(project)
    db.flush()
    return project


def _as(db, user):
    user_id = user.id
    app.dependency_overrides[get_current_user] = lambda: db.get(User, user_id)


@pytest.fixture
def web_open(monkeypatch):
    monkeypatch.setattr(ps, "WEB_PROJECTS_AVAILABLE", True)


# --- the frontends of a project -------------------------------------------------------------------------------------


def test_a_standard_project_has_one_frontend_with_the_projects_login(db_session):
    owner = _user(db_session)

    (by_password,) = ps.surfaces_of(_project(db_session, owner))
    (by_token,) = ps.surfaces_of(_project(db_session, owner, base=BASE + 10, auth_mode="token"))

    assert by_password == ps.Surface(
        name="app",
        label="Aplikácia",
        directory="frontend",
        service="frontend",
        port=BASE + 1,
        login=ps.USERNAME,
        address_suffix="",
    )
    assert (by_token.login, by_token.port) == (ps.TOKEN, BASE + 11)


def test_a_web_project_has_its_administration_by_name_and_its_web_by_email(db_session):
    """Director 20.10.2026: web e-mail + heslo, administrácia meno + heslo."""
    owner = _user(db_session)
    project = _project(db_session, owner, project_type="web", web_port=BASE + 5)

    assert ps.surfaces_of(project) == (
        ps.Surface(
            name="admin",
            label="Administrácia",
            directory="frontend",
            service="frontend",
            port=BASE + 1,
            login=ps.USERNAME,
            address_suffix="-admin",
        ),
        ps.Surface(
            name="web", label="Web", directory="web", service="web", port=BASE + 5, login=ps.EMAIL, address_suffix=""
        ),
    )


# --- the web's port is a port like any other -------------------------------------------------------------------------


def test_every_port_column_of_a_project_is_seen_by_the_port_registry():
    """A port column the registry does not list is invisible to every collision check — the rule, not a list."""
    port_columns = {c.name for c in Project.__table__.columns if c.name.endswith("_port")}

    assert {column.key for column in port_registry.PROJECT_PORT_COLUMNS.values()} == port_columns
    assert "web_port" in port_columns


def test_another_project_cannot_take_a_web_port(db_session):
    owner = _user(db_session)
    holder = _project(db_session, owner, project_type="web", web_port=BASE + 5)

    verdict = port_registry.describe_port_availability(db_session, BASE + 5)

    assert (verdict.state, verdict.holder) == (port_registry.TAKEN, holder.name)
    assert BASE + 5 in port_registry._get_all_used_ports(db_session)


@pytest.mark.parametrize(
    ("project_type", "web_port", "constraint"),
    [
        ("web", None, "ck_projects_web_port"),
        ("standard", BASE + 5, "ck_projects_web_port"),
        ("web", BASE + 1, "ck_projects_ports_distinct"),
    ],
    ids=["web-without-port", "standard-with-port", "web-port-equal-to-frontend"],
)
def test_the_database_refuses_a_web_port_that_does_not_fit_the_type(db_session, project_type, web_port, constraint):
    owner = _user(db_session)

    # pg8000 reports a CHECK violation (23514) as ProgrammingError — both are DatabaseError.
    with pytest.raises(DatabaseError, match=f'"{constraint}"'):
        with db_session.begin_nested():
            _project(db_session, owner, project_type=project_type, web_port=web_port)


# --- Web was closed until every phase knew it (DEV-164 opened it) ----------------------------------------------------


def test_web_is_open_to_the_manazer_now():
    """DEV-164: founding, Vizuál, Verifikácia and deploy know the second frontend — the gate is open."""
    assert ps.WEB_PROJECTS_AVAILABLE is True


@pytest.fixture
def web_closed(monkeypatch):
    """The gate closed again — what a phase found wanting would do; the refusal must still say why."""
    monkeypatch.setattr(ps, "WEB_PROJECTS_AVAILABLE", False)


def test_closed_web_cannot_be_founded_and_the_form_says_why(client, db_session, web_closed):
    owner = _user(db_session)
    _as(db_session, owner)

    presets = client.get("/api/v1/projects/create-presets").json()
    answer = client.post(
        "/api/v1/projects",
        json={"name": "CAS Web", "slug": "cas-web", "type": "web", "auth_mode": "password", "description": ""},
    )

    assert (presets["web_available"], presets["web_note"]) == (False, ps.WEB_NOT_YET)
    assert (answer.status_code, answer.json()["detail"]) == (422, ps.WEB_NOT_YET)
    assert db_session.execute(select(Project).where(Project.slug == "cas-web")).first() is None


def test_closed_web_cannot_be_switched_to(client, db_session, web_closed):
    owner = _user(db_session)
    project = _project(db_session, owner)
    _as(db_session, owner)

    preview = client.get(f"/api/v1/projects/{project.id}/web-switch").json()
    answer = client.post(f"/api/v1/projects/{project.id}/web-switch")

    assert (preview["allowed"], preview["refusal"]) == (False, ps.WEB_NOT_YET)
    assert (answer.status_code, answer.json()["detail"]) == (409, ps.WEB_NOT_YET)
    assert (project.type, project.web_port) == ("standard", None)


# --- switching a project to Web --------------------------------------------------------------------------------------


def test_the_preview_shows_both_frontends_and_changes_nothing(client, db_session, web_open):
    owner = _user(db_session)
    project = _project(db_session, owner)
    _as(db_session, owner)

    preview = client.get(f"/api/v1/projects/{project.id}/web-switch").json()

    assert (preview["allowed"], preview["refusal"], preview["web_port"]) == (True, None, BASE + 5)
    assert [(s["name"], s["directory"], s["port"], s["login"], s["address_suffix"]) for s in preview["surfaces"]] == [
        ("admin", "frontend", BASE + 1, "username", "-admin"),
        ("web", "web", BASE + 5, "email", ""),
    ]
    assert (project.type, project.web_port) == ("standard", None)
    assert db_session.execute(select(ProjectTypeChange)).first() is None


def test_the_switch_gives_the_web_its_port_and_keeps_the_change(client, db_session, web_open, monkeypatch):
    owner = _user(db_session)
    project = _project(db_session, owner)
    _as(db_session, owner)
    # DEV-161: what the switch writes into the workspace has its own tests (test_a_web_project_has_its_public_web).
    monkeypatch.setattr(web_workspace, "add_web", lambda _db, _project: [])

    answer = client.post(f"/api/v1/projects/{project.id}/web-switch")

    assert answer.status_code == 200, answer.text
    db_session.refresh(project)
    assert (project.type, project.web_port) == ("web", BASE + 5)
    change = db_session.execute(
        select(ProjectTypeChange).where(ProjectTypeChange.project_id == project.id)
    ).scalar_one()
    assert (change.previous_type, change.type, change.web_port, change.changed_by) == (
        "standard",
        "web",
        BASE + 5,
        owner.id,
    )
    assert [s.name for s in ps.surfaces_of(project)] == ["admin", "web"]


@pytest.mark.parametrize(
    ("setup", "refusal"),
    [
        (
            {"auth_mode": "token"},
            "Projekt sa prihlasuje tokenom. Web projekt sa prihlasuje heslom — administrácia menom, web e-mailom.",
        ),
        ({"project_type": "web", "web_port": BASE + 5}, "Projekt už je Web — má administráciu aj web."),
    ],
    ids=["token-login", "already-web"],
)
def test_a_project_that_cannot_be_web_says_why(client, db_session, web_open, setup, refusal):
    owner = _user(db_session)
    project = _project(db_session, owner, **setup)
    _as(db_session, owner)

    preview = client.get(f"/api/v1/projects/{project.id}/web-switch").json()
    answer = client.post(f"/api/v1/projects/{project.id}/web-switch")

    assert (preview["allowed"], preview["refusal"]) == (False, refusal)
    assert (answer.status_code, answer.json()["detail"]) == (409, refusal)


def test_a_project_without_a_port_block_says_so(client, db_session, web_open):
    owner = _user(db_session)
    project = _project(db_session, owner)
    project.backend_port = project.frontend_port = project.db_port = None
    db_session.flush()
    _as(db_session, owner)

    preview = client.get(f"/api/v1/projects/{project.id}/web-switch").json()

    assert preview["refusal"] == "Projekt nemá pridelený blok portov, z ktorého by web dostal port."


def test_a_web_port_held_by_someone_else_is_refused_by_name(client, db_session, web_open, host_ports):
    owner = _user(db_session)
    project = _project(db_session, owner)
    host_ports[BASE + 5] = "stary-kontajner"
    _as(db_session, owner)

    answer = client.post(f"/api/v1/projects/{project.id}/web-switch")

    assert (answer.status_code, answer.json()["detail"]) == (
        409,
        f"Port {BASE + 5} pre web je obsadený (stary-kontajner).",
    )
    db_session.refresh(project)
    assert project.type == "standard"


def test_a_host_that_cannot_be_asked_is_a_refusal_not_a_guess(client, db_session, web_open, monkeypatch):
    owner = _user(db_session)
    project = _project(db_session, owner)

    def _no_docker(timeout=port_registry.HOST_PROBE_TIMEOUT_SECONDS):
        raise port_registry.HostProbeError("docker ps failed")

    monkeypatch.setattr(port_registry, "_docker_published_ports", _no_docker)
    port_registry.invalidate_host_port_cache()
    _as(db_session, owner)

    preview = client.get(f"/api/v1/projects/{project.id}/web-switch").json()

    assert preview["refusal"] == f"Či je port {BASE + 5} pre web voľný, sa teraz nedá overiť — skús to o chvíľu."


def test_another_persons_project_is_neither_shown_nor_switched(client, db_session, web_open):
    owner = _user(db_session)
    stranger = _user(db_session, role="shu")
    project = _project(db_session, owner)
    _as(db_session, stranger)

    seen = client.get(f"/api/v1/projects/{project.id}/web-switch")
    switched = client.post(f"/api/v1/projects/{project.id}/web-switch")

    assert seen.status_code in (403, 404) and switched.status_code in (403, 404)
    db_session.refresh(project)
    assert project.type == "standard"
