"""Pydantic schemas for the per-customer Deploy subsystem (v2.0.0, CR-V2-026).

Mirrors :mod:`backend.db.models.deploy.DeployEvent` (the deploy/accept audit-log)
plus the request/response shapes for the deploy + acceptance actions (design §3.4
deploy flow, §3.5 acceptance gate, §3.6 versioning).

**Secret invariant (CLAUDE.md §4/§5, OQ-5).** No schema here carries secret
material. Per-customer secrets live only in the credentials store; the deploy
backend points into it and never echoes a secret in a request or a response.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# Literals kept in lock-step with the DB CHECK value tuples in
# :mod:`backend.db.models.deploy`.
Environment = Literal["uat", "prod"]
EventType = Literal["deploy", "accept"]
DeployStatus = Literal["ok", "failed"]


class DeployRequest(BaseModel):
    """Payload for the Nasadiť action — deploy a verified version to a customer.

    ``customer_id`` is supplied by the route path. The body carries the chosen
    verified ``version_number`` and the target ``environment`` (uat | prod).
    ``force_fresh`` is the explicit, opt-in escape hatch that re-provisions the
    instance from scratch (rotating secrets, wiping data); it defaults to False
    so EVERY redeploy preserves data + secrets + extra_hosts (§3.7, the
    inbox-UAT lesson). It maps to ``uat_provisioner.provision_uat(rotate_secrets=...)``.
    """

    version_number: str = Field(..., min_length=1, max_length=50, description="The verified version to deploy.")
    environment: Environment = Field(..., description="Target environment: uat | prod.")
    force_fresh: bool = Field(
        default=False,
        description=(
            "Opt-in: re-provision from scratch (rotate secrets, fresh data). Default False — "
            "a redeploy PRESERVES data + secrets + extra_hosts and runs migrations (§3.7)."
        ),
    )


class AcceptRequest(BaseModel):
    """Payload for the Akceptovať action — record a Manažér's UAT acceptance.

    ``customer_id`` is supplied by the route path; the actor is the authenticated
    user. The body carries the accepted ``version_number``. Recording an
    acceptance opens the PROD deploy for that (version, customer) pair (§3.5).
    """

    version_number: str = Field(..., min_length=1, max_length=50, description="The UAT version being accepted.")


class DeployEventRead(BaseModel):
    """Serialised deploy/accept audit-log row (who / when / version / customer).

    ``detail`` is a non-secret human-readable summary only.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    seq: int
    customer_id: UUID
    project_id: UUID
    version_number: str
    environment: Environment
    event_type: EventType
    status: DeployStatus
    actor_id: Optional[UUID] = None
    detail: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class DeployResult(BaseModel):
    """Outcome of a deploy action — the recorded event + the deployed URL.

    ``ok`` mirrors the recorded event status; ``detail`` is a non-secret
    summary. ``url`` is the customer instance's public URL (None when no
    frontend route exists). ``bumped_to`` carries the new version_number when a
    first-PROD deploy bumped the project to v1.0.0 (§3.6), else None.
    """

    ok: bool
    event: DeployEventRead
    url: Optional[str] = None
    bumped_to: Optional[str] = Field(
        default=None,
        description="Set when a first-PROD deploy bumped the project version (e.g. 'v1.0.0'); else None.",
    )
    warnings: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# The version × customer matrix (drives the UAT / PROD tabs — CR-V2-027, §3.3)
# ---------------------------------------------------------------------------


class DeployMatrixRow(BaseModel):
    """One customer's row in the version × customer matrix (design §3.3).

    ``uat_version`` / ``prod_version`` are the customer's currently-deployed
    versions per environment (None = never deployed there). Different customers
    may carry different versions simultaneously. ``accepted_versions`` lists the
    versions this customer has a recorded UAT acceptance for — the PROD tab uses
    it to keep Nasadiť disabled until the (version, customer) pair is accepted
    (the never-bypassed gate, §3.5). Carries NO secret material (§4/OQ-5).
    """

    customer_id: UUID
    customer_name: str
    customer_slug: str
    subdomain: Optional[str] = None
    uat_version: Optional[str] = Field(default=None, description="Currently deployed UAT version (None = never).")
    prod_version: Optional[str] = Field(default=None, description="Currently deployed PROD version (None = never).")
    accepted_versions: list[str] = Field(
        default_factory=list,
        description="Versions accepted-for-PROD for this customer — the only versions whose PROD Nasadiť is open.",
    )
    uat_url: Optional[str] = Field(
        default=None,
        description="Link to the customer's live UAT instance (the UAT tab link, §3.5); None until a UAT deploy.",
    )


class DeployMatrix(BaseModel):
    """The full version × customer matrix payload for a project's UAT/PROD tabs.

    One read feeds both tabs: ``verified_versions`` populates the Nasadiť
    dropdown (only Hotovo/verified versions are deployable, §3.4/CR-V2-014), and
    ``rows`` carries each customer's per-environment deployed version + the
    accepted-for-PROD set (§3.3/§3.5).
    """

    project_slug: str
    verified_versions: list[str] = Field(
        default_factory=list,
        description="Deployable (verified / Hotovo) version_numbers — the Nasadiť dropdown options.",
    )
    rows: list[DeployMatrixRow] = Field(default_factory=list)
