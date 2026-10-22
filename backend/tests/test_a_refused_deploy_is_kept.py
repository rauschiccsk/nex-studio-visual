"""DEV-181 — a deploy the cockpit refused is kept with the sentence the Manažér got, so Poradca can read it.

22.10.2026 the UAT deploy of Career Asistent 1.1.0 was refused (a required setting was missing); the sentence lived on
the screen only and Poradca twice asked the Director to copy it. Director 22.10.2026: „Poradca v projekte musí vidieť
všetko.“ A refusal is not a deploy: nothing of it reaches ``deploy_events``.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select

from backend.db.models.customers import Customer
from backend.db.models.deploy import DeployEvent, DeployRefusal
from backend.db.models.foundation import User
from backend.db.models.projects import Project
from backend.services import deploy as deploy_service

REFUSAL = (
    "Nasadenie sa nedá spustiť: Zákazníkovi chýbajú povinné nastavenia: Prihlasovacie meno prvého správcu "
    "(odporúčané admin). Doplň ich v sekcii Zákazníci."
)


def _customer(db) -> Customer:
    owner = User(
        username=f"o_{uuid.uuid4().hex[:8]}", email=f"{uuid.uuid4().hex[:8]}@t.local", password_hash="x", role="ri"
    )
    db.add(owner)
    db.flush()
    project = Project(
        name="Career Asistent",
        slug=f"career-{uuid.uuid4().hex[:8]}",
        type="standard",
        auth_mode="password",
        description="d",
        created_by=owner.id,
    )
    db.add(project)
    db.flush()
    customer = Customer(project_id=project.id, name="ICC s.r.o.", slug="icc", subdomain="icc")
    db.add(customer)
    # The customer exists before anyone deploys — committed (to the test's savepoint), so the route's rollback of a
    # refused deploy does not take it away as it would take a row of its own request.
    db.commit()
    return customer


def test_a_refused_deploy_is_kept_with_what_the_manazer_was_told(client, db_session, monkeypatch):
    customer = _customer(db_session)

    async def _refuse(*args, **kwargs):
        raise ValueError(REFUSAL)

    monkeypatch.setattr(deploy_service, "deploy", _refuse)

    answer = client.post(
        f"/api/v1/customers/{customer.id}/deploy", json={"version_number": "1.1.0", "environment": "uat"}
    )

    assert answer.status_code == 422
    assert answer.json()["detail"] == REFUSAL
    kept = db_session.execute(select(DeployRefusal).where(DeployRefusal.customer_id == customer.id)).scalars().all()
    assert [(r.version_number, r.environment, r.reason, r.project_id) for r in kept] == [
        ("1.1.0", "uat", REFUSAL, customer.project_id)
    ]
    assert db_session.execute(select(DeployEvent).where(DeployEvent.customer_id == customer.id)).first() is None


def test_a_deploy_that_went_through_keeps_no_refusal(client, db_session, monkeypatch):
    customer = _customer(db_session)

    async def _deploy(db, customer_id, **kwargs):
        event = DeployEvent(
            customer_id=customer_id,
            project_id=customer.project_id,
            version_number="1.1.0",
            environment="uat",
            event_type="deploy",
            status="ok",
            detail="OK",
        )
        db.add(event)
        db.flush()
        return deploy_service.DeployOutcome(event, "https://x", None)

    monkeypatch.setattr(deploy_service, "deploy", _deploy)

    answer = client.post(
        f"/api/v1/customers/{customer.id}/deploy", json={"version_number": "1.1.0", "environment": "uat"}
    )

    assert answer.status_code == 200, answer.text
    assert db_session.execute(select(DeployRefusal).where(DeployRefusal.customer_id == customer.id)).first() is None
