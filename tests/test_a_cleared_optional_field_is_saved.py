"""DEV-323 — a cleared optional field is saved.

Director 07.11.2026 in Nastavenia → Používatelia: „Test už nedá sa odstrániť. Ak zadám prázdne priezvisko neuloží.“
The screen sends an emptied field as null, and the updates read null as "leave it" — the save went through and the
value stayed. An explicit null now clears an optional field (its column may be empty) and leaves a required one; a
field that was not sent stays as it is.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from backend.db.models.foundation import User
from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.schemas.backlog import BacklogItemCreate, BacklogItemUpdate
from backend.schemas.user import UserCreate, UserUpdate
from backend.schemas.version import VersionUpdate
from backend.services import backlog as backlog_service
from backend.services import user as user_service
from backend.services import version as version_service


def _person(db: Any) -> User:
    name = f"u_{uuid.uuid4().hex[:8]}"
    payload = UserCreate(
        username=name,
        email=f"{name}@example.com",
        password="heslo123",
        role="ri",
        first_name="Administrátor",
        last_name="Test",
        telegram_chat_id="123456789",
    )
    return user_service.create(db, payload)


def _project(db: Any, owner: User) -> Project:
    project = Project(
        name=f"P {uuid.uuid4().hex[:8]}",
        slug=f"p-{uuid.uuid4().hex[:8]}",
        type="standard",
        auth_mode="password",
        description="d",
        created_by=owner.id,
    )
    db.add(project)
    db.flush()
    return project


def test_users_edit_clears_the_surname_the_name_and_telegram(db_session) -> None:
    person = _person(db_session)
    user_service.update(db_session, person.id, UserUpdate(first_name=None, last_name=None, telegram_chat_id=None))
    assert (person.first_name, person.last_name, person.telegram_chat_id) == (None, None, None)


def test_users_edit_leaves_a_required_field_that_came_as_null(db_session) -> None:
    person = _person(db_session)
    before = (person.username, person.email, person.role, person.is_active)
    user_service.update(db_session, person.id, UserUpdate(username=None, email=None, role=None, is_active=None))
    assert (person.username, person.email, person.role, person.is_active) == before


def test_a_field_that_was_not_sent_stays(db_session) -> None:
    person = _person(db_session)
    user_service.update(db_session, person.id, UserUpdate(first_name="Zoltán"))
    assert (person.first_name, person.last_name, person.telegram_chat_id) == ("Zoltán", "Test", "123456789")


def test_a_requests_description_can_be_cleared(db_session) -> None:
    owner = _person(db_session)
    project = _project(db_session, owner)
    item = backlog_service.create(
        db_session, BacklogItemCreate(project_id=project.id, title="Export do CSV", description="Stĺpce ako v Exceli.")
    )
    backlog_service.update(db_session, item.id, BacklogItemUpdate(title=None, description=None))
    assert (item.title, item.description) == ("Export do CSV", None)


def test_a_versions_optional_fields_can_be_cleared(db_session) -> None:
    owner = _person(db_session)
    project = _project(db_session, owner)
    version = Version(
        project_id=project.id,
        version_number="1.2.0",
        name="Jeseň",
        description="Opravy",
        target_date=date(2026, 12, 1),
    )
    db_session.add(version)
    db_session.flush()
    version_service.update(
        db_session, version.id, VersionUpdate(version_number=None, name=None, description=None, target_date=None)
    )
    assert (version.version_number, version.name, version.description, version.target_date) == (
        "1.2.0",
        None,
        None,
        None,
    )
