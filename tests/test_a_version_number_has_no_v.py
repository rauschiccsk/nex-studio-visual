"""DEV-170 — a version number is ``major.minor.patch``; the first PROD version is 1.0.0, not v1.0.0.

Every number was bare except the one a first PROD deploy renamed to: ``v1.0.0``. The cockpit adds the ``v`` itself
where one belongs, so Career Asistent's and NEX Websites' ``v1.0.0`` became ``vv1.0.0`` — the Špecifikácia looked
for its documents in a folder that does not exist and the deploy comments in Plane carried it. Director 21.10.2026:
„Súhlasím“ — rename the twelve rows, and the database takes no other shape of a number.
"""

from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DatabaseError

from backend.core.security import get_current_user
from backend.db.models.customers import Customer
from backend.db.models.deploy import DeployEvent, DeployRefusal
from backend.db.models.foundation import User
from backend.db.models.projects import Project
from backend.db.models.versions import VERSION_NUMBER_RULE, Version
from backend.main import app

MIGRATION = Path(__file__).resolve().parents[1] / "migrations" / "versions" / "133_version_number_without_v.py"


def _migration():
    spec = importlib.util.spec_from_file_location("migration_133", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _project(db) -> Project:
    name = f"u-{uuid.uuid4().hex[:8]}"
    owner = User(username=name, email=f"{name}@example.com", password_hash="x", role="ri", is_active=True)
    db.add(owner)
    db.flush()
    project = Project(
        name=f"P {uuid.uuid4().hex[:6]}",
        slug=f"p-{uuid.uuid4().hex[:6]}",
        type="standard",
        auth_mode="password",
        description="",
        created_by=owner.id,
    )
    db.add(project)
    db.flush()
    return project


def _customer(db, project: Project) -> Customer:
    customer = Customer(project_id=project.id, name="ICC", slug=f"c{uuid.uuid4().hex[:6]}")
    db.add(customer)
    db.flush()
    return customer


def _deploy(db, customer: Customer, number: str) -> None:
    db.add(
        DeployEvent(
            customer_id=customer.id,
            project_id=customer.project_id,
            version_number=number,
            environment="prod",
            event_type="deploy",
            status="ok",
        )
    )
    db.flush()


def _refuse(db, customer: Customer, number: str) -> None:
    db.add(
        DeployRefusal(
            customer_id=customer.id,
            project_id=customer.project_id,
            version_number=number,
            environment="uat",
            reason="Nasadenie sa nedá spustiť.",
        )
    )
    db.flush()


# --- the API and the database refuse a number with a v ----------------------------------------------------------


@pytest.mark.parametrize("number", ["v1.2.0", "1.2", "1.2.0-rc1", "V2.0.0"])
def test_the_api_refuses_a_number_that_is_not_major_minor_patch(client, db_session, number):
    project = _project(db_session)
    app.dependency_overrides[get_current_user] = lambda: db_session.get(User, project.created_by)

    answer = client.post(f"/api/v1/projects/{project.id}/versions", json={"version_number": number})

    assert answer.status_code == 422, answer.text
    assert VERSION_NUMBER_RULE in answer.text
    assert db_session.execute(select(Version).where(Version.project_id == project.id)).first() is None


def test_the_api_takes_a_bare_number(client, db_session):
    project = _project(db_session)
    app.dependency_overrides[get_current_user] = lambda: db_session.get(User, project.created_by)

    answer = client.post(f"/api/v1/projects/{project.id}/versions", json={"version_number": "1.2.0"})

    assert answer.status_code == 201, answer.text
    assert answer.json()["version_number"] == "1.2.0"


@pytest.mark.parametrize("table", ["versions", "deploy_events", "deploy_refusals"])
def test_the_database_refuses_a_number_with_a_v(db_session, table):
    project = _project(db_session)
    customer = _customer(db_session, project)

    with pytest.raises(DatabaseError, match=f"ck_{table}_version_number"), db_session.begin_nested():
        if table == "versions":
            db_session.add(Version(project_id=project.id, version_number="v1.0.0"))
            db_session.flush()
        elif table == "deploy_refusals":
            _refuse(db_session, customer, "v1.0.0")
        else:
            _deploy(db_session, customer, "v1.0.0")


# --- the twelve rows of 21.10.2026 are renamed -------------------------------------------------------------------


@pytest.fixture
def before_the_rule(db_session):
    """The database as it was before migration 133 — inside a savepoint, so the rule comes back afterwards."""
    savepoint = db_session.begin_nested()
    db_session.execute(text("ALTER TABLE versions DROP CONSTRAINT ck_versions_version_number"))
    db_session.execute(text("ALTER TABLE deploy_events DROP CONSTRAINT ck_deploy_events_version_number"))
    db_session.execute(text("ALTER TABLE deploy_refusals DROP CONSTRAINT ck_deploy_refusals_version_number"))
    yield db_session
    savepoint.rollback()


def _numbers(db, project: Project) -> tuple[list[str], list[str]]:
    versions = db.execute(select(Version.version_number).where(Version.project_id == project.id)).scalars().all()
    events = db.execute(select(DeployEvent.version_number).where(DeployEvent.project_id == project.id)).scalars().all()
    return sorted(versions), sorted(events)


def _refusals(db, project: Project) -> list[str]:
    numbers = db.execute(select(DeployRefusal.version_number).where(DeployRefusal.project_id == project.id))
    return sorted(numbers.scalars().all())


def test_the_migration_renames_v1_0_0_to_1_0_0_in_the_versions_and_the_deploy_log(before_the_rule):
    db = before_the_rule
    project = _project(db)
    customer = _customer(db, project)
    for number in ("0.1.0", "v1.0.0", "1.1.0"):
        db.add(Version(project_id=project.id, version_number=number))
    db.flush()
    for number in ("0.1.0", "v1.0.0", "v1.0.0"):
        _deploy(db, customer, number)
    _refuse(db, customer, "v1.0.0")

    _migration().drop_the_v(db.connection())
    db.expire_all()

    assert _numbers(db, project) == (["0.1.0", "1.0.0", "1.1.0"], ["0.1.0", "1.0.0", "1.0.0"])
    assert _refusals(db, project) == ["1.0.0"]


def test_a_project_that_already_has_the_bare_number_stops_the_migration_before_anything_changes(before_the_rule):
    db = before_the_rule
    clean, clashing = _project(db), _project(db)
    clean_customer = _customer(db, clean)
    db.add(Version(project_id=clean.id, version_number="v1.0.0"))
    db.add(Version(project_id=clashing.id, version_number="v1.0.0"))
    db.add(Version(project_id=clashing.id, version_number="1.0.0"))
    db.flush()
    _deploy(db, clean_customer, "v1.0.0")

    with pytest.raises(RuntimeError, match=r"already has the bare number: \['v1.0.0'\]"):
        _migration().drop_the_v(db.connection())
    db.expire_all()

    assert _numbers(db, clean) == (["v1.0.0"], ["v1.0.0"]), "the migration changed a row before it stopped"
    assert _numbers(db, clashing) == (["1.0.0", "v1.0.0"], [])
