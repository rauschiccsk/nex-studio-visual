"""Pydantic schemas for KbDocument domain objects.

Mirrors :mod:`backend.db.models.kb.KbDocument`.  Field names, max
lengths and default values match the SQLAlchemy model exactly so that
``KbDocumentRead.model_validate(kb_document_orm_instance)`` round-trips
cleanly.

Document-category values correspond to the
``ck_kb_documents_doc_category`` CHECK constraint on the
``kb_documents`` table
(``standards | decisions | lessons | patterns | design | behavior |
session``).  The ORM column is a ``String`` type guarded by a DB-level
CHECK rather than a Python Enum, so ``Literal`` is the narrowest
faithful representation — consistent with the approach used in
:mod:`backend.schemas.architect_message`,
:mod:`backend.schemas.architect_session`,
:mod:`backend.schemas.design_document`,
:mod:`backend.schemas.migration_batch`,
:mod:`backend.schemas.project`, :mod:`backend.schemas.project_module`,
:mod:`backend.schemas.guardian` and :mod:`backend.schemas.user`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# Mirrors the CHECK constraint
# ``doc_category IN ('standards','decisions','lessons','patterns','design','behavior','session')``
# on the ``kb_documents`` table.
KbDocumentCategory = Literal[
    "standards",
    "decisions",
    "lessons",
    "patterns",
    "design",
    "behavior",
    "session",
]


class KbDocumentCreate(BaseModel):
    """Payload for creating a new knowledge-base document.

    ``id``, ``created_at`` and ``updated_at`` are server-generated and
    therefore excluded.  Nullable columns default to ``None``.  A
    document with ``project_id IS NULL`` represents an ICC-wide
    document (see DESIGN.md §1.4); a document with ``module_id IS
    NULL`` is project-level (or ICC-wide when ``project_id`` is also
    ``None``).
    """

    project_id: Optional[UUID] = Field(
        default=None,
        description=("Project the document belongs to. ``None`` denotes an ICC-wide knowledge-base document."),
    )
    module_id: Optional[UUID] = Field(
        default=None,
        description=(
            "Optional project module the document is scoped to. ``None`` denotes a project-level or ICC-wide document."
        ),
    )
    title: str = Field(
        ...,
        min_length=1,
        max_length=500,
        description="Human-readable document title.",
    )
    file_path: str = Field(
        ...,
        min_length=1,
        description="Absolute path to the document on the ANDROS filesystem.",
    )
    doc_category: KbDocumentCategory = Field(
        ...,
        description=("Document category: standards | decisions | lessons | patterns | design | behavior | session."),
    )
    qdrant_collection: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=100,
        description="Qdrant collection name holding the vectorised content.",
    )
    qdrant_point_id: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=100,
        description=("Identifier of the Qdrant point for this document; ``None`` until the document has been indexed."),
    )
    indexed_at: Optional[datetime] = Field(
        default=None,
        description="Timestamp of the most recent Qdrant indexing run.",
    )


class KbDocumentUpdate(BaseModel):
    """Partial update for an existing knowledge-base document.

    ``id`` and ``created_at`` are immutable.  ``updated_at`` is managed
    by the ORM via ``onupdate=func.now()`` and must not be set by
    clients.  ``project_id`` is treated as an immutable foreign key —
    a KB document's scope (project-specific vs ICC-wide) is an
    identity attribute rather than a mutable property, consistent with
    the treatment of ``project_id`` on
    :class:`~backend.schemas.design_document.DesignDocumentUpdate`.
    ``doc_category`` is likewise immutable: it is an identity
    discriminator (``standards`` vs ``session`` etc.) that determines
    the document's storage location and routing, analogous to
    ``doc_type`` on
    :class:`~backend.schemas.design_document.DesignDocumentUpdate` and
    ``role`` on
    :class:`~backend.schemas.architect_message.ArchitectMessageUpdate`.
    ``module_id`` remains mutable because the DB-level ``ON DELETE SET
    NULL`` semantics and project-level documents are expressed through
    the same column.  All remaining fields are optional to support
    PATCH-style semantics.
    """

    module_id: Optional[UUID] = Field(
        default=None,
        description=("Updated module scope for the document. ``None`` denotes a project-level or ICC-wide document."),
    )
    title: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=500,
        description="Updated human-readable document title.",
    )
    file_path: Optional[str] = Field(
        default=None,
        min_length=1,
        description="Updated absolute path on the ANDROS filesystem.",
    )
    qdrant_collection: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=100,
        description="Updated Qdrant collection name.",
    )
    qdrant_point_id: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=100,
        description="Updated Qdrant point identifier for this document.",
    )
    indexed_at: Optional[datetime] = Field(
        default=None,
        description="Updated timestamp of the most recent Qdrant indexing run.",
    )


class KbDocumentRead(BaseModel):
    """Serialised representation of a knowledge-base document row.

    Mirrors every column on :class:`backend.db.models.kb.KbDocument`.
    ``from_attributes=True`` enables construction directly from an ORM
    instance via ``KbDocumentRead.model_validate(obj)``.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: Optional[UUID] = None
    module_id: Optional[UUID] = None
    title: str = Field(..., min_length=1, max_length=500)
    file_path: str = Field(..., min_length=1)
    doc_category: KbDocumentCategory
    qdrant_collection: Optional[str] = Field(default=None, min_length=1, max_length=100)
    qdrant_point_id: Optional[str] = Field(default=None, min_length=1, max_length=100)
    indexed_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
