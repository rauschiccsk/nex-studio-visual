"""Service layer for :class:`~backend.db.models.delegations.AutoFixAttempt`.

Provides the synchronous CRUD surface used by API routers. All methods
accept ``db: Session`` as the first argument and only ever call
``session.flush()`` — transaction commit is the router's responsibility.
Errors are signalled via :class:`ValueError` so the router can translate
them to the appropriate HTTP status code.

Design notes (per DESIGN.md §1.20 AutoFixAttempt, §2 ``auto_fix_attempts``
table and :mod:`backend.db.models.delegations.AutoFixAttempt`):

    * ``id``, ``attempt_number``, ``created_at`` and ``updated_at`` are
      server-managed and therefore immutable from the service layer
      (``updated_at`` is auto-stamped by the ORM via
      ``onupdate=func.now()`` on flush).
    * ``feat_id`` is an immutable foreign key — an auto-fix attempt
      belongs to exactly one feat for its lifetime. :class:`AutoFixAttemptUpdate`
      deliberately omits it and the service's ``allowed_fields``
      allow-list enforces that contract defensively.
    * ``attempt_number`` is auto-assigned by :func:`create` as
      ``MAX(attempt_number) + 1`` for the supplied ``feat_id`` (starts at
      ``1`` for the first attempt per feat). The DB-level
      ``UNIQUE(feat_id, attempt_number)`` constraint
      (``uq_auto_fix_attempts_feat_id_attempt_number``) is re-validated
      defensively before flush so concurrent creates on the same feat —
      rare but possible — still surface as :class:`ValueError` instead
      of raw :class:`~sqlalchemy.exc.IntegrityError`.
    * ``delegation_id`` is optional and points at the auto-fix
      delegation spawned for this attempt. It is typically populated
      shortly after the row is created (once the CC delegation is
      launched). The inbound FK uses ``ON DELETE SET NULL`` at the
      referenced ``delegations`` side, so deleting the delegation later
      silently nulls the reference here.
    * ``auto_fix_attempts`` has no inbound FKs, so :func:`delete` needs
      no RESTRICT dependency check — simply drop the row.
    * List filters (``feat_id``, ``delegation_id``) match the indexed
      column (``ix_auto_fix_attempts_feat_id``) and cover the natural
      lookup paths — "show every attempt for this feat" (feat-scoped
      retry history) and "which attempt spawned this delegation"
      (reverse lookup from the delegation panel).
    * List ordering is ``attempt_number ASC`` — attempts display in
      chronological retry order (attempt 1, attempt 2, …) to mirror the
      numbering convention used throughout the Tasks / Delegation UI
      (DESIGN.md §3.1 ``DelegationStatus`` / ``GuardianPanel``).
"""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.db.models.delegations import AutoFixAttempt
from backend.schemas.auto_fix_attempt import (
    AutoFixAttemptCreate,
    AutoFixAttemptUpdate,
)


def list_auto_fix_attempts(
    db: Session,
    *,
    feat_id: Optional[UUID] = None,
    delegation_id: Optional[UUID] = None,
    limit: int = 100,
    offset: int = 0,
) -> list[AutoFixAttempt]:
    """Return auto-fix attempts filtered by the supplied criteria.

    Results are ordered by ``attempt_number ASC`` so attempts appear in
    their stable, human-readable retry order (attempt 1, attempt 2, …)
    — matching the numbering convention used across the Tasks /
    Delegation UI (DESIGN.md §3.1).

    Args:
        db: Active SQLAlchemy session.
        feat_id: Optional feat filter — restrict to attempts belonging
            to a specific feat (the core feat-scoped retry-history
            query, DESIGN.md §1.20).
        delegation_id: Optional reverse-lookup filter — which attempt
            spawned a given delegation.
        limit: Maximum number of rows to return.
        offset: Number of rows to skip.

    Returns:
        List of :class:`AutoFixAttempt` instances.
    """
    stmt = select(AutoFixAttempt)
    if feat_id is not None:
        stmt = stmt.where(AutoFixAttempt.feat_id == feat_id)
    if delegation_id is not None:
        stmt = stmt.where(AutoFixAttempt.delegation_id == delegation_id)
    stmt = stmt.order_by(AutoFixAttempt.attempt_number.asc()).limit(limit).offset(offset)
    return list(db.execute(stmt).scalars().all())


def get_by_id(db: Session, auto_fix_attempt_id: UUID) -> AutoFixAttempt:
    """Return a single auto-fix attempt by primary key.

    Raises:
        ValueError: If no auto-fix attempt with the supplied
            ``auto_fix_attempt_id`` exists. The router converts this to
            an HTTP 404 response.
    """
    attempt = db.get(AutoFixAttempt, auto_fix_attempt_id)
    if attempt is None:
        raise ValueError(f"AutoFixAttempt {auto_fix_attempt_id} not found")
    return attempt


def _next_attempt_number(db: Session, feat_id: UUID) -> int:
    """Return the next ``attempt_number`` to assign within a feat.

    Scans ``MAX(attempt_number)`` for the supplied ``feat_id`` and
    returns ``max + 1`` (or ``1`` when the feat has no attempts yet).
    The DB-level ``UNIQUE(feat_id, attempt_number)`` constraint is the
    ultimate guard against concurrent duplicates — the service also
    re-checks the pair before flush (see
    :func:`_get_by_feat_and_attempt_number`).
    """
    stmt = select(func.max(AutoFixAttempt.attempt_number)).where(AutoFixAttempt.feat_id == feat_id)
    current_max = db.execute(stmt).scalar()
    return (current_max or 0) + 1


def _get_by_feat_and_attempt_number(
    db: Session,
    feat_id: UUID,
    attempt_number: int,
) -> Optional[AutoFixAttempt]:
    """Internal helper — look up an attempt by the ``(feat_id, attempt_number)`` pair."""
    stmt = select(AutoFixAttempt).where(
        AutoFixAttempt.feat_id == feat_id,
        AutoFixAttempt.attempt_number == attempt_number,
    )
    return db.execute(stmt).scalar_one_or_none()


def create(db: Session, data: AutoFixAttemptCreate) -> AutoFixAttempt:
    """Create a new auto-fix attempt.

    Auto-assigns ``attempt_number`` as ``MAX(attempt_number) + 1`` for
    the supplied ``feat_id``. The computed pair is re-validated against
    the DB unique constraint before flush so a race between concurrent
    creates on the same feat surfaces as a clean :class:`ValueError`
    (HTTP 409 at the router layer) rather than a raw
    :class:`~sqlalchemy.exc.IntegrityError`.

    ``fix_description`` and ``delegation_id`` default to ``NULL`` via
    the Pydantic schema when omitted — they are typically populated
    later via :func:`update` once the fix delegation is spawned /
    completes.

    If the supplied ``feat_id`` foreign key does not match an existing
    row the DB-level FK rejects the flush and the error propagates
    as-is (routed at the API layer as a 409/422).

    Args:
        db: Active SQLAlchemy session.
        data: Validated creation payload.

    Returns:
        The newly created and flushed :class:`AutoFixAttempt` with its
        server-generated ``id``, ``attempt_number``, ``created_at`` and
        ``updated_at`` populated.

    Raises:
        ValueError: If another attempt already uses the same
            ``(feat_id, attempt_number)`` pair (concurrent-create race).
    """
    attempt_number = _next_attempt_number(db, data.feat_id)
    if _get_by_feat_and_attempt_number(db, data.feat_id, attempt_number) is not None:
        raise ValueError(
            f"AutoFixAttempt with feat_id={data.feat_id} and attempt_number={attempt_number} already exists"
        )

    attempt = AutoFixAttempt(
        feat_id=data.feat_id,
        attempt_number=attempt_number,
        error_description=data.error_description,
        fix_description=data.fix_description,
        delegation_id=data.delegation_id,
    )
    db.add(attempt)
    db.flush()
    return attempt


def update(
    db: Session,
    auto_fix_attempt_id: UUID,
    data: AutoFixAttemptUpdate,
) -> AutoFixAttempt:
    """Partially update an auto-fix attempt.

    Only ``error_description``, ``fix_description`` and
    ``delegation_id`` may be changed. ``id``, ``feat_id``,
    ``attempt_number`` and ``created_at`` are immutable — the attempt
    identity and its position within the feat's retry sequence must not
    be rewritten after the fact; ``updated_at`` is auto-stamped by the
    ORM on flush via ``onupdate=func.now()``.

    Fields that are ``None`` in the payload are treated as "leave
    unchanged" to support PATCH semantics. Consequently, the explicit-
    null transitions ``fix_description -> NULL`` and
    ``delegation_id -> NULL`` are not expressible through this service;
    those rare corrections belong to admin tooling rather than the UI
    (and ``delegation_id -> NULL`` already happens automatically on
    delegation deletion via ``ON DELETE SET NULL``).

    Raises:
        ValueError: If the auto-fix attempt does not exist.
    """
    attempt = get_by_id(db, auto_fix_attempt_id)

    update_data = data.model_dump(exclude_unset=True)
    # Defensive guard — the schema already excludes immutable and
    # server-managed fields, but silently dropping any that slip
    # through keeps the service honest.
    allowed_fields = {
        "error_description",
        "fix_description",
        "delegation_id",
    }

    for field, value in update_data.items():
        if field in allowed_fields and value is not None:
            setattr(attempt, field, value)

    db.flush()
    return attempt


def delete(db: Session, auto_fix_attempt_id: UUID) -> None:
    """Hard-delete an auto-fix attempt.

    ``auto_fix_attempts`` has no inbound FKs, so no RESTRICT dependency
    check is required — simply drop the row. Deletion is reserved for
    test fixtures / admin tooling; routine operation retains the full
    retry history for reporting (DESIGN.md §1.20).

    Raises:
        ValueError: If the auto-fix attempt does not exist.
    """
    attempt = get_by_id(db, auto_fix_attempt_id)
    db.delete(attempt)
    db.flush()
