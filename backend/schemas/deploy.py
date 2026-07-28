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
    ``warnings`` carries what went imperfectly on a deploy that SUCCEEDED.
    """

    ok: bool
    event: DeployEventRead
    url: Optional[str] = None
    bumped_to: Optional[str] = Field(
        default=None,
        description="Set when a first-PROD deploy bumped the project version (e.g. 'v1.0.0'); else None.",
    )
    warnings: list[str] = Field(
        default_factory=list,
        # Populated from ``deploy_service.deploy(...).warnings`` (a ``DeployOutcome``, backend/services/
        # deploy.py): the provisioner's non-fatal notes plus a missing NEX Manager pairing, which leaves a
        # token-launch app's one-click "Spustiť" off while the app itself is deployed and serving. The deploy
        # screen renders each as an amber ⚠ line under the customer's row — the ONLY place these reach a human,
        # since ``event.detail`` is shown only when a deploy failed.
        description=(
            "Non-fatal, non-secret notes about a deploy that SUCCEEDED — what a manager must know and act on "
            "although nothing failed (e.g. no paired NEX Manager, so one-click launch stays off until one is "
            "deployed for this customer). A warning NEVER means failure: ``ok`` stays True."
        ),
    )


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
    uat_last_attempt_failed: bool = Field(
        default=False,
        description="True when the customer's newest UAT deploy attempt FAILED (the cell shows the last-good "
        "version but the upgrade didn't land) — surfaced so a failed deploy never reads as a green cell (#5).",
    )
    prod_last_attempt_failed: bool = Field(
        default=False,
        description="True when the customer's newest PROD deploy attempt FAILED — same honest flag as UAT (#5).",
    )
    accepted_versions: list[str] = Field(
        default_factory=list,
        description="Versions accepted-for-PROD for this customer — the only versions whose PROD Nasadiť is open.",
    )
    uat_url: Optional[str] = Field(
        default=None,
        description="Link to the customer's live UAT instance (the UAT tab link, §3.5); None until a UAT deploy.",
    )
    prod_url: Optional[str] = Field(
        default=None,
        description="Link to the customer's live PROD instance (the PROD tab link); None until a PROD deploy.",
    )


#: Nothing is blocking a deploy — at least one version is deployable.
DEPLOY_CAUSE_OK = "ok"
#: The version WAS finished, but the project's code moved past the point it was checked at, so it
#: auto-un-verified. Recoverable in place with ``overit_znovu`` — the ONLY cause that offers a button.
DEPLOY_CAUSE_DRIFT = "drift"
#: A drifted version whose re-verification is running RIGHT NOW (evidence-checked, not merely "busy").
DEPLOY_CAUSE_REVERIFY_RUNNING = "reverify_running"
#: A drifted version that is mid-work or stuck for some OTHER reason (a build turn, a block, a pause).
#: ``overit_znovu`` fail-closes on anything but a settled state, so no button — and no self-unlock promise.
DEPLOY_CAUSE_VERSION_BUSY = "version_busy"
#: The check PASSED and the version is only waiting for the manager's Hotovo approval — it has not reached
#: the deployable stage yet. One approval click away; never describe this as "stale".
DEPLOY_CAUSE_AWAITING_SIGNOFF = "awaiting_signoff"
#: Later work outranked the sign-off. ``overit_znovu`` is not offered for this shape anywhere and its
#: handler rejects it — the screen must route to the version instead of drawing a button that would fail.
DEPLOY_CAUSE_STALE_SIGNOFF = "stale_signoff"
#: No version of this project was ever finished — nothing to deploy, and nothing to re-verify.
DEPLOY_CAUSE_NONE_FINISHED = "none_finished"

#: The closed cause vocabulary. Declared as a ``Literal`` (like ``Environment`` / ``EventType`` above) so
#: FastAPI emits an OpenAPI enum and ``npm run codegen`` carries it to the frontend as a union — the cause
#: vocabulary then has ONE source of truth instead of a hand-synced copy in the TS types.
DeployCause = Literal[
    "ok",
    "drift",
    "reverify_running",
    "version_busy",
    "awaiting_signoff",
    "stale_signoff",
    "none_finished",
]


class DeployBlock(BaseModel):
    """WHY the Nasadiť button is closed — the cause the UAT/PROD screen renders (v4.0.54).

    "Verified" is recomputed against live git on every read, so a version drops out of
    ``verified_versions`` the moment the project's code moves past the commit it was checked at. Without
    this block the screen could only grey the button out in silence (incident 2026-07-26). Carries the
    machine-readable cause, the implicated version's identity (so the screen can name it and act on it)
    and whether THIS user may trigger the recovery.
    """

    cause: DeployCause = Field(
        default=DEPLOY_CAUSE_OK,
        description=(
            "'ok' (a version is deployable) | 'drift' (code moved past the checked commit — re-verifiable "
            "in place, the only cause with a button) | 'reverify_running' (that re-verification is running "
            "now) | 'version_busy' (drifted, but mid-work or stuck — re-verify would be rejected) | "
            "'awaiting_signoff' (the check passed; it only needs the manager's Hotovo approval) | "
            "'stale_signoff' (later work outranked the sign-off — must be re-checked on the version) | "
            "'none_finished' (no version was ever finished)."
        ),
    )
    version_number: Optional[str] = Field(
        default=None,
        description="The implicated version (the one a manager would deploy); None when no version is implicated.",
    )
    version_id: Optional[UUID] = Field(
        default=None,
        description="The implicated version's id — what the re-verify action is posted against.",
    )
    can_reverify: bool = Field(
        default=False,
        description=(
            "True iff THIS user may post 'overit_znovu' for this version right now. Backend-computed: the "
            "matrix is readable more widely than the pipeline action is writable, and only the 'drift' cause "
            "has a handler that accepts the action."
        ),
    )


class DeployMatrix(BaseModel):
    """The full version × customer matrix payload for a project's UAT/PROD tabs.

    One read feeds both tabs: ``verified_versions`` populates the Nasadiť
    dropdown (only Hotovo/verified versions are deployable, §3.4/CR-V2-014), and
    ``rows`` carries each customer's per-environment deployed version + the
    accepted-for-PROD set (§3.3/§3.5).
    """

    project_slug: str
    #: The project's launch mode ('token' | 'password') — drives whether the UAT tab offers a
    #: token-launch 'Spustiť' (opens the app logged-in) vs the plain 'Otvoriť aplikáciu' link (v4.0.30).
    auth_mode: str = "password"
    verified_versions: list[str] = Field(
        default_factory=list,
        description="Deployable (verified / Hotovo) version_numbers — the Nasadiť dropdown options.",
    )
    #: WHY Nasadiť is closed when ``verified_versions`` is empty — cause + implicated version + whether this
    #: user may re-verify it (v4.0.54). Defaulted so an older client / a fixture-built matrix stays valid.
    deployability: DeployBlock = Field(default_factory=DeployBlock)
    can_accept: bool = Field(
        default=False,
        description=(
            "True iff THIS user may record a UAT acceptance (``require_ri_role``). Acceptance is what OPENS "
            "PROD for a (version, customer) — the ri-only Director gate of v4.0.35/D3, deliberately narrower "
            "than the owner-or-ri UAT deploy. Surfaced so the button can be disabled WITH a reason instead of "
            "looking live and then failing with 403."
        ),
    )
    can_deploy_prod: bool = Field(
        default=False,
        description=(
            "True iff THIS user may deploy to PROD (the deploy route's ``environment == 'prod'`` role gate). "
            "PROD deploy is ri-only by the same v4.0.35/D3 Director gate as acceptance, while UAT deploy is "
            "open to the project owner — so the PROD 'Nasadiť' cannot be hidden by page-level role and needs "
            "its own flag, or it looks live to a Junior/Medior and then fails with 403."
        ),
    )
    can_deploy: bool = Field(
        default=True,
        description=(
            "Či tento používateľ smie nasadzovať tento projekt VÔBEC — prvá bránka trasy "
            "nasadenia (vlastník alebo správca), platná pre UAT aj PROD. Bez nej sa tlačidlo "
            "„Nasadiť“ tvárilo živo aj pre Mediora, ktorý projekt nevlastní, a skončilo "
            "zamietnutím — tá istá chyba, akú ``can_deploy_prod`` rieši pre PROD."
        ),
    )
    rows: list[DeployMatrixRow] = Field(default_factory=list)
