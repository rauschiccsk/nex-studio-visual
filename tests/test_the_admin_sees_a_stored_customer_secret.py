"""DEV-437 — the Administrator sees a stored customer secret on demand; nobody else; every showing is recorded.

19.11.2026 the Director, on the customer card where stored secrets read „uložené — prázdne = bez zmeny“: „Chcel by som
tak ako je to vo väčšine moderných aplikácii … hviezdičky … a na konci riadku možnosť zobrazenia daného tokenu, toto
bude mať len Administrátor.“ The secret leaves the vault only when the Administrator asks for that one secret, and the
cockpit records who saw which secret of which customer and when — never the value. The create, update and list
answers still never carry a secret (``test_customer_service.py``). (Test values are fake.)
"""

from __future__ import annotations

import uuid

import pytest

from backend.config.settings import settings
from backend.core import authz
from backend.db.models.customers import CustomerSecretReveal
from backend.db.models.foundation import User
from backend.db.models.projects import Project
from backend.schemas.customer import CustomerCreate
from backend.services import claude_agent
from backend.services import customer as customer_service

TOKEN = "tok-twilio-1234"
INITIAL = "Init-heslo-1"
EXAMPLE = """# @customer-secret required: „Auth Token účtu Twilio“
TWILIO_AUTH_TOKEN=
# @customer-secret: „Kľúč Anthropic API“
ANTHROPIC_API_KEY=
ADMIN_INITIAL_PASSWORD=
"""


@pytest.fixture
def card(db_session, monkeypatch, tmp_path):
    """A project whose app asks a secret of each customer, and a customer that has it and its initial password."""
    monkeypatch.setattr(claude_agent, "PROJECTS_ROOT", tmp_path)
    monkeypatch.setattr(settings, "credentials_storage_path", str(tmp_path / "creds"))
    owner = User(
        username=f"owner_{uuid.uuid4().hex[:6]}", email=f"{uuid.uuid4().hex[:6]}@x.sk", password_hash="x", role="ri"
    )
    db_session.add(owner)
    db_session.flush()
    project = Project(
        name="HelpDesk",
        slug=f"helpdesk-{uuid.uuid4().hex[:6]}",
        type="standard",
        description="x",
        auth_mode="password",
        created_by=owner.id,
    )
    db_session.add(project)
    db_session.flush()
    (tmp_path / project.slug).mkdir()
    (tmp_path / project.slug / ".env.example").write_text(EXAMPLE, encoding="utf-8")
    customer = customer_service.create(
        db_session,
        project.id,
        CustomerCreate(name="ICC s.r.o.", slug="icc", secret=INITIAL, secret_settings={"TWILIO_AUTH_TOKEN": TOKEN}),
    )
    db_session.commit()
    return owner, customer


def _as(user: User) -> None:
    from backend.core.security import get_current_user
    from backend.main import app

    app.dependency_overrides[get_current_user] = lambda: user


def _admin(db_session) -> User:
    admin = db_session.query(User).filter(User.username == authz.ADMIN_USERNAME).one_or_none()
    if admin is None:
        admin = User(username=authz.ADMIN_USERNAME, email="admin@x.sk", password_hash="x", role="ri")
        db_session.add(admin)
        db_session.commit()
    return admin


def _reveals(db_session) -> list[tuple[str, object]]:
    return [
        (r.key, r.user_id) for r in db_session.query(CustomerSecretReveal).order_by(CustomerSecretReveal.created_at)
    ]


def test_the_administrator_sees_a_stored_secret_and_it_is_recorded(client, db_session, card):
    _owner, customer = card
    admin = _admin(db_session)
    _as(admin)

    shown = client.get(f"/api/v1/customers/{customer.id}/secrets/TWILIO_AUTH_TOKEN")

    assert shown.status_code == 200, shown.text
    assert shown.json() == {"key": "TWILIO_AUTH_TOKEN", "value": TOKEN}
    assert _reveals(db_session) == [("TWILIO_AUTH_TOKEN", admin.id)]


def test_the_administrator_sees_the_initial_password_too(client, db_session, card):
    _owner, customer = card
    _as(_admin(db_session))

    shown = client.get(f"/api/v1/customers/{customer.id}/secrets/ADMIN_INITIAL_PASSWORD")

    assert (shown.status_code, shown.json()["value"]) == (200, INITIAL)


def test_nobody_else_gets_it_not_even_the_projects_owner(client, db_session, card):
    owner, customer = card
    _as(owner)

    refused = client.get(f"/api/v1/customers/{customer.id}/secrets/TWILIO_AUTH_TOKEN")

    assert refused.status_code == 403
    assert TOKEN not in refused.text
    assert _reveals(db_session) == []


@pytest.mark.parametrize("key", ["ANTHROPIC_API_KEY", "SECRET_KEY"])
def test_a_secret_not_stored_or_not_the_customers_is_not_found(client, db_session, card, key):
    _owner, customer = card
    _as(_admin(db_session))

    missing = client.get(f"/api/v1/customers/{customer.id}/secrets/{key}")

    assert missing.status_code == 404
    assert _reveals(db_session) == []


def test_the_record_never_holds_the_value():
    assert set(CustomerSecretReveal.__table__.columns.keys()) == {
        "id",
        "customer_id",
        "key",
        "user_id",
        "created_at",
        "updated_at",
    }
