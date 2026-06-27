"""Pydantic schemas for the Customers domain (v2.0.0, CR-V2-025).

Mirrors :mod:`backend.db.models.customers.Customer` — the per-project customer
registry (design §3.2). Field names / max lengths match the SQLAlchemy model so
``CustomerRead.model_validate(customer_orm_instance)`` round-trips cleanly.

**Secret handling is the load-bearing invariant here (CLAUDE.md §4/§5, OQ-5).**
``CustomerCreate`` / ``CustomerUpdate`` ACCEPT a one-shot ``secret`` field
(write-only) which the service hands straight to the credentials store; it is
NEVER persisted in a ``customers`` column. ``CustomerRead`` deliberately has
**no** ``secret`` field — it exposes only a ``has_secret`` boolean derived from
``credential_id``, so the secret value can never be echoed back over the API,
into a log, or into a response model dump.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class CustomerCreate(BaseModel):
    """Payload for registering a new customer via the form (design §3.2).

    Internal apps register **ICC s.r.o.** through this same payload — there is
    no internal/external branch. ``project_id`` is supplied by the route path,
    not the body. The optional ``secret`` is write-only: the service writes it
    to the credentials store and records only the resulting ``credential_id``;
    it is never stored on the customer row and never returned.
    """

    name: str = Field(..., min_length=1, max_length=255, description="Customer name, e.g. 'ICC s.r.o.'.")
    slug: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="URL-safe customer slug, unique within the project.",
    )
    subdomain: Optional[str] = Field(
        default=None,
        max_length=255,
        description="Customer URL host label (e.g. 'andros').",
    )
    integrations: Optional[dict[str, Any]] = Field(
        default=None,
        description="Per-customer external systems config (non-secret). Secrets go to the secret field.",
    )
    notes: Optional[str] = Field(default=None, description="Optional free-text note.")
    secret: Optional[str] = Field(
        default=None,
        description=(
            "Write-only per-customer secret material. Handed to the credentials store; "
            "NEVER stored on the customer row and NEVER returned in any response."
        ),
    )


class CustomerUpdate(BaseModel):
    """Partial update for an existing customer.

    ``id`` / ``project_id`` / ``created_at`` / ``updated_at`` are immutable.
    Supplying ``secret`` rotates the per-customer credentials-store content
    (write-only, never persisted on the row, never echoed back).
    """

    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    slug: Optional[str] = Field(default=None, min_length=1, max_length=100)
    subdomain: Optional[str] = Field(default=None, max_length=255)
    integrations: Optional[dict[str, Any]] = Field(default=None)
    notes: Optional[str] = Field(default=None)
    secret: Optional[str] = Field(
        default=None,
        description="Write-only — rotates the stored per-customer secret. Never persisted on the row / echoed.",
    )


class CustomerRead(BaseModel):
    """Serialised customer row. Carries NO secret material.

    ``has_secret`` is derived from ``credential_id`` (non-NULL) so the UI can
    show whether a secret is recorded WITHOUT ever transmitting it. The raw
    ``credential_id`` is intentionally omitted from the public read shape — the
    secret is reachable only through the separate ``ri``-gated credentials API,
    not via the customer registry.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    name: str
    slug: str
    subdomain: Optional[str] = None
    integrations: Optional[dict[str, Any]] = None
    notes: Optional[str] = None
    has_secret: bool = Field(default=False, description="True iff a per-customer secret is recorded in the store.")
    created_at: datetime
    updated_at: datetime
