"""Service layer for :class:`~backend.db.models.migration.MigrationIdMap`.

Provides the synchronous CRUD surface used by API routers. All methods
accept ``db: Session`` as the first argument and only ever call
``session.flush()`` — transaction commit is the router's responsibility.
Errors are signalled via :class:`ValueError` so the router can translate
them to the appropriate HTTP status code.

Design notes (per DESIGN.md §1.25 MigrationIdMap, DESIGN.md §1.10
Migration Tracking and :mod:`backend.db.models.migration.MigrationIdMap`):

    * ``id``, ``created_at`` and ``updated_at`` are server-managed and
      therefore immutable from the service layer (``updated_at`` is
      auto-stamped by the ORM via ``onupdate=func.now()`` on flush).
    * ``project_id``, ``category`` and ``source_key`` form the row's
      natural key —
      ``UNIQUE(project_id, category, source_key)`` (see
      ``uq_migration_id_map_project_category_source_key``) — and must
      not be rewritten after the fact (DESIGN.md §1.10: "a given source
      key may only map to one target per category per project").
      :class:`MigrationIdMapUpdate` deliberately omits them; the service
      enforces that contract defensively with an ``allowed_fields``
      allow-list.
    * ``target_id`` is a ``VARCHAR(36)`` — a stringified UUID reference
      pointing at whichever new PostgreSQL row the legacy Btrieve key
      now maps to. The value space is not bound by a single FK target
      (different categories map into different destination tables), so
      no referential check is performed at the DB layer beyond the
      length / NOT NULL constraints. The Pydantic schema pins the
      length; the service does not revalidate.
    * Unique constraint on ``(project_id, category, source_key)`` is
      enforced both at the DB layer and pre-emptively by :func:`create`,
      so callers receive a clean :class:`ValueError` (HTTP 409 at the
      router layer) instead of a raw
      :class:`~sqlalchemy.exc.IntegrityError` coming out of ``flush``.
    * ``migration_id_map`` has **no** inbound foreign keys — no other
      table references it — so :func:`delete` performs no dependency
      RESTRICT check. The outbound FKs are ``project_id`` (``ON DELETE
      CASCADE``, so deleting a project cleans up its maps) and
      ``batch_id`` (``ON DELETE SET NULL``, so a batch may be deleted
      without losing the id-map rows).
    * List filters (``project_id``, ``category``, ``source_key``,
      ``batch_id``) support the Migration module UI — "show every
      id-map row for project X", "resolve a specific legacy key across
      the category", "show every mapping produced by batch Y", etc.
      They mirror the indexed columns on ``migration_id_map``.
"""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db.models.migration import MigrationIdMap
from backend.schemas.migration_id_map import (
    MigrationIdMapCreate,
    MigrationIdMapUpdate,
)


def list_migration_id_maps(
    db: Session,
    *,
    project_id: Optional[UUID] = None,
    category: Optional[str] = None,
    source_key: Optional[str] = None,
    batch_id: Optional[UUID] = None,
    limit: int = 100,
    offset: int = 0,
) -> list[MigrationIdMap]:
    """Return migration ID-map rows filtered by the supplied criteria.

    Results are ordered by ``created_at DESC`` so the most recently
    created mappings appear first, matching the Migration module UI
    convention (latest entries on top).

    Args:
        db: Active SQLAlchemy session.
        project_id: Optional project filter — restrict to rows
            belonging to a specific project.
        category: Optional category filter (e.g. ``'PAB'``, ``'GSC'``,
            ``'STK'``, ``'TSH'``).
        source_key: Optional legacy-key filter — look up the target
            for a specific Btrieve source key (typically combined with
            ``project_id`` and ``category`` to hit the natural key).
        batch_id: Optional batch filter — restrict to rows produced by
            a specific migration batch.
        limit: Maximum number of rows to return.
        offset: Number of rows to skip.

    Returns:
        List of :class:`MigrationIdMap` instances.
    """
    stmt = select(MigrationIdMap)
    if project_id is not None:
        stmt = stmt.where(MigrationIdMap.project_id == project_id)
    if category is not None:
        stmt = stmt.where(MigrationIdMap.category == category)
    if source_key is not None:
        stmt = stmt.where(MigrationIdMap.source_key == source_key)
    if batch_id is not None:
        stmt = stmt.where(MigrationIdMap.batch_id == batch_id)
    stmt = stmt.order_by(MigrationIdMap.created_at.desc()).limit(limit).offset(offset)
    return list(db.execute(stmt).scalars().all())


def get_by_id(db: Session, id_map_id: UUID) -> MigrationIdMap:
    """Return a single migration ID-map row by primary key.

    Raises:
        ValueError: If no row with the supplied ``id_map_id`` exists.
            The router converts this to an HTTP 404 response.
    """
    row = db.get(MigrationIdMap, id_map_id)
    if row is None:
        raise ValueError(f"MigrationIdMap {id_map_id} not found")
    return row


def _get_by_natural_key(
    db: Session,
    project_id: UUID,
    category: str,
    source_key: str,
) -> Optional[MigrationIdMap]:
    """Internal helper — look up a row by its ``(project_id, category, source_key)`` natural key."""
    stmt = select(MigrationIdMap).where(
        MigrationIdMap.project_id == project_id,
        MigrationIdMap.category == category,
        MigrationIdMap.source_key == source_key,
    )
    return db.execute(stmt).scalar_one_or_none()


def create(
    db: Session,
    data: MigrationIdMapCreate,
) -> MigrationIdMap:
    """Create a new migration ID-map row.

    Validates the
    ``UNIQUE(project_id, category, source_key)`` constraint before
    insertion so the caller receives a clean :class:`ValueError` (HTTP
    409 at the router layer) instead of a raw
    :class:`~sqlalchemy.exc.IntegrityError` coming out of ``flush``.
    If the supplied ``project_id`` or ``batch_id`` does not match an
    existing row, the DB-level FK rejects the flush and the error
    propagates as-is (routed at the API layer as a 409/422).

    Args:
        db: Active SQLAlchemy session.
        data: Validated creation payload.

    Returns:
        The newly created and flushed :class:`MigrationIdMap` with its
        server-generated ``id``, ``created_at`` and ``updated_at``
        populated.

    Raises:
        ValueError: If a row for the supplied
            ``(project_id, category, source_key)`` triple already exists.
    """
    if _get_by_natural_key(db, data.project_id, data.category, data.source_key) is not None:
        raise ValueError(
            "MigrationIdMap for "
            f"project_id={data.project_id} "
            f"category={data.category!r} "
            f"source_key={data.source_key!r} already exists"
        )

    row = MigrationIdMap(
        project_id=data.project_id,
        category=data.category,
        source_key=data.source_key,
        target_id=data.target_id,
        batch_id=data.batch_id,
    )
    db.add(row)
    db.flush()
    return row


def update(
    db: Session,
    id_map_id: UUID,
    data: MigrationIdMapUpdate,
) -> MigrationIdMap:
    """Partially update a migration ID-map row.

    Only ``target_id`` and ``batch_id`` may be changed. ``id``,
    ``project_id``, ``category``, ``source_key``, ``created_at`` and
    ``updated_at`` are immutable — the natural key (project, category,
    source_key) must not be rewritten after the fact, and
    ``updated_at`` is auto-stamped by the ORM on flush via
    ``onupdate=func.now()``.

    Fields that are ``None`` in the payload are treated as "leave
    unchanged" to support PATCH semantics. Note: this means ``batch_id``
    cannot be cleared through :func:`update` — use a dedicated
    "detach from batch" flow (or rely on ``ON DELETE SET NULL``) if the
    batch reference needs to be removed.

    Raises:
        ValueError: If the row does not exist.
    """
    row = get_by_id(db, id_map_id)

    update_data = data.model_dump(exclude_unset=True)
    # Defensive guard — the schema already excludes immutable fields,
    # but silently dropping any that slip through keeps the service
    # honest.
    allowed_fields = {"target_id", "batch_id"}

    for field, value in update_data.items():
        if field in allowed_fields and value is not None:
            setattr(row, field, value)

    db.flush()
    return row


def delete(db: Session, id_map_id: UUID) -> None:
    """Hard-delete a migration ID-map row.

    ``migration_id_map`` has no inbound FKs — no other table references
    it — so no dependency RESTRICT check is required. The outbound
    ``batch_id`` FK uses ``ON DELETE SET NULL``, so batch-side deletion
    leaves id-map rows intact; this function is the inverse, removing
    the id-map row itself.

    Raises:
        ValueError: If the row does not exist.
    """
    row = get_by_id(db, id_map_id)
    db.delete(row)
    db.flush()
