"""REST router for the per-customer Deploy subsystem (v2.0.0, CR-V2-026).

Implements design §3 (Deploy & Customers) — DEPLOY-6/8/9/10. Like the customers
router, it straddles two URL families, so it mounts with the bare ``/api/v1``
prefix in :mod:`backend.main`:

* ``GET  /projects/{slug}/deploy-events``        → a project's deploy/accept log (matrix feed).
* ``GET  /customers/{customer_id}/deploy-events`` → one customer's deploy/accept log.
* ``POST /customers/{customer_id}/deploy``        → Nasadiť: deploy a verified version (uat | prod).
* ``POST /customers/{customer_id}/accept``        → Akceptovať: record a UAT acceptance (opens PROD).

**Acceptance gate (design §3.5).** A ``prod`` deploy is rejected unless a
recorded acceptance exists for that (customer, version) — the never-bypassed
gate. **Manual + outside the dial (D6).** These endpoints are driven only by the
explicit Manažér action (``ri`` role for the mutating ones); there is no
autonomy path into deploy.

**Secret invariant (CLAUDE.md §4/§5, OQ-5).** No endpoint reads, returns, or
logs secret material — per-customer secrets live only in the credentials store
the deploy backend points into; ``detail`` is a non-secret summary only.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.core.security import get_current_user, require_ri_role
from backend.db.models.customers import Customer
from backend.db.models.deploy import DeployEvent
from backend.db.models.foundation import User
from backend.db.models.projects import Project
from backend.db.session import get_db
from backend.schemas.deploy import (
    AcceptRequest,
    DeployEventRead,
    DeployMatrix,
    DeployRequest,
    DeployResult,
)
from backend.services import deploy as deploy_service
from backend.services import uat_launch as uat_launch_service

router = APIRouter(tags=["Deploy"])


def _map_value_error(exc: ValueError) -> HTTPException:
    message = str(exc)
    lowered = message.lower()
    if "not found" in lowered:
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=message)
    if "blocked" in lowered or "cannot accept" in lowered or "already" in lowered:
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=message)
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=message)


def _resolve_project(db: Session, slug: str) -> Project:
    project = db.execute(select(Project).where(Project.slug == slug)).scalar_one_or_none()
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Project not found: {slug}")
    return project


# ---------------------------------------------------------------------------
# Audit-log reads (the version × customer matrix feed)
# ---------------------------------------------------------------------------


@router.get("/projects/{slug}/deploy-matrix", response_model=DeployMatrix)
def get_deploy_matrix(
    slug: str,
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_user),
) -> DeployMatrix:
    """Return the version × customer matrix for the project ``slug`` (design §3.3).

    One read feeds both the UAT and PROD tabs: the deployable (verified / Hotovo)
    versions for the Nasadiť dropdown, plus per-customer current UAT/PROD versions
    and the accepted-for-PROD set (so the PROD tab disables Nasadiť until the
    (version, customer) pair is accepted — the never-bypassed gate).
    """
    project = _resolve_project(db, slug)
    return DeployMatrix.model_validate(deploy_service.build_matrix(db, project))


class _UatLaunchRequest(BaseModel):
    project_slug: str


class _UatLaunchResponse(BaseModel):
    launch_url: str


@router.post("/customers/{customer_id}/uat-launch", response_model=_UatLaunchResponse)
def uat_launch(
    customer_id: UUID,
    payload: _UatLaunchRequest,
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_user),
) -> _UatLaunchResponse:
    """Mint a short-lived UAT test launch URL so the Manažér can open a deployed token-launch app
    LOGGED-IN directly from the UAT tab (v4.0.30). Token-launch (``auth_mode='token'``) apps only — a
    password app uses the plain 'Otvoriť aplikáciu' link. The launch key is used server-side only, never
    returned; the token's ``sub`` is a UAT test identity (no impersonation). UAT-only convenience."""
    customer = db.execute(select(Customer).where(Customer.id == customer_id)).scalar_one_or_none()
    if customer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Zákazník nenájdený.")
    project = _resolve_project(db, payload.project_slug)
    if project.auth_mode != "token":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Nie je token-launch aplikácia — použi „Otvoriť aplikáciu“.",
        )
    uat_url = deploy_service._instance_url(customer, "uat", project)
    # The deploy .env lives under the CANONICAL customer dir slug (lowercased subdomain-or-slug), the same
    # key the provisioner used — NOT the raw customer.slug (may be mixed-case, e.g. ANDROS → dir andros).
    launch_url = uat_launch_service.build_uat_launch_url(
        deploy_service._customer_dir_slug(customer), project.slug, uat_url
    )
    if not launch_url:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Launch kľúč pre UAT nie je nastavený (chýba spárovaný NEX Manager).",
        )
    return _UatLaunchResponse(launch_url=launch_url)


@router.get("/projects/{slug}/deploy-events", response_model=list[DeployEventRead])
def list_project_deploy_events(
    slug: str,
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_user),
) -> list[DeployEvent]:
    """Return every deploy/accept event for the project ``slug`` (newest first)."""
    project = _resolve_project(db, slug)
    return deploy_service.list_project_events(db, project.id)


@router.get("/customers/{customer_id}/deploy-events", response_model=list[DeployEventRead])
def list_customer_deploy_events(
    customer_id: UUID,
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_user),
) -> list[DeployEvent]:
    """Return every deploy/accept event for one customer (newest first)."""
    return deploy_service.list_events(db, customer_id)


# ---------------------------------------------------------------------------
# Mutating actions (Nasadiť / Akceptovať) — ri role only, manual + outside dial
# ---------------------------------------------------------------------------


@router.post("/customers/{customer_id}/deploy", response_model=DeployResult)
async def deploy_customer(
    customer_id: UUID,
    payload: DeployRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_ri_role),
) -> DeployResult:
    """Nasadiť — deploy a verified version to a customer's UAT/PROD instance (§3.4).

    A PROD deploy is rejected (409) unless the customer's UAT of that version was
    accepted (§3.5). The first PROD deploy of a project bumps it to v1.0.0 (§3.6).
    A redeploy PRESERVES data + secrets + extra_hosts by default; ``force_fresh``
    opts into a fresh re-provision (§3.7).
    """
    try:
        event, url, bumped_to = await deploy_service.deploy(
            db,
            customer_id,
            version_number=payload.version_number,
            environment=payload.environment,
            actor_id=current_user.id,
            force_fresh=payload.force_fresh,
        )
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise _map_value_error(exc) from exc
    db.refresh(event)
    return DeployResult(
        ok=event.status == "ok",
        event=DeployEventRead.model_validate(event),
        url=url,
        bumped_to=bumped_to,
    )


@router.post("/customers/{customer_id}/accept", response_model=DeployEventRead)
def accept_customer_uat(
    customer_id: UUID,
    payload: AcceptRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_ri_role),
) -> DeployEvent:
    """Akceptovať — record a Manažér's UAT acceptance, opening PROD (§3.5).

    Logs who/when/version/customer. Requires the version to have been deployed to
    this customer's UAT first.
    """
    try:
        event = deploy_service.accept(db, customer_id, payload.version_number, current_user.id)
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise _map_value_error(exc) from exc
    db.refresh(event)
    return event
