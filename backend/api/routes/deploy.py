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

from backend.core import authz
from backend.core.offload import run_blocking
from backend.core.security import get_current_user, require_shu_or_above
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
from backend.services import instance_adoption
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
    authz.assert_project_slug_access(db, _current_user, slug)
    project = _resolve_project(db, slug)
    # v4.0.54: the user is passed through so the matrix can answer whether THIS user may re-verify a drifted
    # version (the pipeline action is ri-or-owner while this read is wider — the frontend cannot derive it).
    return DeployMatrix.model_validate(deploy_service.build_matrix(db, project, _current_user))


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
    returned; the token's ``sub`` is the OPERATOR WHO CLICKED (ICCINT-61 — it used to be a made-up
    "uat-test", which no Manager could resolve, so the launch could never succeed). UAT-only convenience."""
    authz.assert_customer_access(db, _current_user, customer_id)
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
    # ICCINT-61: the ticket is that this used to mint for "uat-test" — a name no Manager has ever heard of,
    # so the launch died inside the app every single time. It is minted for the person who CLICKED; that is
    # authentication, not impersonation (impersonation would be minting for somebody else).
    dir_slug = deploy_service._customer_dir_slug(customer)
    known = uat_launch_service.manager_knows_operator(dir_slug, project.slug, _current_user.username)
    if known is False:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"NEX Manager tejto aplikácie nepozná meno „{_current_user.username}“. "
                "Priraď si v ňom prístup (Používatelia), alebo appku otvor z NEX Managera pod menom, "
                "ktoré tam máš."
            ),
        )
    launch_url = uat_launch_service.build_uat_launch_url(
        dir_slug, project.slug, uat_url, subject=_current_user.username
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
    authz.assert_project_slug_access(db, _current_user, slug)
    project = _resolve_project(db, slug)
    return deploy_service.list_project_events(db, project.id)


@router.get("/customers/{customer_id}/deploy-events", response_model=list[DeployEventRead])
def list_customer_deploy_events(
    customer_id: UUID,
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_user),
) -> list[DeployEvent]:
    """Return every deploy/accept event for one customer (newest first)."""
    authz.assert_customer_access(db, _current_user, customer_id)
    return deploy_service.list_events(db, customer_id)


# ---------------------------------------------------------------------------
# Mutating actions (Nasadiť / Akceptovať) — ri role only, manual + outside dial
# ---------------------------------------------------------------------------


@router.post("/customers/{customer_id}/deploy", response_model=DeployResult)
async def deploy_customer(
    customer_id: UUID,
    payload: DeployRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_shu_or_above),
) -> DeployResult:
    """Nasadiť — deploy a verified version to a customer's UAT/PROD instance (§3.4).

    A PROD deploy is rejected (409) unless the customer's UAT of that version was
    accepted (§3.5). The first PROD deploy of a project bumps it to v1.0.0 (§3.6).
    A redeploy PRESERVES data + secrets + extra_hosts by default; ``force_fresh``
    opts into a fresh re-provision (§3.7).

    The project's owner deploys his own project — both environments. The second, role-based PROD gate
    that used to stand here is gone with the tier model: the owner may do everything on his own project,
    and a separate "PROD needs role ri" clause would be the one rule the simplification did not reach.
    """
    authz.assert_customer_access(db, current_user, customer_id)
    try:
        # Keep the outcome itself, do not destructure it away. `DeployOutcome` IS the (event, url,
        # bumped_to) tuple for every existing caller, but it also carries `.warnings` — the channel
        # that reports a deploy which SUCCEEDED yet could not wire something (a customer with no NEX
        # Manager pairing). Unpacking straight into three names dropped it here, severing the channel
        # at the HTTP boundary: the schema declares the field, the screen renders it, and nothing ever
        # arrived.
        outcome = await deploy_service.deploy(
            db,
            customer_id,
            version_number=payload.version_number,
            environment=payload.environment,
            actor_id=current_user.id,
            force_fresh=payload.force_fresh,
        )
        event, url, bumped_to = outcome
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
        warnings=list(outcome.warnings),
    )


class _AdoptionPreviewResponse(BaseModel):
    """Čo by prevzatie inštalácie urobilo — VOPRED (ICCINT-102)."""

    instance_dir: str
    exists: bool
    already_ours: bool
    #: Dvojice (súbor, pod akým menom sa odloží).
    set_aside: list[tuple[str, str]]
    untouched: list[str]
    running_containers: list[str]
    #: Text, ktorý musí Manažér odpísať, aby sa prevzatie vykonalo.
    confirmation_phrase: str


@router.get("/customers/{customer_id}/adoption-preview", response_model=_AdoptionPreviewResponse)
def adoption_preview(
    customer_id: UUID,
    environment: str = "uat",
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> _AdoptionPreviewResponse:
    """Čo by prevzatie ručne písanej inštalácie urobilo — bez toho, aby sa čokoľvek zmenilo (ICCINT-102).

    Manažér sa má rozhodovať z faktov: ktorý priečinok to je, čo sa odloží, čo sa nedotkne a **čo z toho
    priečinka práve beží**. Terminálový ``--dry-run`` to isté ukazoval len tomu, kto vie napísať príkaz.
    """
    customer = db.get(Customer, customer_id)
    if customer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Zákazník neexistuje.")
    authz.assert_customer_access(db, current_user, customer_id)
    project = db.get(Project, customer.project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Projekt zákazníka neexistuje.")

    instance_dir = instance_adoption.instance_dir_for(
        environment=environment,
        customer_slug=deploy_service._customer_dir_slug(customer),
        full_project_slug=project.slug,
    )
    return _AdoptionPreviewResponse(**instance_adoption.preview(instance_dir)._asdict())


class _AdoptRequest(BaseModel):
    """Potvrdenie prevzatia — nie klik, ale odpísaná fráza (ICCINT-102)."""

    version_number: str
    environment: str = "uat"
    #: Musí sa zhodovať s ``confirmation_phrase`` z náhľadu.
    confirm: str


@router.post("/customers/{customer_id}/adopt", response_model=DeployResult)
async def adopt_instance(
    customer_id: UUID,
    payload: _AdoptRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_shu_or_above),
) -> DeployResult:
    """Prevziať ručne písanú inštaláciu pod správu NEX Studia a nasadiť do nej verziu (ICCINT-102).

    ⚠️ **Toto NIE JE „Nasadiť“.** Je to samostatné rozhodnutie s vlastným potvrdením: ručné súbory sa
    odložia ako ``.pre-nex-studio`` (dôkaz zostáva, krok je vratný), zapíše sa záznam kto/kedy/čo, a až
    potom sa do priečinka zapisuje. Tlačidlo „Nasadiť“ takú možnosť nemá a nedostane ju — poistka nad
    ``deploy.py`` (``allow_overwrite`` sa v nej nesmie vyskytnúť) platí bez zmeny.

    **Jedno volanie = jedna inštalácia.** Hromadné prevzatie Director 28.07.2026 zamietol po troch
    nezávislých previerkach a to platí ďalej.

    **409** — odpísaná fráza nesedí, alebo je priečinok už náš (potom niet čo preberať; použi „Nasadiť“).
    """
    customer = db.get(Customer, customer_id)
    if customer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Zákazník neexistuje.")
    authz.assert_customer_access(db, current_user, customer_id)
    project = db.get(Project, customer.project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Projekt zákazníka neexistuje.")

    instance_dir = instance_adoption.instance_dir_for(
        environment=payload.environment,
        customer_slug=deploy_service._customer_dir_slug(customer),
        full_project_slug=project.slug,
    )
    # ICCINT-74: náhľad sa pýta Dockera, čo z toho priečinka beží — to je čakanie na proces a na hlavnú
    # slučku nepatrí. Strop je krátky zámerne: je to doplnkový údaj, nie brána.
    nahlad = await run_blocking(instance_adoption.preview, instance_dir, cap=30)
    if nahlad.already_ours:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Priečinok „{nahlad.instance_dir}“ NEX Studio už spravuje — preberať niet čo. Použi bežné Nasadiť."
            ),
        )
    if payload.confirm.strip() != nahlad.confirmation_phrase:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Potvrdenie nesedí. Na prevzatie treba odpísať „{nahlad.confirmation_phrase}“ — "
                "je to poistka proti prevzatiu nesprávnej inštalácie, nie formalita."
            ),
        )

    try:
        outcome = await deploy_service.deploy(
            db,
            customer_id,
            version_number=payload.version_number,
            environment=payload.environment,
            actor_id=current_user.id,
            force_fresh=False,  # prevzatie NIKDY nerotuje tajomstvá — databáza v tom priečinku beží
            deploy_runner=instance_adoption.adopting_deploy_runner,
        )
        event, url, bumped_to = outcome
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
        warnings=list(outcome.warnings),
    )


@router.post("/customers/{customer_id}/accept", response_model=DeployEventRead)
def accept_customer_uat(
    customer_id: UUID,
    payload: AcceptRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_shu_or_above),
) -> DeployEvent:
    """Akceptovať — record a Manažér's UAT acceptance, opening PROD (§3.5).

    Logs who/when/version/customer. Requires the version to have been deployed to
    this customer's UAT first.

    Guarded by OWNERSHIP, which it was not before: the role dependency was the ONLY gate here, with no
    ownership call at all — so under the tier model any ``ri`` accepted any customer's UAT, and removing
    the role gate without adding ownership would have opened it to every authenticated user. This is the
    step that OPENS PROD, so it gets the same explicit check as the deploy above.
    """
    authz.assert_customer_access(db, current_user, customer_id)
    try:
        event = deploy_service.accept(db, customer_id, payload.version_number, current_user.id)
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise _map_value_error(exc) from exc
    db.refresh(event)
    return event
