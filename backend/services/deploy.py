"""Service layer for the per-customer Deploy subsystem (v2.0.0, CR-V2-026).

Implements design §3 (Deploy & Customers) — the per-customer, OUT-of-pipeline
deploy backend that productizes ``uat_provisioner.py`` (§3.4 deploy flow, §3.5
acceptance gate, §3.6 versioning, §3.7 fresh-first-then-data-preserving). It is
the engine-agnostic deploy layer the UAT/PROD tabs (CR-V2-027) call.

Four load-bearing invariants this module enforces (all tested):

1. **§3.7 fresh-first-then-data-preserving.** The FIRST install of a customer
   instance is empty; EVERY later deploy PRESERVES the accumulated data +
   secrets + ``extra_hosts`` and runs migrations — it NEVER wipes data or
   rotates secrets by default (the inbox-UAT redeploy incident). The opt-in
   ``force_fresh`` flag forces a fresh re-provision (rotating secrets); the
   default preserves. This is delegated to ``uat_provisioner.provision_uat``
   whose ``rotate_secrets`` parameter already implements the preservation
   contract — deploy.py POINTS INTO it, it does not re-implement secrets.

2. **The never-bypassed UAT acceptance gate (§3.5).** No PROD deploy of a
   (version, customer) is permitted without a recorded ``accept`` event for that
   exact pair (incident 2026-06-10). :func:`deploy` checks the audit-log before
   any PROD provisioning.

3. **Versioning (§3.6).** The first PROD deploy of a project bumps its version
   to ``v1.0.0`` (from the dev ``v0.x.y``); subsequent PROD deploys advance
   independently per customer.

4. **Deploy is ALWAYS manual + outside the Miera autonómie dial (D6/OQ-3).**
   This module has no autonomy hooks; it is driven only by an explicit Manažér
   action (the Nasadiť / Akceptovať buttons).

**Secret governance (OQ-5 / CLAUDE.md §4/§5).** This module NEVER reads, writes,
logs, or returns secret material. Per-customer secrets live only in the
credentials store (``backend/services/credentials.py``); the deploy backend
points into it (via ``Customer.credential_id``) and ``uat_provisioner`` handles
secret synthesis/preservation behind a ``chmod 600`` ``.env`` that is never
returned. The ``detail`` recorded on a deploy event is a non-secret summary.

Service conventions (mirrors the other services): methods take ``db: Session``
first and only ``flush()``; commit/rollback is the router's job. Errors surface
as :class:`ValueError` for the router to map to HTTP status codes. The
provision+up leg is async (a ~1–2 min docker build) and is injected via
``deploy_runner`` so it is faked in tests (``git``/``docker`` are never spawned).
"""

from __future__ import annotations

import asyncio
from typing import Awaitable, Callable, Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db.models.customers import Customer
from backend.db.models.deploy import DeployEvent
from backend.db.models.pipeline import PipelineState
from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.services import uat_provisioner

# The terminal pipeline stage = "Hotovo" = a version is VERIFIED (design §3.1,
# CR-V2-014: reaching ``done`` means *verified*, not *deployed*). Only a verified
# version may be deployed to a customer — the Nasadiť dropdown lists exactly these.
VERIFIED_STAGE = "done"

# The version a first PROD deploy bumps the project to (§3.6).
FIRST_PROD_VERSION = "v1.0.0"

# A "deploy runner" provisions + brings up a customer instance and returns
# ``(ok, detail, url)``. The default points at the real provisioner + docker
# compose up (orchestrator-owned, async); tests inject a fake so no docker is
# spawned. ``detail`` / ``url`` are non-secret.
DeployRunner = Callable[..., Awaitable[tuple[bool, str, Optional[str]]]]


# ---------------------------------------------------------------------------
# Resolution helpers
# ---------------------------------------------------------------------------


def _get_customer(db: Session, customer_id: UUID) -> Customer:
    customer = db.get(Customer, customer_id)
    if customer is None:
        raise ValueError(f"Customer {customer_id} not found")
    return customer


def _get_project(db: Session, project_id: UUID) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise ValueError(f"Project {project_id} not found")
    return project


def _resolve_version(db: Session, project_id: UUID, version_number: str) -> Version:
    """The verified version to deploy — it MUST exist for the project (CR-V2-014 boundary).

    Deploy operates only on a version the pipeline has produced; an unknown
    version_number is a hard error (the router maps to 404).
    """
    version = db.execute(
        select(Version).where(Version.project_id == project_id, Version.version_number == version_number)
    ).scalar_one_or_none()
    if version is None:
        raise ValueError(f"Version {version_number!r} not found for this project")
    return version


# ---------------------------------------------------------------------------
# Audit-log queries (the acceptance gate + the version × customer matrix)
# ---------------------------------------------------------------------------


def is_accepted(db: Session, customer_id: UUID, version_number: str) -> bool:
    """True iff an ``accept`` event exists for this exact (customer, version) pair (§3.5).

    This is the source of truth for the never-bypassed PROD gate: PROD opens for
    a customer/version ONLY once the Manažér has accepted that customer's UAT of
    that version.
    """
    stmt = select(DeployEvent.id).where(
        DeployEvent.customer_id == customer_id,
        DeployEvent.version_number == version_number,
        DeployEvent.event_type == "accept",
    )
    return db.execute(stmt).first() is not None


def list_events(db: Session, customer_id: UUID) -> list[DeployEvent]:
    """Every deploy/accept event for a customer, newest first (the audit trail).

    Ordered by the monotonic ``seq`` (not ``created_at``, which is identical for
    rows written in one transaction) so "newest first" is deterministic.
    """
    stmt = select(DeployEvent).where(DeployEvent.customer_id == customer_id).order_by(DeployEvent.seq.desc())
    return list(db.execute(stmt).scalars().all())


def list_project_events(db: Session, project_id: UUID) -> list[DeployEvent]:
    """Every deploy/accept event for a whole project, newest first (UAT/PROD matrix feed)."""
    stmt = select(DeployEvent).where(DeployEvent.project_id == project_id).order_by(DeployEvent.seq.desc())
    return list(db.execute(stmt).scalars().all())


def current_version(db: Session, customer_id: UUID, environment: str) -> Optional[str]:
    """The version a customer currently runs in ``environment`` — the latest OK ``deploy`` event.

    Returns ``None`` when the customer has never had a successful deploy to that
    environment. Drives the version × customer matrix cell (§3.3).
    """
    stmt = (
        select(DeployEvent.version_number)
        .where(
            DeployEvent.customer_id == customer_id,
            DeployEvent.environment == environment,
            DeployEvent.event_type == "deploy",
            DeployEvent.status == "ok",
        )
        .order_by(DeployEvent.seq.desc())
        .limit(1)
    )
    return db.execute(stmt).scalar_one_or_none()


def _project_had_prod_deploy(db: Session, project_id: UUID) -> bool:
    """True iff ANY customer of the project has ever had a successful PROD deploy (§3.6).

    The version bump to v1.0.0 happens on the project's FIRST PROD deploy — across
    all its customers, not per customer (the project graduates once).
    """
    stmt = select(DeployEvent.id).where(
        DeployEvent.project_id == project_id,
        DeployEvent.environment == "prod",
        DeployEvent.event_type == "deploy",
        DeployEvent.status == "ok",
    )
    return db.execute(stmt).first() is not None


# ---------------------------------------------------------------------------
# The version × customer matrix feed (drives the UAT / PROD tabs, CR-V2-027)
# ---------------------------------------------------------------------------


def list_verified_versions(db: Session, project_id: UUID) -> list[str]:
    """The project's VERIFIED version_numbers — deployable via Nasadiť (design §3.4).

    A version is deployable only once the pipeline has carried it to **Hotovo**
    (``pipeline_state.current_stage == 'done'``) — "verified", per CR-V2-014's
    "Hotovo ≠ deployed" boundary. The Nasadiť dropdown in the UAT/PROD tabs lists
    exactly these; a non-verified (still in-flight) version is never offered.

    Ordered ``version_number`` descending so the newest verified version is the
    natural default. A version with no pipeline_state row is NOT verified (it has
    never run), so the INNER join is intentional.
    """
    stmt = (
        select(Version.version_number)
        .join(PipelineState, PipelineState.version_id == Version.id)
        .where(
            Version.project_id == project_id,
            PipelineState.current_stage == VERIFIED_STAGE,
        )
        .order_by(Version.version_number.desc())
    )
    return list(db.execute(stmt).scalars().all())


def accepted_versions(db: Session, customer_id: UUID) -> list[str]:
    """The version_numbers a customer has a recorded UAT acceptance for (§3.5).

    These are the ONLY versions whose PROD deploy is open for this customer — the
    never-bypassed acceptance gate, surfaced to the PROD tab so a blocked Nasadiť
    is shown disabled rather than failing on submit. Deduplicated, newest first.
    """
    stmt = (
        select(DeployEvent.version_number)
        .where(
            DeployEvent.customer_id == customer_id,
            DeployEvent.event_type == "accept",
        )
        .order_by(DeployEvent.seq.desc())
    )
    # Preserve newest-first order while de-duplicating repeat acceptances.
    seen: set[str] = set()
    result: list[str] = []
    for version_number in db.execute(stmt).scalars().all():
        if version_number not in seen:
            seen.add(version_number)
            result.append(version_number)
    return result


def build_matrix(db: Session, project: Project) -> dict:
    """Assemble the version × customer matrix for a project's UAT/PROD tabs (§3.3).

    One read returns everything both tabs render:
      * ``verified_versions`` — the deployable version_numbers (Nasadiť dropdown).
      * ``rows`` — per customer: the currently-deployed UAT and PROD versions plus
        the versions accepted-for-PROD (so the PROD tab can disable Nasadiť until
        that (version, customer) is accepted — the never-bypassed gate).

    Because deployment is per customer, different customers may carry different
    ``uat_version`` / ``prod_version`` at the same time (§3.3). The shape is a
    plain dict the route serialises via the Pydantic response model.
    """
    customers = (
        db.execute(select(Customer).where(Customer.project_id == project.id).order_by(Customer.created_at.desc()))
        .scalars()
        .all()
    )

    rows = []
    for customer in customers:
        uat_version = current_version(db, customer.id, "uat")
        rows.append(
            {
                "customer_id": customer.id,
                "customer_name": customer.name,
                "customer_slug": customer.slug,
                "subdomain": customer.subdomain,
                "uat_version": uat_version,
                "prod_version": current_version(db, customer.id, "prod"),
                "accepted_versions": accepted_versions(db, customer.id),
                # The UAT tab links to the live instance only once it has a UAT
                # deploy (§3.5 "link to the UAT URL"); None hides the link.
                "uat_url": _instance_url(customer, "uat") if uat_version else None,
            }
        )

    return {
        "project_slug": project.slug,
        "verified_versions": list_verified_versions(db, project.id),
        "rows": rows,
    }


# ---------------------------------------------------------------------------
# The default deploy runner (real provision + docker compose up)
# ---------------------------------------------------------------------------


async def _default_deploy_runner(
    *,
    project_slug: str,
    uat_slug: str,
    version_number: str,
    force_fresh: bool,
) -> tuple[bool, str, Optional[str]]:
    """Provision (preserve-by-default) then bring up a customer instance.

    Renders ``/opt/uat/<uat_slug>/`` via :func:`uat_provisioner.provision_uat`
    (``rotate_secrets=force_fresh`` — default False PRESERVES secrets + data +
    extra_hosts and runs migrations on ``up``), then delegates the build/up +
    serve-verify to the orchestrator's ``_run_uat_deploy``. Returns
    ``(ok, detail, url)``; never raises (the orchestrator leg already returns a
    tuple). The ``.env`` content is never read/returned here — only the
    non-secret deploy outcome.

    Imported lazily (the orchestrator imports the deploy chain transitively;
    keeping the import inside the function avoids an import cycle at module load).
    """
    from backend.services import orchestrator

    def _provision() -> uat_provisioner.ProvisionResult:
        return uat_provisioner.provision_uat(
            project_slug,
            uat_slug,
            version=version_number,
            rotate_secrets=force_fresh,
        )

    try:
        result = await asyncio.to_thread(_provision)
    except (FileNotFoundError, ValueError) as exc:
        return False, f"provision failed: {exc}", None

    ok, detail = await orchestrator._run_uat_deploy(project_slug, uat_slug)
    url = _url_for_instance_slug(uat_slug) if result.fe_service else None
    # Surface any provision warnings in the recorded (non-secret) detail.
    if result.warnings:
        detail = f"{detail} | warnings: {'; '.join(result.warnings)}"
    return ok, detail, url


# ---------------------------------------------------------------------------
# Acceptance (Akceptovať) — opens PROD for a (version, customer) pair
# ---------------------------------------------------------------------------


def accept(db: Session, customer_id: UUID, version_number: str, actor_id: Optional[UUID]) -> DeployEvent:
    """Record a Manažér's UAT acceptance for a (customer, version) pair (§3.5).

    Recording an ``accept`` event opens the PROD deploy for that exact pair. The
    acceptance is logged (who=actor / when=created_at / version / customer) — the
    per-customer acceptance gate, never bypassed. Requires the customer to have a
    successful UAT deploy of that version first (you cannot accept what was never
    deployed to UAT).

    Raises:
        ValueError: customer/version not found, or no successful UAT deploy of
            that version exists yet (router → 404 / 409).
    """
    customer = _get_customer(db, customer_id)
    _resolve_version(db, customer.project_id, version_number)

    uat_version = current_version(db, customer_id, "uat")
    has_uat_deploy = db.execute(
        select(DeployEvent.id).where(
            DeployEvent.customer_id == customer_id,
            DeployEvent.version_number == version_number,
            DeployEvent.environment == "uat",
            DeployEvent.event_type == "deploy",
            DeployEvent.status == "ok",
        )
    ).first()
    if has_uat_deploy is None:
        raise ValueError(
            f"Cannot accept {version_number!r}: it has not been successfully deployed to this customer's UAT "
            f"(current UAT version: {uat_version or 'none'})"
        )

    event = DeployEvent(
        customer_id=customer_id,
        project_id=customer.project_id,
        version_number=version_number,
        environment="uat",
        event_type="accept",
        status="ok",
        actor_id=actor_id,
        detail="UAT accepted — PROD opened",
    )
    db.add(event)
    db.flush()
    return event


# ---------------------------------------------------------------------------
# Deploy (Nasadiť) — the per-customer provision/update
# ---------------------------------------------------------------------------


async def deploy(
    db: Session,
    customer_id: UUID,
    *,
    version_number: str,
    environment: str,
    actor_id: Optional[UUID],
    force_fresh: bool = False,
    deploy_runner: Optional[DeployRunner] = None,
) -> tuple[DeployEvent, Optional[str], Optional[str]]:
    """Deploy a verified ``version_number`` to a customer's ``environment`` instance (§3.4).

    Returns ``(event, url, bumped_to)``:
      * ``event`` — the recorded deploy audit-log row (who/when/version/customer/status).
      * ``url`` — the deployed instance URL (None when no FE route / on failure).
      * ``bumped_to`` — the new project version_number when a first-PROD deploy
        bumped to v1.0.0 (§3.6), else None.

    Enforces, in order:
      1. The version exists for the project (verified-version boundary, CR-V2-014).
      2. **PROD gate (§3.5):** a ``prod`` deploy requires a recorded ``accept`` for
         this (customer, version) — never bypassed.
      3. **§3.6 versioning:** the project's FIRST successful PROD deploy bumps the
         project's dev version to ``v1.0.0`` (the bumped version is what is
         actually provisioned + recorded).
      4. The provision is **preserve-by-default** (``force_fresh=False`` →
         ``rotate_secrets=False`` → data + secrets + extra_hosts survive; §3.7).

    NB this method opens NO DB transaction control — it flushes the audit event;
    the router commits. The async provision/up runs OUTSIDE the DB transaction
    (it is I/O), so the event is added AFTER the runner returns to record the
    real outcome.

    Raises:
        ValueError: customer/version not found, unknown environment, or a PROD
            deploy without an acceptance (router → 404 / 409 / 422).
    """
    if environment not in ("uat", "prod"):
        raise ValueError(f"Unknown environment {environment!r} (expected 'uat' or 'prod')")

    # Resolve the runner at call time (NOT a parameter default) so a caller / test
    # that monkeypatches the module-level ``_default_deploy_runner`` is respected
    # — the HTTP route never injects a runner, so the route test patches this.
    runner = deploy_runner or _default_deploy_runner

    customer = _get_customer(db, customer_id)
    project = _get_project(db, customer.project_id)
    # Validate the requested version exists for the project (verified boundary).
    _resolve_version(db, project.id, version_number)

    # PROD gate (§3.5) — never bypassed.
    if environment == "prod" and not is_accepted(db, customer_id, version_number):
        raise ValueError(
            f"PROD deploy blocked: version {version_number!r} has no recorded UAT acceptance for this customer "
            f"— accept the customer's UAT first (the acceptance gate is never bypassed)"
        )

    # §3.6 versioning: the project's first PROD deploy bumps to v1.0.0. The bumped
    # version is what is provisioned + recorded so the audit row reflects PROD reality.
    deployed_version = version_number
    bumped_to: Optional[str] = None
    if environment == "prod" and not _project_had_prod_deploy(db, project.id):
        deployed_version = FIRST_PROD_VERSION
        bumped_to = FIRST_PROD_VERSION
        # Reflect the graduation on the project's own version record set: ensure a
        # v1.0.0 version row exists for the project (idempotent) so the PROD tab and
        # version list show the graduated version. Never overwrites an existing row.
        _ensure_version(db, project.id, FIRST_PROD_VERSION, source=version_number)

    # Per-customer UAT/PROD instance slug: customer subdomain (preferred) or slug,
    # namespaced by environment so a customer's UAT and PROD never collide.
    instance_slug = _instance_slug(customer, environment)

    ok, detail, url = await runner(
        project_slug=project.slug,
        uat_slug=instance_slug,
        version_number=deployed_version,
        force_fresh=force_fresh,
    )

    event = DeployEvent(
        customer_id=customer_id,
        project_id=project.id,
        version_number=deployed_version,
        environment=environment,
        event_type="deploy",
        status="ok" if ok else "failed",
        actor_id=actor_id,
        detail=detail,
    )
    db.add(event)
    db.flush()

    # A failed deploy did not graduate the project — drop the bump signal.
    if not ok:
        bumped_to = None

    return event, (url if ok else None), bumped_to


def _instance_slug(customer: Customer, environment: str) -> str:
    """Derive the per-customer instance slug for an environment.

    ``<subdomain-or-slug>-<env>`` keeps a customer's UAT and PROD instances on
    distinct ``/opt/uat/<slug>`` namespaces (instance-per-customer-per-env). The
    result is validated by ``uat_provisioner.validate_uat_slug`` at provision time.
    """
    base = (customer.subdomain or customer.slug).strip().lower()
    return f"{base}-{environment}"


def _url_for_instance_slug(instance_slug: str) -> str:
    """The public URL for a provisioned instance slug (single source of truth).

    ``https://uat-<instance_slug>.<UAT_DOMAIN_SUFFIX>`` — used by BOTH the deploy
    runner (post-provision URL) and the matrix (the UAT tab's link), so they can
    never drift (§3.4/§3.5).
    """
    return f"https://uat-{instance_slug}.{uat_provisioner.UAT_DOMAIN_SUFFIX}"


def _instance_url(customer: Customer, environment: str) -> str:
    """The public URL of a customer's per-environment instance (design §3.5 link)."""
    return _url_for_instance_slug(_instance_slug(customer, environment))


def _ensure_version(db: Session, project_id: UUID, version_number: str, *, source: str) -> Version:
    """Idempotently ensure a ``version_number`` row exists for the project (§3.6 graduation).

    Used when a first-PROD deploy graduates a project to v1.0.0 — the graduated
    version must appear in the project's version set. Never overwrites an existing
    row. Created directly (not via ``version_service.create``) so the graduation
    carries a descriptive name without re-running the create-flow side effects.
    """
    existing = db.execute(
        select(Version).where(Version.project_id == project_id, Version.version_number == version_number)
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    version = Version(
        project_id=project_id,
        version_number=version_number,
        name=f"PROD release (graduated from {source})",
        description=f"First production deploy — graduated from {source} (design §3.6).",
    )
    db.add(version)
    db.flush()
    return version
