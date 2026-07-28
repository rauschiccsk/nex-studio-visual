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

import re
from typing import Optional
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.db.models.customers import Customer
from backend.db.models.deploy import DeployEvent
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


_INSTANCE_DIR_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def _assert_deploy_safe_instance_dir(dir_slug: str) -> None:
    """Refuse an instance directory the deploy path cannot use.

    Mirrors ``uat_provisioner.validate_uat_slug`` (``^[a-z0-9][a-z0-9-]*$``) — no more and no less,
    so the form never rejects something that would in fact deploy.
    """
    if not _INSTANCE_DIR_RE.match(dir_slug):
        raise ValueError(
            f"Z údajov zákazníka vychádza názov priečinka {dir_slug!r}, ktorý sa pri nasadzovaní "
            "nedá použiť. Povolené sú len malé písmená bez diakritiky, číslice a spojovník, "
            "pričom prvý znak musí byť písmeno alebo číslica."
        )


def _instance_dir_slug(subdomain: Optional[str], slug: str) -> str:
    """The instance DIRECTORY a customer resolves to — mirrors ``deploy._customer_dir_slug``.

    Kept as a local one-liner rather than importing :mod:`backend.services.deploy`
    (which pulls in the provisioner / runner) — the formula is one expression and
    the mirroring is asserted by a test.
    """
    return (subdomain or slug).strip().lower()


def _find_instance_dir_conflict(
    db: Session,
    project_id: UUID,
    dir_slug: str,
    *,
    exclude_id: Optional[UUID] = None,
) -> Optional[Customer]:
    """Return the customer in ``project_id`` already occupying ``dir_slug``, if any.

    The DB unique constraint covers ``(project_id, slug)`` only — but the deploy path
    keys the instance directory, Traefik host and instance slug on
    ``(subdomain or slug).lower()``. So two DIFFERENT slugs sharing one subdomain (or a
    subdomain equal to another customer's slug) collide on ONE directory: the second
    deploy silently lands on the first customer's instance. That is unrecoverable
    through the UI and near-undiagnosable — refuse it at registration instead.

    Scanned in Python rather than SQL: the comparison is over the DERIVED value
    (``coalesce`` + ``lower``) and a project's customer list is small (single digits).
    """
    for other in list_customers(db, project_id):
        if exclude_id is not None and other.id == exclude_id:
            continue
        if _instance_dir_slug(other.subdomain, other.slug) == dir_slug:
            return other
    return None


def create(db: Session, project_id: UUID, data: CustomerCreate) -> Customer:
    """Register a new customer for ``project_id`` (design §3.2).

    Validates the project exists, the ``(project_id, slug)`` pair is free, and the
    customer's derived instance directory ``(subdomain or slug)`` is not already
    taken by another customer of the project.
    If ``data.secret`` is supplied, it is written to the credentials store and
    only the resulting ``credential_id`` is recorded — the secret value never
    touches a ``customers`` column (CLAUDE.md §4/§5, OQ-5).

    Raises:
        ValueError: project not found, a customer with the same slug already
            exists in the project, or another customer already resolves to the
            same instance directory (router → 404 / 409).
    """
    if db.get(Project, project_id) is None:
        raise ValueError(f"Project {project_id} not found")

    if _get_by_project_and_slug(db, project_id, data.slug) is not None:
        raise ValueError(f"Customer with slug {data.slug!r} already exists in this project")

    dir_slug = _instance_dir_slug(data.subdomain, data.slug)
    conflict = _find_instance_dir_conflict(db, project_id, dir_slug)
    if conflict is not None:
        raise ValueError(
            f"Customer {conflict.slug!r} already exists in this project with instance directory "
            f"{dir_slug!r} — two customers cannot share one instance directory"
        )

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
        ValueError: customer not found, the new slug collides with another
            customer in the same project, or the edit would make this customer
            resolve to another customer's instance directory (router → 404 / 409).
    """
    customer = get_by_id(db, customer_id)

    # Every conflict check runs BEFORE any mutation — a rejected edit must leave the
    # in-session row untouched, not half-applied waiting on the router's rollback.
    if data.slug is not None and data.slug != customer.slug:
        existing = _get_by_project_and_slug(db, customer.project_id, data.slug)
        if existing is not None and existing.id != customer.id:
            raise ValueError(f"Customer with slug {data.slug!r} already exists in this project")

    # ``None`` = field omitted from the PATCH → the stored value stays in force.
    new_slug = data.slug if data.slug is not None else customer.slug
    new_subdomain = data.subdomain if data.subdomain is not None else customer.subdomain
    dir_slug = _instance_dir_slug(new_subdomain, new_slug)
    # Only an edit that MOVES the customer to a different instance directory is checked. A row that
    # already collides (registered before this guard existed) must stay editable — otherwise every save
    # would be refused, including the subdomain change that would resolve the collision, and the person
    # would be locked out of their own record with no way forward.
    if dir_slug != _instance_dir_slug(customer.subdomain, customer.slug):
        # The EFFECTIVE label is what deploy validates, so it is checked here rather than per field
        # on the schema: a legacy row with slug ``icc.sk`` and subdomain ``icc`` resolves to ``icc``
        # and deploys fine, and refusing its slug on the schema would block every unrelated edit.
        # An edit that MOVES the customer must still land somewhere deploy can use — otherwise the
        # mistake surfaces days later as a failed deploy, far from the form where it was made.
        _assert_deploy_safe_instance_dir(dir_slug)
        conflict = _find_instance_dir_conflict(db, customer.project_id, dir_slug, exclude_id=customer.id)
        if conflict is not None:
            raise ValueError(
                f"Customer {conflict.slug!r} already exists in this project with instance directory "
                f"{dir_slug!r} — two customers cannot share one instance directory"
            )

    if data.slug is not None:
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

    Refuses a customer that has a DEPLOY HISTORY. ``deploy_events.customer_id`` is ``ON DELETE
    CASCADE`` (migration 076), so deleting the row silently erases every deploy and accept event
    recorded for that customer — and those rows are not merely history: the project hard-delete guard
    reads them to decide whether a project still has a live PROD deployment. Deleting a customer
    therefore destroyed the evidence a DIFFERENT safety check depends on, and the next hard delete
    would find a clean slate and proceed. Nothing warned; the cascade is silent by design.

    A customer with deployments is a customer with something running. If the intent really is to
    remove them, the deployments come down first — which is a deliberate act, not a side effect.

    Raises:
        ValueError: customer not found (router → 404), or the customer still has deploy events
            (router → 409).
    """
    customer = get_by_id(db, customer_id)
    credential_id = customer.credential_id

    event_count = db.scalar(select(func.count()).select_from(DeployEvent).where(DeployEvent.customer_id == customer_id))
    if event_count:
        raise ValueError(
            f"Zákazník má v evidencii {event_count} záznamov o nasadení, ktoré by sa spolu s ním "
            f"nenávratne zmazali — a práve tie rozhodujú, či sa projekt smie zmazať. Conflict: "
            f"najprv zruš nasadenia tohto zákazníka, potom ho odstráň."
        )

    db.delete(customer)
    db.flush()

    if credential_id is not None:
        try:
            credentials_service.delete(db, credential_id)
        except ValueError:
            # The credential was already gone — nothing to clean up.
            pass
