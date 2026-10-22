"""``GET /versions/{version_id}/price-estimate`` — the orientačná cena of a version, after its Návrh (DEV-154).

``null`` when there is none: a fix of our error (not billed), a version without a plan yet, nothing measured yet.
Access as to the version. A sync route: measuring a finished version runs git, so it runs in the worker threads.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.core import authz
from backend.core.security import get_current_user
from backend.db.models.foundation import User
from backend.db.session import get_db
from backend.schemas.price_estimate import PriceEstimateRead
from backend.services import claude_agent, price_estimate

router = APIRouter(tags=["Orientačná cena"])


def _eur(tokens: int, rate: Decimal) -> Decimal:
    return (Decimal(tokens) / Decimal(1000) * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


@router.get("/versions/{version_id}/price-estimate", response_model=Optional[PriceEstimateRead])
def version_price_estimate(
    version_id: UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
) -> Optional[PriceEstimateRead]:
    version = authz.assert_version_access(db, current_user, version_id)
    estimate = price_estimate.current(db, version, Path(claude_agent.PROJECTS_ROOT))
    if estimate is None:
        return None
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
