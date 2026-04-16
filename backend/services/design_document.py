"""Service layer for :class:`~backend.db.models.specifications.DesignDocument`.

Provides the synchronous CRUD surface used by API routers. All methods
accept ``db: Session`` as the first argument and only ever call
``session.flush()`` — transaction commit is the router's responsibility.
Errors are signalled via :class:`ValueError` so the router can translate
them to the appropriate HTTP status code.

Design notes (per DESIGN.md §1.9 DesignDocument, §2 ``design_documents``
table, D-04 Per-module DESIGN.md, §1.5 Architect context injection
("Foundation DESIGN.md == ``module_id IS NULL AND doc_type='design'``")
and :mod:`backend.db.models.specifications.DesignDocument`):

    * ``id``, ``created_at`` and ``updated_at`` are server-managed and
      therefore immutable from the service layer (``updated_at`` is
      auto-stamped by the ORM via ``onupdate=func.now()`` on flush).
    * ``project_id`` is an immutable foreign key — a document belongs
      to exactly one project for its lifetime (versions are new rows,
      not a reassignment across projects).
      :class:`DesignDocumentUpdate` deliberately omits it and the
      service's ``allowed_fields`` allow-list enforces that contract
      defensively.
    * ``doc_type`` is likewise immutable — it is an identity
      discriminator (``design`` vs ``behavior``) rather than a mutable
      property. Switching a document between DESIGN.md and BEHAVIOR.md
      makes no business sense; the caller is expected to
      :func:`delete` / :func:`create` instead. This mirrors the
      treatment of ``role`` on
      :class:`~backend.schemas.architect_message.ArchitectMessageUpdate`
      and ``direction`` on
      :class:`~backend.schemas.migration_batch.MigrationBatchUpdate`.
    * ``module_id`` remains mutable: ``NULL`` denotes a Foundation /
      project-level document (DESIGN.md §1.5 "Foundation DESIGN.md ==
      ``module_id IS NULL``") and the DB-level ``ON DELETE SET NULL``
      naturally expresses the same transition when the referenced
      module is removed. In-place re-scoping of an existing document is
      rare but expressible.
    * ``doc_type`` is constrained by the
      ``ck_design_documents_doc_type`` DB CHECK (``design |
      behavior``). The Pydantic
      :data:`~backend.schemas.design_document.DesignDocumentType`
      literal mirrors the DB constraint, so the service does not
      revalidate — if an invalid value ever reaches the service (e.g.
      a bypassed schema) the DB CHECK rejects it on flush.
    * :class:`DesignDocument` has **no** UNIQUE constraints beyond the
      PK. The composite index
      ``ix_design_documents_project_module_type`` on
      ``(project_id, module_id, doc_type)`` is non-unique — a single
      ``(project, module, doc_type)`` triple may legitimately contain
      many rows, one per ``version``. :func:`create` therefore
      performs no pre-flush natural-key check.
    * Convenience behaviour: when :func:`update` sets ``approved_by``
      to a non-``None`` value and the caller did not explicitly supply
      ``approved_at`` in the same payload, the service stamps
      ``approved_at = now()`` automatically so the UI doesn't have to.
      Explicit ``approved_at`` values always win, so backfill /
      correction flows remain possible. This mirrors the
      ``resolved_at`` auto-stamp pattern in :mod:`backend.services.bug`
      and the ``closed_at`` auto-stamp pattern in
      :mod:`backend.services.architect_session`.
    * ``design_documents`` has **no inbound foreign keys** — no other
      table references it. :func:`delete` therefore performs no
      RESTRICT dependency check and is a straightforward hard-delete.
      In normal operation documents are retained as version history;
      :func:`delete` is reserved for test fixtures / admin tooling.
      The outbound FKs ``project_id`` (``ON DELETE CASCADE``),
      ``module_id`` (``ON DELETE SET NULL``) and ``approved_by``
      (``ON DELETE RESTRICT``) keep the row self-consistent when the
      parent rows change.
    * List filters (``project_id``, ``module_id``, ``doc_type``,
      ``approved_by``) match the indexed columns
      (``ix_design_documents_project_id``,
      ``ix_design_documents_module_id``,
      ``ix_design_documents_project_module_type``) and support the
      Architect context-injection flow (DESIGN.md §1.5: "Foundation
      DESIGN.md == ``module_id IS NULL AND doc_type='design'``") and
      the Specification Pipeline UI (DESIGN.md §3.1
      ``SpecificationPage`` / ``DesignDocViewer`` with version
      history) — "load the Foundation DESIGN.md for this project",
      "load the BEHAVIOR.md for this module", "show unapproved
      documents pending ri review".
    * List ordering is ``created_at DESC`` so the newest version
      appears first, matching the version-history UI convention
      (``DesignDocViewer``). In practice ``version`` is monotonically
      incremented on regeneration, so newest-by-``created_at`` is
      equivalent to highest-by-``version`` for any given ``(project,
      module, doc_type)`` triple; ordering by ``created_at`` is used
      for consistency with the rest of the service layer.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db.models.specifications import DesignDocument
from backend.schemas.design_document import (
    DesignDocumentCreate,
    DesignDocumentType,
    DesignDocumentUpdate,
)


def list_design_documents(
    db: Session,
    *,
    project_id: Optional[UUID] = None,
    module_id: Optional[UUID] = None,
    doc_type: Optional[DesignDocumentType] = None,
    approved_by: Optional[UUID] = None,
    limit: int = 100,
    offset: int = 0,
) -> list[DesignDocument]:
    """Return design documents filtered by the supplied criteria.

    Results are ordered by ``created_at DESC`` so the most recently
    created version appears first, matching the Specification Pipeline
    UI convention (``DesignDocViewer`` with version history — newest on
    top).

    Args:
        db: Active SQLAlchemy session.
        project_id: Optional project filter — restrict to documents
            belonging to a specific project (the core Specification
            Pipeline query, DESIGN.md §3.1 ``SpecificationPage``).
        module_id: Optional module filter — restrict to documents
            scoped to a specific module. Pass the module UUID to fetch
            module-level documents; project-level (Foundation) documents
            (``module_id IS NULL``) are filtered out when this argument
            is supplied. To fetch Foundation documents explicitly, use
            ``doc_type`` with a ``project_id`` filter and sort by
            ``created_at``.
        doc_type: Optional document-type filter (``design`` |
            ``behavior``).
        approved_by: Optional approver filter — restrict to documents
            approved by a specific ``ri``-role user.
        limit: Maximum number of rows to return.
        offset: Number of rows to skip.

    Returns:
        List of :class:`DesignDocument` instances.
    """
    stmt = select(DesignDocument)
    if project_id is not None:
        stmt = stmt.where(DesignDocument.project_id == project_id)
    if module_id is not None:
        stmt = stmt.where(DesignDocument.module_id == module_id)
    if doc_type is not None:
        stmt = stmt.where(DesignDocument.doc_type == doc_type)
    if approved_by is not None:
        stmt = stmt.where(DesignDocument.approved_by == approved_by)
    stmt = stmt.order_by(DesignDocument.created_at.desc()).limit(limit).offset(offset)
    return list(db.execute(stmt).scalars().all())


def get_by_id(db: Session, document_id: UUID) -> DesignDocument:
    """Return a single design document by primary key.

    Raises:
        ValueError: If no document with the supplied ``document_id``
            exists. The router converts this to an HTTP 404 response.
    """
    document = db.get(DesignDocument, document_id)
    if document is None:
        raise ValueError(f"DesignDocument {document_id} not found")
    return document


def create(db: Session, data: DesignDocumentCreate) -> DesignDocument:
    """Create a new design or behavior document.

    ``version`` defaults to ``1`` via the Pydantic schema / DB
    ``server_default`` when omitted, matching the model declaration.
    ``module_id`` may be ``None`` to register a Foundation /
    project-level document (DESIGN.md §1.5: "Foundation DESIGN.md ==
    ``module_id IS NULL``"). ``approved_by`` / ``approved_at`` are
    typically ``None`` at creation — a document is approved via a
    subsequent :func:`update` call by a user with the ``ri`` role.

    :class:`DesignDocument` has no UNIQUE constraints beyond the PK,
    so no pre-flush natural-key validation is required; multiple rows
    sharing the same ``(project_id, module_id, doc_type)`` are
    expected and represent version history. If the supplied
    ``project_id``, ``module_id`` or ``approved_by`` foreign keys do
    not match existing rows the DB-level FK rejects the flush and the
    error propagates as-is (routed at the API layer as a 409/422).

    Args:
        db: Active SQLAlchemy session.
        data: Validated creation payload.

    Returns:
        The newly created and flushed :class:`DesignDocument` with its
        server-generated ``id``, ``created_at`` and ``updated_at``
        populated.
    """
    document = DesignDocument(
        project_id=data.project_id,
        module_id=data.module_id,
        doc_type=data.doc_type,
        content=data.content,
        version=data.version,
        approved_by=data.approved_by,
        approved_at=data.approved_at,
    )
    db.add(document)
    db.flush()
    return document


def update(
    db: Session,
    document_id: UUID,
    data: DesignDocumentUpdate,
) -> DesignDocument:
    """Partially update a design document.

    Only ``module_id``, ``content``, ``version``, ``approved_by`` and
    ``approved_at`` may be changed. ``id``, ``project_id``,
    ``doc_type`` and ``created_at`` are immutable — a document belongs
    to exactly one project for its lifetime, ``doc_type`` is an
    identity discriminator rather than a mutable attribute, and
    ``updated_at`` is auto-stamped by the ORM on flush via
    ``onupdate=func.now()``.

    Fields that are ``None`` in the payload are treated as "leave
    unchanged" to support PATCH semantics — ``module_id``,
    ``approved_by`` and ``approved_at`` are therefore sticky once set.
    The explicit-null "downgrade to Foundation-level" or "un-approve"
    transitions are not expressible through this service; they are
    deliberately rare corrections that belong to admin tooling rather
    than the UI.

    Convenience behaviour: when ``approved_by`` transitions from
    ``None`` to a user UUID and the caller did not explicitly supply
    ``approved_at`` in the same payload, the service stamps
    ``approved_at = now()`` automatically. Explicit ``approved_at``
    values always win, so backfill / correction flows remain possible.
    This mirrors the ``resolved_at`` auto-stamp pattern in
    :mod:`backend.services.bug` and the ``closed_at`` auto-stamp in
    :mod:`backend.services.architect_session`.

    Raises:
        ValueError: If the document does not exist.
    """
    document = get_by_id(db, document_id)

    update_data = data.model_dump(exclude_unset=True)
    # Defensive guard — the schema already excludes immutable fields,
    # but silently dropping any that slip through keeps the service
    # honest.
    allowed_fields = {
        "module_id",
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
    if new_approved_by is not None and document.approved_by is None and "approved_at" not in update_data:
        auto_approved_at = datetime.now(tz=timezone.utc)

    for field, value in update_data.items():
        if field in allowed_fields and value is not None:
            setattr(document, field, value)

    if auto_approved_at is not None:
        document.approved_at = auto_approved_at

    db.flush()
    return document


def delete(db: Session, document_id: UUID) -> None:
    """Hard-delete a design document.

    ``design_documents`` has no inbound foreign keys — no other table
    references it — so no RESTRICT dependency check is required. The
    outbound FKs (``project_id`` ``ON DELETE CASCADE``, ``module_id``
    ``ON DELETE SET NULL``) keep the row self-consistent when the
    parent rows change; deleting the document itself is the explicit
    inverse. In normal operation documents are retained as version
    history (DESIGN.md §3.1 ``DesignDocViewer`` "version history");
    :func:`delete` is reserved for test fixtures / admin tooling.

    Raises:
        ValueError: If the document does not exist.
    """
    document = get_by_id(db, document_id)
    db.delete(document)
    db.flush()
