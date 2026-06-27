"""Customers domain model — the per-project customer registry (v2.0.0, CR-V2-025).

Design source: ``docs/architecture/nex-studio-v2-design.md`` §3.2 ("Zákazníci")
and the build plan CR-V2-025 (DEPLOY-1..3).

A ``customers`` row is a **project-scoped** registry entry describing one
customer that runs the project's app on its **own** UAT + PROD instance / DB /
data (the proven instance-per-customer model). Customers are added through a
single form (design §3.2):

* ``name`` / ``slug`` — the customer identity.
* ``subdomain`` — the customer's URL host label.
* ``integrations`` — per-customer external systems (free-form JSON).
* per-customer **secrets** — credentials for that customer's instance.

**Secret ownership (OQ-5).** This model NEVER stores secret material. The
per-customer deploy backend (CR-V2-026) and this registry both *point into* the
existing credentials store (``backend/services/credentials.py`` →
``settings.credentials_storage_path``); they do not own or duplicate the
secret. A customer's secrets therefore live in a credentials registry row /
on-disk file under the ``ri``-gated credentials API, and this table holds only
the nullable ``credential_id`` POINTER to it. No secret value ever lands in a
``customers`` column, in source, or in a log line (CLAUDE.md §4/§5).

**Uniform structure — no internal/external branch (design §3.2).** Internal
apps register their customer as **ICC s.r.o.** through the *same* form as any
external customer. There is exactly one code path; this model has no
"internal" flag.
"""

from sqlalchemy import Column, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID

from backend.db.models.base import Base, TimestampMixin, UUIDMixin


class Customer(Base, UUIDMixin, TimestampMixin):
    """A project-scoped customer that deploys the app to its own instance."""

    __tablename__ = "customers"

    # Owning project — a customer belongs to exactly one project for its
    # lifetime. Deleting a project cascades to its customers (the registry is
    # meaningless without the project).
    project_id = Column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name = Column(String(255), nullable=False)
    slug = Column(String(100), nullable=False)
    # Customer's URL host label (e.g. ``andros`` → ``andros.example.com``).
    # Nullable: a customer may be registered before its subdomain is assigned.
    subdomain = Column(String(255), nullable=True)
    # Per-customer external systems (free-form structured config). Never holds
    # secret material — secrets go to the credentials store via credential_id.
    integrations = Column(JSONB, nullable=True)
    # POINTER into the credentials store (OQ-5). NULL = no secret recorded yet.
    # ON DELETE SET NULL: deleting the credentials row leaves the customer
    # registry intact (just secret-less) rather than cascading the customer
    # away. The secret VALUE is NEVER stored here — only this FK to the
    # ``ri``-gated credentials registry row whose on-disk file holds it.
    credential_id = Column(
        UUID(as_uuid=True),
        ForeignKey("credentials.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Optional free-text note (e.g. contact, deploy caveats).
    notes = Column(Text, nullable=True)

    __table_args__ = (
        # One slug per project — two customers of the same project may not
        # share a slug. Different projects MAY reuse a slug (registry is
        # project-scoped).
        UniqueConstraint("project_id", "slug", name="uq_customers_project_slug"),
    )
