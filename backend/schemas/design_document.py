"""Pydantic schemas for DesignDocument domain objects.

Mirrors :mod:`backend.db.models.specifications.DesignDocument`.  Field
names, max lengths and default values match the SQLAlchemy model
exactly so that
``DesignDocumentRead.model_validate(design_document_orm_instance)``
round-trips cleanly.

Document-type values correspond to the
``ck_design_documents_doc_type`` CHECK constraint on the
``design_documents`` table (``design | behavior``).  The ORM column is
a ``String`` type guarded by a DB-level CHECK rather than a Python
Enum, so ``Literal`` is the narrowest faithful representation —
consistent with the approach used in
:mod:`backend.schemas.architect_message`,
:mod:`backend.schemas.architect_session`,
:mod:`backend.schemas.migration_batch`,
:mod:`backend.schemas.project`, :mod:`backend.schemas.project_module`,
:mod:`backend.schemas.guardian` and :mod:`backend.schemas.user`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# Mirrors the CHECK constraint `doc_type IN ('design', 'behavior')`
# on the ``design_documents`` table.
DesignDocumentType = Literal["design", "behavior"]


class DesignDocumentCreate(BaseModel):
    """Payload for creating a new design or behavior document.

    ``id``, ``created_at`` and ``updated_at`` are server-generated and
    therefore excluded.  ``version`` defaults to the value set by the
    DB-level ``server_default`` (``1``) so callers may omit it.
    Nullable columns default to ``None``.  A document with ``module_id
    IS NULL`` represents a Foundation/project-level document (see
    DESIGN.md D-04).
    """

    project_id: UUID = Field(
        ...,
        description="Project the design document belongs to.",
    )
    module_id: Optional[UUID] = Field(
        default=None,
        description=(
            "Optional project module the document is scoped to. ``None`` denotes a Foundation/project-level document."
        ),
    )
    doc_type: DesignDocumentType = Field(
        ...,
        description="Document type: design | behavior.",
    )
    content: str = Field(
        ...,
        min_length=1,
        description="Full markdown content of the document.",
    )
    version: int = Field(
        default=1,
        ge=1,
        description="Monotonic version number, incremented on each regeneration or edit.",
    )
    approved_by: Optional[UUID] = Field(
        default=None,
        description="User (typically ``ri`` role) who approved the document.",
    )
    approved_at: Optional[datetime] = Field(
        default=None,
        description="Timestamp when the document was approved.",
    )


class DesignDocumentUpdate(BaseModel):
    """Partial update for an existing design or behavior document.

    ``id`` and ``created_at`` are immutable.  ``updated_at`` is managed
    by the ORM via ``onupdate=func.now()`` and must not be set by
    clients.  ``project_id`` is an immutable foreign key — a document
    belongs to exactly one project for its lifetime.  ``doc_type`` is
    likewise immutable: it is an identity discriminator (``design`` vs
    ``behavior``) rather than a mutable property — consistent with the
    treatment of ``role`` on
    :class:`~backend.schemas.architect_message.ArchitectMessageUpdate`
    and ``direction`` on
    :class:`~backend.schemas.migration_batch.MigrationBatchUpdate`.
    ``module_id`` remains mutable because the DB-level ``ON DELETE SET
    NULL`` semantics and project-level documents are expressed through
    the same column.  All remaining fields are optional to support
    PATCH-style semantics.
    """

    module_id: Optional[UUID] = Field(
        default=None,
        description=("Updated module scope for the document. ``None`` denotes a Foundation/project-level document."),
    )
    content: Optional[str] = Field(
        default=None,
        min_length=1,
        description="Updated markdown content of the document.",
    )
    version: Optional[int] = Field(
        default=None,
        ge=1,
        description="Updated version number (typically incremented on regeneration).",
    )
    approved_by: Optional[UUID] = Field(
        default=None,
        description="Updated approver (``ri``-role user) for the document.",
    )
    approved_at: Optional[datetime] = Field(
        default=None,
        description="Updated approval timestamp.",
    )


class DesignDocumentRead(BaseModel):
    """Serialised representation of a design document row.

    Mirrors every column on
    :class:`backend.db.models.specifications.DesignDocument`.
    ``from_attributes=True`` enables construction directly from an ORM
    instance via ``DesignDocumentRead.model_validate(obj)``.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    module_id: Optional[UUID] = None
    doc_type: DesignDocumentType
    content: str = Field(..., min_length=1)
    version: int = Field(..., ge=1)
    approved_by: Optional[UUID] = None
    approved_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
