"""Service layer for :class:`~backend.db.models.backlog.BacklogItem` (E2, CR-NS-041).

Synchronous CRUD + lifecycle transitions used by the backlog router and the version-release path. All
methods take ``db: Session`` first and only ``flush()`` — commit is the caller's job. Errors are
``ValueError`` so the router maps them to HTTP. Mirrors :mod:`backend.services.epic` (per-project
``MAX(number)+1`` numbering). The backlog NEVER creates Epics/Tasks.
"""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy import update as sa_update
from sqlalchemy.orm import Session

from backend.db.models.backlog import BacklogItem
from backend.db.models.versions import Version
from backend.schemas.backlog import BacklogItemCreate, BacklogItemUpdate, BacklogStatus


def list_backlog(
    db: Session,
    *,
    project_id: Optional[UUID] = None,
    status: Optional[BacklogStatus] = None,
    limit: int = 100,
    offset: int = 0,
) -> list[BacklogItem]:
    """Return backlog items filtered by ``project_id`` / ``status``, ordered by ``number ASC``."""
    stmt = select(BacklogItem)
    if project_id is not None:
        stmt = stmt.where(BacklogItem.project_id == project_id)
    if status is not None:
        stmt = stmt.where(BacklogItem.status == status)
    stmt = stmt.order_by(BacklogItem.number.asc()).limit(limit).offset(offset)
    return list(db.execute(stmt).scalars().all())


def count_backlog(
    db: Session,
    *,
    project_id: Optional[UUID] = None,
    status: Optional[BacklogStatus] = None,
) -> int:
    """Total backlog items matching the filters (for the paginated response)."""
    stmt = select(func.count()).select_from(BacklogItem)
    if project_id is not None:
        stmt = stmt.where(BacklogItem.project_id == project_id)
    if status is not None:
        stmt = stmt.where(BacklogItem.status == status)
    return int(db.execute(stmt).scalar_one())


def get_by_id(db: Session, item_id: UUID) -> BacklogItem:
    """Return one backlog item or raise ``ValueError`` (→ 404)."""
    item = db.get(BacklogItem, item_id)
    if item is None:
        raise ValueError(f"Backlog item {item_id} not found")
    return item


def _next_number(db: Session, project_id: UUID) -> int:
    """Next per-project ``number`` (``MAX(number)+1``, starts at 1)."""
    current_max = db.execute(select(func.max(BacklogItem.number)).where(BacklogItem.project_id == project_id)).scalar()
    return (current_max or 0) + 1


def create(db: Session, data: BacklogItemCreate) -> BacklogItem:
    """Create a backlog item with an auto per-project ``number`` (status ``open``).

    Re-checks the ``(project_id, number)`` pair before flush so a concurrent-create race surfaces as a
    clean ``ValueError`` (HTTP 409) rather than a raw ``IntegrityError``.
    """
    number = _next_number(db, data.project_id)
    if (
        db.execute(
            select(BacklogItem).where(
                BacklogItem.project_id == data.project_id,
                BacklogItem.number == number,
            )
        ).scalar_one_or_none()
        is not None
    ):
        raise ValueError(f"Backlog item with project_id={data.project_id} and number={number} already exists")
    item = BacklogItem(
        project_id=data.project_id,
        number=number,
        title=data.title,
        description=data.description,
        priority=data.priority,
        status="open",
    )
    db.add(item)
    db.flush()
    return item


def update(db: Session, item_id: UUID, data: BacklogItemUpdate) -> BacklogItem:
    """Edit an item's ``title`` / ``description`` / ``priority`` / ``status`` (reject = status='rejected' with
    ``rejection_reason``, DEV-121).

    Version assignment goes through :func:`assign_to_version`, not here — ``version_id`` is intentionally
    NOT in the allow-list. Fields omitted from the payload are left unchanged (PATCH semantics).
    """
    item = get_by_id(db, item_id)
    update_data = data.model_dump(exclude_unset=True)
    allowed_fields = {"title", "description", "priority", "status"}
    for field, value in update_data.items():
        if field in allowed_fields and value is not None:
            setattr(item, field, value)
    # DEV-121: a rejection carries its reason (it is written to the requirement's ticket); a requirement returned
    # to the Zásobník sheds it.
    if update_data.get("status") == "rejected":
        reason = (update_data.get("rejection_reason") or "").strip()
        if not reason:
            raise ValueError("Pri zamietnutí napíš dôvod — zapíše sa aj do tiketu požiadavky.")
        item.rejection_reason = reason
    elif update_data.get("status") == "open":
        item.rejection_reason = None
    db.flush()
    return item


def assign_to_version(db: Session, item_id: UUID, version_id: UUID) -> BacklogItem:
    """Assign the requirement to a version → ``version_id`` set + ``status='included'``.

    Validates the version exists and belongs to the SAME project (a requirement is included only in its
    own project's versions). Raises ``ValueError`` (→ 404/422) otherwise.
    """
    item = get_by_id(db, item_id)
    version = db.get(Version, version_id)
    if version is None:
        raise ValueError(f"Version {version_id} not found")
    if version.project_id != item.project_id:
        raise ValueError(f"Version {version_id} belongs to a different project")
    item.version_id = version_id
    item.status = "included"
    db.flush()
    return item


def realize_for_version(db: Session, version_id: UUID) -> int:
    """Transition this version's ``included`` items → ``realized`` + stamp ``realized_at`` (E2 History).

    Called by the version-release path AFTER its blocking-epic gate — additive, never blocks a release.
    Returns the number of items realized.
    """
    result = db.execute(
        sa_update(BacklogItem)
        .where(BacklogItem.version_id == version_id, BacklogItem.status == "included")
        # updated_at set explicitly: a Core bulk UPDATE does NOT fire the ORM ``onupdate`` (CR-NS-042 polish).
        .values(status="realized", realized_at=func.now(), updated_at=func.now())
    )
    return int(result.rowcount or 0)


#: What may be deleted: a request never put into a version. Included and realized ones are the History of a version.
DELETABLE_STATUSES = ("open", "rejected")


def delete(db: Session, item_id: UUID) -> None:
    """Delete a backlog item — when it is ``open`` or ``rejected``, never one included in a version or realized.

    DEV-98: a rejected request was not deletable, so a request rejected by mistake (13.10.2026, the Director's test
    request — the red reject button stands beside the bin) had to be restored before it could go."""
    item = get_by_id(db, item_id)
    if item.status not in DELETABLE_STATUSES:
        raise ValueError(
            f"Cannot delete backlog item {item_id}: status is '{item.status}' (only open or rejected is deletable)"
        )
    db.delete(item)
    db.flush()
