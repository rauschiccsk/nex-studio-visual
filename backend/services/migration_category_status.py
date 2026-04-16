"""Service layer for :class:`~backend.db.models.migration.MigrationCategoryStatus`.

Provides the synchronous CRUD surface used by API routers. All methods
accept ``db: Session`` as the first argument and only ever call
``session.flush()`` — transaction commit is the router's responsibility.
Errors are signalled via :class:`ValueError` so the router can translate
them to the appropriate HTTP status code.

Design notes (per DESIGN.md §1.26 MigrationCategoryStatus, DESIGN.md
§1.10 Migration Tracking and
:mod:`backend.db.models.migration.MigrationCategoryStatus`):

    * ``id``, ``created_at`` and ``updated_at`` are server-managed and
      therefore immutable from the service layer (``updated_at`` is
      auto-stamped by the ORM via ``onupdate=func.now()`` on flush).
    * ``project_id`` and ``category`` are the row identity pair —
      ``UNIQUE(project_id, category)`` — and must not be rewritten after
      the fact (DESIGN.md §1.10: "one status row per category per
      project"). :class:`MigrationCategoryStatusUpdate` deliberately
      omits them; the service enforces that contract defensively with an
      ``allowed_fields`` allow-list.
    * ``status`` is constrained by the
      ``ck_migration_category_status_status`` DB CHECK
      (``pending | in_progress | completed | failed``). The Pydantic
      :data:`~backend.schemas.migration_category_status.MigrationCategoryStatusStatus`
      literal mirrors the DB constraint, so the service does not
      revalidate — if an invalid value ever reaches the service (e.g. a
      bypassed schema) the DB CHECK rejects it on flush.
    * Unique constraint on ``(project_id, category)`` is enforced both
      at the DB layer and pre-emptively by :func:`create`, so callers
      receive a clean :class:`ValueError` (HTTP 409 at the router layer)
      instead of a raw :class:`~sqlalchemy.exc.IntegrityError` coming
      out of ``flush``.
    * ``migration_category_status`` has **no** inbound foreign keys —
      no other table references it — so :func:`delete` performs no
      dependency RESTRICT check.
    * List filters (``project_id``, ``category``, ``status``) support
      the Migration module UI — "show every category status for
      project X", "show every failed migration category across a
      project", etc. They mirror the indexed columns on
      ``migration_category_status``.
"""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db.models.migration import MigrationCategoryStatus
from backend.schemas.migration_category_status import (
    MigrationCategoryStatusCreate,
    MigrationCategoryStatusStatus,
    MigrationCategoryStatusUpdate,
)


def list_migration_category_statuses(
    db: Session,
    *,
    project_id: Optional[UUID] = None,
    category: Optional[str] = None,
    status: Optional[MigrationCategoryStatusStatus] = None,
    limit: int = 100,
    offset: int = 0,
) -> list[MigrationCategoryStatus]:
    """Return migration category status rows filtered by the supplied criteria.

    Results are ordered by ``created_at DESC`` so the most recently
    created category status rows appear first, matching the Migration
    module UI convention (latest entries on top).

    Args:
        db: Active SQLAlchemy session.
        project_id: Optional project filter — restrict to rows
            belonging to a specific project.
        category: Optional category filter (e.g. ``'PAB'``, ``'GSC'``,
            ``'STK'``, ``'TSH'``).
        status: Optional lifecycle-status filter (``pending`` |
            ``in_progress`` | ``completed`` | ``failed``).
        limit: Maximum number of rows to return.
        offset: Number of rows to skip.

    Returns:
        List of :class:`MigrationCategoryStatus` instances.
    """
    stmt = select(MigrationCategoryStatus)
    if project_id is not None:
        stmt = stmt.where(MigrationCategoryStatus.project_id == project_id)
    if category is not None:
        stmt = stmt.where(MigrationCategoryStatus.category == category)
    if status is not None:
        stmt = stmt.where(MigrationCategoryStatus.status == status)
    stmt = stmt.order_by(MigrationCategoryStatus.created_at.desc()).limit(limit).offset(offset)
    return list(db.execute(stmt).scalars().all())


def get_by_id(db: Session, status_id: UUID) -> MigrationCategoryStatus:
    """Return a single migration category status row by primary key.

    Raises:
        ValueError: If no row with the supplied ``status_id`` exists.
            The router converts this to an HTTP 404 response.
    """
    row = db.get(MigrationCategoryStatus, status_id)
    if row is None:
        raise ValueError(f"MigrationCategoryStatus {status_id} not found")
    return row


def _get_by_project_category(
    db: Session,
    project_id: UUID,
    category: str,
) -> Optional[MigrationCategoryStatus]:
    """Internal helper — look up a row by its ``(project_id, category)`` identity pair."""
    stmt = select(MigrationCategoryStatus).where(
        MigrationCategoryStatus.project_id == project_id,
        MigrationCategoryStatus.category == category,
    )
    return db.execute(stmt).scalar_one_or_none()


def create(
    db: Session,
    data: MigrationCategoryStatusCreate,
) -> MigrationCategoryStatus:
    """Create a new migration category status row.

    ``status`` defaults to the value set by the Pydantic schema / DB
    ``server_default`` when omitted (``pending``), matching the model
    declaration.

    Validates the ``UNIQUE(project_id, category)`` constraint before
    insertion so the caller receives a clean :class:`ValueError` (HTTP
    409 at the router layer) instead of a raw
    :class:`~sqlalchemy.exc.IntegrityError` coming out of ``flush``.
    If the supplied ``project_id`` does not match an existing project,
    the DB-level FK rejects the flush and the error propagates as-is
    (routed at the API layer as a 409/422).

    Args:
        db: Active SQLAlchemy session.
        data: Validated creation payload.

    Returns:
        The newly created and flushed :class:`MigrationCategoryStatus`
        with its server-generated ``id``, ``created_at`` and
        ``updated_at`` populated.

    Raises:
        ValueError: If a row for the supplied ``(project_id, category)``
            pair already exists.
    """
    if _get_by_project_category(db, data.project_id, data.category) is not None:
        raise ValueError(
            f"MigrationCategoryStatus for project_id={data.project_id} category={data.category!r} already exists"
        )

    row = MigrationCategoryStatus(
        project_id=data.project_id,
        category=data.category,
        status=data.status,
        last_run_at=data.last_run_at,
        notes=data.notes,
    )
    db.add(row)
    db.flush()
    return row


def update(
    db: Session,
    status_id: UUID,
    data: MigrationCategoryStatusUpdate,
) -> MigrationCategoryStatus:
    """Partially update a migration category status row.

    Only ``status``, ``last_run_at`` and ``notes`` may be changed.
    ``id``, ``project_id``, ``category``, ``created_at`` and
    ``updated_at`` are immutable — the row identity pair (project,
    category) must not be rewritten after the fact, and ``updated_at``
    is auto-stamped by the ORM on flush via ``onupdate=func.now()``.

    Fields that are ``None`` in the payload are treated as "leave
    unchanged" to support PATCH semantics.

    Raises:
        ValueError: If the row does not exist.
    """
    row = get_by_id(db, status_id)

    update_data = data.model_dump(exclude_unset=True)
    # Defensive guard — the schema already excludes immutable fields,
    # but silently dropping any that slip through keeps the service
    # honest.
    allowed_fields = {"status", "last_run_at", "notes"}

    for field, value in update_data.items():
        if field in allowed_fields and value is not None:
            setattr(row, field, value)

    db.flush()
    return row


def delete(db: Session, status_id: UUID) -> None:
    """Hard-delete a migration category status row.

    ``migration_category_status`` has no inbound FKs — no other table
    references it — so no dependency RESTRICT check is required.

    Raises:
        ValueError: If the row does not exist.
    """
    row = get_by_id(db, status_id)
    db.delete(row)
    db.flush()
