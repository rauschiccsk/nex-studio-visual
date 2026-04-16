"""Service layer for :class:`~backend.db.models.specifications.ProfessionalSpecification`.

Provides the synchronous CRUD surface used by API routers. All methods
accept ``db: Session`` as the first argument and only ever call
``session.flush()`` — transaction commit is the router's responsibility.
Errors are signalled via :class:`ValueError` so the router can translate
them to the appropriate HTTP status code.

Design notes (per DESIGN.md §1.8 ProfessionalSpecification, §2
``professional_specifications`` table, §6.5 Specification Pipeline, §9
approval gating, §10 pipeline gating and
:mod:`backend.db.models.specifications.ProfessionalSpecification`):

    * ``id``, ``created_at`` and ``updated_at`` are server-managed and
      therefore immutable from the service layer (``updated_at`` is
      auto-stamped by the ORM via ``onupdate=func.now()`` on flush).
    * ``project_id`` and ``raw_spec_id`` are immutable foreign keys — a
      professional specification belongs to exactly one project and is
      derived from exactly one raw specification for its lifetime
      (regenerations are new rows with an incremented ``version``, not a
      reassignment). :class:`ProfessionalSpecificationUpdate`
      deliberately omits them and the service's ``allowed_fields``
      allow-list enforces that contract defensively. This mirrors the
      treatment of ``project_id`` on
      :class:`~backend.schemas.design_document.DesignDocumentUpdate` and
      ``project_id`` / ``created_by`` on
      :class:`~backend.schemas.raw_specification.RawSpecificationUpdate`.
    * :class:`ProfessionalSpecification` has **no** UNIQUE constraints
      beyond the PK — multiple rows sharing the same
      ``(project_id, raw_spec_id)`` pair are expected and represent
      regeneration history (one row per ``version``). :func:`create`
      therefore performs no pre-flush natural-key check.
    * Convenience behaviour: when :func:`update` sets ``approved_by``
      to a non-``None`` value and the caller did not explicitly supply
      ``approved_at`` in the same payload, the service stamps
      ``approved_at = now()`` automatically so the UI doesn't have to.
      Explicit ``approved_at`` values always win, so backfill /
      correction flows remain possible. This mirrors the
      ``approved_at`` auto-stamp pattern in
      :mod:`backend.services.design_document`, the ``resolved_at``
      auto-stamp pattern in :mod:`backend.services.bug` and the
      ``closed_at`` auto-stamp pattern in
      :mod:`backend.services.architect_session`. Approval unlocks
      downstream DESIGN.md generation (DESIGN.md §9 / §10 pipeline
      gating: ``professional_specifications.approved_by`` must be
      non-null before ``design-documents/generate`` can be triggered).
    * ``professional_specifications`` has **no inbound foreign keys** —
      no other table references it. :func:`delete` therefore performs
      no RESTRICT dependency check and is a straightforward
      hard-delete. In normal operation professional specifications are
      retained as version history (DESIGN.md §3.1
      ``SpecificationPage``); :func:`delete` is reserved for test
      fixtures / admin tooling where the generated document itself
      must go. The outbound FKs ``project_id`` (``ON DELETE CASCADE``),
      ``raw_spec_id`` (``ON DELETE CASCADE``) and ``approved_by``
      (``ON DELETE RESTRICT``) keep the row self-consistent when the
      parent rows change.
    * List filters (``project_id``, ``raw_spec_id``, ``approved_by``,
      ``version``) match the indexed columns
      (``ix_professional_specifications_project_id``,
      ``ix_professional_specifications_raw_spec_id``) and support the
      Specification Pipeline UI (DESIGN.md §3.1 ``SpecificationPage`` /
      ``SpecificationViewer`` with version history) — "load this
      project's professional specifications", "load the professional
      specifications derived from this raw specification", "show
      unapproved specifications pending ri review", "fetch a specific
      version for display".
    * List ordering is ``created_at DESC`` so the newest version
      appears first, matching the Specification Pipeline UI convention
      (``SpecificationViewer`` — latest regeneration on top). In
      practice ``version`` is monotonically incremented on
      regeneration, so newest-by-``created_at`` is equivalent to
      highest-by-``version`` for any given ``(project, raw_spec)`` pair;
      ordering by ``created_at`` is used for consistency with the rest
      of the service layer.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db.models.specifications import ProfessionalSpecification
from backend.schemas.professional_specification import (
    ProfessionalSpecificationCreate,
    ProfessionalSpecificationUpdate,
)


def list_professional_specifications(
    db: Session,
    *,
    project_id: Optional[UUID] = None,
    raw_spec_id: Optional[UUID] = None,
    approved_by: Optional[UUID] = None,
    version: Optional[int] = None,
    limit: int = 100,
    offset: int = 0,
) -> list[ProfessionalSpecification]:
    """Return professional specifications filtered by the supplied criteria.

    Results are ordered by ``created_at DESC`` so the most recently
    generated version appears first, matching the Specification Pipeline
    UI convention (``SpecificationViewer`` with version history —
    latest regeneration on top).

    Args:
        db: Active SQLAlchemy session.
        project_id: Optional project filter — restrict to specifications
            belonging to a specific project (the core Specification
            Pipeline query, DESIGN.md §3.1 ``SpecificationPage``).
        raw_spec_id: Optional raw-specification filter — restrict to
            professional specifications derived from a specific raw
            specification (one raw spec can have multiple regenerated
            professional specs, one per ``version``).
        approved_by: Optional approver filter — restrict to
            specifications approved by a specific ``ri``-role user.
        version: Optional version filter — fetch a specific version
            from the regeneration history.
        limit: Maximum number of rows to return.
        offset: Number of rows to skip.

    Returns:
        List of :class:`ProfessionalSpecification` instances.
    """
    stmt = select(ProfessionalSpecification)
    if project_id is not None:
        stmt = stmt.where(ProfessionalSpecification.project_id == project_id)
    if raw_spec_id is not None:
        stmt = stmt.where(ProfessionalSpecification.raw_spec_id == raw_spec_id)
    if approved_by is not None:
        stmt = stmt.where(ProfessionalSpecification.approved_by == approved_by)
    if version is not None:
        stmt = stmt.where(ProfessionalSpecification.version == version)
    stmt = stmt.order_by(ProfessionalSpecification.created_at.desc()).limit(limit).offset(offset)
    return list(db.execute(stmt).scalars().all())


def get_by_id(db: Session, spec_id: UUID) -> ProfessionalSpecification:
    """Return a single professional specification by primary key.

    Raises:
        ValueError: If no specification with the supplied ``spec_id``
            exists. The router converts this to an HTTP 404 response.
    """
    spec = db.get(ProfessionalSpecification, spec_id)
    if spec is None:
        raise ValueError(f"ProfessionalSpecification {spec_id} not found")
    return spec


def create(
    db: Session,
    data: ProfessionalSpecificationCreate,
) -> ProfessionalSpecification:
    """Create a new AI-generated professional specification.

    ``version`` defaults to ``1`` via the Pydantic schema / DB
    ``server_default`` when omitted, matching the model declaration.
    ``approved_by`` / ``approved_at`` are typically ``None`` at
    creation — a specification is approved via a subsequent
    :func:`update` call by a user with the ``ri`` role (DESIGN.md §9
    business rule).

    :class:`ProfessionalSpecification` has no UNIQUE constraints beyond
    the PK, so no pre-flush natural-key validation is required;
    multiple rows sharing the same ``(project_id, raw_spec_id)`` pair
    are expected and represent regeneration history. If the supplied
    ``project_id``, ``raw_spec_id`` or ``approved_by`` foreign keys do
    not match existing rows the DB-level FK rejects the flush and the
    error propagates as-is (routed at the API layer as a 409/422).

    Args:
        db: Active SQLAlchemy session.
        data: Validated creation payload.

    Returns:
        The newly created and flushed
        :class:`ProfessionalSpecification` with its server-generated
        ``id``, ``created_at`` and ``updated_at`` populated.
    """
    spec = ProfessionalSpecification(
        raw_spec_id=data.raw_spec_id,
        project_id=data.project_id,
        content=data.content,
        version=data.version,
        approved_by=data.approved_by,
        approved_at=data.approved_at,
    )
    db.add(spec)
    db.flush()
    return spec


def update(
    db: Session,
    spec_id: UUID,
    data: ProfessionalSpecificationUpdate,
) -> ProfessionalSpecification:
    """Partially update a professional specification.

    Only ``content``, ``version``, ``approved_by`` and ``approved_at``
    may be changed. ``id``, ``project_id``, ``raw_spec_id`` and
    ``created_at`` are immutable — a specification belongs to exactly
    one project and is derived from exactly one raw specification for
    its lifetime (regenerations are new rows with an incremented
    ``version``), and ``updated_at`` is auto-stamped by the ORM on
    flush via ``onupdate=func.now()``.

    Fields that are ``None`` in the payload are treated as "leave
    unchanged" to support PATCH semantics — ``approved_by`` and
    ``approved_at`` are therefore sticky once set. The explicit-null
    "un-approve" transition is not expressible through this service;
    it is a deliberately rare correction that belongs to admin tooling
    rather than the UI.

    Convenience behaviour: when ``approved_by`` transitions from
    ``None`` to a user UUID and the caller did not explicitly supply
    ``approved_at`` in the same payload, the service stamps
    ``approved_at = now()`` automatically. Explicit ``approved_at``
    values always win, so backfill / correction flows remain possible.
    This mirrors the ``approved_at`` auto-stamp pattern in
    :mod:`backend.services.design_document`, the ``resolved_at``
    auto-stamp in :mod:`backend.services.bug` and the ``closed_at``
    auto-stamp in :mod:`backend.services.architect_session`.

    Raises:
        ValueError: If the specification does not exist.
    """
    spec = get_by_id(db, spec_id)

    update_data = data.model_dump(exclude_unset=True)
    # Defensive guard — the schema already excludes immutable fields,
    # but silently dropping any that slip through keeps the service
    # honest.
    allowed_fields = {
        "content",
        "version",
        "approved_by",
        "approved_at",
    }

    new_approved_by = update_data.get("approved_by")
    # Auto-stamp ``approved_at`` on transition into approved state when
    # the caller did not set it explicitly. ``exclude_unset=True``
    # above means the key is present iff the client sent it, so we can
    # distinguish "not supplied" from "explicitly None".
    auto_approved_at: Optional[datetime] = None
    if new_approved_by is not None and spec.approved_by is None and "approved_at" not in update_data:
        auto_approved_at = datetime.now(tz=timezone.utc)

    for field, value in update_data.items():
        if field in allowed_fields and value is not None:
            setattr(spec, field, value)

    if auto_approved_at is not None:
        spec.approved_at = auto_approved_at

    db.flush()
    return spec


def delete(db: Session, spec_id: UUID) -> None:
    """Hard-delete a professional specification.

    ``professional_specifications`` has no inbound foreign keys — no
    other table references it — so no RESTRICT dependency check is
    required. The outbound FKs (``project_id`` ``ON DELETE CASCADE``,
    ``raw_spec_id`` ``ON DELETE CASCADE``, ``approved_by``
    ``ON DELETE RESTRICT``) keep the row self-consistent when the
    parent rows change; deleting the specification itself is the
    explicit inverse. In normal operation professional specifications
    are retained as version history (DESIGN.md §3.1
    ``SpecificationPage``); :func:`delete` is reserved for test
    fixtures / admin tooling.

    Raises:
        ValueError: If the specification does not exist.
    """
    spec = get_by_id(db, spec_id)
    db.delete(spec)
    db.flush()
