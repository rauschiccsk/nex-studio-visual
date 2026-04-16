"""Service layer for :class:`~backend.db.models.migration.MigrationBatch`.

Provides the synchronous CRUD surface used by API routers. All methods
accept ``db: Session`` as the first argument and only ever call
``session.flush()`` — transaction commit is the router's responsibility.
Errors are signalled via :class:`ValueError` so the router can translate
them to the appropriate HTTP status code.

Design notes (per DESIGN.md §1.24 MigrationBatch, DESIGN.md §1.10
Migration Tracking and :mod:`backend.db.models.migration.MigrationBatch`):

    * ``id`` and ``created_at`` are server-managed and therefore
      immutable from the service layer. Unlike the other
      audit-tracked tables, ``migration_batches`` intentionally has no
      ``updated_at`` column — a batch is an append-only run record that
      gets its lifecycle fields (``status``, counts, ``started_at``,
      ``completed_at``) stamped once as the run progresses.
    * ``project_id``, ``category`` and ``direction`` are the batch
      identity triple — they anchor a batch to a concrete ``(project,
      category, direction)`` migration run and must not be rewritten
      after the fact. :class:`MigrationBatchUpdate` deliberately omits
      them, the service enforces that contract defensively with an
      ``allowed_fields`` allow-list.
    * ``status`` is constrained by the
      ``ck_migration_batches_status`` DB CHECK
      (``pending | running | completed | failed``). ``direction`` is
      constrained by the ``ck_migration_batches_direction`` CHECK
      (``extract | load``). The Pydantic
      :data:`~backend.schemas.migration_batch.MigrationBatchStatus` /
      :data:`~backend.schemas.migration_batch.MigrationBatchDirection`
      literals mirror the DB constraints, so the service does not
      revalidate them — if an invalid value ever reaches the service
      (e.g. a bypassed schema) the DB CHECK rejects it on flush.
    * ``migration_batches`` has no natural unique constraint — a
      project/category pair can have many batches (one per run attempt,
      one per direction), so :func:`create` performs no pre-insert
      uniqueness check.
    * ``migration_batches`` has one inbound FK
      (``migration_id_map.batch_id``) with ``ON DELETE SET NULL`` —
      :func:`delete` therefore needs no RESTRICT dependency check; the
      DB preserves the id-map rows with their ``batch_id`` nulled out
      so cross-reference integrity survives a deleted run record
      (DESIGN.md §1.10).
    * List filters (``project_id``, ``category``, ``direction``,
      ``status``) support the Migration module UI — "show every running
      batch for project X", "show every failed GSC load across the
      project", etc. They mirror the indexed columns on
      ``migration_batches``.
"""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db.models.migration import MigrationBatch
from backend.schemas.migration_batch import (
    MigrationBatchCreate,
    MigrationBatchDirection,
    MigrationBatchStatus,
    MigrationBatchUpdate,
)


def list_migration_batches(
    db: Session,
    *,
    project_id: Optional[UUID] = None,
    category: Optional[str] = None,
    direction: Optional[MigrationBatchDirection] = None,
    status: Optional[MigrationBatchStatus] = None,
    limit: int = 100,
    offset: int = 0,
) -> list[MigrationBatch]:
    """Return migration batches filtered by the supplied criteria.

    Results are ordered by ``created_at DESC`` so the most recently
    queued / executed batches appear first, matching the Migration
    module UI convention (latest runs on top).

    Args:
        db: Active SQLAlchemy session.
        project_id: Optional project filter — restrict to batches
            belonging to a specific project.
        category: Optional category filter (e.g. ``'PAB'``, ``'GSC'``,
            ``'STK'``, ``'TSH'``).
        direction: Optional direction filter (``extract`` | ``load``).
        status: Optional lifecycle-status filter (``pending`` |
            ``running`` | ``completed`` | ``failed``).
        limit: Maximum number of rows to return.
        offset: Number of rows to skip.

    Returns:
        List of :class:`MigrationBatch` instances.
    """
    stmt = select(MigrationBatch)
    if project_id is not None:
        stmt = stmt.where(MigrationBatch.project_id == project_id)
    if category is not None:
        stmt = stmt.where(MigrationBatch.category == category)
    if direction is not None:
        stmt = stmt.where(MigrationBatch.direction == direction)
    if status is not None:
        stmt = stmt.where(MigrationBatch.status == status)
    stmt = stmt.order_by(MigrationBatch.created_at.desc()).limit(limit).offset(offset)
    return list(db.execute(stmt).scalars().all())


def get_by_id(db: Session, batch_id: UUID) -> MigrationBatch:
    """Return a single migration batch by primary key.

    Raises:
        ValueError: If no migration batch with the supplied ``batch_id``
            exists. The router converts this to an HTTP 404 response.
    """
    batch = db.get(MigrationBatch, batch_id)
    if batch is None:
        raise ValueError(f"MigrationBatch {batch_id} not found")
    return batch


def create(db: Session, data: MigrationBatchCreate) -> MigrationBatch:
    """Create a new migration batch.

    ``direction``, ``status`` and ``error_count`` default to the values
    set by the Pydantic schema / DB ``server_default`` when omitted
    (``extract`` / ``pending`` / ``0`` respectively), matching the model
    declarations.

    ``migration_batches`` has no natural unique constraint — a project
    may accumulate many batches per ``(category, direction)`` pair as
    runs are retried or re-attempted — so no pre-insert uniqueness
    check is performed. If the supplied ``project_id`` does not match
    an existing project, the DB-level FK rejects the flush and the
    error propagates as-is (routed at the API layer as a 409/422).

    Args:
        db: Active SQLAlchemy session.
        data: Validated creation payload.

    Returns:
        The newly created and flushed :class:`MigrationBatch` with its
        server-generated ``id`` and ``created_at`` populated.
    """
    batch = MigrationBatch(
        project_id=data.project_id,
        category=data.category,
        direction=data.direction,
        status=data.status,
        source_count=data.source_count,
        target_count=data.target_count,
        error_count=data.error_count,
        error_log=data.error_log,
        started_at=data.started_at,
        completed_at=data.completed_at,
    )
    db.add(batch)
    db.flush()
    return batch


def update(
    db: Session,
    batch_id: UUID,
    data: MigrationBatchUpdate,
) -> MigrationBatch:
    """Partially update a migration batch.

    Only ``status``, ``source_count``, ``target_count``, ``error_count``,
    ``error_log``, ``started_at`` and ``completed_at`` may be changed.
    ``id``, ``project_id``, ``category``, ``direction`` and
    ``created_at`` are immutable — the batch identity triple (project,
    category, direction) must not be rewritten after the fact.
    ``migration_batches`` has no ``updated_at`` column (append-only run
    record) so no auto-stamp is required.

    Fields that are ``None`` in the payload are treated as "leave
    unchanged" to support PATCH semantics.

    Raises:
        ValueError: If the migration batch does not exist.
    """
    batch = get_by_id(db, batch_id)

    update_data = data.model_dump(exclude_unset=True)
    # Defensive guard — the schema already excludes immutable fields,
    # but silently dropping any that slip through keeps the service
    # honest.
    allowed_fields = {
        "status",
        "source_count",
        "target_count",
        "error_count",
        "error_log",
        "started_at",
        "completed_at",
    }

    for field, value in update_data.items():
        if field in allowed_fields and value is not None:
            setattr(batch, field, value)

    db.flush()
    return batch


def delete(db: Session, batch_id: UUID) -> None:
    """Hard-delete a migration batch.

    The single inbound FK (``migration_id_map.batch_id``) uses
    ``ON DELETE SET NULL``, so dependent id-map rows are retained with
    their ``batch_id`` nulled out at the DB level (DESIGN.md §1.10) —
    cross-reference integrity of the migrated data survives the loss of
    the run record. No RESTRICT dependency check is required.

    ``status='failed'`` via :func:`update` is the preferred soft-disable
    path for routine operation; :func:`delete` is reserved for test
    fixtures / admin tooling.

    Raises:
        ValueError: If the migration batch does not exist.
    """
    batch = get_by_id(db, batch_id)
    db.delete(batch)
    db.flush()
