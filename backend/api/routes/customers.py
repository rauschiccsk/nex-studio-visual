"""REST router for the per-project Customers registry (v2.0.0, CR-V2-025).

Implements design §3.2 ("Zákazníci") — DEPLOY-1..3. The endpoint set straddles
two URL families, so (like the versions router) it is mounted with the bare
``/api/v1`` prefix in :mod:`backend.main`:

* ``GET    /projects/{slug}/customers``      → list a project's customers.
* ``POST   /projects/{slug}/customers``      → register a customer (the form).
* ``GET    /customers/{customer_id}``        → one customer.
* ``PATCH  /customers/{customer_id}``        → partial update / secret rotation.
* ``DELETE /customers/{customer_id}``        → delete (also removes its secret).

**Secret invariant (CLAUDE.md §4/§5, OQ-5).** The write payloads accept a
write-only ``secret`` that the service routes to the credentials store; no
endpoint ever returns the secret. ``CustomerRead`` exposes only ``has_secret``.
The router resolves a project by slug (matching the metrics router) so the
project-scoped FE pages can address customers by the pinned project's slug.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.core.security import get_current_user, require_ri_role
from backend.db.models.customers import Customer
from backend.db.models.foundation import User
from backend.db.models.projects import Project
from backend.db.session import get_db
from backend.schemas.customer import CustomerCreate, CustomerRead, CustomerUpdate
from backend.services import customer as customer_service

router = APIRouter(tags=["Customers"])


def _map_value_error(exc: ValueError) -> HTTPException:
    message = str(exc)
    lowered = message.lower()
    if "not found" in lowered:
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=message)
    if "already exists" in lowered or "duplicate" in lowered or "conflict" in lowered:
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=message)
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=message)


def _to_read(customer: Customer) -> CustomerRead:
    """Serialise a customer WITHOUT its secret — only the ``has_secret`` flag."""
    return CustomerRead(
        id=customer.id,
        project_id=customer.project_id,
        name=customer.name,
        slug=customer.slug,
        subdomain=customer.subdomain,
        integrations=customer.integrations,
        notes=customer.notes,
        has_secret=customer.credential_id is not None,
        created_at=customer.created_at,
        updated_at=customer.updated_at,
    )


def _resolve_project(db: Session, slug: str) -> Project:
    project = db.execute(select(Project).where(Project.slug == slug)).scalar_one_or_none()
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Project not found: {slug}")
    return project


# ---------------------------------------------------------------------------
# Project-scoped endpoints
# ---------------------------------------------------------------------------


@router.get("/projects/{slug}/customers", response_model=list[CustomerRead])
def list_customers(
    slug: str,
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_user),
) -> list[CustomerRead]:
    """Return every customer registered for the project ``slug`` (newest first)."""
    project = _resolve_project(db, slug)
    rows = customer_service.list_customers(db, project.id)
    return [_to_read(row) for row in rows]


@router.post(
    "/projects/{slug}/customers",
    response_model=CustomerRead,
    status_code=status.HTTP_201_CREATED,
)
def create_customer(
    slug: str,
    payload: CustomerCreate,
    db: Session = Depends(get_db),
    _current_user: User = Depends(require_ri_role),
) -> CustomerRead:
    """Register a customer through the form (design §3.2). ``ri`` role only.

    Internal apps register **ICC s.r.o.** through this same endpoint — one code
    path, no internal/external branch. A supplied ``secret`` is written to the
    credentials store; the response never echoes it back.
    """
    project = _resolve_project(db, slug)
    try:
        customer = customer_service.create(db, project.id, payload)
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise _map_value_error(exc) from exc
    db.refresh(customer)
    return _to_read(customer)


# ---------------------------------------------------------------------------
# Customer-scoped endpoints
# ---------------------------------------------------------------------------


@router.get("/customers/{customer_id}", response_model=CustomerRead)
def get_customer(
    customer_id: UUID,
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_user),
) -> CustomerRead:
    try:
        customer = customer_service.get_by_id(db, customer_id)
    except ValueError as exc:
        raise _map_value_error(exc) from exc
    return _to_read(customer)


@router.patch("/customers/{customer_id}", response_model=CustomerRead)
def update_customer(
    customer_id: UUID,
    payload: CustomerUpdate,
    db: Session = Depends(get_db),
    _current_user: User = Depends(require_ri_role),
) -> CustomerRead:
    """Partially update a customer / rotate its secret. ``ri`` role only.

    A supplied ``secret`` overwrites the stored credentials-store content; the
    response never echoes it back.
    """
    try:
        customer = customer_service.update(db, customer_id, payload)
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise _map_value_error(exc) from exc
    db.refresh(customer)
    return _to_read(customer)


@router.delete(
    "/customers/{customer_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
def delete_customer(
    customer_id: UUID,
    db: Session = Depends(get_db),
    _current_user: User = Depends(require_ri_role),
) -> Response:
    """Delete a customer and its stored secret (if any). ``ri`` role only."""
    try:
        customer_service.delete(db, customer_id)
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise _map_value_error(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
