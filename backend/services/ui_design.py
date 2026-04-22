"""Service layer for UIDesign — Step 2B of the pipeline."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session

from backend.db.models.specifications import UIDesign
from backend.schemas.ui_design import UIDesignCreate, UIDesignUpdate

_ALLOWED_UPDATE_FIELDS = {"content", "html_preview", "approved_by", "approved_at"}


def create(db: Session, data: UIDesignCreate) -> UIDesign:
    obj = UIDesign(
        project_id=data.project_id,
        content=data.content,
        html_preview=data.html_preview,
        approved_by=data.approved_by,
        approved_at=data.approved_at,
    )
    db.add(obj)
    db.flush()
    return obj


def get_by_id(db: Session, ui_design_id: UUID) -> UIDesign:
    obj = db.query(UIDesign).filter(UIDesign.id == ui_design_id).first()
    if obj is None:
        raise ValueError(f"UIDesign {ui_design_id} not found")
    return obj


def list_ui_designs(
    db: Session,
    project_id: Optional[UUID] = None,
    limit: int = 50,
    offset: int = 0,
) -> list[UIDesign]:
    q = db.query(UIDesign)
    if project_id is not None:
        q = q.filter(UIDesign.project_id == project_id)
    return q.order_by(UIDesign.created_at.desc()).offset(offset).limit(limit).all()


def count_ui_designs(
    db: Session,
    project_id: Optional[UUID] = None,
) -> int:
    q = db.query(UIDesign)
    if project_id is not None:
        q = q.filter(UIDesign.project_id == project_id)
    return q.count()


def update(db: Session, ui_design_id: UUID, data: UIDesignUpdate) -> UIDesign:
    obj = get_by_id(db, ui_design_id)
    patch = data.model_dump(exclude_unset=True)
    for field, value in patch.items():
        if field not in _ALLOWED_UPDATE_FIELDS:
            continue
        setattr(obj, field, value)
    # Auto-stamp approved_at when approved_by is set
    if "approved_by" in patch and patch["approved_by"] is not None and "approved_at" not in patch:
        obj.approved_at = datetime.now(tz=timezone.utc)
    db.flush()
    return obj


def delete(db: Session, ui_design_id: UUID) -> None:
    obj = get_by_id(db, ui_design_id)
    db.delete(obj)
    db.flush()
