"""DEV-50 — the delivered-token statement of a version: preview, issue, CSV, and the version's work kind.

Sync routes on purpose: counting reads git and tokenizes the delivery — FastAPI runs a sync route in a worker thread,
so the event loop never waits on it.
"""

from __future__ import annotations

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
from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.db.session import get_db
from backend.schemas.delivery_statement import (
    CalibrationRead,
    DeliveredFileRead,
    DeliveryStatementView,
    IssuedStatementRead,
    IssueStatementWrite,
    KindTotalRead,
    StatementPreviewRead,
    WorkKindRead,
    WorkKindWrite,
)
from backend.services import claude_agent, delivered_tokens
from backend.services import delivery_statement as statements

router = APIRouter(tags=["Súpis dodaných tokenov"])


def _repo(db: Session, version: Version):
    slug = db.execute(select(Project.slug).where(Project.id == version.project_id)).scalar_one()
    return claude_agent.PROJECTS_ROOT / slug


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
        amount_code_eur=p.amounts.code if p.amounts else None,
        amount_docs_eur=p.amounts.docs if p.amounts else None,
        amount_eur=p.amounts.total if p.amounts else None,
        cannot_issue=p.cannot_issue,
        calibration=CalibrationRead(**c.__dict__) if c else None,
    )


def _issued(db: Session, version_id: UUID) -> list[IssuedStatementRead]:
    rows = db.execute(
        select(DeliveryStatement)
        .where(DeliveryStatement.version_id == version_id)
        # DEV-54: the valid statement (never replaced) first, then the replaced ones, the most recently replaced first.
        .order_by(DeliveryStatement.replaced_at.desc().nulls_first(), DeliveryStatement.created_at.desc())
    ).scalars()
    return [IssuedStatementRead.model_validate(r) for r in rows]


@router.get("/versions/{version_id}/delivery-statement", response_model=DeliveryStatementView)
def get_statement(
    version_id: UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
) -> DeliveryStatementView:
    """The statement as it would be issued now, and the ones already issued (the valid one first)."""
    version = authz.assert_version_access(db, current_user, version_id)
    return DeliveryStatementView(
        preview=_preview_read(statements.preview(db, version, _repo(db, version))), issued=_issued(db, version_id)
    )


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
