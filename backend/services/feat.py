"""Service layer for :class:`~backend.db.models.tasks.Feat` — read one and create.

The build writes a version's plan through :func:`create`; every function takes ``db: Session`` first and only
flushes — the caller commits. ``number`` is ``MAX(number) + 1`` per epic, re-validated against
``uq_feats_epic_id_number`` so a concurrent create surfaces as :class:`ValueError`. DEV-155 (23.10.2026) removed
update, delete and the listings, which only the first version's API called.
"""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.db.models.tasks import Feat
from backend.schemas.feat import (
    FeatCreate,
)


def get_by_id(db: Session, feat_id: UUID) -> Feat:
    """Return a single feat by primary key.

    Raises:
        ValueError: If no feat with the supplied ``feat_id`` exists.
            The router converts this to an HTTP 404 response.
    """
    feat = db.get(Feat, feat_id)
    if feat is None:
        raise ValueError(f"Feat {feat_id} not found")
    return feat


def _next_feat_number(db: Session, epic_id: UUID) -> int:
    """Return the next ``number`` to assign within an epic.

    Scans ``MAX(number)`` for the supplied ``epic_id`` and returns
    ``max + 1`` (or ``1`` when the epic has no feats yet). The DB-level
    ``UNIQUE(epic_id, number)`` constraint is the ultimate guard against
    concurrent duplicates — the service also re-checks the pair before
    flush (see :func:`_get_by_epic_and_number`).
    """
    stmt = select(func.max(Feat.number)).where(Feat.epic_id == epic_id)
    current_max = db.execute(stmt).scalar()
    return (current_max or 0) + 1


def _get_by_epic_and_number(
    db: Session,
    epic_id: UUID,
    number: int,
) -> Optional[Feat]:
    """Internal helper — look up a feat by the ``(epic_id, number)`` pair."""
    stmt = select(Feat).where(
        Feat.epic_id == epic_id,
        Feat.number == number,
    )
    return db.execute(stmt).scalar_one_or_none()


def create(db: Session, data: FeatCreate) -> Feat:
    """Create a new feat.

    Auto-assigns ``number`` as ``MAX(number) + 1`` for the supplied
    ``epic_id``. The computed pair is re-validated against the DB
    unique constraint before flush so a race between concurrent creates
    on the same epic surfaces as a clean :class:`ValueError` (HTTP 409
    at the router layer) rather than a raw
    :class:`~sqlalchemy.exc.IntegrityError`.

    ``status`` and ``description`` default to ``todo`` / ``""`` via the
    Pydantic schema / DB ``server_default`` when omitted, matching the
    model declaration. ``task_count`` and ``auto_fix_count`` are
    server-managed counters seeded to ``0`` by the DB
    ``server_default`` — they are not accepted on input.

    If the supplied ``epic_id`` foreign key does not match an existing
    row the DB-level FK rejects the flush and the error propagates
    as-is (routed at the API layer as a 409/422).

    Args:
        db: Active SQLAlchemy session.
        data: Validated creation payload.

    Returns:
        The newly created and flushed :class:`Feat` with its
        server-generated ``id``, ``number``, ``task_count``,
        ``auto_fix_count``, ``created_at`` and ``updated_at`` populated.

    Raises:
        ValueError: If another feat already uses the same
            ``(epic_id, number)`` pair (concurrent-create race).
    """
    number = _next_feat_number(db, data.epic_id)
    if _get_by_epic_and_number(db, data.epic_id, number) is not None:
        raise ValueError(f"Feat with epic_id={data.epic_id} and number={number} already exists")

    feat = Feat(
        epic_id=data.epic_id,
        number=number,
        title=data.title,
        description=data.description,
        plain_description=data.plain_description,
        status=data.status,
    )
    db.add(feat)
    db.flush()
    return feat
