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
from datetime import date
from pathlib import Path
from typing import Awaitable, Callable, Optional
from uuid import UUID

from sqlalchemy import select, update
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


def project_had_prod_deploy(db: Session, project_id: UUID) -> bool:
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


def _semver_sort_key(version_number: str) -> tuple[int, ...]:
    """A v-prefix-agnostic numeric sort key: ``'v1.2.0'`` and ``'1.2.0'`` → ``(1, 2, 0)``. A non-numeric part
    sorts as 0 (never crash on an odd label). Used so the deploy dropdown defaults to the genuinely newest
    verified version regardless of a mixed ``v``/no-``v`` version_number (Director obs 2026-07-11)."""
    return tuple(int(p) if p.isdigit() else 0 for p in version_number.lstrip("vV").split("."))


def list_verified_versions(db: Session, project_id: UUID) -> list[str]:
    """The project's VERIFIED version_numbers — deployable via Nasadiť (design §3.4).

    CR-V2-056 (layer-1 reality-anchoring): "verified" is COMPUTED from the live repo, not read as a stored
    ``done`` snapshot. Candidates = versions whose pipeline reached the ``done`` phase (the cache); each is
    then kept only if :func:`orchestrator.version_verified` still holds — i.e. its latest PASS verdict is
    bound to a commit SHA that STILL equals the repo HEAD. So a version whose HEAD drifted past its verified
    commit silently drops out of the deployable list (no frozen PASS). The repo HEAD is read ONCE per project
    (batch), then each candidate's stored SHA is compared in-DB — never a git subprocess per version.

    Ordered ``version_number`` descending so the newest verified version is the natural default.
    """
    from backend.services import claude_agent
    from backend.services.orchestrator import _repo_head, version_verified

    rows = db.execute(
        select(Version.id, Version.version_number)
        .join(PipelineState, PipelineState.version_id == Version.id)
        .where(Version.project_id == project_id, PipelineState.current_stage == VERIFIED_STAGE)
        .order_by(Version.version_number.desc())
    ).all()
    if not rows:
        return []
    slug = db.execute(select(Project.slug).where(Project.id == project_id)).scalar_one_or_none()
    head = _repo_head(claude_agent.PROJECTS_ROOT / slug) if slug else None  # read HEAD ONCE per project
    verified = [num for (vid, num) in rows if version_verified(db, vid, head=head)[0]]
    # Sort SEMANTICALLY, not by the SQL string order: version_number mixes 'v1.0.0' (the graduated first-PROD)
    # and '1.1.0', so a string desc put the OLDER 'v1.0.0' first ('v' > '1' in ASCII) and the Nasadiť dropdown
    # (verified_versions[0]) defaulted to an OLD version → accidental old-version deploy risk on UAT + PROD.
    return sorted(verified, key=_semver_sort_key, reverse=True)


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
        prod_version = current_version(db, customer.id, "prod")
        rows.append(
            {
                "customer_id": customer.id,
                "customer_name": customer.name,
                "customer_slug": customer.slug,
                "subdomain": customer.subdomain,
                "uat_version": uat_version,
                "prod_version": prod_version,
                "accepted_versions": accepted_versions(db, customer.id),
                # Each tab links to its live instance only once it has a deploy there (§3.5 "link to the URL");
                # None hides the link. Audit Theme 4: PROD now carries its own link, mirroring UAT.
                "uat_url": _instance_url(customer, "uat", project) if uat_version else None,
                "prod_url": _instance_url(customer, "prod", project) if prod_version else None,
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
    admin_password: Optional[str] = None,
) -> tuple[bool, str, Optional[str]]:
    """Provision (preserve-by-default) then bring up a customer instance — environment-aware.

    The environment + PROD layout are derived from the instance slug (``<base>-<env>``, see
    :func:`_instance_slug`): a ``-prod`` suffix selects PROD — provisioning the clean
    ``/opt/customers/<customer>/<full-project-slug>/`` layout with ``<customer>-<app>-*`` names + the
    ``<customer>-<app>.isnex.eu`` Traefik host — else UAT (``/opt/uat/<slug>/``, unchanged). The
    runner's public seam stays ``(project_slug, uat_slug, version_number, force_fresh)`` so an
    injected/faked runner is untouched; the env is threaded to the provisioner + deployer HERE.

    Renders via :func:`uat_provisioner.provision_uat` (``rotate_secrets=force_fresh`` — default False
    PRESERVES secrets + data + extra_hosts and runs migrations on ``up``), then delegates the build/up
    + serve-verify to the orchestrator (``_run_uat_deploy`` / ``_run_prod_deploy``). Returns
    ``(ok, detail, url)``; never raises (the orchestrator leg already returns a tuple). The ``.env``
    content is never read/returned here — only the non-secret deploy outcome.

    Imported lazily (the orchestrator imports the deploy chain transitively;
    keeping the import inside the function avoids an import cycle at module load).
    """
    from backend.services import orchestrator

    # The cockpit per-customer deploy ALWAYS has a customer: derive the clean per-project layout components for
    # BOTH environments (audit fix 2026-07-11 — UAT used to fall through to the flat ``/opt/uat/<slug>`` path,
    # yielding ``uat-<customer>-uat`` instead of the per-project ``uat-<customer>-<app>`` + nested
    # ``/opt/uat/<customer>/<project>``). The ``-prod``/``-uat`` suffix (from ``_instance_slug``) selects the
    # env + strips back to the customer. PROD is unchanged; the project-level uat-deploy.py path (which passes
    # NO customer_slug) stays flat + untouched.
    is_prod = uat_slug.endswith("-prod")
    environment = "prod" if is_prod else "uat"
    customer_slug = uat_slug.removesuffix("-prod") if is_prod else uat_slug.removesuffix("-uat")
    app = uat_provisioner.derive_uat_slug(project_slug)

    def _provision() -> uat_provisioner.ProvisionResult:
        return uat_provisioner.provision_uat(
            project_slug,
            uat_slug,
            version=version_number,
            rotate_secrets=force_fresh,
            environment=environment,
            customer_slug=customer_slug,
            app=app,
            full_project_slug=project_slug,
            admin_password=admin_password,
        )

    try:
        result = await asyncio.to_thread(_provision)
    except (FileNotFoundError, ValueError) as exc:
        return False, f"provision failed: {exc}", None

    if is_prod:
        ok, detail = await orchestrator._run_prod_deploy(
            project_slug, customer_slug, app, project_slug, version_number=version_number
        )
        url = _prod_url(customer_slug, app) if result.fe_service else None
    else:
        ok, detail = await orchestrator._run_uat_deploy(
            project_slug,
            uat_slug,
            environment="uat",
            customer_slug=customer_slug,
            app=app,
            full_project_slug=project_slug,
            version_number=version_number,
        )
        url = _url_for_instance_slug(f"{customer_slug}-{app}") if result.fe_service else None
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
    version = _resolve_version(db, project.id, version_number)
    # CR-V2-056 (layer-1): fail-closed verified guard on the ACTION itself (not just the dropdown) —
    # version_verified recomputes against the live HEAD, so a stale-done version whose HEAD drifted (or that
    # never actually passed) is refused here, closing the direct-POST bypass of the deployable-list filter.
    from backend.services.orchestrator import version_verified

    _ok, _prov = version_verified(db, version.id)
    if not _ok:
        raise ValueError(
            f"Deploy blocked: version {version_number!r} is not verified against the current code ({_prov}) — "
            "re-run Verifikácia on the current HEAD before deploying."
        )

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
    first_prod = environment == "prod" and not project_had_prod_deploy(db, project.id)
    if first_prod:
        deployed_version = FIRST_PROD_VERSION
        bumped_to = FIRST_PROD_VERSION

    # Per-customer UAT/PROD instance slug: customer subdomain (preferred) or slug,
    # namespaced by environment so a customer's UAT and PROD never collide.
    instance_slug = _instance_slug(customer, environment)

    # Self-sufficiency gate (audit P0, 2026-07-12): if the app seeds its admin login from ADMIN_INITIAL_PASSWORD
    # and the customer has NO secret set, the provisioner would render a RANDOM synthetic admin password nobody
    # can discover (§4 forbids surfacing it) → the manager is locked out of the instance they just deployed.
    # Block the deploy UP-FRONT (before any teardown) with a clear, actionable message, rather than shipping a
    # lockout. Reads the SAME ``<project>/.env.example`` the provisioner renders from.
    if customer.credential_id is None:
        from backend.services import claude_agent

        _env_example = claude_agent.PROJECTS_ROOT / project.slug / ".env.example"
        try:
            _declares_admin = uat_provisioner.ADMIN_LOGIN_ENV_KEY in uat_provisioner._parse_env_file(_env_example)
        except OSError:
            _declares_admin = False
        if _declares_admin:
            raise ValueError(
                "Nasadenie sa nedá spustiť: táto aplikácia potrebuje prihlasovacie heslo administrátora, ale "
                "zákazník ho nemá nastavené. Najprv nastav heslo zákazníka v sekcii Zákazníci a skús to znova."
            )

    # The deployed app's initial admin login (username ``admin``) = the customer's OWN secret (set in Zákazníci),
    # so the manager KNOWS it — otherwise the app seeds ``admin`` with a random synthetic nobody can discover and
    # the manager is locked out of their own instance (self-sufficiency kernel, 2026-07-11). Read via the
    # ri-gated credentials store by the customer's ``credential_id`` (None when no secret was set → the app's own
    # default applies). Held in-process, passed to the provisioner, NEVER logged/returned (§4).
    admin_password: Optional[str] = None
    if customer.credential_id is not None:
        from backend.services import credentials as credentials_service

        try:
            admin_password = credentials_service.read_content(db, customer.credential_id).content
        except (ValueError, OSError):
            admin_password = None

    ok, detail, url = await runner(
        project_slug=project.slug,
        uat_slug=instance_slug,
        version_number=deployed_version,
        force_fresh=force_fresh,
        admin_password=admin_password,
    )

    # Graduation (§3.6): on a SUCCESSFUL first PROD deploy, promote the BUILT version to v1.0.0 IN
    # PLACE + mark it released — never spin up a new empty v1.0.0 shell beside it (which would strand
    # its epics/backlog/tokens/pipeline history + break the metrics page). ``version`` is the row
    # resolved above; renaming it keeps all its children under the same id. Gated on ``ok`` so a failed
    # deploy never graduates and the version stays resolvable under its old number for a retry.
    if first_prod and ok:
        from backend.services import claude_agent

        _graduate_version_in_place(db, version, FIRST_PROD_VERSION, claude_agent.PROJECTS_ROOT / project.slug)

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
    """The per-customer instance slug carrying the ENV: ``<subdomain-or-slug>-<env>`` (``andros-uat`` /
    ``andros-prod``). The runner detects PROD via the ``-prod`` suffix and strips it back to the customer; the
    real instance DIR + name are the clean per-project ``[uat-]<customer>-<app>`` derived downstream (audit fix
    2026-07-11 — UAT no longer lands on the flat ``<customer>-uat``). Validated at provision time."""
    base = (customer.subdomain or customer.slug).strip().lower()
    return f"{base}-{environment}"


def _url_for_instance_slug(instance_slug: str) -> str:
    """The public URL for a provisioned instance slug (single source of truth).

    ``https://uat-<instance_slug>.<UAT_DOMAIN_SUFFIX>`` — used by BOTH the deploy
    runner (post-provision URL) and the matrix (the UAT tab's link), so they can
    never drift (§3.4/§3.5).
    """
    return f"https://uat-{instance_slug}.{uat_provisioner.UAT_DOMAIN_SUFFIX}"


def _instance_url(customer: Customer, environment: str, project: Project) -> str:
    """The public URL of a customer's per-environment instance (design §3.5 link) — UAT
    ``uat-<customer>-<app>.isnex.eu`` / PROD the clean ``<customer>-<app>.isnex.eu`` (via :func:`_prod_url`,
    NOT the ``uat-`` prefixed builder — a Theme-4 slip previously pointed the PROD matrix link at a
    non-existent ``uat-<customer>-prod`` host)."""
    base = (customer.subdomain or customer.slug).strip().lower()
    app = uat_provisioner.derive_uat_slug(project.slug)
    if environment == "prod":
        return _prod_url(base, app)
    return _url_for_instance_slug(f"{base}-{app}")


def _prod_url(customer_slug: str, app: str) -> str:
    """The public URL for a PROD instance — the clean ``https://<customer>-<app>.isnex.eu`` host (§2).

    ``customer_slug`` = ``(subdomain or slug).lower()`` (the base :func:`_instance_slug` uses); ``app``
    = the project slug with a leading ``nex-`` stripped (``uat_provisioner.derive_uat_slug``). The
    PROD ``(customer_slug, app, full_project_slug)`` triple is derived at the deploy-runner seam (from
    ``uat_slug``/``project_slug``) rather than passed as objects — the runner's public interface stays
    ``(project_slug, uat_slug, version_number, force_fresh)`` so an injected/faked runner is untouched.
    """
    return f"https://{customer_slug}-{app}.{uat_provisioner.UAT_DOMAIN_SUFFIX}"


def _move_release_note_dir(proj_root: Path, old_number: str, target: str) -> None:
    """Part 1 (per-app-changelog-standard.md §4): when graduation renames the built version, MOVE its
    committed ``RELEASE_NOTES.md`` into the target version dir + commit, so the served version number matches
    the note dir (the endpoint globs ``v*/RELEASE_NOTES.md`` and matches by ``version_number``). Without this
    the graduated version would show no note. BEST-EFFORT, NEVER raises (a moved note is not a release gate)."""
    from backend.services import release_note_writer
    from backend.services.orchestrator import _git_ok

    old_file = release_note_writer.version_notes_dir(proj_root, old_number) / "RELEASE_NOTES.md"
    new_dir = release_note_writer.version_notes_dir(proj_root, target)
    new_file = new_dir / "RELEASE_NOTES.md"
    if new_file == old_file or not old_file.is_file():
        return
    try:
        new_dir.mkdir(parents=True, exist_ok=True)
        old_file.replace(new_file)
    except OSError:
        return
    old_rel = str(old_file.relative_to(proj_root))
    new_rel = str(new_file.relative_to(proj_root))
    if not _git_ok(proj_root, ["add", "-A", "--", old_rel, new_rel]):
        return
    _git_ok(
        proj_root,
        ["commit", "-m", f"docs(release-notes): graduate notes to v{target.lstrip('v')}", "--", old_rel, new_rel],
    )


def _graduate_version_in_place(db: Session, version: Version, target: str, proj_root: Path) -> Version:
    """Promote the BUILT ``version`` to ``target`` (v1.0.0) IN PLACE + mark it released (§3.6).

    A first-PROD graduation renames the version the pipeline actually built — the row
    carrying all its epics, backlog, tokens and pipeline history — to ``target``, rather
    than creating a NEW empty ``v1.0.0`` shell beside it. The old behaviour stranded every
    child row under the pre-graduation number and left an empty "released" v1.0.0 that broke
    the metrics page (all tokens sat on the old version) and confused the version list.

    Behaviour:
      * ``version.version_number == target`` already ⇒ no rename (idempotent) — e.g. a free-form
        ``v1.0.0`` reaching its first prod deploy, or a repeat call.
      * a DIFFERENT row already carries ``(project_id, target)`` ⇒ ``ValueError``. This is a
        DEFENSIVE guard, not a strictly unreachable branch: the ``project_had_prod_deploy`` guard
        only means no *prior prod deploy* graduated a row — it does NOT preclude a manually-created
        free-form ``v1.0.0`` row already existing while this project does its first prod deploy. The
        raise then prevents a silent collision on the ``uq_versions_project_id_version_number`` UNIQUE
        (a clean 409/422 for the router) instead of letting the flush blow up.
      * otherwise ⇒ rename ``version.version_number = target``.

    In all cases the version is marked released (``status='released'`` + ``release_date`` today):
    a first-prod graduation IS the release. The two fields are set directly rather than routed
    through ``version_service.release`` — graduation already cleared the deploy gates
    (``version_verified`` + ``is_accepted``), so coupling to ``release``'s ``done``-state
    precondition would be wrong. ``version.name`` / ``version.description`` are left untouched
    (keep the built version's own identity — §8 anti-destructive).
    """
    old_number = version.version_number  # capture BEFORE the rename — drives the §4 note dir-move
    if version.version_number != target:
        clash = db.execute(
            select(Version).where(Version.project_id == version.project_id, Version.version_number == target)
        ).scalar_one_or_none()
        if clash is not None and clash.id != version.id:
            raise ValueError(
                f"Cannot graduate to {target!r}: a different version row already carries it for this "
                f"project (uq_versions_project_id_version_number) — a first-prod graduation must not collide"
            )
        version.version_number = target
    version.status = "released"
    version.release_date = date.today()
    # Audit P1 (2026-07-12): the graduation RENAMED the version old→target, but its DeployEvent audit rows (the
    # customer's UAT ``accept`` + ``deploy`` events) still carry ``old_number`` — so ``is_accepted(customer,
    # target)`` / ``current_version`` would read False/None for the version the manager DID accept + deploy,
    # blocking every SUBSEQUENT PROD action (redeploy / infra change) on the graduated number. Re-point the
    # project's events for the old number to the target so the acceptance gate + the version row agree.
    if old_number != target:
        db.execute(
            update(DeployEvent)
            .where(DeployEvent.project_id == version.project_id, DeployEvent.version_number == old_number)
            .values(version_number=target)
        )
    db.flush()
    # §4: keep the served note dir in sync with the renamed version (no-op when the number is unchanged).
    if old_number != target:
        _move_release_note_dir(proj_root, old_number, target)
    return version
