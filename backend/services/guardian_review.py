"""Service layer for :class:`~backend.db.models.guardian.GuardianReview`.

Provides the synchronous CRUD surface used by API routers. All methods
accept ``db: Session`` as the first argument and only ever call
``session.flush()`` — transaction commit is the router's responsibility.
Errors are signalled via :class:`ValueError` so the router can translate
them to the appropriate HTTP status code.

Design notes (per DESIGN.md §1.21 GuardianReview, §1.8 ``guardian_reviews``
table and :mod:`backend.db.models.guardian.GuardianReview`):

    * ``id`` and ``created_at`` are server-managed and therefore
      immutable from the service layer. There is **no** ``updated_at``
      column — reviews are conceptually immutable per DESIGN.md §1.21.
    * ``delegation_id`` is the review's parent reference — a review
      belongs to exactly one delegation for its lifetime, so the FK is
      immutable on the service layer. ``delegation_id`` uses
      ``ON DELETE CASCADE`` at the DB level so the review is removed
      automatically when its parent delegation is dropped.
      :class:`GuardianReviewUpdate` deliberately omits this column and
      the service's ``allowed_fields`` allow-list enforces that contract
      defensively.
    * ``layer`` is fixed at creation time (a review for ``layer1``
      cannot become a ``layer2`` review) and is also excluded from the
      update schema and the allow-list.
    * ``risk_level``, ``findings``, ``passed`` and ``duration_ms``
      remain updatable to support post-hoc precedent filtering —
      applying a new ``allow`` precedent may flip ``passed`` from
      ``False`` to ``True`` and prune matched entries from ``findings``
      (DESIGN.md §1.21 / §1.22 interaction).
    * ``layer`` is constrained by the ``ck_guardian_reviews_layer`` DB
      CHECK (``layer1 | layer2 | layer3``) and ``risk_level`` by
      ``ck_guardian_reviews_risk_level`` (``low | medium | high |
      critical``). The Pydantic ``Literal`` aliases mirror those
      constraints, so the service does not revalidate — if an invalid
      value ever reaches the service (e.g. a bypassed schema) the DB
      CHECK rejects it on flush.
    * ``findings`` defaults to ``[]`` and ``passed`` defaults to
      ``False`` via DB-level ``server_default``; the Pydantic schema
      mirrors those defaults so callers may omit them on create.
    * ``guardian_reviews`` has no inbound FKs, so :func:`delete` needs
      no RESTRICT dependency check — simply drop the row. Deletion is
      reserved for test fixtures / admin tooling; routine operation
      retains the full review history.
    * List filters (``delegation_id``, ``layer``, ``risk_level``,
      ``passed``) match the indexed columns
      (``ix_guardian_reviews_delegation_id``,
      ``ix_guardian_reviews_layer``, ``ix_guardian_reviews_risk_level``)
      and cover the natural lookup paths — "show every review for this
      delegation" (the core delegation-scoped query that drives the
      Guardian panel, DESIGN.md §3.1 ``GuardianPanel``), "show all
      Layer 2 reviews", "show all critical-risk reviews", and "show all
      blocking (``passed=False``) reviews".
    * List ordering is ``created_at DESC`` — the most recently recorded
      reviews appear first, matching the reporting / audit-log
      conventions used throughout :mod:`backend.services`.
"""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db.models.guardian import GuardianReview
from backend.schemas.guardian import (
    GuardianReviewCreate,
    GuardianReviewLayer,
    GuardianReviewRiskLevel,
    GuardianReviewUpdate,
)


def list_guardian_reviews(
    db: Session,
    *,
    delegation_id: Optional[UUID] = None,
    layer: Optional[GuardianReviewLayer] = None,
    risk_level: Optional[GuardianReviewRiskLevel] = None,
    passed: Optional[bool] = None,
    limit: int = 100,
    offset: int = 0,
) -> list[GuardianReview]:
    """Return Guardian reviews filtered by the supplied criteria.

    Results are ordered by ``created_at DESC`` so the most recently
    recorded reviews appear first — matching the reporting / audit-log
    conventions used throughout :mod:`backend.services`.

    Args:
        db: Active SQLAlchemy session.
        delegation_id: Optional delegation filter — restrict to reviews
            belonging to a specific delegation (the core
            delegation-scoped query that drives the Guardian panel,
            DESIGN.md §3.1 ``GuardianPanel``).
        layer: Optional pipeline-layer filter (``layer1`` | ``layer2``
            | ``layer3``).
        risk_level: Optional risk-level filter (``low`` | ``medium`` |
            ``high`` | ``critical``).
        passed: Optional blocking-flag filter — ``False`` lists
            blocking reviews (those that stopped the pipeline),
            ``True`` lists reviews that passed cleanly.
        limit: Maximum number of rows to return.
        offset: Number of rows to skip.

    Returns:
        List of :class:`GuardianReview` instances.
    """
    stmt = select(GuardianReview)
    if delegation_id is not None:
        stmt = stmt.where(GuardianReview.delegation_id == delegation_id)
    if layer is not None:
        stmt = stmt.where(GuardianReview.layer == layer)
    if risk_level is not None:
        stmt = stmt.where(GuardianReview.risk_level == risk_level)
    if passed is not None:
        stmt = stmt.where(GuardianReview.passed == passed)
    stmt = stmt.order_by(GuardianReview.created_at.desc()).limit(limit).offset(offset)
    return list(db.execute(stmt).scalars().all())


def get_by_id(db: Session, guardian_review_id: UUID) -> GuardianReview:
    """Return a single Guardian review by primary key.

    Raises:
        ValueError: If no Guardian review with the supplied
            ``guardian_review_id`` exists. The router converts this to
            an HTTP 404 response.
    """
    review = db.get(GuardianReview, guardian_review_id)
    if review is None:
        raise ValueError(f"GuardianReview {guardian_review_id} not found")
    return review


def create(db: Session, data: GuardianReviewCreate) -> GuardianReview:
    """Create a new Guardian review.

    ``findings`` defaults to ``[]`` and ``passed`` to ``False`` via the
    Pydantic schema (mirroring the DB ``server_default`` values) when
    omitted. ``duration_ms`` is optional.

    If the supplied ``delegation_id`` does not match an existing row,
    the DB-level FK rejects the flush and the error propagates as-is
    (routed at the API layer as a 409/422).

    Args:
        db: Active SQLAlchemy session.
        data: Validated creation payload.

    Returns:
        The newly created and flushed :class:`GuardianReview` with its
        server-generated ``id`` and ``created_at`` populated.
    """
    review = GuardianReview(
        delegation_id=data.delegation_id,
        layer=data.layer,
        risk_level=data.risk_level,
        findings=data.findings,
        passed=data.passed,
        duration_ms=data.duration_ms,
    )
    db.add(review)
    db.flush()
    return review


def update(
    db: Session,
    guardian_review_id: UUID,
    data: GuardianReviewUpdate,
) -> GuardianReview:
    """Partially update a Guardian review.

    Only ``risk_level``, ``findings``, ``passed`` and ``duration_ms``
    may be changed. ``id``, ``delegation_id``, ``layer`` and
    ``created_at`` are immutable: the review identity, its parent
    delegation and the pipeline layer that produced it must not be
    rewritten after the fact (DESIGN.md §1.21 "Reviews are immutable").
    Post-hoc precedent filtering is the primary use case — applying a
    new ``allow`` precedent may flip ``passed`` from ``False`` to
    ``True`` and prune matched entries from ``findings``.

    Fields that are ``None`` in the payload are treated as "leave
    unchanged" to support PATCH semantics. ``passed`` is the notable
    exception — it is declared ``Optional[bool]`` in the schema, so
    ``None`` means "omit" and any explicit ``True`` / ``False`` is
    applied. Consequently, the explicit-null transitions on
    ``findings`` and ``duration_ms`` are not expressible through this
    service; those are rare corrections that belong to admin tooling
    rather than the UI.

    Raises:
        ValueError: If the Guardian review does not exist.
    """
    review = get_by_id(db, guardian_review_id)

    update_data = data.model_dump(exclude_unset=True)
    # Defensive guard — the schema already excludes immutable fields,
    # but silently dropping any that slip through keeps the service
    # honest.
    allowed_fields = {
        "risk_level",
        "findings",
        "passed",
        "duration_ms",
    }

    for field, value in update_data.items():
        if field in allowed_fields and value is not None:
            setattr(review, field, value)

    db.flush()
    return review


def delete(db: Session, guardian_review_id: UUID) -> None:
    """Hard-delete a Guardian review.

    ``guardian_reviews`` has no inbound FKs, so no RESTRICT dependency
    check is required — simply drop the row. Deletion is reserved for
    test fixtures / admin tooling; routine operation retains the full
    review history (DESIGN.md §1.21).

    Raises:
        ValueError: If the Guardian review does not exist.
    """
    review = get_by_id(db, guardian_review_id)
    db.delete(review)
    db.flush()
