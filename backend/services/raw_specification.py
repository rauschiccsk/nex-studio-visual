"""Service layer for :class:`~backend.db.models.specifications.RawSpecification`.

Provides the synchronous CRUD surface used by API routers. All methods
accept ``db: Session`` as the first argument and only ever call
``session.flush()`` — transaction commit is the router's responsibility.
Errors are signalled via :class:`ValueError` so the router can translate
them to the appropriate HTTP status code.

Design notes (per DESIGN.md §1.7 RawSpecification, §2
``raw_specifications`` table, §3.1 ``SpecificationPage`` /
``RawSpecInput``, and :mod:`backend.db.models.specifications.RawSpecification`):

    * ``id``, ``created_at`` and ``updated_at`` are server-managed and
      therefore immutable from the service layer (``updated_at`` is
      auto-stamped by the ORM via ``onupdate=func.now()`` on flush).
    * ``project_id`` and ``created_by`` are immutable foreign keys — a
      raw specification belongs to exactly one project and is
      attributed to exactly one uploader for its lifetime (new
      submissions are new rows, not a reassignment).
      :class:`RawSpecificationUpdate` deliberately omits them and the
      service's ``allowed_fields`` allow-list enforces that contract
      defensively. This mirrors the treatment of ``project_id`` and
      ``created_by`` on
      :class:`~backend.schemas.architect_session.ArchitectSessionUpdate`.
    * ``input_format`` is constrained by the
      ``ck_raw_specifications_input_format`` DB CHECK (``text | pdf |
      docx``). The Pydantic
      :data:`~backend.schemas.raw_specification.RawSpecificationInputFormat`
      literal mirrors the DB constraint, so the service does not
      revalidate — if an invalid value ever reaches the service (e.g.
      a bypassed schema) the DB CHECK rejects it on flush.
    * ``status`` is constrained by the
      ``ck_raw_specifications_status`` DB CHECK (``pending |
      processing | done | failed``). The Pydantic
      :data:`~backend.schemas.raw_specification.RawSpecificationStatus`
      literal mirrors the DB constraint. Unlike
      :class:`~backend.db.models.architect.ArchitectSession` and
      :class:`~backend.db.models.bugs.Bug`, ``RawSpecification`` has
      no lifecycle-timestamp column (no ``processed_at`` / ``done_at``
      / ``failed_at``), so no auto-stamp convenience is required — a
      transition between statuses is a simple column update.
    * :class:`RawSpecification` has **no** UNIQUE constraints beyond
      the PK — a project may legitimately hold many raw specifications
      (historical submissions, re-uploads, iterations).
      :func:`create` therefore performs no pre-flush natural-key check.
    * The single inbound FK
      (``professional_specifications.raw_spec_id``) uses
      ``ON DELETE CASCADE``, so dependent AI-generated professional
      specifications are removed automatically at the DB level. No
      RESTRICT dependency check is required in :func:`delete`. In
      normal operation raw specifications are retained as submission
      history; :func:`delete` is reserved for test fixtures / admin
      tooling where the upload itself must go.
    * List filters (``project_id``, ``status``, ``created_by``,
      ``input_format``, ``language``) match the indexed columns
      (``ix_raw_specifications_project_id``,
      ``ix_raw_specifications_status``) and support the Specification
      Pipeline UI (DESIGN.md §3.1 ``SpecificationPage`` /
      ``RawSpecInput``) — "list this project's raw specifications",
      "show uploads still pending AI processing", "show this user's
      submissions", "show only PDF uploads".
    * List ordering is ``created_at DESC`` so the most recently
      uploaded specifications appear first, matching the Specification
      Pipeline UI convention (latest uploads on top), consistent with
      the rest of the service layer.
"""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db.models.specifications import RawSpecification
from backend.schemas.raw_specification import (
    RawSpecificationCreate,
    RawSpecificationInputFormat,
    RawSpecificationStatus,
    RawSpecificationUpdate,
)


def list_raw_specifications(
    db: Session,
    *,
    project_id: Optional[UUID] = None,
    status: Optional[RawSpecificationStatus] = None,
    created_by: Optional[UUID] = None,
    input_format: Optional[RawSpecificationInputFormat] = None,
    language: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> list[RawSpecification]:
    """Return raw specifications filtered by the supplied criteria.

    Results are ordered by ``created_at DESC`` so the most recently
    uploaded specifications appear first, matching the Specification
    Pipeline UI convention (``SpecificationPage`` / ``RawSpecInput`` —
    latest submissions on top).

    Args:
        db: Active SQLAlchemy session.
        project_id: Optional project filter — restrict to specifications
            belonging to a specific project (the core Specification
            Pipeline query, DESIGN.md §3.1 ``SpecificationPage``).
        status: Optional processing-status filter (``pending`` |
            ``processing`` | ``done`` | ``failed``).
        created_by: Optional uploader filter — restrict to
            specifications submitted by a specific user.
        input_format: Optional input-format filter (``text`` | ``pdf``
            | ``docx``) — restrict to a particular upload modality.
        language: Optional ISO-style language-code filter
            (e.g. ``sk``, ``en``).
        limit: Maximum number of rows to return.
        offset: Number of rows to skip.

    Returns:
        List of :class:`RawSpecification` instances.
    """
    stmt = select(RawSpecification)
    if project_id is not None:
        stmt = stmt.where(RawSpecification.project_id == project_id)
    if status is not None:
        stmt = stmt.where(RawSpecification.status == status)
    if created_by is not None:
        stmt = stmt.where(RawSpecification.created_by == created_by)
    if input_format is not None:
        stmt = stmt.where(RawSpecification.input_format == input_format)
    if language is not None:
        stmt = stmt.where(RawSpecification.language == language)
    stmt = stmt.order_by(RawSpecification.created_at.desc()).limit(limit).offset(offset)
    return list(db.execute(stmt).scalars().all())


def get_by_id(db: Session, spec_id: UUID) -> RawSpecification:
    """Return a single raw specification by primary key.

    Raises:
        ValueError: If no specification with the supplied ``spec_id``
            exists. The router converts this to an HTTP 404 response.
    """
    spec = db.get(RawSpecification, spec_id)
    if spec is None:
        raise ValueError(f"RawSpecification {spec_id} not found")
    return spec


def create(db: Session, data: RawSpecificationCreate) -> RawSpecification:
    """Create a new raw customer specification.

    ``input_format``, ``language`` and ``status`` default to the values
    set by the Pydantic schema / DB ``server_default`` when omitted
    (``text``, ``sk`` and ``pending`` respectively).

    :class:`RawSpecification` has no UNIQUE constraints beyond the PK,
    so no pre-flush natural-key validation is required; if the supplied
    ``project_id`` or ``created_by`` foreign keys do not match existing
    rows the DB-level FK rejects the flush and the error propagates
    as-is (routed at the API layer as a 409/422).

    Args:
        db: Active SQLAlchemy session.
        data: Validated creation payload.

    Returns:
        The newly created and flushed :class:`RawSpecification` with
        its server-generated ``id``, ``created_at`` and ``updated_at``
        populated.
    """
    spec = RawSpecification(
        project_id=data.project_id,
        input_text=data.input_text,
        input_format=data.input_format,
        language=data.language,
        status=data.status,
        created_by=data.created_by,
    )
    db.add(spec)
    db.flush()
    return spec


def update(
    db: Session,
    spec_id: UUID,
    data: RawSpecificationUpdate,
) -> RawSpecification:
    """Partially update a raw specification.

    Only ``input_text``, ``input_format``, ``language`` and ``status``
    may be changed. ``id``, ``project_id``, ``created_by`` and
    ``created_at`` are immutable — a specification belongs to exactly
    one project and uploader for its lifetime (resubmissions are new
    rows, not a reassignment) and ``updated_at`` is auto-stamped by
    the ORM on flush via ``onupdate=func.now()``.

    Fields that are ``None`` in the payload are treated as "leave
    unchanged" to support PATCH semantics.

    Raises:
        ValueError: If the specification does not exist.
    """
    spec = get_by_id(db, spec_id)

    update_data = data.model_dump(exclude_unset=True)
    # Defensive guard — the schema already excludes immutable fields,
    # but silently dropping any that slip through keeps the service
    # honest.
    allowed_fields = {
        "input_text",
        "input_format",
        "language",
        "status",
    }

    for field, value in update_data.items():
        if field in allowed_fields and value is not None:
            setattr(spec, field, value)

    db.flush()
    return spec


def delete(db: Session, spec_id: UUID) -> None:
    """Hard-delete a raw specification.

    The single inbound FK
    (``professional_specifications.raw_spec_id``) uses
    ``ON DELETE CASCADE``, so dependent AI-generated professional
    specifications are removed automatically at the DB level. No
    RESTRICT dependency check is required. In normal operation raw
    specifications are retained as submission history (DESIGN.md §3.1
    ``SpecificationPage``); :func:`delete` is reserved for test
    fixtures / admin tooling where the upload itself must go.

    Raises:
        ValueError: If the specification does not exist.
    """
    spec = get_by_id(db, spec_id)
    db.delete(spec)
    db.flush()
