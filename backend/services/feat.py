"""Service layer for :class:`~backend.db.models.tasks.Feat`.

Provides the synchronous CRUD surface used by API routers. All methods
accept ``db: Session`` as the first argument and only ever call
``session.flush()`` — transaction commit is the router's responsibility.
Errors are signalled via :class:`ValueError` so the router can translate
them to the appropriate HTTP status code.

Design notes (per DESIGN.md §1.9 Tasks (Epic/Feat/Task hierarchy), §2
``feats`` table, §2.6 ``POST /epics/{id}/feats`` / ``GET /epics/{id}/feats``,
and :mod:`backend.db.models.tasks.Feat`):

    * ``id``, ``number``, ``created_at`` and ``updated_at`` are
      server-managed and therefore immutable from the service layer
      (``updated_at`` is auto-stamped by the ORM via
      ``onupdate=func.now()`` on flush).
    * ``epic_id`` is an immutable foreign key — a feat belongs to
      exactly one epic for its lifetime. :class:`FeatUpdate`
      deliberately omits it and the service's ``allowed_fields``
      allow-list enforces that contract defensively.
    * ``number`` is auto-assigned by :func:`create` as
      ``MAX(number) + 1`` for the supplied ``epic_id`` (starts at
      ``1`` for the first feat in an epic). The DB-level
      ``UNIQUE(epic_id, number)`` constraint
      (``uq_feats_epic_id_number``) is re-validated defensively before
      flush so concurrent creates on the same epic — which are rare but
      possible — still surface as :class:`ValueError` instead of raw
      :class:`~sqlalchemy.exc.IntegrityError`.
    * ``status`` is constrained by the ``ck_feats_status`` DB CHECK
      (``todo | in_progress | done | failed``). The Pydantic
      :data:`~backend.schemas.feat.FeatStatus` literal mirrors the DB
      constraint, so the service does not revalidate — if an invalid
      value ever reaches the service (e.g. a bypassed schema) the DB
      CHECK rejects it on flush.
    * ``task_count`` and ``auto_fix_count`` are server-managed counters
      maintained elsewhere (task-creation hook / auto-fix loop). They
      are not accepted on :class:`FeatCreate` and not in the update
      allow-list — the DB ``server_default='0'`` seeds them on insert.
    * ``actual_minutes`` is normally measured from delegation duration
      but is exposed in :class:`FeatUpdate` for backfill / correction
      flows (consistent with ``resolved_at`` in
      :mod:`backend.schemas.bug`).
    * Inbound FKs on ``feats`` — ``tasks.feat_id``
      (``ON DELETE CASCADE``), ``delegations.feat_id``
      (``ON DELETE SET NULL``) and ``auto_fix_attempts.feat_id``
      (``ON DELETE CASCADE``) — are all handled at the DB level, so
      :func:`delete` needs no RESTRICT dependency check; dependent rows
      are either removed or NULL-ed automatically on flush.
    * List filters (``epic_id``, ``status``) match the indexed columns
      (``ix_feats_epic_id``, ``ix_feats_status``) and support the Tasks
      UI (DESIGN.md §3.1 ``TasksPage`` / ``EpicList`` with per-epic feat
      display and status filtering via ``FeatCard``). ``GET
      /epics/{id}/feats`` (DESIGN.md §2.6) maps directly onto
      ``list_feats(epic_id=...)``.
    * List ordering is ``number ASC`` — feats display in creation order
      (feat 1, feat 2, …) to match the hierarchical-numbering
      convention described in DESIGN.md §1.9 and the ``EpicList``
      collapsible UI. Within an epic the ``number`` column is
      monotonically increasing, so ordering by ``number`` gives a
      stable, human-readable sequence that aligns with the user-facing
      ``{epic.number}.{feat.number}`` identifiers.
"""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.db.models.tasks import Feat
from backend.schemas.feat import (
    FeatCreate,
    FeatStatus,
    FeatUpdate,
)


def list_feats(
    db: Session,
    *,
    epic_id: Optional[UUID] = None,
    status: Optional[FeatStatus] = None,
    limit: int = 100,
    offset: int = 0,
) -> list[Feat]:
    """Return feats filtered by the supplied criteria.

    Results are ordered by ``number ASC`` so feats appear in their
    stable, human-readable numbering order (feat 1, feat 2, …) — this
    matches the hierarchical-numbering convention documented in
    DESIGN.md §1.9 and the ``EpicList`` UI (DESIGN.md §3.1).

    Args:
        db: Active SQLAlchemy session.
        epic_id: Optional epic filter — restrict to feats belonging to
            a specific epic (the core ``GET /epics/{id}/feats`` query,
            DESIGN.md §2.6).
        status: Optional lifecycle-status filter (``todo`` |
            ``in_progress`` | ``done`` | ``failed``).
        limit: Maximum number of rows to return.
        offset: Number of rows to skip.

    Returns:
        List of :class:`Feat` instances.
    """
    stmt = select(Feat)
    if epic_id is not None:
        stmt = stmt.where(Feat.epic_id == epic_id)
    if status is not None:
        stmt = stmt.where(Feat.status == status)
    stmt = stmt.order_by(Feat.number.asc()).limit(limit).offset(offset)
    return list(db.execute(stmt).scalars().all())


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
        status=data.status,
        estimated_minutes=data.estimated_minutes,
    )
    db.add(feat)
    db.flush()
    return feat


def update(db: Session, feat_id: UUID, data: FeatUpdate) -> Feat:
    """Partially update a feat.

    Only ``title``, ``description``, ``status``, ``estimated_minutes``
    and ``actual_minutes`` may be changed. ``id``, ``epic_id``,
    ``number`` and ``created_at`` are immutable — a feat belongs to
    exactly one epic for its lifetime, its position within the epic
    (``number``) must not be rewritten after the fact, and
    ``updated_at`` is auto-stamped by the ORM on flush via
    ``onupdate=func.now()``. ``task_count`` and ``auto_fix_count`` are
    server-managed counters maintained elsewhere and are therefore not
    exposed for direct edits.

    Fields that are ``None`` in the payload are treated as "leave
    unchanged" to support PATCH semantics. Consequently, the
    explicit-null transitions ``estimated_minutes -> NULL`` and
    ``actual_minutes -> NULL`` are not expressible through this
    service; they are rare corrections that belong to admin tooling
    rather than the UI.

    Raises:
        ValueError: If the feat does not exist.
    """
    feat = get_by_id(db, feat_id)

    update_data = data.model_dump(exclude_unset=True)
    # Defensive guard — the schema already excludes immutable and
    # server-managed fields, but silently dropping any that slip
    # through keeps the service honest.
    allowed_fields = {
        "title",
        "description",
        "status",
        "estimated_minutes",
        "actual_minutes",
    }

    for field, value in update_data.items():
        if field in allowed_fields and value is not None:
            setattr(feat, field, value)

    db.flush()
    return feat


def delete(db: Session, feat_id: UUID) -> None:
    """Hard-delete a feat.

    Inbound FKs — ``tasks.feat_id`` (``ON DELETE CASCADE``),
    ``delegations.feat_id`` (``ON DELETE SET NULL``) and
    ``auto_fix_attempts.feat_id`` (``ON DELETE CASCADE``) — are all
    handled at the DB level, so dependent rows are either removed or
    NULL-ed automatically on flush. No RESTRICT dependency check is
    required.

    Raises:
        ValueError: If the feat does not exist.
    """
    feat = get_by_id(db, feat_id)
    db.delete(feat)
    db.flush()
