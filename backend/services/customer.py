"""Service layer for the per-project Customers registry (v2.0.0, CR-V2-025).

Implements design §3.2 ("Zákazníci") — the project-scoped registry of customers
that run the app on their own UAT + PROD instance / DB / data. Every method
takes ``db: Session`` first and only ``flush()``es; commit/rollback is the
router's job (mirrors :mod:`backend.services.version`). Errors surface as
:class:`ValueError` for the router to map to HTTP status codes.

**Secret governance (CLAUDE.md §4/§5, OQ-5).** Per-customer secrets are NEVER
stored on a ``customers`` column. When a caller supplies ``secret``, the
service routes it to the existing credentials store
(:mod:`backend.services.credentials`) and records only the resulting
``credential_id`` POINTER on the customer row. The secret value is never
returned, never logged, and never written back into source. ``CustomerRead``
exposes only a ``has_secret`` boolean.

**No internal/external branch (design §3.2).** Internal apps register
**ICC s.r.o.** through the identical :func:`create` path as any external
customer — there is exactly one code path.
"""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db.models.customers import Customer
from backend.db.models.projects import Project
from backend.schemas.credentials import CredentialCreate
from backend.schemas.customer import CustomerCreate, CustomerUpdate
from backend.services import credentials as credentials_service


def _credential_filename(project_id: UUID, slug: str) -> str:
    """Deterministic, flat credentials-store filename for a customer's secret.

    Flat name (no slashes) per the credentials store's flat-directory invariant.
    Project-id + customer-slug keeps it unique across projects that reuse a slug.
    """
    return f"customer-{project_id}-{slug}.md"


def _store_secret(db: Session, project_id: UUID, slug: str, secret: str) -> UUID:
    """Persist ``secret`` to the credentials store, returning its credential_id.

    The secret VALUE goes only to the ``ri``-gated credentials store
    (on-disk file, mode 0600); only the returned pointer is kept by the caller.
    Never logs or returns the secret itself.
    """
    cred = credentials_service.create(
        db,
        CredentialCreate(
            title=f"Customer secret: {slug}",
            filename=_credential_filename(project_id, slug),
            content=secret,
        ),
    )
    return cred.id


def list_customers(db: Session, project_id: UUID) -> list[Customer]:
    """Return every customer of ``project_id``, newest first."""
    stmt = select(Customer).where(Customer.project_id == project_id).order_by(Customer.created_at.desc())
    return list(db.execute(stmt).scalars().all())


def get_by_id(db: Session, customer_id: UUID) -> Customer:
    """Return one customer by primary key.

    Raises:
        ValueError: If no customer with ``customer_id`` exists (router → 404).
    """
    customer = db.get(Customer, customer_id)
    if customer is None:
        raise ValueError(f"Customer {customer_id} not found")
    return customer


def _get_by_project_and_slug(db: Session, project_id: UUID, slug: str) -> Optional[Customer]:
    """Internal helper — look up a customer by the unique (project_id, slug) pair."""
    stmt = select(Customer).where(Customer.project_id == project_id, Customer.slug == slug)
    return db.execute(stmt).scalar_one_or_none()


def create(db: Session, project_id: UUID, data: CustomerCreate) -> Customer:
    """Register a new customer for ``project_id`` (design §3.2).

    Validates the project exists and the ``(project_id, slug)`` pair is free.
    If ``data.secret`` is supplied, it is written to the credentials store and
    only the resulting ``credential_id`` is recorded — the secret value never
    touches a ``customers`` column (CLAUDE.md §4/§5, OQ-5).

    Raises:
        ValueError: project not found, or a customer with the same slug already
            exists in the project (router → 404 / 409).
    """
    if db.get(Project, project_id) is None:
        raise ValueError(f"Project {project_id} not found")

    if _get_by_project_and_slug(db, project_id, data.slug) is not None:
        raise ValueError(f"Customer with slug {data.slug!r} already exists in this project")

    credential_id: UUID | None = None
    if data.secret:
        credential_id = _store_secret(db, project_id, data.slug, data.secret)

    customer = Customer(
        project_id=project_id,
        name=data.name,
        slug=data.slug,
        subdomain=data.subdomain,
        integrations=data.integrations,
        notes=data.notes,
        credential_id=credential_id,
    )
    db.add(customer)
    db.flush()
    return customer


def update(db: Session, customer_id: UUID, data: CustomerUpdate) -> Customer:
    """Partially update a customer's mutable fields.

    Allowed: ``name``, ``slug``, ``subdomain``, ``integrations``, ``notes`` and
    a write-only ``secret`` rotation. ``id`` / ``project_id`` / timestamps are
    immutable. Supplying ``secret`` overwrites the stored credentials-store
    content (or creates it if the customer had none); the value is never
    persisted on the row or echoed back.

    Raises:
        ValueError: customer not found, or the new slug collides with another
            customer in the same project (router → 404 / 409).
    """
    customer = get_by_id(db, customer_id)

    if data.slug is not None and data.slug != customer.slug:
        existing = _get_by_project_and_slug(db, customer.project_id, data.slug)
        if existing is not None and existing.id != customer.id:
            raise ValueError(f"Customer with slug {data.slug!r} already exists in this project")
        customer.slug = data.slug

    if data.name is not None:
        customer.name = data.name
    if data.subdomain is not None:
        customer.subdomain = data.subdomain
    if data.integrations is not None:
        customer.integrations = data.integrations
    if data.notes is not None:
        customer.notes = data.notes

    if data.secret is not None:
        if customer.credential_id is not None:
            credentials_service.write_content(db, customer.credential_id, data.secret)
        else:
            customer.credential_id = _store_secret(db, customer.project_id, customer.slug, data.secret)

    db.flush()
    return customer


def delete(db: Session, customer_id: UUID) -> None:
    """Delete a customer registry row and its stored secret (if any).

    The customer's credentials-store entry (DB row + on-disk file) is removed
    so no orphan secret survives. ``ON DELETE SET NULL`` on ``credential_id``
    is the defensive fallback; here we delete the credential explicitly.

    Raises:
        ValueError: customer not found (router → 404).
    """
    customer = get_by_id(db, customer_id)
    credential_id = customer.credential_id

    db.delete(customer)
    db.flush()

    if credential_id is not None:
        try:
            credentials_service.delete(db, credential_id)
        except ValueError:
            # The credential was already gone — nothing to clean up.
            pass
