"""REST router for the per-project Customers registry (v2.0.0, CR-V2-025).

Implements design §3.2 ("Zákazníci") — DEPLOY-1..3. The endpoint set straddles
two URL families, so (like the versions router) it is mounted with the bare
``/api/v1`` prefix in :mod:`backend.main`:

* ``GET    /projects/{slug}/customers``      → list a project's customers.
* ``POST   /projects/{slug}/customers``      → register a customer (the form).
* ``PATCH  /customers/{customer_id}``        → partial update / secret rotation.
* ``DELETE /customers/{customer_id}``        → delete (also removes its secret).

**Secret invariant (CLAUDE.md §4/§5, OQ-5).** The write payloads accept a
write-only ``secret`` that the service routes to the credentials store; no
endpoint ever returns the secret. ``CustomerRead`` exposes only ``has_secret``.
The router resolves a project by slug (matching the metrics router) so the
project-scoped FE pages can address customers by the pinned project's slug.
"""

from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from backend.core import authz
from backend.core.errors import http_error
from backend.core.security import get_current_user, require_shu_or_above
from backend.db.models.customers import Customer
from backend.db.models.foundation import User
from backend.db.session import get_db
from backend.schemas.customer import (
    CustomerCreate,
    CustomerLanguageOption,
    CustomerRead,
    CustomerSecretRead,
    CustomerSettingRead,
    CustomerSettingsContractRead,
    CustomerUpdate,
    ProdMachineRead,
)
from backend.services import app_languages, prod_machines
from backend.services import customer as customer_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Customers"])


def _to_read(customer: Customer, db: Session) -> CustomerRead:
    """Serialise a customer WITHOUT its secrets — only the ``has_secret`` flag and, for the app's
    ``@customer-secret`` settings, which of them have a value (DEV-72)."""
    return CustomerRead(
        id=customer.id,
        project_id=customer.project_id,
        name=customer.name,
        slug=customer.slug,
        subdomain=customer.subdomain,
        integrations=customer.integrations,
        notes=customer.notes,
        prod_host=customer.prod_host or "",
        prod_machine=prod_machines.label(customer.prod_host),
        language=customer.language or "",
        has_secret=customer.credential_id is not None,
        settings=dict(customer.settings or {}),
        secret_settings_set=customer_service.secret_setting_keys(db, customer),
        created_at=customer.created_at,
        updated_at=customer.updated_at,
    )


# ---------------------------------------------------------------------------
# Project-scoped endpoints
# ---------------------------------------------------------------------------


@router.get("/prod-machines", response_model=list[ProdMachineRead])
def list_prod_machines(current_user: User = Depends(get_current_user)) -> list[ProdMachineRead]:
    """DEV-274 — the machines a customer's PROD may run on, for the choice on the customer form."""
    return [ProdMachineRead(value=value, label=label) for value, label in prod_machines.choices()]


@router.get("/projects/{slug}/customers", response_model=list[CustomerRead])
def list_customers(
    slug: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[CustomerRead]:
    """Return every customer registered for the project ``slug`` (newest first)."""
    # v4.0.35: owner-or-admin — a Junior sees only their OWN project's customers.
    project = authz.assert_project_slug_access(db, current_user, slug)
    rows = customer_service.list_customers(db, project.id)
    return [_to_read(row, db) for row in rows]


@router.get("/projects/{slug}/customer-settings", response_model=CustomerSettingsContractRead)
def customer_settings_contract(
    slug: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CustomerSettingsContractRead:
    """DEV-72: what the project asks of each customer — the settings its ``.env.example`` marks ``@customer`` /
    ``@customer-secret`` / ``@public-url``, read from the working tree the deploy renders from."""
    project = authz.assert_project_slug_access(db, current_user, slug)
    from backend.services import claude_agent, env_contract

    contract = env_contract.for_project(claude_agent.PROJECTS_ROOT / project.slug)
    return CustomerSettingsContractRead(
        settings=[
            CustomerSettingRead(key=s.key, kind=s.kind, required=s.required, label=s.label) for s in contract.settings
        ],
        problems=list(contract.problems),
        legacy_admin=env_contract.declares_legacy_admin(claude_agent.PROJECTS_ROOT / project.slug),
        # DEV-395: the language the customer's installations start in is offered only where there is a choice.
        languages=(
            [CustomerLanguageOption(code=c, label=app_languages.name_of(c)) for c in project.languages]
            if project.template and app_languages.is_multilingual(project.languages)
            else []
        ),
    )


@router.post(
    "/projects/{slug}/customers",
    response_model=CustomerRead,
    status_code=status.HTTP_201_CREATED,
)
def create_customer(
    slug: str,
    payload: CustomerCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_shu_or_above),
) -> CustomerRead:
    """Register a customer through the form (design §3.2).

    v4.0.35: was owner-or-admin → now owner-or-admin (the creator, or an ``ri`` lead, may add customers to their OWN
    project). ``ha`` is NOT privileged for this write; a Junior touching another user's project gets 403.

    Internal apps register **ICC s.r.o.** through this same endpoint — one code
    path, no internal/external branch. A supplied ``secret`` is written to the
    credentials store; the response never echoes it back.
    """
    project = authz.assert_project_slug_access(db, current_user, slug)
    try:
        customer = customer_service.create(db, project.id, payload)
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise http_error(exc) from exc
    db.refresh(customer)
    return _to_read(customer, db)


# ---------------------------------------------------------------------------
# Customer-scoped endpoints
# ---------------------------------------------------------------------------


@router.get("/customers/{customer_id}/secrets/{key}", response_model=CustomerSecretRead)
def reveal_customer_secret(
    customer_id: UUID,
    key: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CustomerSecretRead:
    """DEV-437 — one stored secret of the customer, for the eye on the customer card. Only the Administrator (the
    ``admin`` account): the Director, 19.11.2026, „toto bude mať len Administrátor“. Every showing is recorded; the
    create, update and list answers still never carry a secret."""
    customer = authz.assert_customer_access(db, current_user, customer_id)
    if not authz.is_admin(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Uložené tajomstvo zobrazí len Administrátor."
        )
    try:
        value = customer_service.reveal_secret(db, customer, key, current_user)
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise http_error(exc) from exc
    logger.info("customer secret shown: customer=%s key=%s user=%s", customer.id, key, current_user.username)
    return CustomerSecretRead(key=key, value=value)


@router.patch("/customers/{customer_id}", response_model=CustomerRead)
def update_customer(
    customer_id: UUID,
    payload: CustomerUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_shu_or_above),
) -> CustomerRead:
    """Partially update a customer / rotate its secret.

    v4.0.35: was owner-or-admin → now owner-or-admin (the owner, or an ``ri`` lead, may edit a customer under their
    OWN project; ``ha`` not privileged for this write); a Junior touching another user's project gets 403.

    A supplied ``secret`` overwrites the stored credentials-store content; the
    response never echoes it back.
    """
    # v4.0.35: ownership gate (owner-or-admin) before any mutation.
    authz.assert_customer_access(db, current_user, customer_id)
    try:
        customer = customer_service.update(db, customer_id, payload)
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise http_error(exc) from exc
    db.refresh(customer)
    return _to_read(customer, db)


@router.delete(
    "/customers/{customer_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
def delete_customer(
    customer_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_shu_or_above),
) -> Response:
    """Delete a customer and its stored secret (if any).

    v4.0.35: was owner-or-admin → now owner-or-admin (the owner, or an ``ri`` lead, may delete a customer under their
    OWN project; ``ha`` not privileged for this write); a Junior touching another user's project gets 403.
    """
    # v4.0.35: ownership gate (owner-or-admin) before the delete.
    authz.assert_customer_access(db, current_user, customer_id)
    try:
        customer_service.delete(db, customer_id)
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise http_error(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
