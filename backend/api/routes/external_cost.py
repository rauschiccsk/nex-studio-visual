"""REST router for the hand-entered external costs of a project (CR-V2-063).

* ``GET    /projects/{slug}/external-costs``        → list, newest ``occurred_on`` first.
* ``POST   /projects/{slug}/external-costs``        → create.
* ``PATCH  /projects/{slug}/external-costs/{id}``   → partial update.
* ``DELETE /projects/{slug}/external-costs/{id}``   → hard delete.

The cockpit meters only its own builds; these entries are how work done OUTSIDE one (Dedo in the
terminal, a developer working directly) reaches the Náklady screen — always as a separate row and a
separate ``…_external`` total, never merged into the measured figures.

Access is identical to the metrics GET (``authz.assert_project_slug_access`` on every handler): the
entries are that screen's own data, so whoever may read the costs may maintain them. The router owns
commit/rollback. Mounted under the bare ``/api/v1`` prefix in ``backend/main.py`` (the path is
``/projects/{slug}/…``), next to the metrics router.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.core import authz
from backend.core.security import require_shu_or_above
from backend.db.models.external_cost import ExternalCost
from backend.db.models.foundation import User
from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.db.session import get_db
from backend.schemas.external_cost import (
    ExternalCostCreate,
    ExternalCostRead,
    ExternalCostUpdate,
)

router = APIRouter(tags=["External costs"])

#: The NOT NULL columns of ``external_cost``, with the label the Manažér sees. ``ExternalCostUpdate``
#: types every field ``Optional`` to express "not supplied", so an explicit JSON ``null`` for one of
#: these reaches the DB as a NULL and would 500 on an IntegrityError — it is a bad request, so it is
#: rejected here as a 422. ``version_id`` is absent on purpose: it IS nullable (explicit ``null`` moves
#: the entry back to the project level).
_REQUIRED_UPDATE_FIELDS: dict[str, str] = {
    "occurred_on": "Dátum",
    "description": "Popis",
    "model": "Model",
    "input_tokens": "Vstupné tokeny",
    "output_tokens": "Výstupné tokeny",
}


def _unprocessable(message: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=message)


def _validate_no_null_required(fields: dict[str, object]) -> None:
    """A supplied-but-``null`` value for a NOT NULL column is a 422, never a 500 (see the map above)."""
    for name, label in _REQUIRED_UPDATE_FIELDS.items():
        if name in fields and fields[name] is None:
            raise _unprocessable(f"Pole „{label}“ je povinné.")


def _validate_occurred_on(payload: ExternalCostCreate | ExternalCostUpdate) -> None:
    """An entry records work that ALREADY happened — a future date is a typo, not a plan (422)."""
    if payload.occurred_on is not None and payload.occurred_on > datetime.now(timezone.utc).date():
        raise _unprocessable("Dátum nesmie byť v budúcnosti.")


def _validate_version(db: Session, project: Project, version_id: UUID | None) -> None:
    """``version_id`` must be a version OF THIS PROJECT — deliberately not a DB constraint (a composite
    FK would need a redundant column), so it is enforced here (422)."""
    if version_id is None:
        return
    version = db.execute(select(Version).where(Version.id == version_id)).scalar_one_or_none()
    if version is None or version.project_id != project.id:
        raise _unprocessable("Verzia nepatrí tomuto projektu.")


def _get_entry(db: Session, project: Project, entry_id: UUID) -> ExternalCost:
    entry = db.execute(
        select(ExternalCost).where(ExternalCost.id == entry_id, ExternalCost.project_id == project.id)
    ).scalar_one_or_none()
    if entry is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"External cost not found: {entry_id}",
        )
    return entry


@router.get("/projects/{slug}/external-costs", response_model=list[ExternalCostRead])
def list_external_costs(
    slug: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_shu_or_above),
) -> list[ExternalCostRead]:
    """The project's external-cost entries, newest ``occurred_on`` first (``created_at`` breaks ties)."""
    project = authz.assert_project_slug_access(db, current_user, slug)
    rows = (
        db.execute(
            select(ExternalCost)
            .where(ExternalCost.project_id == project.id)
            .order_by(ExternalCost.occurred_on.desc(), ExternalCost.created_at.desc())
        )
        .scalars()
        .all()
    )
    return [ExternalCostRead.model_validate(row) for row in rows]


@router.post(
    "/projects/{slug}/external-costs",
    response_model=ExternalCostRead,
    status_code=status.HTTP_201_CREATED,
)
def create_external_cost(
    slug: str,
    payload: ExternalCostCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_shu_or_above),
) -> ExternalCostRead:
    """Record one piece of work the cockpit could not meter."""
    project = authz.assert_project_slug_access(db, current_user, slug)
    _validate_occurred_on(payload)
    _validate_version(db, project, payload.version_id)

    entry = ExternalCost(
        project_id=project.id,
        version_id=payload.version_id,
        occurred_on=payload.occurred_on,
        description=payload.description,
        model=payload.model,
        input_tokens=payload.input_tokens,
        output_tokens=payload.output_tokens,
        created_by=current_user.id,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return ExternalCostRead.model_validate(entry)


@router.patch("/projects/{slug}/external-costs/{entry_id}", response_model=ExternalCostRead)
def update_external_cost(
    slug: str,
    entry_id: UUID,
    payload: ExternalCostUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_shu_or_above),
) -> ExternalCostRead:
    """Partial update — only the supplied fields change."""
    project = authz.assert_project_slug_access(db, current_user, slug)
    entry = _get_entry(db, project, entry_id)
    _validate_occurred_on(payload)

    fields = payload.model_dump(exclude_unset=True)
    _validate_no_null_required(fields)
    if "version_id" in fields:
        _validate_version(db, project, fields["version_id"])
    for name, value in fields.items():
        setattr(entry, name, value)

    db.commit()
    db.refresh(entry)
    return ExternalCostRead.model_validate(entry)


@router.delete(
    "/projects/{slug}/external-costs/{entry_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
def delete_external_cost(
    slug: str,
    entry_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_shu_or_above),
) -> Response:
    """Hard delete — a mistyped entry is noise in the totals, there is no history to preserve."""
    project = authz.assert_project_slug_access(db, current_user, slug)
    entry = _get_entry(db, project, entry_id)
    db.delete(entry)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
