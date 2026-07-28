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

import re
from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ---------------------------------------------------------------------------
# Deploy-safe identifiers — the form must refuse what the deploy path refuses
# ---------------------------------------------------------------------------
#
# ``backend.services.deploy._customer_dir_slug`` derives a customer's instance
# DIRECTORY / Traefik host / instance slug from ``(subdomain or slug).strip().lower()``
# and hands it to ``uat_provisioner.validate_uat_slug``, which enforces
# ``^[a-z0-9][a-z0-9-]*$``. Until this validator existed, only a length limit
# guarded the form: ``andros s.r.o.`` / ``andros_sk`` / ``andros.sk`` were accepted
# happily and blew up days later as a FAILED DEPLOY — far from the person who typed
# them, with nothing on screen tying the failure back to the form. The SAME pattern
# is therefore enforced here, at the form, where the mistake is made and fixable.
_INSTANCE_LABEL_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def _instance_label_message(field_label: str) -> str:
    """The Slovak rule text shown to the person (surfaces as the 422 ``detail``).

    States what IS allowed — a bare "invalid" would leave them guessing.
    """
    return (
        f"{field_label} smie obsahovať len malé písmená a-z, číslice 0-9 a spojovník (-) "
        f"a musí začínať písmenom alebo číslicou (napr. „andros-sk“). "
        f"Medzery, bodky, podčiarkovníky ani diakritika povolené nie sú."
    )


def _normalize_instance_label(value: str, field_label: str) -> str:
    """Trim + lowercase, then enforce the deploy pattern on the result.

    Normalisation is deliberate and mirrors :func:`deploy._customer_dir_slug`
    (``.strip().lower()``): what is stored is then exactly what the deploy path
    will use, so the two can never drift, the ``(project_id, …)`` uniqueness checks
    compare canonical values, and a legacy mixed-case row (e.g. ``ANDROS``) stays
    EDITABLE through the form instead of becoming a dead end.

    Raises:
        ValueError: with the Slovak rule text, when the normalised value still
            carries a character the deploy path cannot use.
    """
    normalized = value.strip().lower()
    if not _INSTANCE_LABEL_RE.match(normalized):
        raise ValueError(_instance_label_message(field_label))
    return normalized


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
        description="URL-safe customer slug, unique within the project. Pattern: ^[a-z0-9][a-z0-9-]*$.",
    )
    subdomain: Optional[str] = Field(
        default=None,
        max_length=255,
        description="Customer URL host label (e.g. 'andros'). Pattern: ^[a-z0-9][a-z0-9-]*$.",
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

    @field_validator("slug")
    @classmethod
    def _validate_slug(cls, v: str) -> str:
        return _normalize_instance_label(v, "Skratka")

    @field_validator("subdomain")
    @classmethod
    def _validate_subdomain(cls, v: Optional[str]) -> Optional[str]:
        # A blank / whitespace-only subdomain is NOT a valid host label — it is "not set".
        # (Whitespace-only is truthy in ``(subdomain or slug)``, so it would otherwise reach
        # the deploy path as an EMPTY instance dir.) Normalise it to None → falls back to slug.
        if v is None or not v.strip():
            return None
        return _normalize_instance_label(v, "Subdoména")


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

    @field_validator("slug")
    @classmethod
    def _validate_slug(cls, v: Optional[str]) -> Optional[str]:
        # ``None`` = field omitted from the PATCH ("leave unchanged"), not a value to validate.
        #
        # Lenient here, unlike create. The deploy path validates the DERIVED label
        # ``(subdomain or slug)``, not the two fields separately, so a customer registered before
        # this rule — slug ``icc.sk`` with subdomain ``icc`` — deploys perfectly well today.
        # Rejecting its slug on edit would block every unrelated change (fixing a typo in the name)
        # on a row that is not broken, and the form resends the existing slug on every save.
        # Collisions and deploy-safety of the EFFECTIVE label are enforced in ``customer_service``,
        # where both values are known after the merge.
        if v is None:
            return None
        return v.strip().lower()

    @field_validator("subdomain")
    @classmethod
    def _validate_subdomain(cls, v: Optional[str]) -> Optional[str]:
        # Blank → None = "leave unchanged" (PATCH semantics), never an empty instance dir.
        if v is None or not v.strip():
            return None
        return _normalize_instance_label(v, "Subdoména")


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
