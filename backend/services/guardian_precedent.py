"""Service layer for :class:`~backend.db.models.guardian.GuardianPrecedent`.

Provides the synchronous CRUD surface used by API routers. All methods accept
``db: Session`` as the first argument and only ever call ``session.flush()`` —
transaction commit is the router's responsibility. Errors are signalled via
``ValueError`` so the router can translate them to the appropriate HTTP
status code.

Design notes (per DESIGN.md §1.22 / §4.5 and model constraints):
    * ``pattern_hash`` is the content-addressed identifier and is immutable —
      only ``pattern_description`` and ``verdict`` may be updated.
    * ``created_by`` and ``created_at`` are audit columns — immutable.
    * There are no inbound foreign keys to ``guardian_precedents`` (the
      matching against findings happens via ``pattern_hash`` comparison, not
      FK), so ``delete`` has no dependency checks.
    * ``verdict`` filter on ``list`` mirrors the common "show all 'allow'
      precedents" UI query; ``created_by`` filter supports per-user audit
      views.
"""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db.models.guardian import GuardianPrecedent
from backend.schemas.guardian import (
    GuardianPrecedentCreate,
    GuardianPrecedentUpdate,
    GuardianVerdict,
)


def list_precedents(
    db: Session,
    *,
    verdict: Optional[GuardianVerdict] = None,
    created_by: Optional[UUID] = None,
    limit: int = 100,
    offset: int = 0,
) -> list[GuardianPrecedent]:
    """Return Guardian precedents filtered by the supplied criteria.

    The result is ordered by ``created_at DESC`` so the most recently added
    precedents appear first, matching the UI's audit-log expectations.

    Args:
        db: Active SQLAlchemy session.
        verdict: Optional verdict filter (``allow`` | ``notice`` | ``block``).
        created_by: Optional filter restricting results to precedents created
            by a specific user. ``None`` (the default) returns rows for all
            users — it does **not** filter to system-seeded precedents.
        limit: Maximum number of rows to return.
        offset: Number of rows to skip.

    Returns:
        List of :class:`GuardianPrecedent` instances.
    """
    stmt = select(GuardianPrecedent)
    if verdict is not None:
        stmt = stmt.where(GuardianPrecedent.verdict == verdict)
    if created_by is not None:
        stmt = stmt.where(GuardianPrecedent.created_by == created_by)
    stmt = stmt.order_by(GuardianPrecedent.created_at.desc()).limit(limit).offset(offset)
    return list(db.execute(stmt).scalars().all())


def get_by_id(db: Session, precedent_id: UUID) -> GuardianPrecedent:
    """Return a single Guardian precedent by primary key.

    Raises:
        ValueError: If no precedent with the supplied ``precedent_id`` exists.
            The router converts this to an HTTP 404 response.
    """
    precedent = db.get(GuardianPrecedent, precedent_id)
    if precedent is None:
        raise ValueError(f"GuardianPrecedent {precedent_id} not found")
    return precedent


def _get_by_pattern_hash(db: Session, pattern_hash: str) -> Optional[GuardianPrecedent]:
    """Internal helper — look up a precedent by its content-addressed hash."""
    stmt = select(GuardianPrecedent).where(GuardianPrecedent.pattern_hash == pattern_hash)
    return db.execute(stmt).scalar_one_or_none()


def create(db: Session, data: GuardianPrecedentCreate) -> GuardianPrecedent:
    """Create a new Guardian precedent.

    Validates the unique ``pattern_hash`` constraint before insertion so the
    caller receives a clean :class:`ValueError` (HTTP 409 at the router
    layer) instead of a raw :class:`~sqlalchemy.exc.IntegrityError` coming
    out of ``flush``.

    Args:
        db: Active SQLAlchemy session.
        data: Validated creation payload.

    Returns:
        The newly created and flushed :class:`GuardianPrecedent` with its
        server-generated ``id`` and ``created_at`` populated.

    Raises:
        ValueError: If a precedent with the same ``pattern_hash`` already
            exists.
    """
    if _get_by_pattern_hash(db, data.pattern_hash) is not None:
        raise ValueError(f"GuardianPrecedent with pattern_hash {data.pattern_hash!r} already exists")

    precedent = GuardianPrecedent(
        pattern_hash=data.pattern_hash,
        pattern_description=data.pattern_description,
        verdict=data.verdict,
        created_by=data.created_by,
    )
    db.add(precedent)
    db.flush()
    return precedent


def update(
    db: Session,
    precedent_id: UUID,
    data: GuardianPrecedentUpdate,
) -> GuardianPrecedent:
    """Partially update a Guardian precedent.

    Only ``pattern_description`` and ``verdict`` are updatable — the hash and
    audit columns are immutable (see module docstring). ``None`` values in
    the payload are treated as "leave unchanged" to support PATCH semantics.

    Raises:
        ValueError: If the precedent does not exist.
    """
    precedent = get_by_id(db, precedent_id)

    update_data = data.model_dump(exclude_unset=True)
    # Defensive guard — the schema already excludes immutable fields, but
    # silently dropping any that slip through keeps the service honest.
    allowed_fields = {"pattern_description", "verdict"}
    for field, value in update_data.items():
        if field in allowed_fields and value is not None:
            setattr(precedent, field, value)

    db.flush()
    return precedent


def delete(db: Session, precedent_id: UUID) -> None:
    """Delete a Guardian precedent.

    There are no inbound FKs to ``guardian_precedents`` (precedent matching
    against findings is done by ``pattern_hash`` comparison, not FK), so
    no dependency check is required.

    Raises:
        ValueError: If the precedent does not exist.
    """
    precedent = get_by_id(db, precedent_id)
    db.delete(precedent)
    db.flush()
