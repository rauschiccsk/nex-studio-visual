"""``GET /standing`` — „Kde stojíme“ on the Prehľad: where every project's development stands (DEV-150).

The projects the person works on, as in the project list (the admin all, anyone else their own), those waiting on a
person first and the longest waiting at the top. Read only.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.core.security import get_current_user
from backend.db.models.foundation import User
from backend.db.session import get_db
from backend.schemas.standing import StandingRead
from backend.services import standing

router = APIRouter(tags=["Prehľad"])


@router.get("/standing", response_model=StandingRead)
def where_we_stand(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> StandingRead:
    """Where each project stands: its open versions with phase, state, waiting time and tasks; the version on PROD."""
    return StandingRead(projects=standing.project_standing(db, current_user))
