"""DEV-50 — the delivered-token statement of a version: preview, issue, CSV, and the version's work kind.
DEV-75 — the billing category of the version's tasks and the version's own discounts.

Sync routes on purpose: counting reads git and tokenizes the delivery — FastAPI runs a sync route in a worker thread,
so the event loop never waits on it. The reading routes commit too: who wrote each delivered line is kept the first
time it is counted (seconds of ``git blame`` per version), so the next reading does not count it again.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from backend.core import authz
from backend.core.security import get_current_user, require_shu_or_above
from backend.db.models.delivery_statement import DeliveryStatement
from backend.db.models.foundation import User
from backend.db.models.price_estimate import PriceEstimate
from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.db.session import get_db
from backend.schemas.delivery_statement import (
    BillingOverviewRead,
    BillingRowRead,
    CalibrationRead,
    DeliveredFileRead,
    DeliveryStatementView,
    DiscountChangeRead,
    DiscountsWrite,
    IssuedStatementRead,
    IssueStatementWrite,
    KindTotalRead,
    RateChangeRead,
    RatesWrite,
    StatementLineRead,
    StatementPreviewRead,
    StatementTaskRead,
    TaskCategoriesRead,
    TaskCategoriesWrite,
    WorkKindRead,
    WorkKindWrite,
)
from backend.schemas.price_estimate import PriceEstimateRead
from backend.services import billing_category, claude_agent, delivered_tokens, price_estimate
from backend.services import delivery_statement as statements

router = APIRouter(tags=["Súpis dodaných tokenov"])


def _repo(db: Session, version: Version):
    slug = db.execute(select(Project.slug).where(Project.id == version.project_id)).scalar_one()
    return claude_agent.PROJECTS_ROOT / slug


def _eur(tokens: int, rate: Decimal) -> Decimal:
    return (Decimal(tokens) / Decimal(1000) * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _estimate_read(estimate: PriceEstimate) -> PriceEstimateRead:
    return PriceEstimateRead(
        tasks=estimate.tasks,
        first_version=estimate.first_version,
        code_tokens=estimate.code_tokens,
        docs_tokens=estimate.docs_tokens,
        rate_code=estimate.rate_code,
        rate_docs=estimate.rate_docs,
        code_eur=_eur(estimate.code_tokens, estimate.rate_code),
        docs_eur=_eur(estimate.docs_tokens, estimate.rate_docs),
        amount_eur=estimate.amount_eur,
        spread_pct=estimate.spread_pct,
        samples=estimate.samples,
        enough=price_estimate.enough(estimate),
        created_at=estimate.created_at,
    )


def _preview_read(p: statements.Preview) -> StatementPreviewRead:
    def total(kind: str):
        return KindTotalRead(tokens=p.count.tokens(kind), lines=p.count.lines(kind)) if p.count else None

    c = p.calibration
    return StatementPreviewRead(
        version_number=p.version_number,
        blocked=p.blocked,
        work_kind=p.work_kind,
        base_sha=p.base_sha,
        delivered_sha=p.delivered_sha,
        delivered_source=p.delivered_source,
        delivered_source_label=statements.SOURCE_LABELS.get(p.delivered_source or ""),
        tokenizer=p.tokenizer,
        code=total(delivered_tokens.KIND_CODE),
        tests=total(delivered_tokens.KIND_TESTS),
        docs=total(delivered_tokens.KIND_DOCS),
        files=[DeliveredFileRead(**f) for f in statements.files_payload(p.count)] if p.count else [],
        rate_code=p.rate_code,
        rate_docs=p.rate_docs,
        settings_rate_code=p.settings_rate_code,
        settings_rate_docs=p.settings_rate_docs,
        rate_code_own=p.rate_code_own,
        rate_docs_own=p.rate_docs_own,
        amount_code_eur=p.amounts.code if p.amounts else None,
        amount_docs_eur=p.amounts.docs if p.amounts else None,
        amount_eur=p.amounts.total if p.amounts else None,
        cannot_issue=p.cannot_issue,
        calibration=CalibrationRead(**c.__dict__) if c else None,
        version_category=p.version_category,
        discounts=p.discounts,
        settings_discounts=p.settings_discounts,
        discounts_own=p.discounts_own,
        lines=[StatementLineRead(**line.payload()) for line in p.lines],
        tasks=[
            StatementTaskRead(
                id=s.task.id,
                label=s.task.label,
                title=s.task.title,
                epic_title=s.task.epic_title,
                own_category=s.task.category,
                category=s.category,
                lines=s.lines,
                tokens=s.tokens,
            )
            for s in p.tasks
        ],
        full_eur=p.amounts.full if p.amounts else None,
    )


def _issued(db: Session, version_id: UUID) -> list[IssuedStatementRead]:
    rows = db.execute(
        select(DeliveryStatement)
        .where(DeliveryStatement.version_id == version_id)
        # DEV-54: the valid statement (never replaced) first, then the replaced ones, the most recently replaced first.
        .order_by(DeliveryStatement.replaced_at.desc().nulls_first(), DeliveryStatement.created_at.desc())
    ).scalars()
    return [IssuedStatementRead.model_validate(r) for r in rows]


@router.get("/projects/{slug}/billing", response_model=BillingOverviewRead)
def get_billing(
    slug: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
) -> BillingOverviewRead:
    """DEV-73 — the billing tab: every version of the project, the newest first, with where it stands for
    invoicing (issued, issuable, waiting for the work kind, …) and the totals.

    Sync like the rest: counting reads git."""
    project = authz.assert_project_slug_access(db, current_user, slug)
    rows = statements.billing_rows(db, project, claude_agent.PROJECTS_ROOT / project.slug)
    # DEV-225: the orientačná cena is measured here, for every version — the measurements refreshed once for the tab.
    price_estimate.refresh_samples(db, claude_agent.PROJECTS_ROOT)

    def read(r: statements.BillingRow) -> BillingRowRead:
        p, count = r.preview, r.preview.count
        # DEV-154: the orientačná cena given after Návrh, beside what the statement counts — how well it estimated.
        estimate = price_estimate.current(db, r.version)
        return BillingRowRead(
            estimate=_estimate_read(estimate) if estimate else None,
            version_id=r.version.id,
            version_number=r.version.version_number,
            flow_type=r.flow_type,
            state=r.state,
            reason=r.reason,
            work_kind=p.work_kind,
            base_sha=p.base_sha,
            delivered_sha=p.delivered_sha,
            delivered_source_label=statements.SOURCE_LABELS.get(p.delivered_source or ""),
            tokens_code=(count.tokens(delivered_tokens.KIND_CODE) + count.tokens(delivered_tokens.KIND_TESTS))
            if count
            else None,
            tokens_docs=count.tokens(delivered_tokens.KIND_DOCS) if count else None,
            preview_amount_eur=p.amounts.total if p.amounts else None,
            issued=IssuedStatementRead.model_validate(r.valid) if r.valid else None,
            differs=r.differs,
            rate_code=p.rate_code,
            rate_docs=p.rate_docs,
            own_rates=p.rate_code_own or p.rate_docs_own,
        )

    issued_total = sum((r.valid.amount_eur for r in rows if r.valid), Decimal("0"))
    ready_total = sum(
        (Decimal(r.preview.amounts.total) for r in rows if r.state == statements.BILLING_READY and r.preview.amounts),
        Decimal("0"),
    )
    overview = BillingOverviewRead(
        rows=[read(r) for r in rows],
        issued_total_eur=issued_total,
        ready_total_eur=ready_total,
        needs_kind=sum(1 for r in rows if r.state == statements.BILLING_NEEDS_KIND),
    )
    db.commit()  # DEV-75: keeps who wrote each delivered line, counted for the first time just now
    return overview


@router.get("/versions/{version_id}/delivery-statement", response_model=DeliveryStatementView)
def get_statement(
    version_id: UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
) -> DeliveryStatementView:
    """The statement as it would be issued now, and the ones already issued (the valid one first)."""
    version = authz.assert_version_access(db, current_user, version_id)
    view = DeliveryStatementView(
        preview=_preview_read(statements.preview(db, version, _repo(db, version))),
        issued=_issued(db, version_id),
        rate_changes=_rate_changes(db, version_id),
        discount_changes=_discount_changes(db, version_id),
    )
    db.commit()  # DEV-75: keeps who wrote each delivered line, counted for the first time just now
    return view


def _person(user: Optional[User]) -> Optional[str]:
    if user is None:
        return None
    return " ".join(part for part in (user.first_name, user.last_name) if part) or user.username


def _rate_changes(db: Session, version_id: UUID) -> list[RateChangeRead]:
    return [
        RateChangeRead(
            created_at=c.created_at,
            changed_by=_person(db.get(User, c.changed_by) if c.changed_by else None),
            previous_rate_code=c.previous_rate_code,
            previous_rate_docs=c.previous_rate_docs,
            rate_code=c.rate_code,
            rate_docs=c.rate_docs,
        )
        for c in statements.rate_changes(db, version_id)
    ]


@router.put("/versions/{version_id}/billing-rates", response_model=list[RateChangeRead])
def set_billing_rates(
    version_id: UUID,
    payload: RatesWrite,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_shu_or_above),
) -> list[RateChangeRead]:
    """DEV-74 — the version's own token rates (``null`` = back to Nastavenia). An issued statement keeps its rates;
    only a new statement that replaces it counts with these. Returns every change, the newest first."""
    version = authz.assert_version_access(db, current_user, version_id)
    try:
        statements.set_rates(db, version, payload.rate_code, payload.rate_docs, current_user.id)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    db.commit()
    return _rate_changes(db, version_id)


def _discount_changes(db: Session, version_id: UUID) -> list[DiscountChangeRead]:
    return [
        DiscountChangeRead(
            created_at=c.created_at,
            changed_by=_person(db.get(User, c.changed_by) if c.changed_by else None),
            previous={
                billing_category.BUG: c.previous_discount_bug,
                billing_category.REQUEST: c.previous_discount_request,
                billing_category.GIFT: c.previous_discount_gift,
            },
            current={
                billing_category.BUG: c.discount_bug,
                billing_category.REQUEST: c.discount_request,
                billing_category.GIFT: c.discount_gift,
            },
        )
        for c in billing_category.discount_changes(db, version_id)
    ]


@router.put("/versions/{version_id}/billing-discounts", response_model=list[DiscountChangeRead])
def set_billing_discounts(
    version_id: UUID,
    payload: DiscountsWrite,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_shu_or_above),
) -> list[DiscountChangeRead]:
    """DEV-75 — the version's own discount per category in % (``null`` = back to Nastavenia). An issued statement
    keeps its discounts; only a new statement that replaces it counts with these. Returns every change, the newest
    first."""
    version = authz.assert_version_access(db, current_user, version_id)
    try:
        billing_category.set_discounts(db, version, payload.model_dump(), current_user.id)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    db.commit()
    return _discount_changes(db, version_id)


@router.put("/versions/{version_id}/task-categories", response_model=TaskCategoriesRead)
def set_task_categories(
    version_id: UUID,
    payload: TaskCategoriesWrite,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_shu_or_above),
) -> TaskCategoriesRead:
    """DEV-75 — what these tasks' delivered work is billed as (``null`` = the version's category); the screen sends
    every task of an epic or feat to set them at once. A task of another version refuses the whole change (422)."""
    authz.assert_version_access(db, current_user, version_id)
    try:
        changed = billing_category.set_categories(db, version_id, payload.task_ids, payload.category)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    db.commit()
    return TaskCategoriesRead(changed=changed)


@router.post(
    "/versions/{version_id}/delivery-statement",
    response_model=IssuedStatementRead,
    status_code=status.HTTP_201_CREATED,
)
def issue_statement(
    version_id: UUID,
    payload: Optional[IssueStatementWrite] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_shu_or_above),
) -> IssuedStatementRead:
    """Issue it: the counts, the rates and the tokenizer are frozen — a later change cannot alter it (409 + why).

    A version that has a valid statement gets a new one only as its confirmed replacement (``replace`` = its id)."""
    version = authz.assert_version_access(db, current_user, version_id)
    try:
        row = statements.issue(db, version, _repo(db, version), current_user.id, payload.replace if payload else None)
    except statements.CannotIssue as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except DBAPIError as exc:
        # Two issues at once: the database keeps one valid statement per version and refuses the second.
        if "ux_delivery_statements_" not in str(exc):
            raise
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, detail=statements.VALID_CHANGED) from exc
    db.commit()
    db.refresh(row)
    return IssuedStatementRead.model_validate(row)


@router.put("/versions/{version_id}/work-kind", response_model=WorkKindRead)
def set_work_kind(
    version_id: UUID,
    payload: WorkKindWrite,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_shu_or_above),
) -> WorkKindRead:
    """Decide whether the version fixed an error in delivered code (never billed) or was a change (billed)."""
    version = authz.assert_version_access(db, current_user, version_id)
    version.work_kind = payload.work_kind
    db.commit()
    return WorkKindRead(work_kind=version.work_kind)


@router.get("/delivery-statements/{statement_id}/csv")
def statement_csv(
    statement_id: UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
) -> Response:
    """The issued statement as CSV — the basis for the invoice."""
    row = db.get(DeliveryStatement, statement_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Súpis sa nenašiel.")
    version = authz.assert_version_access(db, current_user, row.version_id)
    project = db.get(Project, version.project_id)
    replaced = "-nahradeny" if row.replaced_at is not None else ""
    name = f"supis-{project.slug}-{version.version_number}-{row.created_at:%Y%m%d}{replaced}.csv"
    return Response(
        content=statements.csv_text(row, version, project).encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )
