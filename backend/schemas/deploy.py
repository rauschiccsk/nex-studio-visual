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
